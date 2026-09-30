import json
import logging
import os
import re
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException

from common.constants import SERVICE_URLS, SUPPORTED_TASKS, TASK_TO_EXPERT
from common.env import env_float
from common.logging_utils import configure_logging
from common.model_client import complete
from common.model_manager import ensure_model
from common.schemas import ExpertResponse, LLMRequest, RouterDecision, UnifiedResponse

configure_logging("router")
_LOG = logging.getLogger("router")

app = FastAPI(title="Super AI Stack - Router")

# Remote Phi-3 API endpoint (replace with your actual URL)
PHI3_URL = os.getenv("PHI3_URL", "")
ROUTER_MODEL_NAME = os.getenv("ROUTER_MODEL_NAME", "phi3:mini")
PHI3_TIMEOUT = env_float("PHI3_TIMEOUT", 60.0, minimum=1.0)
MEMORY_TIMEOUT = env_float("MEMORY_TIMEOUT", 30.0, minimum=1.0)
EXPERT_TIMEOUT = env_float("ROUTER_TIMEOUT", 300.0, minimum=1.0)

# Internal services
SERVICE_ENDPOINTS = {
    "chat": f"{SERVICE_URLS['general']}/infer",
    "code": f"{SERVICE_URLS['coding']}/infer",
    "reasoning": f"{SERVICE_URLS['reasoning']}/infer",
    "vision": f"{SERVICE_URLS['vision']}/infer",
    "speech": f"{SERVICE_URLS['speech']}/infer",
    "image_gen": f"{SERVICE_URLS['image_gen']}/infer",
    "agent": f"{SERVICE_URLS['agent']}/execute",
}
MEMORY_URL = f"{SERVICE_URLS['memory']}/search"

# Keyword routing. Plain substring matching produced real misroutes - "api"
# matched "therapist", "plan" matched "explanation" - so each pattern is matched
# on word boundaries instead.
ROUTE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("code", ("code", "python", "script", "api", "debug", "function", "bug", "refactor", "compile")),
    ("reasoning", ("reason", "prove", "proof", "calculate", "plan", "why", "derive", "logic")),
    ("vision", ("image", "screenshot", "diagram", "photo", "ocr", "picture")),
    ("speech", ("speak", "audio", "transcribe", "voice", "whisper")),
    (
        "image_gen",
        (
            "draw",
            "render",
            "generate an image",
            "concept art",
            "create an icon",
            "generate an icon",
            "make an image",
            "create an image",
        ),
    ),
    ("agent", ("run a tool", "call an api", "execute", "agent", "automate")),
)

# "create a simple blue square icon" needs both a creation verb and an image
# noun; either half alone is too weak to route away from a normal chat turn.
CREATE_VERBS = ("create", "generate", "make", "draw", "design", "render")
IMAGE_NOUNS = ("icon", "logo", "illustration", "image", "picture", "artwork", "banner", "avatar")
CREATE_IMAGE_RE = re.compile(rf"\b({'|'.join(CREATE_VERBS)})\b(?:\s+\w+){{0,4}}\s+(?:{'|'.join(IMAGE_NOUNS)})\b")


def _matches(text: str, phrase: str) -> bool:
    """Whole-word match for a single word, substring for a multi-word phrase."""
    if " " in phrase:
        return phrase in text
    return re.search(rf"\b{re.escape(phrase)}", text) is not None


def _keyword_task(text: str) -> str | None:
    """First keyword family that matches, or ``None`` for a plain chat turn."""
    if CREATE_IMAGE_RE.search(text):
        return "image_gen"
    for candidate, keywords in ROUTE_KEYWORDS:
        if any(_matches(text, keyword) for keyword in keywords):
            return candidate
    return None


