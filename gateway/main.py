import asyncio
import json
import logging
import os
import platform
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from common.constants import EXPERT_CATALOG, SERVICE_URLS, STREAM_EXPERTS, TASK_TO_EXPERT
from common.env import env_float, env_int, env_str
from common.health import probe_many, probe_many_with_latency
from common.logging_utils import configure_logging, new_request_id, request_context
from common.schemas import LLMRequest, UnifiedResponse
from common.session_store import append, history

configure_logging("gateway")
_LOG = logging.getLogger("gateway")

# Experts the gateway talks to (everything except the router).
EXPERT_PORTS = {name: SERVICE_URLS[name] for name in SERVICE_URLS if name != "router"}

# Unified API entrypoint for Super AI Stack
app = FastAPI(title="Super AI Stack Gateway")
WEB_INDEX = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "index.html")
IMAGE_ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "SAS Images")
if os.path.isdir(IMAGE_ASSETS):
    app.mount("/assets", StaticFiles(directory=IMAGE_ASSETS), name="assets")

# Router service URL (running on port 8001)
ROUTER_URL = f"{SERVICE_URLS['router']}/route"
CLASSIFY_URL = f"{SERVICE_URLS['router']}/classify"
OLLAMA_URL = env_str("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
ROUTER_TIMEOUT = env_float("ROUTER_TIMEOUT", 300.0, minimum=1.0)
CLASSIFY_TIMEOUT = env_float("CLASSIFY_TIMEOUT", 180.0, minimum=1.0)
STREAM_TIMEOUT = env_float("STREAM_TIMEOUT", 300.0, minimum=1.0)
MEMORY_TIMEOUT = env_float("MEMORY_TIMEOUT", 15.0, minimum=1.0)
HEALTH_TIMEOUT = env_float("HEALTH_TIMEOUT", 2.0, minimum=0.1)
BACKEND_TIMEOUT = env_float("BACKEND_TIMEOUT", 3.0, minimum=0.1)
STREAM_MODELS = {
    "general": env_str("GENERAL_STREAM_MODEL", "llama3.1:latest"),
    "coding": env_str("CODING_STREAM_MODEL", "qwen2.5-coder:7b"),
    "reasoning": env_str("REASONING_STREAM_MODEL", "llama3.1:latest"),
}
STREAM_SYSTEM = {
    "general": "You are the general language expert for Super AI Stack. Respond clearly and concisely.",
    "coding": "You are the coding expert for Super AI Stack. Return practical, correct code.",
    "reasoning": "You are the reasoning core for Super AI Stack. Think step by step, then answer.",
}


def system_for(expert: str) -> str:
    return STREAM_SYSTEM.get(expert, STREAM_SYSTEM["general"])


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
    except (httpx.HTTPError, ValueError):
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
            async with httpx.AsyncClient(timeout=MEMORY_TIMEOUT) as client:
                response = await client.post(f"{SERVICE_URLS['memory']}/search", json={"query": req.message})
                response.raise_for_status()
            context.extend(response.json().get("results", []))
        except (httpx.HTTPError, ValueError):
            _LOG.warning("memory recall unavailable", exc_info=True)
    return list(dict.fromkeys(item for item in context if item))


async def resolve_stream_target(req: LLMRequest) -> tuple[str, str, str, str] | None:
    """Return (task_type, expert, model, prompt) when the task can be token-streamed."""
    # A cold local model can take a minute to load, so this timeout must be generous;
    # otherwise streaming is skipped and every reply falls back to the buffered path.
    async with httpx.AsyncClient(timeout=CLASSIFY_TIMEOUT) as client:
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


API_KEY = env_str("SUPER_AI_API_KEY")
RATE_LIMIT = env_int("RATE_LIMIT_PER_MINUTE", 60, minimum=0)
# Cap the number of tracked clients so a flood of source IPs cannot grow the
# rate-limit map without bound (entries were previously never pruned).
MAX_TRACKED_CLIENTS = env_int("MAX_TRACKED_CLIENTS", 10_000, minimum=100)
REQUESTS: dict[str, deque[float]] = defaultdict(deque)
METRICS = {
    "requests_total": 0,
    "errors_total": 0,
    "stream_requests_total": 0,
    "rate_limited_total": 0,
    "unauthorized_total": 0,
}
PROCESS_START = time.monotonic()
LATENCIES: deque[float] = deque(maxlen=300)
INFLIGHT = 0
EVENTS: deque[dict] = deque(maxlen=60)

# Endpoints that stay reachable without a key so orchestrators keep working
# while the API surface stays protected.
PUBLIC_PATHS = frozenset({"/health", "/", "/ready", "/metrics", "/favicon.ico"})
PUBLIC_PREFIXES = ("/assets/", "/api/stack", "/docs", "/redoc", "/openapi.json")


def record_event(kind: str, **fields: object) -> None:
    """Append to the live event feed shown on the Activity / Memory panels."""
    EVENTS.appendleft({"t": time.strftime("%H:%M:%S"), "kind": kind, **fields})


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * pct / 100))]


