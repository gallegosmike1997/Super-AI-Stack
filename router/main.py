from fastapi import FastAPI, HTTPException
import httpx
import json
import os
from pathlib import Path
from uuid import uuid4

from common.constants import SERVICE_URLS, SUPPORTED_TASKS
from common.model_client import complete
from common.model_manager import ensure_model
from common.schemas import ExpertResponse, LLMRequest, RouterDecision, UnifiedResponse

app = FastAPI(title="Super AI Stack - Router")

# Remote Phi-3 API endpoint (replace with your actual URL)
PHI3_URL = os.getenv("PHI3_URL", "")
ROUTER_MODEL_NAME = os.getenv("ROUTER_MODEL_NAME", "phi3:mini")

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


import re


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
    if PHI3_URL:
        prompt = build_router_prompt(message, metadata)
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(PHI3_URL, json={"prompt": prompt})
            resp.raise_for_status()
        text = resp.json().get("reply", resp.json())
        if isinstance(text, dict):
            return text
        parsed = extract_json(text) if isinstance(text, str) else None
        return parsed or heuristic_route(message, metadata)

    generated = None
    router_base = os.getenv("ROUTER_MODEL_BASE_URL") or os.getenv("MODEL_BASE_URL", "")
    router_timeout = float(os.getenv("ROUTER_MODEL_TIMEOUT", os.getenv("MODEL_TIMEOUT", "120")))
    if router_base:
        saved_base, saved_timeout = os.getenv("MODEL_BASE_URL", ""), os.getenv("MODEL_TIMEOUT", "")
        os.environ["MODEL_BASE_URL"], os.environ["MODEL_TIMEOUT"] = router_base, str(router_timeout)
        try:
            generated = await complete(
                "You route requests for a multi-expert AI stack. Return JSON only.",
                build_router_prompt(message, metadata),
                ROUTER_MODEL_NAME,
                max_tokens=int(os.getenv("ROUTER_MAX_TOKENS", "160")),
            )
        finally:
            os.environ["MODEL_BASE_URL"], os.environ["MODEL_TIMEOUT"] = saved_base, saved_timeout
    if generated:
        parsed = extract_json(generated)
        if parsed and parsed.get("task_type") in SUPPORTED_TASKS:
            parsed.setdefault("confidence", 0.9)
            parsed.setdefault("notes", "phi3-router")
            return parsed
    return heuristic_route(message, metadata)


def heuristic_route(message: str, metadata: dict | None = None) -> dict:
    text = message.lower()
    if metadata and metadata.get("task_type") in SUPPORTED_TASKS:
        task_type = metadata["task_type"]
    elif any(word in text for word in ("code", "python", "script", "api", "debug")):
        task_type = "code"
    elif any(word in text for word in ("reason", "prove", "proof", "calculate", "plan")):
        task_type = "reasoning"
    elif any(word in text for word in ("image", "screenshot", "diagram", "photo")):
        task_type = "vision"
    elif any(word in text for word in ("speak", "audio", "transcribe", "voice")):
        task_type = "speech"
    elif any(word in text for word in ("draw", "generate an image", "concept art", "create an icon", "generate an icon", "make an image", "create an image")) or ("icon" in text and any(word in text for word in ("create", "generate", "make"))):
        task_type = "image_gen"
    elif any(word in text for word in ("run a tool", "call an api", "execute", "agent")):
        task_type = "agent"
    else:
        task_type = "chat"
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


# Router task types map to expert service keys; "code" is the odd one out.
TASK_TO_EXPERT = {"chat": "general", "code": "coding"}


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
    except httpx.HTTPError:
        return


async def call_memory(query: str) -> list:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(MEMORY_URL, json={"query": query})
        resp.raise_for_status()
    return resp.json().get("results", [])


async def call_service(url: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
    return resp.json()


async def decide_route(req: LLMRequest) -> tuple[str, RouterDecision]:
    """Pick the expert for a request without dispatching to it."""
    router_decision = RouterDecision.model_validate(await call_phi3_router(req.message, req.metadata))
    task_type = router_decision.task_type if router_decision.task_type in SERVICE_ENDPOINTS else "chat"
    if not req.task_type and not req.metadata.get("task_type"):
        if any(item.kind == "image" or (item.content_type and item.content_type.startswith("image/")) for item in req.attachments):
            task_type = "vision"
            router_decision.task_type = "vision"
            router_decision.confidence = 0.95
            router_decision.notes = "attachment-driven routing"
        elif any(item.kind == "audio" or (item.content_type and item.content_type.startswith("audio/")) for item in req.attachments):
            task_type = "speech"
            router_decision.task_type = "speech"
            router_decision.confidence = 0.95
            router_decision.notes = "attachment-driven routing"
    return task_type, router_decision


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
        result = ExpertResponse.model_validate(await call_service(SERVICE_ENDPOINTS[task_type], service_payload))
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"{task_type} expert unavailable") from exc
    return UnifiedResponse(
        request_id=req.request_id or str(uuid4()), task_type=task_type, response=result.text,
        expert=result.expert, model=result.model, artifacts=result.artifacts,
        routing=router_decision,
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "router"}
