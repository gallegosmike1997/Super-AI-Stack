FROM python:3.13-slim

ARG INSTALL_SPEECH=false

WORKDIR /app
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir fastapi httpx "uvicorn[standard]" numpy faiss-cpu
RUN pip install --no-cache-dir pypdf
RUN if [ "$INSTALL_SPEECH" = "true" ]; then pip install --no-cache-dir faster-whisper; fi
# Local models can take a while to load on first call; allow for a cold start.
ENV MODEL_TIMEOUT=300
COPY . .