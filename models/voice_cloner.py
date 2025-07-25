import os
import json
import uuid
import torch
import asyncio
import logging
import numpy as np
import torchaudio
import librosa
import soundfile as sf

from pathlib import Path
from typing import Dict, List, Optional
from TTS.api import TTS

logger = logging.getLogger(__name__)


class VoiceCloner:
    def __init__(self):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info(f"Initializing Voice Cloner on device: {self.device}")

        try:
            # Load Coqui XTTS model
            logger.info("Loading Coqui XTTS v2 model...")
            self.tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(self.device)
            logger.info("XTTS v2 model loaded successfully!")
        except Exception as e:
            logger.error(f"Failed to load XTTS model: {e}")
            raise

        # Storage for embeddings and metadata
        self.voices_dir = Path("voice_embeddings")
        self.voices_dir.mkdir(exist_ok=True)
        self.voice_metadata_file = self.voices_dir / "metadata.json"
        self.voices = self.load_voices_metadata()
        logger.info(f"Loaded {len(self.voices)} existing voice(s)")

    def load_voices_metadata(self) -> Dict:
        if self.voice_metadata_file.exists():
            try:
                with open(self.voice_metadata_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading voice metadata: {e}")
        return {}

    def save_voices_metadata(self):
        try:
            with open(self.voice_metadata_file, 'w') as f:
                json.dump(self.voices, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving voice metadata: {e}")

    def preprocess_audio(self, audio_path: str, target_sr: int = 22050) -> str:
        try:
            audio, sr = librosa.load(audio_path, sr=None)
            audio, _ = librosa.effects.trim(audio, top_db=20)
            audio = librosa.util.normalize(audio)

            if sr != target_sr:
                audio = librosa.resample(audio, orig_sr=sr, target_sr=target_sr)

            min_length = target_sr * 3
            max_length = target_sr * 30

            if len(audio) < min_length:
                audio = np.tile(audio, int(np.ceil(min_length / len(audio))))[:min_length]
            if len(audio) > max_length:
                audio = audio[:max_length]

            processed_path = audio_path.replace('.', '_processed.')
            sf.write(processed_path, audio, target_sr)
            return processed_path
        except Exception as e:
            logger.error(f"Error preprocessing audio: {e}")
            return audio_path

    async def create_voice_embedding(self, audio_path: str, speaker_name: str) -> bool:
        try:
            processed_audio_path = self.preprocess_audio(audio_path)
            voice_dir = self.voices_dir / speaker_name
            voice_dir.mkdir(exist_ok=True)

            reference_path = voice_dir / "reference.wav"
            waveform, sample_rate = torchaudio.load(processed_audio_path)

            if waveform.shape[0] > 1:
                waveform = torch.mean(waveform, dim=0, keepdim=True)

            if sample_rate != 22050:
                resampler = torchaudio.transforms.Resample(orig_freq=sample_rate, new_freq=22050)
                waveform = resampler(waveform)

            torchaudio.save(reference_path, waveform, 22050)

            test_text = "This is a test of the voice cloning system."
            test_output = voice_dir / "test_sample.wav"

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.tts.tts_to_file(
                    text=test_text,
                    speaker_wav=str(reference_path),
                    language="en",
                    file_path=str(test_output)
                )
            )

            self.voices[speaker_name] = {
                "reference_path": str(reference_path),
                "test_sample_path": str(test_output),
                "created_at": str(uuid.uuid4()),
                "sample_rate": 22050,
                "audio_duration": float(waveform.shape[1] / 22050),
                "status": "active"
            }
            self.save_voices_metadata()

            if processed_audio_path != audio_path and os.path.exists(processed_audio_path):
                os.remove(processed_audio_path)

            logger.info(f"Successfully created voice embedding for: {speaker_name}")
            return True
        except Exception as e:
            logger.error(f"Error creating voice embedding for {speaker_name}: {e}")
            return False

    async def synthesize(self, text: str, speaker_name: str, language: str = "en", speed: float = 1.0) -> str:
        if speaker_name not in self.voices:
            raise ValueError(f"Speaker '{speaker_name}' not found")

        reference_path = self.voices[speaker_name]["reference_path"]
        output_filename = f"output_{speaker_name}_{uuid.uuid4().hex[:8]}.wav"
        output_path = Path("outputs") / output_filename
        output_path.parent.mkdir(exist_ok=True)

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.tts.tts_to_file(
                    text=text,
                    speaker_wav=reference_path,
                    language=language,
                    file_path=str(output_path),
                    split_sentences=True
                )
            )

            if speed != 1.0:
                self._adjust_speed(str(output_path), speed)

            logger.info(f"Successfully synthesized speech for {speaker_name}")
            return str(output_path)
        except Exception as e:
            logger.error(f"Error synthesizing speech: {e}")
            raise

    def _adjust_speed(self, audio_path: str, speed: float):
        try:
            audio, sr = librosa.load(audio_path)
            audio_fast = librosa.effects.time_stretch(audio, rate=speed)
            sf.write(audio_path, audio_fast, sr)
        except Exception as e:
            logger.warning(f"Could not adjust speed: {e}")

    def has_speaker(self, speaker_name: str) -> bool:
        return speaker_name in self.voices

    def list_speakers(self) -> List[str]:
        return list(self.voices.keys())

    def get_speaker_info(self, speaker_name: str) -> Optional[Dict]:
        if speaker_name not in self.voices:
            return None

        info = self.voices[speaker_name].copy()
        reference_path = info.get("reference_path")
        if reference_path and os.path.exists(reference_path):
            info["file_size_mb"] = round(os.path.getsize(reference_path) / (1024 * 1024), 2)
        return info

    def delete_speaker(self, speaker_name: str) -> bool:
        if speaker_name not in self.voices:
            return False
        try:
            voice_dir = self.voices_dir / speaker_name
            if voice_dir.exists():
                import shutil
                shutil.rmtree(voice_dir)
            del self.voices[speaker_name]
            self.save_voices_metadata()
            logger.info(f"Successfully deleted speaker: {speaker_name}")
            return True
        except Exception as e:
            logger.error(f"Error deleting speaker {speaker_name}: {e}")
            return False

    def get_model_info(self) -> Dict:
        return {
            "model_name": "Coqui XTTS v2",
            "device": self.device,
            "languages_supported": [
                "en", "es", "fr", "de", "it", "pt", "pl", "tr", "ru",
                "nl", "cs", "ar", "zh-cn"
            ],
            "features": [
                "Voice cloning from 3-second samples",
                "Cross-language voice cloning",
                "Multi-lingual speech synthesis",
                "Real-time inference capable"
            ]
      }
