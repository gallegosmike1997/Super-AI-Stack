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


@app.middleware("http")
async def policy(request: Request, call_next):
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
    return await call_next(request)


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
                    yield sse("done", {"chars": len(answer), "expert": expert, "model": model})
                    yield "data: [DONE]\n\n"
                    return
            except httpx.HTTPError as exc:
                _LOG.warning("stream from %s failed (%s): %r", OLLAMA_URL, model, exc)
                yield sse("error", {"detail": f"{expert} stream unavailable ({type(exc).__name__}) - using buffered expert call."})

        # Fallback: buffered expert call, re-framed as SSE so framing stays valid.
        result = await chat(req)
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


@app.post("/api/memory/add")
async def memory_add(payload: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{SERVICE_URLS['memory']}/add", json=payload)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Memory service unavailable") from exc


@app.get("/")
async def web_app() -> FileResponse:
    return FileResponse(WEB_INDEX)