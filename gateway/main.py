import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque
import sqlite3
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
import httpx
from common.schemas import LLMRequest, UnifiedResponse
from common.constants import EXPERT_CATALOG, SERVICE_URLS
from common.session_store import append, history

_LOG = logging.getLogger("gateway")

EXPERT_PORTS = {
    "general": SERVICE_URLS["general"],
    "coding": SERVICE_URLS["coding"],
    "reasoning": SERVICE_URLS["reasoning"],
    "vision": SERVICE_URLS["vision"],
    "speech": SERVICE_URLS["speech"],
    "image_gen": SERVICE_URLS["image_gen"],
    "memory": SERVICE_URLS["memory"],
    "agent": SERVICE_URLS["agent"],
}

# Unified API entrypoint for Super AI Stack
app = FastAPI(title="Super AI Stack Gateway")
WEB_INDEX = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "index.html")
IMAGE_ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "SAS Images")
if os.path.isdir(IMAGE_ASSETS):
    app.mount("/assets", StaticFiles(directory=IMAGE_ASSETS), name="assets")

# Router service URL (running on port 8001)
ROUTER_URL = f"{SERVICE_URLS['router']}/route"
CLASSIFY_URL = f"{SERVICE_URLS['router']}/classify"
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
STREAM_MODELS = {
    "general": os.getenv("GENERAL_STREAM_MODEL", "llama3.1:latest"),
    "coding": os.getenv("CODING_STREAM_MODEL", "qwen2.5-coder:7b"),
    "reasoning": os.getenv("REASONING_STREAM_MODEL", "llama3.1:latest"),
}
STREAM_SYSTEM = {
    "general": "You are the general language expert for Super AI Stack. Respond clearly and concisely.",
    "coding": "You are the coding expert for Super AI Stack. Return practical, correct code.",
    "reasoning": "You are the reasoning core for Super AI Stack. Think step by step, then answer.",
}


def system_for(expert: str) -> str:
    return STREAM_SYSTEM.get(expert, STREAM_SYSTEM["general"])


# Router emits task types that do not always match the expert service key.
TASK_TO_EXPERT = {
    "chat": "general",
    "code": "coding",
    "reasoning": "reasoning",
    "vision": "vision",
    "speech": "speech",
    "image_gen": "image_gen",
    "agent": "agent",
}
# Only these experts are text LLMs that can token-stream through Ollama.
STREAM_EXPERTS = ("general", "coding", "reasoning")


def sse(event: str, payload: dict) -> str:
    """Frame one SSE event. JSON keeps newlines inside a token on a single data line."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def expert_model(expert: str) -> str | None:
    """Read the live model tag from the expert so UI labels stay truthful."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{EXPERT_PORTS[expert]}/health")
            response.raise_for_status()
        return response.json().get("model")
    except httpx.HTTPError:
        return None


async def stream_context(req: LLMRequest) -> list[str]:
    """Session history, plus FAISS hits when the client asked for memory."""
    context = list(req.memory_context)
    if req.session_id:
        try:
            context.extend(history(req.session_id, 6))
        except (OSError, sqlite3.Error):
            # Session history is optional context; never fail the request for it.
            _LOG.warning("session history unavailable for %s", req.session_id, exc_info=True)
    if req.metadata.get("use_memory"):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{SERVICE_URLS['memory']}/search", json={"query": req.message}
                )
                response.raise_for_status()
            context.extend(response.json().get("results", []))
        except httpx.HTTPError:
            pass
    return list(dict.fromkeys(item for item in context if item))


async def resolve_stream_target(req: LLMRequest) -> tuple[str, str, str, str] | None:
    """Return (task_type, expert, model, prompt) when the task can be token-streamed."""
    # A cold local model can take a minute to load, so this timeout must be generous;
    # otherwise streaming is skipped and every reply falls back to the buffered path.
    timeout = float(os.getenv("ROUTER_TIMEOUT", "180"))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(CLASSIFY_URL, json=req.model_dump())
        response.raise_for_status()
    body = response.json()
    task_type = body.get("task_type") or body.get("routing", {}).get("task_type", "chat")
    expert = TASK_TO_EXPERT.get(task_type, "general")
    if expert not in STREAM_EXPERTS:
        return None
    prompt = req.message
    context = await stream_context(req)
    if context:
        prompt += "\n\nRelevant memory:\n" + "\n".join(f"- {item}" for item in context)
    model = await expert_model(expert) or STREAM_MODELS[expert]
    return task_type, expert, model, prompt
