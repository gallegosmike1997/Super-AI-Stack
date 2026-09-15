#!/usr/bin/env bash
# Smoke-test runner for Super AI Stack.
# Modes:
#   bash scripts/smoke_local.sh up|smoke|down|test            -> offline heuristic stack (no Ollama)
#   bash scripts/smoke_local.sh ollama-up|ollama-smoke        -> Ollama-backed stack (real LLMs)
# Env knobs: OLLAMA_URL (default http://127.0.0.1:11434), TESTDIR, SESSION_DB_PATH, MEMORY_DATA_DIR.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TESTDIR=${TESTDIR:-/tmp/sas-test}
export MODEL_MAX_TOKENS=${MODEL_MAX_TOKENS:-1024}
export ROUTER_MAX_TOKENS=${ROUTER_MAX_TOKENS:-160}
# Transient transport failures (Ollama swapping models / restarting after an OOM
# kill) are retried with backoff instead of surfacing as an empty answer.
export MODEL_RETRIES=${MODEL_RETRIES:-3}
export MODEL_RETRY_DELAY=${MODEL_RETRY_DELAY:-3}
OLLAMA_URL=${OLLAMA_URL:-http://127.0.0.1:11434}
# Per-layer model tags. Override these to match your hardware, e.g. on a small
# box: GENERAL_MODEL_NAME=llama3.2:3b CODING_MODEL_NAME=qwen2.5-coder:1.5b
ROUTER_MODEL_NAME=${ROUTER_MODEL_NAME:-phi3:mini}
GENERAL_MODEL_NAME=${GENERAL_MODEL_NAME:-llama3.1:latest}
CODING_MODEL_NAME=${CODING_MODEL_NAME:-qwen2.5-coder:7b}
REASONING_MODEL_NAME=${REASONING_MODEL_NAME:-deepseek-r1:8b}
REASONING_FALLBACK_NAME=${REASONING_FALLBACK_NAME:-llama3.1:latest}
VISION_MODEL_NAME=${VISION_MODEL_NAME:-qwen2.5vl:3b}
mkdir -p "$TESTDIR/logs" "$TESTDIR/data"
export SESSION_DB_PATH="${SESSION_DB_PATH:-$TESTDIR/data/sessions.db}"
export MEMORY_DATA_DIR="${MEMORY_DATA_DIR:-$TESTDIR/data}"
cd "$ROOT"

# service module -> port
SVCS="gateway:8000 router:8001 llm_general:8002 llm_coding:8003 memory:8004 llm_reasoning:8005 vision:8006 speech:8007 image_gen:8008 agent:8009"
MOD_gateway="gateway.main:app"
MOD_router="router.main:app"
MOD_llm_general="llm_general.main:app"
MOD_llm_coding="llm_coding.main:app"
MOD_memory="memory.main:app"
MOD_llm_reasoning="llm_reasoning.main:app"
MOD_vision="vision.main:app"
MOD_speech="speech.main:app"
MOD_image_gen="image_gen.main:app"
MOD_agent="agent.main:app"

mod_of() {
  case "$1" in
    gateway) echo "gateway.main:app" ;;
    router) echo "router.main:app" ;;
    llm_general) echo "llm_general.main:app" ;;
    llm_coding) echo "llm_coding.main:app" ;;
    memory) echo "memory.main:app" ;;
    llm_reasoning) echo "llm_reasoning.main:app" ;;
    vision) echo "vision.main:app" ;;
    speech) echo "speech.main:app" ;;
    image_gen) echo "image_gen.main:app" ;;
    agent) echo "agent.main:app" ;;
  esac
}