def _proc_system() -> dict[str, object]:
    """CPU/memory from /proc (Linux)."""

    def cpu_ticks() -> tuple[int, int]:
        with open("/proc/stat", encoding="ascii") as handle:
            parts = handle.readline().split()[1:]
        values = [int(v) for v in parts]
        return sum(values), values[3] + values[4]

    total_a, idle_a = cpu_ticks()
    time.sleep(0.12)
    total_b, idle_b = cpu_ticks()
    busy = total_b - total_a - (idle_b - idle_a)
    with open("/proc/loadavg", encoding="ascii") as handle:
        load_1 = float(handle.read().split()[0])
    with open("/proc/meminfo", encoding="ascii") as handle:
        fields = {line.split(":")[0]: int(line.split()[1]) for line in handle}
    mem_total = fields.get("MemTotal", 0)
    mem_avail = fields.get("MemAvailable", 0)
    return {
        "cpu_pct": round(100 * busy / max(1, total_b - total_a), 1),
        "load_1": load_1,
        "mem_total_gb": round(mem_total / 1024 / 1024, 1),
        "mem_used_gb": round((mem_total - mem_avail) / 1024 / 1024, 1),
        "cores": os.cpu_count(),
    }


def _windows_system() -> dict[str, object]:
    """CPU/memory on Windows via ctypes, so the console is not blank there."""
    import ctypes

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    kernel32.GlobalMemoryStatusEx(ctypes.byref(status))

    def file_time_to_int(value: int) -> int:
        return (value >> 32) | (value & 0xFFFFFFFF)

    FILETIME = ctypes.c_ulonglong
    idle, kernel, user = FILETIME(), FILETIME(), FILETIME()

    def cpu_times() -> tuple[int, int, int]:
        if not kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            raise OSError("GetSystemTimes failed")
        return (
            file_time_to_int(idle.value),
            file_time_to_int(kernel.value),
            file_time_to_int(user.value),
        )

    idle_a, kernel_a, user_a = cpu_times()
    time.sleep(0.12)
    idle_b, kernel_b, user_b = cpu_times()
    idle_delta = idle_b - idle_a
    total_delta = (kernel_b - kernel_a) + (user_b - user_a)
    busy = total_delta - idle_delta

    total_gb = status.ullTotalPhys / 1024**3
    avail_gb = status.ullAvailPhys / 1024**3
    return {
        "cpu_pct": round(100 * busy / total_delta, 1) if total_delta > 0 else 0.0,
        "load_1": round(busy / 1_000_000, 2),
        "mem_total_gb": round(total_gb, 1),
        "mem_used_gb": round(total_gb - avail_gb, 1),
        "cores": os.cpu_count(),
    }


EMPTY_SYSTEM: dict[str, object] = {
    "cpu_pct": None,
    "load_1": None,
    "mem_total_gb": None,
    "mem_used_gb": None,
    "cores": os.cpu_count(),
}


async def read_system() -> dict[str, object]:
    """Host CPU/memory - no psutil dependency, real numbers only.

    The previous implementation read /proc exclusively, so every panel showed
    zeros on Windows even though the project targets it.
    """
    reader = _proc_system if platform.system() != "Windows" else _windows_system
    try:
        return await asyncio.to_thread(reader)
    except (OSError, AttributeError, ValueError) as exc:
        _LOG.debug("system metrics unavailable: %r", exc)
        return dict(EMPTY_SYSTEM)


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
        "uptime_seconds": round(uptime, 1),
        "requests_total": total,
        "stream_requests_total": METRICS["stream_requests_total"],
        "errors_total": errors,
        "rate_limited_total": METRICS["rate_limited_total"],
        "unauthorized_total": METRICS["unauthorized_total"],
        "success_rate": round(100 * (total - errors) / total, 2) if total else None,
        "req_per_min": recent,
        "inflight": INFLIGHT,
        "tracked_clients": len(REQUESTS),
        "latency_avg_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "latency_p50_ms": round(_percentile(latencies, 50), 1) if latencies else None,
        "latency_p95_ms": round(_percentile(latencies, 95), 1) if latencies else None,
    }