API_KEY = os.getenv("SUPER_AI_API_KEY", "")
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
REQUESTS: dict[str, deque[float]] = defaultdict(deque)
METRICS = {"requests_total": 0, "errors_total": 0, "stream_requests_total": 0}
PROCESS_START = time.monotonic()
LATENCIES: deque[float] = deque(maxlen=300)
INFLIGHT = 0
EVENTS: deque[dict] = deque(maxlen=60)


def record_event(kind: str, **fields: object) -> None:
    """Append to the live event feed shown on the Activity / Memory panels."""
    EVENTS.appendleft({"t": time.strftime("%H:%M:%S"), "kind": kind, **fields})


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * pct / 100))]


async def read_system() -> dict[str, object]:
    """CPU% / memory from /proc - no psutil dependency, real numbers only."""
    info: dict[str, object] = {}
    try:
        def cpu_ticks() -> tuple[int, int]:
            with open("/proc/stat", encoding="ascii") as handle:
                parts = handle.readline().split()[1:]
            values = [int(v) for v in parts]
            return sum(values), values[3] + values[4]

        total_a, idle_a = cpu_ticks()
        await asyncio.sleep(0.12)
        total_b, idle_b = cpu_ticks()
        busy = total_b - total_a - (idle_b - idle_a)
        info["cpu_pct"] = round(100 * busy / max(1, total_b - total_a), 1)
        with open("/proc/loadavg", encoding="ascii") as handle:
            info["load_1"] = float(handle.read().split()[0])
        with open("/proc/meminfo", encoding="ascii") as handle:
            fields = {line.split(":")[0]: int(line.split()[1]) for line in handle}
        mem_total = fields.get("MemTotal", 0)
        mem_avail = fields.get("MemAvailable", 0)
        info["mem_total_gb"] = round(mem_total / 1024 / 1024, 1)
        info["mem_used_gb"] = round((mem_total - mem_avail) / 1024 / 1024, 1)
        with open("/proc/cpuinfo", encoding="ascii") as handle:
            info["cores"] = sum(1 for line in handle if line.startswith("processor"))
    except OSError:
        info = {"cpu_pct": None, "load_1": None, "mem_total_gb": None, "mem_used_gb": None, "cores": None}
    return info


def gateway_stats() -> dict[str, object]:
    latencies = [v * 1000 for v in LATENCIES]
    minute_ago = time.monotonic() - 60
    recent = sum(1 for client in REQUESTS.values() for stamp in client if stamp > minute_ago)
    uptime = time.monotonic() - PROCESS_START
    errors = METRICS["errors_total"]
    total = METRICS["requests_total"]
    return {
        "uptime_s": int(uptime),
        "uptime_h": round(uptime / 3600, 1),
        "requests_total": total,
        "stream_requests_total": METRICS["stream_requests_total"],
        "errors_total": errors,
        "success_rate": round(100 * (total - errors) / total, 2) if total else None,
        "req_per_min": recent,
        "inflight": INFLIGHT,
        "latency_avg_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "latency_p50_ms": round(_percentile(latencies, 50), 1) if latencies else None,
        "latency_p95_ms": round(_percentile(latencies, 95), 1) if latencies else None,
    }


@app.middleware("http")
async def policy(request: Request, call_next):
    global INFLIGHT
    if API_KEY and request.url.path not in {"/health", "/", "/ready", "/metrics"}:
        if request.url.path.startswith(("/assets/", "/api/stack")):
            return await call_next(request)
        if request.headers.get("x-api-key") != API_KEY:
            return JSONResponse(status_code=401, content={"detail": "Invalid API key"})
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window = REQUESTS[client]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= RATE_LIMIT:
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
    window.append(now)
    INFLIGHT += 1
    try:
        response = await call_next(request)
    finally:
        INFLIGHT -= 1
    if request.url.path.startswith(("/chat", "/api/")):
        LATENCIES.append(time.monotonic() - now)
    return response


@app.post("/chat")
async def chat(req: LLMRequest) -> UnifiedResponse:
    """
    Gateway → Router → Expert Models → Final Response
    """
    if not req.session_id:
        req.session_id = str(uuid4())
    if not req.request_id:
        req.request_id = str(uuid4())
    METRICS["requests_total"] += 1
    if req.session_id:
        try:
            req.memory_context = history(req.session_id)
        except (OSError, sqlite3.Error):
            _LOG.warning("session history unavailable for %s", req.session_id, exc_info=True)
    try:
        async with httpx.AsyncClient(timeout=float(os.getenv("ROUTER_TIMEOUT", "300"))) as client:
            response = await client.post(ROUTER_URL, json=req.model_dump())
            response.raise_for_status()
    except httpx.HTTPError as exc:
        METRICS["errors_total"] += 1
        raise HTTPException(status_code=503, detail="Router unavailable") from exc
    result = UnifiedResponse.model_validate(response.json())
    if req.session_id:
        append(req.session_id, "user", req.message)
        append(req.session_id, "assistant", result.response)
    record_event(
        "route",
        task=result.task_type,
        expert=result.expert,
        model=result.model,
        chars=len(result.response),
    )
    return result


