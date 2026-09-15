import os
import base64
import asyncio
import logging
from pathlib import Path
from urllib.request import urlopen
from typing import Any

import httpx

_LOG = logging.getLogger("model_client")


def _headers() -> dict[str, str]:
    headers = {}
    api_key = os.getenv("MODEL_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _extract_content(payload: dict[str, Any]) -> str | None:
    """Read assistant text from an OpenAI-compatible chat response."""
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        # Some servers return a content-part list instead of a plain string.
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else None


def _max_tokens(override: int | None = None) -> int:
    """Cap generation length.

    Local CPU inference runs at single-digit tokens/second, so an uncapped reply
    generates until the context window fills (~4096 tokens = ~10 minutes) and the
    caller times out. A cap turns that into a fast, bounded answer.
    """
    if override is not None:
        return max(1, override)
    return max(1, int(os.getenv("MODEL_MAX_TOKENS", "1024")))


async def _post_chat(base_url: str, payload: dict[str, Any], model: str) -> str | None:
    """POST a chat completion, retrying transient transport failures.

    Local runtimes (Ollama) restart after an OOM kill or a model swap, which shows up
    as ConnectError / RemoteProtocolError. Retrying with backoff rides that out.
    """
    attempts = max(1, int(os.getenv("MODEL_RETRIES", "3")))
    delay = max(0.0, float(os.getenv("MODEL_RETRY_DELAY", "3")))
    timeout = float(os.getenv("MODEL_TIMEOUT", "120"))
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{base_url}/v1/chat/completions", json=payload, headers=_headers()
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # A bad model tag or malformed request will not fix itself by retrying.
            _LOG.warning(
                "model HTTP %s from %s (%s): %s",
                exc.response.status_code,
                base_url,
                model,
                exc.response.text[:300],
            )
            return None
        except httpx.TransportError as exc:
            last_error = exc
            _LOG.warning(
                "model transport error (attempt %s/%s) to %s (%s): %r",
                attempt,
                attempts,
                base_url,
                model,
                exc,
            )
            if attempt < attempts:
                await asyncio.sleep(delay * attempt)
            continue

        content = _extract_content(response.json())
        if not content:
            _LOG.warning("model %s returned no assistant content: %s", model, response.text[:300])
        return content

    _LOG.warning("model %s unreachable at %s after %s attempts: %r", model, base_url, attempts, last_error)
    return None


async def complete(
    system_prompt: str,
    user_prompt: str,
    default_model: str,
    max_tokens: int | None = None,
) -> str | None:
    base_url = os.getenv("MODEL_BASE_URL", "").rstrip("/")
    if not base_url:
        _LOG.info("MODEL_BASE_URL unset - using offline prompt stub for %s", default_model)
        return None

    model = os.getenv("MODEL_NAME", default_model)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": float(os.getenv("MODEL_TEMPERATURE", "0.2")),
        "max_tokens": _max_tokens(max_tokens),
    }
    return await _post_chat(base_url, payload, model)


async def complete_with_images(
    system_prompt: str,
    user_prompt: str,
    images: list[tuple[str, str]],
    default_model: str,
) -> str | None:
    base_url = os.getenv("MODEL_BASE_URL", "").rstrip("/")
    if not base_url:
        return None
    content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
    try:
        for uri, content_type in images:
            if uri.startswith("data:"):
                data_uri = uri
            elif uri.startswith(("http://", "https://")):
                with urlopen(uri, timeout=30) as response:
                    encoded = base64.b64encode(response.read()).decode("ascii")
                data_uri = f"data:{content_type};base64,{encoded}"
            else:
                encoded = base64.b64encode(Path(uri).read_bytes()).decode("ascii")
                data_uri = f"data:{content_type};base64,{encoded}"
            content.append({"type": "image_url", "image_url": {"url": data_uri}})
        return await _complete_payload(
            base_url,
            {"model": os.getenv("MODEL_NAME", default_model), "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
             "max_tokens": _max_tokens()},
        )
    except (OSError, ValueError):
        return None


async def _complete_payload(base_url: str, payload: dict[str, Any]) -> str | None:
    return await _post_chat(base_url, payload, str(payload.get("model", "")))