# ---------- Stage 1: Base environment ----------
FROM python:3.9-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TTS_CACHE_PATH=/app/.cache

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Pre-download or warm-up model (optional, helps cold starts)
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

COPY --from=base /usr/local /usr/local
COPY --from=base /app /app

RUN mkdir -p /app/logs /app/voice_embeddings && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "app.py"]
