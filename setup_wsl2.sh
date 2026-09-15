#!/usr/bin/env bash
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[Super AI Stack] WSL2 setup starting..."

sudo apt update && sudo apt install -y \
  python3 python3-venv python3-pip build-essential \
  ffmpeg libsndfile1 wget curl git

echo "[Super AI Stack] Base dependencies installed."

cd "$PROJECT_ROOT"

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip

echo "[Super AI Stack] Root venv created."

services=(
  "gateway"
  "router"
  "llm_general"
  "llm_reasoning"
  "llm_coding"
  "vision"
  "speech"
  "image_gen"
  "memory"
)

for svc in "${services[@]}"; do
  if [ -d "$svc" ]; then
    echo "[Super AI Stack] Setting up service: $svc"
    cd "$svc"
    python3 -m venv venv
    source venv/bin/activate
    if [ -f requirements.txt ]; then
      pip install -r requirements.txt
    fi
    deactivate
    cd "$PROJECT_ROOT"
  fi
done

echo "[Super AI Stack] WSL2 setup complete."
