n a test version# Super AI Stack

Super AI Stack presents one API while coordinating specialized local and remote experts:

```text
client -> gateway -> router (Phi-3) -> memory (FAISS/Milvus) -> expert / agent
																			-> general / reasoning / coding
																			-> vision / speech / image generation
```

## Current vertical slice

Local MoE map: Router `phi3:mini`, Reasoning `deepseek-r1:8b`, General `llama3.1`, Coding `qwen2.5-coder:7b` (or `mistral-large`), Vision `qwen2.5vl:3b`, Speech `whisper-small`, Image `stable-diffusion-3`, Memory `faiss`. Pull Ollama models once, then run Compose. The web console is the outward SAS UX built from your `SAS Images/` artwork (Dashboard, Stack, Models, Memory, Activity views over `GET /api/stack`, `POST /chat`, `POST /chat/stream`, `POST /api/memory/add`).

Every service exposes `GET /health`. The gateway exposes `POST /chat` and returns one stable response shape:

```json
{
	"message": "Explain this design",
	"attachments": [{"kind": "image", "uri": "file:///tmp/design.png"}],
	"metadata": {"use_memory": true}
}
```

The router uses Phi-3 when `PHI3_URL` is set. Without it, a local heuristic router keeps the stack runnable offline. Expert implementations currently return integration-ready responses; connect vLLM, Ollama, Whisper, Stable Diffusion, and FAISS/Milvus behind those same endpoints as each model is deployed. The agent endpoint requires explicit approval and queues work until allowlisted tool adapters are connected.

On a CPU-only workstation, the router defaults to the lightweight heuristic mode so it does not compete with the general and coding models for memory. Enable local Phi-3 routing explicitly with `ROUTER_MODEL_BASE_URL=http://host.docker.internal:11434` and `ROUTER_MODEL_NAME=phi3:mini`; a separate GPU or inference host is recommended when running all three models concurrently.

## Run locally

Install the root dependencies, then run services from the repository root so Python can resolve the shared package:

```bash
pip install fastapi httpx "uvicorn[standard]"
uvicorn gateway.main:app --port 8000
```

Run the other services on ports `8001` through `8008`, or use the Compose profile:

```bash
docker compose -f infra/docker-compose.yml up --build
```

Then call `http://localhost:8000/chat`. Service URLs and model providers are environment-configured; Compose uses service names while local development defaults to `localhost`.

Memory is persisted in a FAISS index mounted at the Compose `memory_data` volume. Add knowledge directly with `POST /add` on the memory service, then request retrieval through the gateway with `metadata.use_memory=true`.

Speech supports `POST /infer` and `POST /transcribe` with an audio attachment. Install `speech/requirements.txt` to enable local faster-whisper; set `WHISPER_MODEL`, `WHISPER_DEVICE`, and `WHISPER_COMPUTE_TYPE` for the machine. Audio can be a mounted file path, HTTP URL, or base64 data URI.

The web console is available at `http://localhost:8000/`. It keeps a browser session, can request memory, and consumes the SSE stream. Set `SUPER_AI_API_KEY` and `RATE_LIMIT_PER_MINUTE` in `.env` to enable gateway protection. Tool execution is deny-by-default; set `ALLOWED_TOOLS` only for adapters you have implemented. Every decision is persisted in the `agent_audit` volume.

The agent exposes `GET /tools` for discovery and returns a `job_id` for every denied, approval-required, or queued request. The default agent does not execute operating-system commands.

Operational endpoints include `GET /ready` and Prometheus-compatible `GET /metrics`. Each chat request receives a correlation ID that is returned in the unified response.

Optional monitoring is available with `docker compose -f infra/docker-compose.yml -f infra/docker-compose.monitoring.yml up -d`. Prometheus runs at `http://localhost:9090` and Grafana at `http://localhost:3000`; configure dashboards and credentials before exposing either service beyond the local machine.

Use `GET /ready` for orchestration checks and `GET /health` for a process-level liveness check. The gateway readiness probe verifies router connectivity; expert/model availability is reported by each service's own health endpoint.

Vision accepts image attachments and uses an OpenAI-compatible multimodal endpoint when `VISION_MODEL_BASE_URL` is set. The local profile uses `qwen2.5vl:3b` through Ollama. Image generation uses the Stable Diffusion WebUI API when `IMAGE_MODEL_BASE_URL` is set; otherwise it returns a queued artifact without claiming an image was generated.

For an NVIDIA host, layer GPU reservations onto the default deployment with `docker compose -f infra/docker-compose.yml -f infra/docker-compose.gpu.yml up -d`. The default Compose file remains CPU-compatible.

## Connect a real LLM

General and coding services speak the OpenAI-compatible `/v1/chat/completions` protocol. For Ollama, start it and pull both models. The repository `.env` file supplies their persistent Compose settings:

```bash
ollama pull llama3.1
ollama pull qwen2.5-coder:7b
docker compose -f infra/docker-compose.yml up -d
```

Use `CODING_MODEL_BASE_URL` and `CODING_MODEL_NAME` for a separate coding model. The same variables work with vLLM or a hosted provider; set `MODEL_API_KEY` when required.
