#!/usr/bin/env bash
# Tune Ollama for the Super AI Stack on a memory-constrained box.
#
# Why: the stack loads one model per layer (router/general/coding/reasoning/vision).
# Ollama's default OLLAMA_MAX_LOADED_MODELS is 3, so it keeps several multi-GB models
# resident at once. On a 8 GB machine that triggers the kernel OOM killer:
#
#   Out of memory: Killed process ... (llama-server) anon-rss:5408740kB
#   task_memcg=/system.slice/ollama.service
#
# The stack then sees 'Server disconnected without sending a response' /
# 'All connection attempts failed'. Serialising model loads fixes it.
#
# Usage:
#   bash scripts/ollama_tune.sh status      # show current ollama env + loaded models
#   bash scripts/ollama_tune.sh apply       # write systemd override + restart (needs sudo)
#   bash scripts/ollama_tune.sh local       # run a tuned 'ollama serve' on :11435 (no sudo)
#   bash scripts/ollama_tune.sh local-stop  # stop the no-sudo instance
set -uo pipefail

PORT=${PORT:-11435}
OVERRIDE_DIR=/etc/systemd/system/ollama.service.d
OVERRIDE_FILE="$OVERRIDE_DIR/sas-memory.conf"
PIDFILE=${TMPDIR:-/tmp}/sas-ollama-local.pid
LOGFILE=${TMPDIR:-/tmp}/sas-ollama-local.log

# Serialised loading is the important setting; one model resident at a time.
TUNE_ENV=(
  "OLLAMA_MAX_LOADED_MODELS=1"
  "OLLAMA_NUM_PARALLEL=1"
  "OLLAMA_KEEP_ALIVE=5m"
)

do_status() {
  echo "--- loaded models ---"
  ollama ps 2>/dev/null || echo "(ollama not reachable)"
  echo "--- systemd unit environment ---"
  systemctl show ollama -p Environment 2>/dev/null | tr ' ' '\n' | grep -E 'OLLAMA_' || echo "(no OLLAMA_ vars set)"
  echo "--- memory ---"
  free -h | head -3
  echo "--- recent OOM kills of llama-server ---"
  dmesg 2>/dev/null | grep -c 'llama-server' | sed 's/^/oom-related lines: /' || echo "(dmesg needs root)"
}

do_apply() {
  if ! sudo -n true 2>/dev/null; then
    echo "sudo needs a password; writing via sudo (you may be prompted)."
  fi
  sudo mkdir -p "$OVERRIDE_DIR" || exit 1
  {
    echo "[Service]"
    for pair in "${TUNE_ENV[@]}"; do echo "Environment=\"$pair\""; done
  } | sudo tee "$OVERRIDE_FILE" >/dev/null || exit 1
  echo "wrote $OVERRIDE_FILE"
  sudo systemctl daemon-reload || exit 1
  sudo systemctl restart ollama || exit 1
  echo "ollama restarted with serialised model loading (MAX_LOADED_MODELS=1)."
  sleep 3
  do_status
}

do_local() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "already running on :$PORT (pid $(cat "$PIDFILE"))"; exit 0
  fi
  # Ollama stores models per user. When the systemd service runs as 'ollama' its
  # store lives outside your home, so point this instance at wherever blobs are.
  local models_dir=${MODELS_DIR:-$HOME/.ollama/models}
  if [ ! -d "$models_dir/blobs" ] || [ -z "$(ls -A "$models_dir/blobs" 2>/dev/null)" ]; then
    echo "WARNING: $models_dir has no blobs - this instance will report 'model not found'."
    echo "         The system Ollama service keeps models in its own user's home"
    echo "         (often /usr/share/ollama/.ollama/models), which needs root to read."
    echo "         Use 'MODELS_DIR=/path/to/models bash $0 local', or run '$0 apply'"
    echo "         to tune the system service instead (recommended)."
  fi
  echo "starting tuned 'ollama serve' on 127.0.0.1:$PORT (models: $models_dir, log: $LOGFILE)"
  env "${TUNE_ENV[@]}" OLLAMA_HOST="127.0.0.1:$PORT" OLLAMA_MODELS="$models_dir" \
    nohup ollama serve >"$LOGFILE" 2>&1 &
  echo $! >"$PIDFILE"
  for _ in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:$PORT/api/tags" >/dev/null 2>&1; then
      local n
      n=$(curl -sf "http://127.0.0.1:$PORT/api/tags" | grep -o '"name"' | wc -l)
      echo "OK tuned ollama on http://127.0.0.1:$PORT (pid $(cat "$PIDFILE"), models visible: $n)"
      echo "point the stack at it with: OLLAMA_URL=http://127.0.0.1:$PORT"
      return 0
    fi
    sleep 1
  done
  echo "FAIL to start; see $LOGFILE"; tail -20 "$LOGFILE"; return 1
}

do_local_stop() {
  if [ -f "$PIDFILE" ]; then
    kill "$(cat "$PIDFILE")" 2>/dev/null && echo "stopped tuned ollama"
    rm -f "$PIDFILE"
  else
    echo "no tuned instance running"
  fi
}

case "${1:-status}" in
  status) do_status ;;
  apply) do_apply ;;
  local) do_local ;;
  local-stop) do_local_stop ;;
  *) echo "usage: $0 status|apply|local|local-stop"; exit 2 ;;
esac