def _is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def _prune_clients(now: float) -> None:
    """Drop clients whose window has fully expired, bounding the rate-limit map."""
    cutoff = now - 60
    for client in [c for c, window in REQUESTS.items() if not window or window[-1] <= cutoff]:
        del REQUESTS[client]
    if len(REQUESTS) > MAX_TRACKED_CLIENTS:
        # Newest first: evict the least recently active clients.
        ordered = sorted(REQUESTS.items(), key=lambda item: item[1][-1] if item[1] else 0.0)
        for client, _ in ordered[: len(REQUESTS) - MAX_TRACKED_CLIENTS]:
            del REQUESTS[client]


def _authorized(request: Request) -> bool:
    """Constant-time API key check (``!=`` leaks the key byte by byte)."""
    if not API_KEY:
        return True
    if _is_public(request.url.path):
        return True
    provided = request.headers.get("x-api-key", "")
    return secrets.compare_digest(provided, API_KEY)


@app.middleware("http")
async def policy(request: Request, call_next):
    global INFLIGHT
    request_id = request.headers.get("x-request-id") or new_request_id()
    if not _authorized(request):
        METRICS["unauthorized_total"] += 1
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid API key", "request_id": request_id},
            headers={"X-Request-ID": request_id},
        )

    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    if RATE_LIMIT > 0:
        _prune_clients(now)
        window = REQUESTS[client]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= RATE_LIMIT:
            METRICS["rate_limited_total"] += 1
            record_event("rate_limit", client=client)
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded", "request_id": request_id},
                headers={"X-Request-ID": request_id, "Retry-After": "60"},
            )
        window.append(now)

    INFLIGHT += 1
    started = time.monotonic()
    try:
        with request_context(request_id):
            response = await call_next(request)
    finally:
        INFLIGHT -= 1
    if request.url.path.startswith(("/chat", "/api/")):
        LATENCIES.append(time.monotonic() - started)
    if response is not None:
        response.headers["X-Request-ID"] = request_id
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
        async with httpx.AsyncClient(timeout=ROUTER_TIMEOUT) as client:
            response = await client.post(ROUTER_URL, json=req.model_dump())
            response.raise_for_status()
        result = UnifiedResponse.model_validate(response.json())
    except httpx.HTTPError as exc:
        METRICS["errors_total"] += 1
        raise HTTPException(status_code=503, detail="Router unavailable") from exc
    except ValueError as exc:
        # A malformed router payload is a server-side fault, not a client error.
        METRICS["errors_total"] += 1
        _LOG.error("router returned an invalid unified response: %r", exc)
        raise HTTPException(status_code=502, detail="Router returned an invalid response") from exc
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
    if not req.request_id:
        req.request_id = str(uuid4())

    async def events():
        target = None
        try:
            target = await resolve_stream_target(req)
        except (httpx.HTTPError, HTTPException, ValueError) as exc:
            _LOG.info("stream classification unavailable (%r); using buffered path", exc)
            target = None

        if target is not None:
            task_type, expert, model, prompt = target
            yield sse(
                "meta",
                {
                    "task_type": task_type,
                    "expert": expert,
                    "model": model,
                    "source": "router",
                    "request_id": req.request_id,
                },
            )
            collected: list[str] = []
            try:
                async with (
                    httpx.AsyncClient(timeout=STREAM_TIMEOUT) as client,
                    client.stream(
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
                            "max_tokens": env_int("MODEL_MAX_TOKENS", 1024, minimum=1),
                        },
                    ) as response,
                ):
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
            except (httpx.HTTPError, OSError) as exc:
                _LOG.warning("stream from %s failed (%s): %r", OLLAMA_URL, model, exc)
                yield sse(
                    "error",
                    {"detail": f"{expert} stream unavailable ({type(exc).__name__}) - using buffered expert call."},
                )

        # Fallback: buffered expert call, re-framed as SSE so framing stays valid.
        try:
            result = await chat(req)
        except HTTPException as exc:
            # Without this the client saw a truncated stream and no terminal frame.
            METRICS["errors_total"] += 1
            yield sse("error", {"detail": exc.detail, "status": exc.status_code})
            yield "data: [DONE]\n\n"
            return
        record_event(
            "route",
            task=result.task_type,
            expert=result.expert,
            model=result.model,
            chars=len(result.response),
            via="fallback",
        )
        yield sse(
            "meta",
            {
                "task_type": result.task_type,
                "expert": result.expert,
                "model": result.model,
                "source": "buffered",
                "request_id": result.request_id,
            },
        )
        yield sse("delta", {"text": result.response})
        yield sse("done", {"chars": len(result.response), "expert": result.expert, "model": result.model})
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Request-ID": req.request_id or "",
        },
    )


