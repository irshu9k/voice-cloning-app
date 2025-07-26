# ---------- Stage 1: Base environment ----------
FROM python:3.8-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TTS_CACHE_PATH=/app/.cache

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies (with verbose output)
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt --verbose

# Copy project files
COPY . .

# Preload models (optional for performance)
RUN python -c "from app import preload_models; preload_models()"

# ---------- Stage 2: Final container ----------
FROM python:3.9-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TTS_CACHE_PATH=/app/.cache \
    PORT=8000

# Add non-root user
RUN adduser --disabled-password --gecos '' appuser

WORKDIR /app

# Copy Python and app files from base
COPY --from=base /usr/local /usr/local
COPY --from=base /app /app

# Permissions
RUN mkdir -p /app/logs /app/voice_embeddings && chown -R appuser:appuser /app
USER appuser

# Expose app port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Start the app
CMD ["python", "app.py"]
