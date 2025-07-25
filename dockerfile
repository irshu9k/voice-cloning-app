FROM python:3.9-slim as base

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y \
    # Build tools
    gcc \
    g++ \
    make \
    cmake \
    build-essential \
    # Audio processing libraries
    ffmpeg \
    libsndfile1 \
    libsndfile1-dev \
    libasound2-dev \
    portaudio19-dev \
    # System utilities
    curl \
    wget \
    git \
    # Cleanup
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Stage 2: Python dependencies installation
FROM base as dependencies

# Create app directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Upgrade pip and install Python dependencies
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Pre-download TTS models to reduce startup time
RUN python -c "from TTS.api import TTS; TTS('tts_models/multilingual/multi-dataset/xtts_v2')"

# Stage 3: Final application image
FROM dependencies as final

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Set working directory
WORKDIR /app

# Copy application code
COPY --chown=appuser:appuser . .

# Create necessary directories with proper permissions
RUN mkdir -p uploads outputs voice_embeddings models .cache logs && \
    chown -R appuser:appuser /app

# Set environment variables for the application
ENV PYTHONPATH=/app \
    TTS_CACHE_PATH=/app/.cache \
    PORT=8000 \
    HOST=0.0.0.0 \
    WORKERS=1 \
    LOG_LEVEL=INFO \
    DEVICE=auto

# Create entrypoint script
RUN cat > /app/entrypoint.sh << 'EOF'
#!/bin/bash
set -e

# Function to log messages
log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1"
}

# Check if running as root and switch to appuser if needed
if [ "$(id -u)" = "0" ]; then
    log "Switching to non-root user..."
    exec gosu appuser "$0" "$@"
fi

# Create directories if they don't exist
mkdir -p uploads outputs voice_embeddings logs

# Check GPU availability
if command -v nvidia-smi >/dev/null 2>&1; then
    if nvidia-smi >/dev/null 2>&1; then
        log "GPU detected, enabling CUDA support"
        export DEVICE=cuda
    else
        log "NVIDIA driver not available, using CPU"
        export DEVICE=cpu
    fi
else
    log "No GPU detected, using CPU"
    export DEVICE=cpu
fi

# Check available memory
MEMORY_GB=$(free -g | awk 'NR==2{printf "%.1f", $2}')
log "Available memory: ${MEMORY_GB}GB"

if (( $(echo "$MEMORY_GB < 4" | bc -l) )); then
    log "WARNING: Low memory detected. Performance may be impacted."
fi

# Pre-warm the model
log "Pre-warming TTS model..."
python -c "
import os
os.environ['DEVICE'] = '$DEVICE'
try:
    from models.voice_cloner import VoiceCloner
    cloner = VoiceCloner()
    print('✅ Model loaded successfully')
except Exception as e:
    print(f'❌ Model loading failed: {e}')
    exit(1)
" || {
    log "Model pre-warming failed, but continuing..."
}

# Start the application
log "Starting Voice Cloning API on $HOST:$PORT"
exec python app.py
EOF

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

# Install gosu for proper user switching
RUN apt-get update && apt-get install -y gosu && rm -rf /var/lib/apt/lists/*

# Switch to non-root user
USER appuser

# Expose the port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

# Use entrypoint script
ENTRYPOINT ["/app/entrypoint.sh"]

# Default command
CMD ["python", "app.py"]