@app.get("/sessions")
async def sessions(limit: int = 20) -> dict[str, object]:
    """List recent sessions so a client can resume a conversation."""
    from common.session_store import list_sessions

    return {"sessions": list_sessions(max(1, min(limit, 100)))}


@app.get("/sessions/{session_id}")
async def session(session_id: str) -> dict[str, object]:
    return {"session_id": session_id, "messages": history(session_id, 100)}


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, object]:
    """Forget a conversation's history."""
    from common.session_store import clear

    return {"session_id": session_id, "deleted": clear(session_id)}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "gateway"}


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus exposition.

    HELP/TYPE lines were missing, which makes the series unlabelled in Grafana
    and breaks strict parsers.
    """
    lines: list[str] = []
    for name, value in METRICS.items():
        metric = f"super_ai_{name}"
        lines.append(f"# HELP {metric} Super AI Stack gateway {name.replace('_', ' ')}")
        lines.append(f"# TYPE {metric} counter")
        lines.append(f"{metric} {value}")
    stats = gateway_stats()
    for name, help_text in (
        ("inflight", "Requests currently being served"),
        ("uptime_seconds", "Seconds since gateway start"),
        ("req_per_min", "Requests in the last 60 seconds"),
        ("tracked_clients", "Clients tracked by the rate limiter"),
        ("latency_p50_ms", "Median request latency in milliseconds"),
        ("latency_p95_ms", "95th percentile request latency in milliseconds"),
    ):
        metric = f"super_ai_{name}"
        lines.append(f"# HELP {metric} {help_text}")
        lines.append(f"# TYPE {metric} gauge")
        # A gauge with no samples yet is 0, never missing.
        lines.append(f"{metric} {stats.get(name) or 0}")
    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@app.get("/ready")
async def ready() -> dict[str, object]:
    """Orchestration probe: 200 only when the router can serve traffic.

    Experts are probed concurrently - sequentially this cost the sum of every
    timeout, so a fully-down stack took ~16 s to report.
    """
    all_services = {"router": SERVICE_URLS["router"], **EXPERT_PORTS}
    async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT) as client:
        probed = await probe_many(all_services, client, HEALTH_TIMEOUT)
    dependencies = {name: ("ok" if body.get("status") == "ok" else "unavailable") for name, body in probed.items()}
    if dependencies["router"] != "ok":
        raise HTTPException(status_code=503, detail={"status": "not_ready", "dependencies": dependencies})
    return {"status": "ready", "dependencies": dependencies, "stack": EXPERT_CATALOG}


@app.get("/api/stack")
async def stack() -> dict[str, object]:
    async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT) as client:
        services = await probe_many(EXPERT_PORTS, client, HEALTH_TIMEOUT)
    return {"catalog": EXPERT_CATALOG, "services": services}


@app.get("/api/diagnostics")
async def diagnostics() -> dict[str, object]:
    """Live stack telemetry for the console panels.

    Aggregates: request metrics, per-expert health + latency, the inference
    backend state (resident + installed models), memory size, and agent tools.
    Every probe runs concurrently so one slow peer cannot stall the panel.
    """
    async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT) as client:
        services_task = probe_many_with_latency(EXPERT_PORTS, client, HEALTH_TIMEOUT)
        memory_task = client.get(f"{SERVICE_URLS['memory']}/health", timeout=HEALTH_TIMEOUT)
        tools_task = client.get(f"{SERVICE_URLS['agent']}/tools", timeout=HEALTH_TIMEOUT)
        services, memory_response, tools_response = await asyncio.gather(
            services_task, memory_task, tools_task, return_exceptions=True
        )

    return {
        "metrics": dict(METRICS),
        "gateway": gateway_stats(),
        "system": await read_system(),
        "events": list(EVENTS)[:20],
        "services": services if isinstance(services, dict) else {},
        "backend": await _backend_state(),
        "memory": _json_body(memory_response) or {"status": "unavailable"},
        "tools": _tools_from(tools_response),
    }


async def _backend_state() -> dict[str, object]:
    """Resident + installed models from the inference backend."""
    try:
        async with httpx.AsyncClient(timeout=BACKEND_TIMEOUT) as client:
            resident, installed = await asyncio.gather(
                client.get(f"{OLLAMA_URL}/api/ps"),
                client.get(f"{OLLAMA_URL}/api/tags"),
            )
            resident.raise_for_status()
            installed.raise_for_status()
            resident_body = resident.json()
            installed_body = installed.json()
        return {
            "url": OLLAMA_URL,
            "reachable": True,
            "resident": [
                {
                    "model": m.get("model"),
                    "size": m.get("size"),
                    "vram": m.get("size_vram"),
                    "context": m.get("context"),
                }
                for m in resident_body.get("models", [])
                if isinstance(m, dict)
            ],
            "installed": [m.get("name") for m in installed_body.get("models", []) if isinstance(m, dict)],
        }
    except (httpx.HTTPError, ValueError, AttributeError):
        return {"url": OLLAMA_URL, "reachable": False}


def _json_body(response: object) -> dict[str, object] | None:
    """Parse a probe response, tolerating the ``return_exceptions=True`` shape."""
    if isinstance(response, BaseException) or response is None:
        return None
    try:
        body = response.json()  # type: ignore[attr-defined]
    except (ValueError, AttributeError):
        return None
    return body if isinstance(body, dict) else None


def _tools_from(response: object) -> list[object]:
    body = _json_body(response)
    if not body:
        return []
    tools = body.get("tools", [])
    return tools if isinstance(tools, list) else []


@app.post("/api/memory/add")
async def memory_add(payload: dict) -> dict:
    if not str(payload.get("text", "")).strip():
        raise HTTPException(status_code=400, detail="text is required")
    try:
        async with httpx.AsyncClient(timeout=MEMORY_TIMEOUT) as client:
            response = await client.post(f"{SERVICE_URLS['memory']}/add", json=payload)
            response.raise_for_status()
            body = response.json()
        record_event("memory.write", chars=len(str(payload.get("text", ""))), detail=body.get("status", "ok"))
        return body
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Memory service unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Memory service returned an invalid response") from exc


@app.post("/api/memory/search")
async def memory_search(payload: dict) -> dict:
    """Proxy a vector-store query so the console can probe FAISS directly."""
    if not str(payload.get("query", "")).strip():
        raise HTTPException(status_code=400, detail="query is required")
    try:
        async with httpx.AsyncClient(timeout=MEMORY_TIMEOUT) as client:
            response = await client.post(f"{SERVICE_URLS['memory']}/search", json=payload)
            response.raise_for_status()
            body = response.json()
        record_event("memory.recall", hits=len(body.get("results", [])))
        return body
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Memory service unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Memory service returned an invalid response") from exc


@app.get("/api/activity")
async def activity() -> dict[str, object]:
    """Live event feed + derived system alerts (all computed from real state)."""
    gw = gateway_stats()
    async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT) as client:
        probed = await probe_many(EXPERT_PORTS, client, HEALTH_TIMEOUT)
    diag_services = {name: ("ok" if body.get("status") == "ok" else "down") for name, body in probed.items()}

    alerts: list[dict[str, str]] = []
    down = [name for name, status in diag_services.items() if status != "ok"]
    if down:
        alerts.append({"level": "warn", "text": f"Experts down: {', '.join(sorted(down))}"})
    backend = await _backend_state()
    if not backend.get("reachable"):
        alerts.append({"level": "error", "text": f"Inference backend unreachable at {OLLAMA_URL}"})
    elif not backend.get("resident"):
        alerts.append({"level": "info", "text": "No models resident - first call pays cold-load time."})
    if gw["errors_total"]:
        alerts.append({"level": "warn", "text": f"{gw['errors_total']} gateway errors this uptime"})
    if gw["rate_limited_total"]:
        alerts.append({"level": "warn", "text": f"{gw['rate_limited_total']} requests rate limited"})
    return {"gateway": gw, "events": list(EVENTS), "alerts": alerts, "services": diag_services}


@app.get("/")
async def web_app() -> FileResponse:
    # Always revalidate: a cached console silently hides UI updates from the user.
    return FileResponse(WEB_INDEX, headers={"Cache-Control": "no-cache"})
