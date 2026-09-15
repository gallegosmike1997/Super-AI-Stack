#!/usr/bin/env bash
set -e

if [ -z "$1" ]; then
  echo "Usage: scripts/bootstrap_service.sh <service_name>"
  exit 1
fi

SERVICE_NAME="$1"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="$PROJECT_ROOT/$SERVICE_NAME"

mkdir -p "$SERVICE_DIR"
cd "$SERVICE_DIR"

cat > requirements.txt <<EOF
fastapi
uvicorn
httpx
pydantic
EOF

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
deactivate

cat > main.py <<EOF
from fastapi import FastAPI

app = FastAPI(title="Super AI Stack - ${SERVICE_NAME}")

@app.get("/health")
def health():
    return {"service": "${SERVICE_NAME}", "status": "ok"}
EOF

echo "[Super AI Stack] Bootstrapped service: ${SERVICE_NAME}"
