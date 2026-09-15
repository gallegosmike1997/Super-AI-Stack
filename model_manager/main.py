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

import logging
import os

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
_LOG = logging.getLogger("model_manager")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
KEEP_ALIVE = os.getenv("MODEL_KEEP_ALIVE", "30m")
# Models too large to share RAM with anything else.
BIG_MODELS = tuple(
    filter(None, os.getenv("BIG_MODELS", "llama3.1,deepseek-r1,qwen2.5-coder,mistral-large").split(","))
)
LOAD_TIMEOUT = float(os.getenv("MODEL_LOAD_TIMEOUT", "600"))

app = FastAPI(title="Super AI Stack - model manager")


def is_big(model: str) -> bool:
    return any(model.startswith(big) for big in BIG_MODELS)


async def loaded_models() -> list[str]:
    async with httpx.AsyncClient(timeout=10) as client:
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
    # An empty generate request loads the weights and applies keep_alive.
    async with httpx.AsyncClient(timeout=LOAD_TIMEOUT) as client:
        await client.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "keep_alive": KEEP_ALIVE},
        )
    _LOG.info("loaded %s (keep_alive=%s)", model, KEEP_ALIVE)


class EnsureRequest(BaseModel):
    model: str


@app.post("/ensure")
async def ensure(request: EnsureRequest) -> dict:
    """Make sure ``model`` is the resident model under the policy, then report."""
    target = request.model
    resident = await loaded_models()
    if target in resident and not (is_big(target) and len(resident) > 1):
        return {"status": "resident", "model": target, "loaded": resident}

    for other in resident:
        if other == target:
            continue
        if is_big(target) or is_big(other):
            await unload(other)

    if target not in resident:
        await load(target)

    final = await loaded_models()
    return {"status": "swapped", "model": target, "loaded": final}


@app.get("/status")
async def status() -> dict:
    resident = await loaded_models()
    return {
        "ollama": OLLAMA_URL,
        "policy": {"big_models": list(BIG_MODELS), "keep_alive": KEEP_ALIVE},
        "loaded": resident,
    }
