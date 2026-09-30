# Super AI Stack

One API in front of a local Mixture-of-Experts stack. The router classifies each
request, the gateway dispatches it to the right expert, and memory is injected
as context along the way.

```text
client -> gateway -> router (Phi-3) -> memory (FAISS) -> expert / agent
                    -> general / reasoning / coding
                    -> vision / speech / image generation
```

## Quick start

```bash
git clone https://github.com/gallegosmike1997/Super-AI-Stack
cd Super-AI-Stack

python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

pip install -e ".[dev]"
cp .env.example .env      # Windows: Copy-Item .env.example .env

python scripts/dev.py up  # start every service, wait until healthy
```

The console is then at <http://127.0.0.1:8000/>. `python scripts/dev.py status`
health-checks every port, and `python scripts/dev.py stop` shuts it all down.
`make up` / `make down` / `make status` do the same thing.

Everything also runs under Docker:

```bash
docker compose -f infra/docker-compose.yml up --build
```

## The stack

| Service | Port | Default model | Purpose |
| --- | --- | --- | --- |
| gateway | 8000 | - | The only public API; serves the console |
| router | 8001 | `phi3:mini` | Task routing (keyword fallback when offline) |
| llm_general | 8002 | `llama3.1` | Language and knowledge |
| llm_coding | 8003 | `qwen2.5-coder:7b` | Code and tools |
| memory | 8004 | `faiss` | Long-term vector store |
| llm_reasoning | 8005 | `deepseek-r1:8b` | Logic, planning, math |
| vision | 8006 | `qwen2.5vl:3b` | OCR, diagrams, photos |
| speech | 8007 | `whisper-small` | Transcription |
| image_gen | 8008 | `stable-diffusion-3` | Image creation |
| agent | 8009 | - | Approval-gated tool execution |
| model_manager | 8010 | - | Model-swap scheduler |

Pull the models once, then start the stack:

```bash
make models    # or: ollama pull llama3.1 && ollama pull qwen2.5-coder:7b
```

## API

The gateway returns one stable response shape:

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H 'content-type: application/json' \
  -d '{"message": "Explain this design", "metadata": {"use_memory": true}}'
```

```json
{
  "request_id": "3353b43f-e058-4b27-85ea-2b4df97b3f9f",
  "task_type": "chat",
  "response": "...",
  "expert": "general",
  "model": "llama3.1",
  "artifacts": [],
  "routing": {"task_type": "chat", "needs_memory": true, "confidence": 0.85, "notes": "local fallback"}
}
```

| Endpoint | Purpose |
| --- | --- |
| `POST /chat` | One request, one buffered answer |
| `POST /chat/stream` | The same, streamed as SSE (`meta`, `delta`, `done`, `error`) |
| `GET /health` | Process liveness |
| `GET /ready` | Readiness; 503 when the router is down |
| `GET /metrics` | Prometheus exposition |
| `GET /api/stack` | The MoE catalog plus live per-service health |
| `GET /api/diagnostics` | Metrics, host CPU/memory, backend models, agent tools |
| `GET /api/activity` | Event feed and derived alerts |
| `POST /api/memory/add` · `POST /api/memory/search` | Vector store |
| `GET /sessions` · `GET /sessions/{id}` · `DELETE /sessions/{id}` | Conversation history |
| `GET /` | The web console |

Every response carries an `X-Request-ID`; pass one in to have your own id
echoed back, and every log line for that request will carry it too.

## Configuration

All configuration is environment-driven; `.env.example` documents every
variable. `sas env` prints the ones the running stack actually reads.

- **Model backends.** Set `GENERAL_MODEL_BASE_URL`, `CODING_MODEL_BASE_URL`,
  `REASONING_MODEL_BASE_URL`, `VISION_MODEL_BASE_URL` to any
  OpenAI-compatible endpoint (Ollama, vLLM, llama.cpp, or a hosted provider).
  Add `MODEL_API_KEY` for hosted ones.
- **Router.** Leave `ROUTER_MODEL_BASE_URL` empty to use the built-in keyword
  router. It needs no GPU and keeps working with the inference backend offline,
  which matters on CPU-only hosts where the router would otherwise compete with
  the experts for memory.
- **Protection.** Set `SUPER_AI_API_KEY` to require an `x-api-key` header on
  every non-public endpoint, and `RATE_LIMIT_PER_MINUTE` to cap per-client
  traffic (`0` disables it).
- **Tools.** Execution is deny-by-default. Set `ALLOWED_TOOLS` only for
  adapters you have implemented; every decision is written to the agent audit
  log regardless.

## Operating notes

- **CPU hosts.** Uncapped generation runs to the context limit and looks like a
  hang, so `MODEL_MAX_TOKENS` (1024) and `MODEL_TIMEOUT` (300) are set with that
  in mind. The router overrides the cap with `ROUTER_MAX_TOKENS` (160) because it
  only returns a small JSON object.
- **Memory pressure.** Ollama keeps several models resident by default, which
  OOMs a small machine. `model_manager` enforces a residency policy: big models
  get exclusive residency, small ones may coexist. Point the router at it with
  `MODEL_MANAGER_URL`.
- **Honest responses.** When no model answers, an expert says so and names the
  variable to set. Image generation reports `queued` rather than claiming an
  image exists. Nothing echoes your prompt back as if it were an answer.
- **Degradation.** If memory, the router model, or a session store is
  unavailable, the request still completes without that context rather than
  failing outright.

## Monitoring

```bash
docker compose -f infra/docker-compose.yml -f infra/docker-compose.monitoring.yml up -d
```

Prometheus is at <http://localhost:9090> and Grafana at <http://localhost:3000>.
Both are configured for anonymous local access; set real credentials before
exposing either beyond the machine.

## Development

```bash
make test     # pytest
make lint     # ruff check + format --check
make fmt      # apply fixes
make check    # import-check every service module
```

The tests need no running services and no GPU: routing, health aggregation,
auth policy, the memory store and the session store are all exercised directly.

## Layout

```text
common/     shared: config, schemas, logging, health probes, model client
gateway/    the public API and the web console host
router/     classification and dispatch
<expert>/   one directory per expert service
web/        the single-file console
infra/      Dockerfile, Compose, Prometheus
scripts/    dev launcher and operational helpers
tests/      pytest suite
```

## License

MIT
