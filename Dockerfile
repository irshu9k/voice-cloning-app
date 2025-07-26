# ---------- Stage 2: Final container ----------
FROM python:3.9-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TTS_CACHE_PATH=/app/.cache \
    PORT=8000

# ✅ FIX: Install runtime system dependencies here too
RUN apt-get update && apt-get install -y \
    curl \
    libexpat1 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

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