def extract_json(text: str) -> dict | None:
    """Pull a JSON object out of chatty model output (fences, prose, etc.)."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[^{}]*\"task_type\"[^{}]*\}", cleaned, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


async def call_phi3_router(message: str, metadata: dict | None = None) -> dict:
    """Ask the configured router model to classify, falling back to keywords.

    Any failure here degrades to the heuristic rather than failing the request:
    routing must not be the reason a user gets no answer.
    """
    if PHI3_URL:
        try:
            async with httpx.AsyncClient(timeout=PHI3_TIMEOUT) as client:
                resp = await client.post(PHI3_URL, json={"prompt": build_router_prompt(message, metadata)})
                resp.raise_for_status()
            body = resp.json()
            text = body.get("reply", body) if isinstance(body, dict) else body
            if isinstance(text, dict):
                return text
            parsed = extract_json(text) if isinstance(text, str) else None
            if parsed and parsed.get("task_type") in SUPPORTED_TASKS:
                return parsed
            _LOG.warning("phi3 router returned an unusable payload; using keyword routing")
        except (httpx.HTTPError, ValueError, OSError) as exc:
            _LOG.warning("phi3 router unavailable (%r); using keyword routing", exc)
        return heuristic_route(message, metadata)

    router_base = os.getenv("ROUTER_MODEL_BASE_URL") or os.getenv("MODEL_BASE_URL", "")
    if router_base:
        try:
            generated = await complete(
                "You route requests for a multi-expert AI stack. Return JSON only.",
                build_router_prompt(message, metadata),
                ROUTER_MODEL_NAME,
                max_tokens=int(os.getenv("ROUTER_MAX_TOKENS", "160")),
                base_url=router_base,
                model=ROUTER_MODEL_NAME,
            )
        except (httpx.HTTPError, ValueError) as exc:
            _LOG.warning("router model call failed (%r); using keyword routing", exc)
            generated = None
        if generated:
            parsed = extract_json(generated)
            if parsed and parsed.get("task_type") in SUPPORTED_TASKS:
                parsed.setdefault("confidence", 0.9)
                parsed.setdefault("notes", "phi3-router")
                return parsed
    return heuristic_route(message, metadata)


def heuristic_route(message: str, metadata: dict | None = None) -> dict:
    """Offline keyword routing, used whenever no router model is available."""
    text = message.lower()
    task_type = "chat"
    if metadata and metadata.get("task_type") in SUPPORTED_TASKS:
        task_type = metadata["task_type"]
    else:
        task_type = _keyword_task(text) or "chat"
    return {
        "task_type": task_type,
        "needs_memory": bool(metadata and metadata.get("use_memory")),
        "confidence": 0.85 if task_type != "chat" else 0.55,
        "notes": "local fallback",
    }


def build_router_prompt(message: str, metadata: dict | None) -> str:
    base = (Path(__file__).with_name("router_prompt.txt")).read_text(encoding="utf-8")
    meta_str = json.dumps(metadata or {}, ensure_ascii=False)
    return f"{base}\n\nUser message:\n{message}\n\nMetadata:\n{meta_str}\n\nRouter JSON:"


async def ensure_expert_model(task_type: str) -> None:
    """Ask the model manager to swap in the expert's model before dispatch.

    Reads the live model tag from the expert's /health so it always matches what
    the expert will actually call. Best-effort: no manager or a slow swap just
    means the request proceeds without one.
    """
    expert = TASK_TO_EXPERT.get(task_type, task_type)
    base = SERVICE_URLS.get(expert)
    if not base:
        return
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{base}/health")
            response.raise_for_status()
        await ensure_model(response.json().get("model"))
    except (httpx.HTTPError, ValueError):
        return


def _attachment_driven_task(req: LLMRequest) -> str | None:
    """Infer the task from attachment types when the caller did not specify one."""
    for item in req.attachments:
        content_type = item.content_type or ""
        if item.kind == "image" or content_type.startswith("image/"):
            return "vision"
        if item.kind == "audio" or content_type.startswith("audio/"):
            return "speech"
    return None


async def decide_route(req: LLMRequest) -> tuple[str, RouterDecision]:
    """Pick the expert for a request without dispatching to it."""
    try:
        raw = await call_phi3_router(req.message, req.metadata)
    except Exception:  # noqa: BLE001 - routing must never fail the request
        _LOG.exception("router decision failed; falling back to keyword routing")
        raw = heuristic_route(req.message, req.metadata)
    router_decision = RouterDecision.model_validate(raw)
    task_type = router_decision.task_type if router_decision.task_type in SERVICE_ENDPOINTS else "chat"
    if not req.task_type and not req.metadata.get("task_type"):
        attached = _attachment_driven_task(req)
        if attached:
            task_type = attached
            router_decision.task_type = attached
            router_decision.confidence = 0.95
            router_decision.notes = "attachment-driven routing"
    return task_type, router_decision


async def call_memory(query: str) -> list:
    """Retrieve memory hits; an unreachable store degrades to no context."""
    try:
        async with httpx.AsyncClient(timeout=MEMORY_TIMEOUT) as client:
            resp = await client.post(MEMORY_URL, json={"query": query})
            resp.raise_for_status()
        results = resp.json().get("results", [])
        return [str(item) for item in results] if isinstance(results, list) else []
    except (httpx.HTTPError, ValueError) as exc:
        _LOG.warning("memory lookup failed (%r); continuing without context", exc)
        return []


async def call_service(url: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=EXPERT_TIMEOUT) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
    return resp.json()


@app.post("/classify")
async def classify(req: LLMRequest) -> dict:
    """Return the routing decision on its own.

    The gateway streams tokens directly from the model backend, so it only needs
    to know which expert owns the task. Sending that through /route would generate
    a full answer as a side effect and then discard it.
    """
    task_type, router_decision = await decide_route(req)
    return {"task_type": task_type, "routing": router_decision.model_dump()}


@app.post("/route")
async def route(req: LLMRequest) -> UnifiedResponse:
    task_type, router_decision = await decide_route(req)

    memory_context = list(req.memory_context)
    if router_decision.needs_memory:
        memory_context = await call_memory(req.message)

    payload = {
        **req.model_dump(exclude={"memory_context", "task_type"}),
        "task_type": task_type,
        "memory_context": memory_context,
    }
    service_payload = payload
    if task_type == "agent":
        service_payload = {
            "tool": "planned_task",
            "arguments": {"message": req.message, "metadata": req.metadata},
            "approved": bool(req.metadata.get("approved")),
        }
    try:
        await ensure_expert_model(task_type)
        raw = await call_service(SERVICE_ENDPOINTS[task_type], service_payload)
        result = ExpertResponse.model_validate(raw)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"{task_type} expert unavailable") from exc
    except ValueError as exc:
        _LOG.error("expert %s returned an unexpected payload: %r", task_type, exc)
        raise HTTPException(status_code=502, detail=f"{task_type} expert returned an invalid response") from exc
    return UnifiedResponse(
        request_id=req.request_id or str(uuid4()),
        task_type=task_type,
        response=result.text,
        expert=result.expert,
        model=result.model,
        artifacts=result.artifacts,
        routing=router_decision,
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "router"}


@app.get("/ready")
async def ready() -> dict[str, object]:
    """Report whether the router can reach the experts it dispatches to."""
    async with httpx.AsyncClient(timeout=3.0) as client:
        response = await client.get(f"{SERVICE_URLS['general']}/health")
        response.raise_for_status()
    return {"status": "ready", "experts": sorted(SERVICE_ENDPOINTS)}