@app.post("/chat/stream")
async def chat_stream(req: LLMRequest) -> StreamingResponse:
    """Stream an answer token by token as JSON SSE frames.

    Frames: ``event: meta`` (routing), ``event: delta`` (text), ``event: done``,
    ``event: error``. A terminal ``data: [DONE]`` is kept for simple clients.
    """
    METRICS["stream_requests_total"] += 1
    if not req.session_id:
        req.session_id = str(uuid4())

    async def events():
        target = None
        try:
            target = await resolve_stream_target(req)
        except (httpx.HTTPError, HTTPException):
            target = None

        if target is not None:
            task_type, expert, model, prompt = target
            yield sse("meta", {"task_type": task_type, "expert": expert, "model": model, "source": "router"})
            collected: list[str] = []
            try:
                async with httpx.AsyncClient(timeout=300.0) as client:
                    async with client.stream(
                        "POST",
                        f"{OLLAMA_URL}/v1/chat/completions",
                        json={
                            "model": model,
                            "messages": [
                                {"role": "system", "content": system_for(expert)},
                                {"role": "user", "content": prompt},
                            ],
                            "stream": True,
                            # Uncapped local inference runs to the context limit (~10 min
                            # on CPU), which looks like a hang to the client.
                            "max_tokens": int(os.getenv("MODEL_MAX_TOKENS", "1024")),
                        },
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                delta = json.loads(data)["choices"][0]["delta"].get("content", "")
                            except (KeyError, IndexError, TypeError, ValueError):
                                continue
                            if delta:
                                collected.append(delta)
                                yield sse("delta", {"text": delta})
                answer = "".join(collected)
                if answer:
                    append(req.session_id, "user", req.message)
                    append(req.session_id, "assistant", answer)
                    record_event("stream", task=task_type, expert=expert, model=model, chars=len(answer))
                    yield sse("done", {"chars": len(answer), "expert": expert, "model": model})
                    yield "data: [DONE]\n\n"
                    return
            except httpx.HTTPError as exc:
                _LOG.warning("stream from %s failed (%s): %r", OLLAMA_URL, model, exc)
                yield sse("error", {"detail": f"{expert} stream unavailable ({type(exc).__name__}) - using buffered expert call."})

        # Fallback: buffered expert call, re-framed as SSE so framing stays valid.
        result = await chat(req)
        record_event("route", task=result.task_type, expert=result.expert, model=result.model, chars=len(result.response), via="fallback")
        yield sse("meta", {"task_type": result.task_type, "expert": result.expert, "model": result.model, "source": "buffered"})
        yield sse("delta", {"text": result.response})
        yield sse("done", {"chars": len(result.response), "expert": result.expert, "model": result.model})
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/sessions/{session_id}")
async def session(session_id: str) -> dict[str, object]:
    return {"session_id": session_id, "messages": history(session_id, 100)}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "gateway"}


@app.get("/metrics")
async def metrics() -> Response:
    body = "\n".join(f"super_ai_{name} {value}" for name, value in METRICS.items()) + "\n"
    return Response(content=body, media_type="text/plain; version=0.0.4")


@app.get("/ready")
async def ready() -> dict[str, object]:
    dependencies = {name: "unavailable" for name in ("router", *EXPERT_PORTS)}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{SERVICE_URLS['router']}/health")
            response.raise_for_status()
            dependencies["router"] = "ok"
    except httpx.HTTPError:
        pass
    for name, url in EXPERT_PORTS.items():
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{url}/health")
                response.raise_for_status()
                dependencies[name] = "ok"
        except httpx.HTTPError:
            continue
    if dependencies["router"] != "ok":
        raise HTTPException(status_code=503, detail={"status": "not_ready", "dependencies": dependencies})
    return {"status": "ready", "dependencies": dependencies, "stack": EXPERT_CATALOG}


@app.get("/api/stack")
async def stack() -> dict[str, object]:
    services: dict[str, object] = {}
    async with httpx.AsyncClient(timeout=2.5) as client:
        for name, url in EXPERT_PORTS.items():
            try:
                response = await client.get(f"{url}/health")
                response.raise_for_status()
                services[name] = response.json()
            except httpx.HTTPError:
                services[name] = {"status": "unavailable", "service": name}
    return {"catalog": EXPERT_CATALOG, "services": services}


