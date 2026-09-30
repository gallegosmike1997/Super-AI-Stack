import inspect
import os
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI

from common.logging_utils import configure_logging
from common.schemas import ExpertResponse, LLMRequest


def model_backend() -> str:
    """Report whether a live inference backend is configured, for honest health."""
    return "configured" if os.getenv("MODEL_BASE_URL") else "offline"


def offline_notice(expert: str, model: str) -> str:
    """Actionable message shown when no model answered, instead of echoing the prompt."""
    base_url = os.getenv("MODEL_BASE_URL", "")
    lines = [
        f"⚠️ {expert} expert has no live language model right now.",
        f"Requested model: {model}",
    ]
    if base_url:
        lines.append(
            f"Backend {base_url} is configured but the call failed - check that the "
            "server is up, the model tag exists, and MODEL_TIMEOUT is large enough for a cold load."
        )
    else:
        lines.append("Set MODEL_BASE_URL (e.g. http://127.0.0.1:11434 for Ollama) to enable real inference.")
    return "\n".join(lines)


def create_expert_app(
    expert: str,
    model: str,
    responder: Callable[
        [LLMRequest],
        tuple[str, list[dict[str, Any]]] | Awaitable[tuple[str, list[dict[str, Any]]]],
    ],
) -> FastAPI:
    """Build the standard FastAPI app shared by the expert microservices.

    Every expert exposes the same two routes so the gateway can treat them
    uniformly: ``GET /health`` for liveness and ``POST /infer`` for work.
    """
    configure_logging(expert)
    app = FastAPI(title=f"Super AI Stack - {expert}")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": expert, "model": model, "backend": model_backend()}

    @app.post("/infer", response_model=ExpertResponse)
    async def infer(request: LLMRequest) -> ExpertResponse:
        result = responder(request)
        if inspect.isawaitable(result):
            result = await result
        text, artifacts = result
        return ExpertResponse(text=text, expert=expert, model=model, artifacts=artifacts)

    return app
