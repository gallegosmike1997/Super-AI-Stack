"""Model-swap scheduler for memory-constrained boxes.

Ollama keeps several models resident (OLLAMA_MAX_LOADED_MODELS defaults to 3),
which OOM-kills llama-server on an 8 GB machine when big models stack up.
This service enforces a residency policy instead:

- big models (llama3.1, deepseek-r1, qwen2.5-coder) get *exclusive* residency:
  loading one unloads everything else;
- small models (phi3, qwen2.5vl) may coexist, but any big model is evicted
  first to free RAM.

The gateway calls ``POST /ensure`` with the routed expert's model tag before
dispatching, so a reasoning request transparently swaps phi3 out and
deepseek-r1 in.
"""

import asyncio
import logging

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from common.env import env_float, env_list, env_str
from common.logging_utils import configure_logging

configure_logging("model_manager")
_LOG = logging.getLogger("model_manager")

OLLAMA_URL = env_str("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
KEEP_ALIVE = env_str("MODEL_KEEP_ALIVE", "30m")
# Models too large to share RAM with anything else.
BIG_MODELS = env_list(
    "BIG_MODELS",
    ("llama3.1", "deepseek-r1", "qwen2.5-coder", "mistral-large"),
)
LOAD_TIMEOUT = env_float("MODEL_LOAD_TIMEOUT", 600.0, minimum=1.0)
PROBE_TIMEOUT = env_float("MODEL_PROBE_TIMEOUT", 10.0, minimum=1.0)

app = FastAPI(title="Super AI Stack - model manager")

# Serialises swaps: two concurrent /ensure calls would otherwise each unload
# what the other just loaded.
_SWAP_LOCK = asyncio.Lock()


def is_big(model: str) -> bool:
    return any(model.startswith(big) for big in BIG_MODELS)


async def loaded_models() -> list[str]:
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        response = await client.get(f"{OLLAMA_URL}/api/ps")
        response.raise_for_status()
        return [entry["model"] for entry in response.json().get("models", [])]


async def unload(model: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={"model": model, "messages": [], "keep_alive": 0},
            )
        _LOG.info("unloaded %s", model)
    except httpx.HTTPError:
        _LOG.warning("failed to unload %s", model, exc_info=True)


async def load(model: str) -> None:
    """Load weights and apply keep_alive.

    The response was previously discarded without checking, so a failed load was
    reported to the caller as a successful swap.
    """
    async with httpx.AsyncClient(timeout=LOAD_TIMEOUT) as client:
        response = await client.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "keep_alive": KEEP_ALIVE},
        )
        response.raise_for_status()
    _LOG.info("loaded %s (keep_alive=%s)", model, KEEP_ALIVE)


class EnsureRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "model_manager"}


@app.post("/ensure")
async def ensure(request: EnsureRequest) -> dict:
    """Make sure ``model`` is the resident model under the policy, then report."""
    target = request.model
    async with _SWAP_LOCK:
        try:
            resident = await loaded_models()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=f"inference backend unreachable: {OLLAMA_URL}") from exc
        if target in resident and not (is_big(target) and len(resident) > 1):
            return {"status": "resident", "model": target, "loaded": resident}

        for other in resident:
            if other == target:
                continue
            if is_big(target) or is_big(other):
                await unload(other)

        if target not in resident:
            try:
                await load(target)
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502, detail=f"could not load {target}: {exc}") from exc

        try:
            final = await loaded_models()
        except httpx.HTTPError:
            final = []
        return {"status": "swapped", "model": target, "loaded": final}


@app.get("/status")
async def status() -> dict:
    try:
        resident = await loaded_models()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"inference backend unreachable: {OLLAMA_URL}") from exc
    return {
        "ollama": OLLAMA_URL,
        "policy": {"big_models": list(BIG_MODELS), "keep_alive": KEEP_ALIVE},
        "loaded": resident,
    }
