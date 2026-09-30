"""Client side of the model-swap scheduler.

Experts and the gateway call ``ensure_model()`` before dispatching work so the
resident model matches what the request needs. Everything is best-effort: with
no manager configured (or one that is down) the call returns immediately and
requests proceed exactly as before.
"""

import logging
import os

import httpx

_LOG = logging.getLogger("model_manager_client")


def manager_url() -> str:
    return os.getenv("MODEL_MANAGER_URL", "").rstrip("/")


async def ensure_model(model: str | None) -> None:
    """Ask the model manager to make ``model`` the resident model.

    A cold swap can take minutes on CPU (big model load), so the timeout is
    generous and configurable via MODEL_ENSURE_TIMEOUT.
    """
    url = manager_url()
    if not url or not model:
        return
    try:
        async with httpx.AsyncClient(timeout=float(os.getenv("MODEL_ENSURE_TIMEOUT", "620"))) as client:
            response = await client.post(f"{url}/ensure", json={"model": model})
            response.raise_for_status()
        _LOG.info("model manager: %s", response.json())
    except httpx.HTTPError:
        _LOG.warning("model manager unavailable at %s - continuing without swap", url)