@app.get("/api/diagnostics")
async def diagnostics() -> dict[str, object]:
    """Live stack telemetry for the console panels.

    Aggregates: request metrics, per-expert health + latency, the inference
    backend state (resident + installed models), memory size, and agent tools.
    """
    import time

    out: dict[str, object] = {
        "metrics": dict(METRICS),
        "gateway": gateway_stats(),
        "system": await read_system(),
        "events": list(EVENTS)[:20],
        "services": {},
        "backend": {},
        "memory": {},
        "tools": [],
    }

    async with httpx.AsyncClient(timeout=2.5) as client:
        for name, url in EXPERT_PORTS.items():
            started = time.perf_counter()
            try:
                response = await client.get(f"{url}/health")
                response.raise_for_status()
                body: dict[str, object] = response.json()
                body["latency_ms"] = int((time.perf_counter() - started) * 1000)
            except httpx.HTTPError:
                body = {"status": "unavailable", "service": name}
            out["services"][name] = body

        memory_url = SERVICE_URLS["memory"]
        try:
            response = await client.get(f"{memory_url}/health")
            response.raise_for_status()
            out["memory"] = response.json()
        except httpx.HTTPError:
            out["memory"] = {"status": "unavailable"}

        agent_url = SERVICE_URLS["agent"]
        try:
            response = await client.get(f"{agent_url}/tools")
            response.raise_for_status()
            tools = response.json()
            out["tools"] = tools.get("tools", tools) if isinstance(tools, dict) else tools
        except httpx.HTTPError:
            pass

    ollama = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resident = (await client.get(f"{ollama}/api/ps")).json()
            installed = (await client.get(f"{ollama}/api/tags")).json()
        out["backend"] = {
            "url": ollama,
            "reachable": True,
            "resident": [
                {
                    "model": m.get("model"),
                    "size": m.get("size"),
                    "vram": m.get("size_vram"),
                    "context": m.get("context"),
                }
                for m in resident if isinstance(m, dict)
            ],
            "installed": [
                m.get("name") for m in installed.get("models", []) if isinstance(m, dict)
            ],
        }
    except (httpx.HTTPError, ValueError):
        out["backend"] = {"url": ollama, "reachable": False}
    return out


@app.post("/api/memory/add")
async def memory_add(payload: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{SERVICE_URLS['memory']}/add", json=payload)
            response.raise_for_status()
            body = response.json()
            record_event("memory.write", chars=len(str(payload.get("text", ""))), detail=body.get("status", "ok"))
            return body
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Memory service unavailable") from exc


@app.post("/api/memory/search")
async def memory_search(payload: dict) -> dict:
    """Proxy a vector-store query so the console can probe FAISS directly."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{SERVICE_URLS['memory']}/search", json=payload)
            response.raise_for_status()
            body = response.json()
            record_event("memory.recall", hits=len(body.get("results", [])))
            return body
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Memory service unavailable") from exc


@app.get("/api/activity")
async def activity() -> dict[str, object]:
    """Live event feed + derived system alerts (all computed from real state)."""
    gw = gateway_stats()
    events = list(EVENTS)
    alerts: list[dict[str, str]] = []
    diag_services = {}
    async with httpx.AsyncClient(timeout=2.0) as client:
        for name, url in EXPERT_PORTS.items():
            try:
                response = await client.get(f"{url}/health")
                response.raise_for_status()
                diag_services[name] = "ok"
            except httpx.HTTPError:
                diag_services[name] = "down"
    down = [name for name, status in diag_services.items() if status != "ok"]
    if down:
        alerts.append({"level": "warn", "text": f"Experts down: {', '.join(down)}"})
    backend_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resident = (await client.get(f"{backend_url}/api/ps")).json()
        if not resident:
            alerts.append({"level": "info", "text": "No models resident - first call pays cold-load time."})
    except (httpx.HTTPError, ValueError):
        alerts.append({"level": "error", "text": f"Inference backend unreachable at {backend_url}"})
    if gw["errors_total"]:
        alerts.append({"level": "warn", "text": f"{gw['errors_total']} gateway errors this uptime"})
    return {"gateway": gw, "events": events, "alerts": alerts, "services": diag_services}


@app.get("/")
async def web_app() -> FileResponse:
    # Always revalidate: a cached console silently hides UI updates from the user.
    return FileResponse(WEB_INDEX, headers={"Cache-Control": "no-cache"})