do_up() {
  # Offline mode: force heuristic router + stub experts.
  unset ROUTER_MODEL_BASE_URL MODEL_BASE_URL PHI3_URL || true
  for pair in $SVCS; do
    name="${pair%%:*}"; port="${pair##*:}"
    if curl -sf "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
      echo "UP   $name :$port (already running)"
      continue
    fi
    # shellcheck disable=SC2086
    nohup "$ROOT/.venv/bin/python" -m uvicorn "$(mod_of "$name")" --host 127.0.0.1 --port "$port" \
      >"$TESTDIR/logs/${name}.log" 2>&1 &
    echo "$!" >"$TESTDIR/${name}.pid"
    echo "START $name :$port pid $!"
  done
  echo "--- waiting for health ---"
  for pair in $SVCS; do
    name="${pair%%:*}"; port="${pair##*:}"
    ok=0
    for _ in $(seq 1 60); do
      if curl -sf "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then ok=1; break; fi
      sleep 1
    done
    if [ "$ok" = "1" ]; then echo "OK   $name :$port"; else echo "FAIL $name :$port (see $TESTDIR/logs/${name}.log)"; fi
  done
}

do_smoke() {
  PY="$ROOT/.venv/bin/python"
  echo "--- smoke tests ---"
  curl -sf http://127.0.0.1:8000/health && echo " <- gateway health"
  curl -sf http://127.0.0.1:8000/ready -o "$TESTDIR/ready.json" && "$PY" - "$TESTDIR/ready.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
assert d["status"]=="ready", d
assert d["dependencies"]["router"]=="ok", d
print("PASS ready, experts ok:", sorted(k for k,v in d["dependencies"].items() if v=="ok"))
PY
  curl -sf http://127.0.0.1:8000/api/stack -o "$TESTDIR/stack.json" && "$PY" - "$TESTDIR/stack.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
assert len(d["catalog"])==8, d["catalog"]
print("PASS catalog:", [(c["layer"],c["model"]) for c in d["catalog"]])
print("services:", {k:(v.get("status"),v.get("model","")) for k,v in d["services"].items()})
PY
  cat >"$TESTDIR/chat_code.json" <<'EOF'
{"message":"write a python script that prints hello","metadata":{}}
EOF
  curl -sf http://127.0.0.1:8000/chat -H 'content-type: application/json' --data-binary @"$TESTDIR/chat_code.json" -o "$TESTDIR/resp_code.json" && "$PY" - "$TESTDIR/resp_code.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
assert d["task_type"]=="code" and d["expert"]=="coding", d
print("PASS code route ->", d["expert"], "|", d["model"], "|", d["response"][:80])
PY
  cat >"$TESTDIR/mem_add.json" <<'EOF'
{"text":"SAS smoke note: stepdown for aluminum is 0.5mm"}
EOF
  curl -sf http://127.0.0.1:8000/api/memory/add -H 'content-type: application/json' --data-binary @"$TESTDIR/mem_add.json" && echo " <- memory add"
  cat >"$TESTDIR/chat_mem.json" <<'EOF'
{"message":"what stepdown do we use for aluminum?","metadata":{"use_memory":true}}
EOF
  curl -sf http://127.0.0.1:8000/chat -H 'content-type: application/json' --data-binary @"$TESTDIR/chat_mem.json" -o "$TESTDIR/resp_mem.json" && "$PY" - "$TESTDIR/resp_mem.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
assert d["task_type"]=="chat" and d["expert"]=="general", d
print("PASS memory chat ->", d["expert"], "|", d["response"][:120])
PY
  cat >"$TESTDIR/chat_img.json" <<'EOF'
{"message":"draw a logo concept for the SAS dashboard","metadata":{}}
EOF
  curl -sf http://127.0.0.1:8000/chat -H 'content-type: application/json' --data-binary @"$TESTDIR/chat_img.json" -o "$TESTDIR/resp_img.json" && "$PY" - "$TESTDIR/resp_img.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
assert d["task_type"]=="image_gen", d
print("PASS image route ->", d["expert"], "| artifacts:", d["artifacts"])
PY
  curl -sf http://127.0.0.1:8000/chat/stream -H 'content-type: application/json' --data-binary @"$TESTDIR/chat_mem.json" -o "$TESTDIR/stream.txt" && grep -q '\[DONE\]' "$TESTDIR/stream.txt" && echo "PASS stream ends with [DONE]"
  curl -sf http://127.0.0.1:8000/ -o "$TESTDIR/index.html" && grep -q 'SUPER AI STACK' "$TESTDIR/index.html" && echo "PASS web UI serves"
  code=$(curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:8000/assets/SAS%20LOGO.png'); [ "$code" = "200" ] && echo "PASS SAS logo asset ($code)" || echo "FAIL logo asset ($code)"
  echo "--- smoke done ---"
}

do_ollama_up() {
  # Ollama mode: point router + experts at real local models.
  # Uses your installed tags; falls back gracefully if deepseek-r1 is still pulling.
  do_up   # start the whole stack first; do_up unsets MODEL_* so this must precede the exports
  export ROUTER_MODEL_BASE_URL="$OLLAMA_URL"
  export ROUTER_MODEL_NAME="${ROUTER_MODEL_NAME:-phi3:mini}"
  export MODEL_API_KEY=""
  restart() { # $1=name $2=port $3+=extra env assignments
    local name="$1" port="$2"; shift 2
    if [ -f "$TESTDIR/${name}.pid" ]; then kill "$(cat "$TESTDIR/${name}.pid")" 2>/dev/null || true; rm -f "$TESTDIR/${name}.pid"; fi
    # shellcheck disable=SC2086
    env "$@" SESSION_DB_PATH="$SESSION_DB_PATH" MEMORY_DATA_DIR="$MEMORY_DATA_DIR" \
      nohup "$ROOT/.venv/bin/python" -m uvicorn "$(mod_of "$name")" --host 127.0.0.1 --port "$port" \
      >"$TESTDIR/logs/${name}.log" 2>&1 &
    echo "$!" >"$TESTDIR/${name}.pid"
    echo "OLLAMA $name :$port pid $! ($*)"
  }
  if curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then echo "Ollama reachable at $OLLAMA_URL"; else echo "WARN: Ollama not reachable at $OLLAMA_URL (is 'ollama serve' running?)"; fi
  REASONING_TAG="$REASONING_MODEL_NAME"
  if ! curl -sf "$OLLAMA_URL/api/show" -H 'content-type: application/json' -d "{\"model\":\"$REASONING_MODEL_NAME\"}" >/dev/null 2>&1; then
    echo "NOTE: $REASONING_MODEL_NAME not ready yet (still pulling?) — reasoning falls back to $REASONING_FALLBACK_NAME until it lands."
    REASONING_TAG="$REASONING_FALLBACK_NAME"
  fi
  restart router 8001 ROUTER_MODEL_BASE_URL="$OLLAMA_URL" ROUTER_MODEL_NAME="$ROUTER_MODEL_NAME" MODEL_BASE_URL="$OLLAMA_URL" MODEL_NAME="$ROUTER_MODEL_NAME" MODEL_TIMEOUT=300
  restart llm_general 8002 MODEL_BASE_URL="$OLLAMA_URL" MODEL_NAME="$GENERAL_MODEL_NAME" MODEL_TIMEOUT=300
  restart llm_coding 8003 MODEL_BASE_URL="$OLLAMA_URL" MODEL_NAME="$CODING_MODEL_NAME" MODEL_TIMEOUT=300
  restart llm_reasoning 8005 MODEL_BASE_URL="$OLLAMA_URL" MODEL_NAME="$REASONING_TAG" MODEL_TIMEOUT=600
  restart vision 8006 MODEL_BASE_URL="$OLLAMA_URL" MODEL_NAME="$VISION_MODEL_NAME" MODEL_TIMEOUT=300
  # The gateway streams tokens straight from Ollama, so it needs the same backend
  # as the experts; otherwise streaming and expert calls hit different servers.
  restart gateway 8000 OLLAMA_URL="$OLLAMA_URL" MODEL_TIMEOUT=300
  echo "--- waiting for health ---"
  for pair in $SVCS; do
    name="${pair%%:*}"; port="${pair##*:}"
    ok=0
    for _ in $(seq 1 60); do
      if curl -sf "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then ok=1; break; fi
      sleep 1
    done
    if [ "$ok" = "1" ]; then echo "OK   $name :$port"; else echo "FAIL $name :$port (see $TESTDIR/logs/${name}.log)"; fi
  done
}

do_ollama_smoke() {
  PY="$ROOT/.venv/bin/python"
  echo "--- ollama smoke (real LLMs, slower) ---"
  cat >"$TESTDIR/chat_ollama.json" <<'EOF'
{"message":"Reply with exactly: OLLAMA_OK","metadata":{}}
EOF
  curl -sf --max-time 180 http://127.0.0.1:8000/chat -H 'content-type: application/json' --data-binary @"$TESTDIR/chat_ollama.json" -o "$TESTDIR/resp_ollama.json" && "$PY" - "$TESTDIR/resp_ollama.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
print("route:", d["task_type"], d["expert"], d["model"])
print("response:", d["response"][:400])
assert "ollamaok" in d["response"].lower().replace("_","").replace(" ",""), "expected OLLAMA_OK marker from real LLM"
print("PASS ollama-backed chat")
PY
  cat >"$TESTDIR/code_ollama.json" <<'EOF'
{"message":"Write a python function add(a,b) that returns a+b. Reply with code only.","metadata":{}}
EOF
  curl -sf --max-time 300 http://127.0.0.1:8000/chat -H 'content-type: application/json' --data-binary @"$TESTDIR/code_ollama.json" -o "$TESTDIR/resp_code_ollama.json" && "$PY" - "$TESTDIR/resp_code_ollama.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
assert d["task_type"]=="code", d
body = d["response"].lower()
assert ("def add" in d["response"]) or ("return a" in body and "b" in body), d["response"][:400]
print("PASS ollama-backed code ->", d["model"], "|", d["response"][:200])
PY
  cat >"$TESTDIR/stream_ollama.json" <<'EOF'
{"message":"Reply with exactly: STREAM_OK","metadata":{}}
EOF
  curl -sf --max-time 300 -N http://127.0.0.1:8000/chat/stream -H 'content-type: application/json' --data-binary @"$TESTDIR/stream_ollama.json" -o "$TESTDIR/stream_ollama.txt" && "$PY" - "$TESTDIR/stream_ollama.txt" <<'PY'
import json,sys
raw=open(sys.argv[1]).read()
events={}
for block in raw.split("\n\n"):
    name,data=None,[]
    for line in block.split("\n"):
        if line.startswith("event:"): name=line[6:].strip()
        elif line.startswith("data:"): data.append(line[5:].lstrip())
    if name and data and data[0]!="[DONE]":
        events.setdefault(name,[]).append("\n".join(data))
meta=json.loads(events.get("meta",["{}"])[0])
text="".join(json.loads(d)["text"] for d in events.get("delta",[]))
print("stream route:", meta.get("task_type"), meta.get("expert"), meta.get("model"), "source="+str(meta.get("source")))
print("stream text:", text[:200])
assert "delta" in events, "no delta frames were streamed"
assert "done" in events, "no done frame"
assert meta.get("expert"), "meta frame missing expert"
assert raw.rstrip().endswith("[DONE]"), "stream must terminate with [DONE]"
assert "streamok" in text.lower().replace("_","").replace(" ",""), f"expected STREAM_OK from streamed answer, got {text[:200]!r}"
print("PASS token stream (meta/delta/done + [DONE])")
PY
  echo "--- ollama smoke done ---"
}

do_down() {
  for pair in $SVCS; do
    name="${pair%%:*}"
    if [ -f "$TESTDIR/${name}.pid" ]; then kill "$(cat "$TESTDIR/${name}.pid")" 2>/dev/null && echo "STOP $name"; rm -f "$TESTDIR/${name}.pid"; fi
  done
}

case "${1:-test}" in
  up) do_up ;;
  smoke) do_smoke ;;
  down) do_down ;;
  test) do_up; do_smoke ;;
  ollama-up) do_ollama_up ;;
  ollama-smoke) do_ollama_smoke ;;
  ollama-test) do_ollama_up; do_ollama_smoke ;;
esac
