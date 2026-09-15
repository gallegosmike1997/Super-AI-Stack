#!/usr/bin/env bash
# Start the full stack pointed at Ollama, sharing one model tag across layers.
# Usage: TAG=phi3:mini bash scripts/up_ollama.sh
set -uo pipefail
ROOT="$HOME/Super_AI_Stack"; cd "$ROOT"
TESTDIR=/tmp/sas-test; mkdir -p "$TESTDIR/logs" "$TESTDIR/data"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
TAG="${TAG:-phi3:mini}"
export SESSION_DB_PATH="$TESTDIR/data/sessions.db" MEMORY_DATA_DIR="$TESTDIR/data"

start() { # $1=name $2=port, rest=env assignments
  local name=$1 port=$2; shift 2
  if curl -sf "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
    echo "UP   $name :$port (already running)"; return
  fi
  env "$@" nohup "$ROOT/.venv/bin/python" -m uvicorn "$name.main:app" \
    --host 127.0.0.1 --port "$port" >"$TESTDIR/logs/${name}.log" 2>&1 &
  echo $! >"$TESTDIR/${name}.pid"
  echo "START $name :$port pid $! ($*)"
}

start gateway       8000 OLLAMA_URL="$OLLAMA_URL" MODEL_TIMEOUT=300
start router        8001 MODEL_BASE_URL="$OLLAMA_URL" MODEL_NAME="$TAG" MODEL_TIMEOUT=300
start llm_general   8002 MODEL_BASE_URL="$OLLAMA_URL" MODEL_NAME="$TAG" MODEL_TIMEOUT=300
start llm_coding    8003 MODEL_BASE_URL="$OLLAMA_URL" MODEL_NAME="$TAG" MODEL_TIMEOUT=300
start llm_reasoning 8005 MODEL_BASE_URL="$OLLAMA_URL" MODEL_NAME="$TAG" MODEL_TIMEOUT=300
start vision        8006 MODEL_BASE_URL="$OLLAMA_URL" MODEL_NAME="$TAG" MODEL_TIMEOUT=300
start memory        8004
start speech        8007
start image_gen     8008
start agent         8009

echo "--- health ---"
for pair in gateway:8000 router:8001 llm_general:8002 llm_coding:8003 memory:8004 llm_reasoning:8005 vision:8006 speech:8007 image_gen:8008 agent:8009; do
  name=${pair%%:*}; port=${pair##*:}; ok=0
  for _ in $(seq 1 40); do
    if curl -sf "http://127.0.0.1:$port/health" >/dev/null 2>&1; then ok=1; break; fi
    sleep 1
  done
  if [ "$ok" = 1 ]; then
    printf "OK   %-14s :%s  %s\n" "$name" "$port" "$(curl -s --max-time 3 "http://127.0.0.1:$port/health")"
  else
    printf "FAIL %-14s :%s  (see %s/logs/%s.log)\n" "$name" "$port" "$TESTDIR" "$name"
  fi
done