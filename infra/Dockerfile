# syntax=docker/dockerfile:1
FROM python:3.13-slim

# faster-whisper needs ffmpeg to decode audio containers.
ARG INSTALL_SPEECH=false

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first so a source-only change reuses the cached layer.
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir fastapi httpx "uvicorn[standard]" numpy faiss-cpu pypdf \
    && if [ "$INSTALL_SPEECH" = "true" ]; then \
         pip install --no-cache-dir faster-whisper && apt-get update \
         && apt-get install -y --no-install-recommends ffmpeg \
         && rm -rf /var/lib/apt/lists/*; \
       fi

# Local models can take a while to load on first call; allow for a cold start.
ENV MODEL_TIMEOUT=300

COPY . .

# Run unprivileged; the service volumes are mounted writable by root by
# default, so create them and hand them over.
RUN useradd --create-home --uid 10001 sas \
    && mkdir -p /data \
    && chown -R sas:sas /app /data
USER sas

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${PORT:-8000}/health || exit 1

CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]