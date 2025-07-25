from fastapi import FastAPI, File, UploadFile, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import uuid
import asyncio
from pathlib import Path
from models.voice_cloner import VoiceCloner
import logging
from typing import Optional

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Voice Cloning API",
    version="1.0.0",
    description="Clone voices and generate speech using Coqui XTTS v2"
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize voice cloner
voice_cloner = None


@app.on_event("startup")
async def startup_event():
    """Initialize voice cloner on startup"""
    global voice_cloner
    logger.info("Initializing Voice Cloner...")
    voice_cloner = VoiceCloner()

    # Create directories
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("outputs", exist_ok=True)

    logger.info("Voice Cloning API started successfully!")


def cleanup_old_files():
    """Clean up old temporary files"""
    import time
    current_time = time.time()
    for folder in ["uploads", "outputs"]:
        if os.path.exists(folder):
            for file in os.listdir(folder):
                file_path = os.path.join(folder, file)
                if os.path.isfile(file_path):
                    # Delete files older than 1 hour
                    if current_time - os.path.getctime(file_path) > 3600:
                        try:
                            os.remove(file_path)
                            logger.info(f"Cleaned up old file: {file_path}")
                        except:
                            pass


@app.get("/")
async def root():
    """API status endpoint"""
    return {
        "message": "Voice Cloning API is running!",
        "version": "1.0.0",
        "model": "Coqui XTTS v2",
        "endpoints": {
            "clone_voice": "/clone-voice",
            "synthesize": "/synthesize",
            "speakers": "/speakers",
            "health": "/health"
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "model_loaded": voice_cloner is not None,
        "device": voice_cloner.device if voice_cloner else "unknown"
    }


@app.post("/clone-voice")
async def clone_voice(
    background_tasks: BackgroundTasks,
    audio_file: UploadFile = File(...),
    speaker_name: str = Form(...),
    overwrite: bool = Form(default=False)
):
    """Upload audio file and create voice embedding"""
    if not voice_cloner:
        raise HTTPException(status_code=503, detail="Voice cloner not initialized")

    # Validate file type
    if not audio_file.content_type or not audio_file.content_type.startswith('audio/'):
        raise HTTPException(status_code=400, detail="File must be audio format (WAV, MP3, FLAC)")

    # Check if speaker already exists
    if voice_cloner.has_speaker(speaker_name) and not overwrite:
        raise HTTPException(
            status_code=409,
            detail=f"Speaker '{speaker_name}' already exists. Use overwrite=true to replace."
        )

    # Validate file size (max 50MB)
    max_size = 50 * 1024 * 1024  # 50MB in bytes
    content = await audio_file.read()
    if len(content) > max_size:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 50MB")

    # Save uploaded file
    file_id = str(uuid.uuid4())
    file_extension = os.path.splitext(audio_file.filename)[1] or '.wav'
    file_path = f"uploads/{file_id}_{speaker_name}{file_extension}"

    try:
        with open(file_path, "wb") as buffer:
            buffer.write(content)

        logger.info(f"Processing voice cloning for speaker: {speaker_name}")

        # Create voice embedding
        success = await voice_cloner.create_voice_embedding(
            audio_path=file_path,
            speaker_name=speaker_name
        )

        # Schedule cleanup
        background_tasks.add_task(cleanup_old_files)

        if success:
            logger.info(f"Successfully cloned voice: {speaker_name}")
            return {
                "message": f"Voice '{speaker_name}' cloned successfully",
                "speaker_id": speaker_name,
                "status": "success"
            }
        else:
            raise HTTPException(status_code=500, detail="Voice cloning failed")

    except Exception as e:
        logger.error(f"Error cloning voice: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Voice cloning failed: {str(e)}")

    finally:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass


@app.post("/synthesize")
async def synthesize_speech(
    background_tasks: BackgroundTasks,
    text: str = Form(..., max_length=1000),
    speaker_name: str = Form(...),
    language: str = Form(default="en"),
    speed: float = Form(default=1.0, ge=0.5, le=2.0)
):
    """Generate speech from text using cloned voice"""
    if not voice_cloner:
        raise HTTPException(status_code=503, detail="Voice cloner not initialized")

    if not voice_cloner.has_speaker(speaker_name):
        available_speakers = voice_cloner.list_speakers()
        raise HTTPException(
            status_code=404,
            detail=f"Speaker '{speaker_name}' not found. Available speakers: {available_speakers}"
        )

    if len(text.strip()) == 0:
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    try:
        logger.info(f"Synthesizing speech for speaker: {speaker_name}")
        output_file = await voice_cloner.synthesize(
            text=text,
            speaker_name=speaker_name,
            language=language,
            speed=speed
        )

        background_tasks.add_task(cleanup_old_files)

        download_filename = f"speech_{speaker_name}_{uuid.uuid4().hex[:8]}.wav"

        return FileResponse(
            output_file,
            media_type="audio/wav",
            filename=download_filename,
            headers={"Content-Disposition": f"attachment; filename={download_filename}"}
        )

    except Exception as e:
        logger.error(f"Error synthesizing speech: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Speech synthesis failed: {str(e)}")


@app.get("/speakers")
async def list_speakers():
    """List all available cloned voices"""
    if not voice_cloner:
        raise HTTPException(status_code=503, detail="Voice cloner not initialized")

    speakers = voice_cloner.list_speakers()
    return {
        "speakers": speakers,
        "count": len(speakers),
        "status": "success"
    }


@app.delete("/speaker/{speaker_name}")
async def delete_speaker(speaker_name: str):
    """Delete a cloned voice"""
    if not voice_cloner:
        raise HTTPException(status_code=503, detail="Voice cloner not initialized")

    success = voice_cloner.delete_speaker(speaker_name)
    if success:
        logger.info(f"Deleted speaker: {speaker_name}")
        return {"message": f"Speaker '{speaker_name}' deleted successfully"}
    else:
        raise HTTPException(status_code=404, detail=f"Speaker '{speaker_name}' not found")


@app.get("/speaker/{speaker_name}/info")
async def get_speaker_info(speaker_name: str):
    """Get information about a specific speaker"""
    if not voice_cloner:
        raise HTTPException(status_code=503, detail="Voice cloner not initialized")

    if not voice_cloner.has_speaker(speaker_name):
        raise HTTPException(status_code=404, detail=f"Speaker '{speaker_name}' not found")

    info = voice_cloner.get_speaker_info(speaker_name)
    return {
        "speaker_name": speaker_name,
        "info": info,
        "status": "success"
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
