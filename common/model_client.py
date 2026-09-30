import asyncio
import base64
import logging
import os
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import httpx

from common.env import env_float, env_int

_LOG = logging.getLogger("model_client")

# Base64 inflates payloads by ~4/3; cap the decoded size so one oversized
# attachment cannot exhaust memory before the model is even called.
MAX_IMAGE_BYTES = env_int("MODEL_MAX_IMAGE_BYTES", 8 * 1024 * 1024, minimum=1024)
FETCH_TIMEOUT = env_float("MODEL_FETCH_TIMEOUT", 30.0, minimum=1.0)


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
    return env_int("MODEL_MAX_TOKENS", 1024, minimum=1)


async def _post_chat(base_url: str, payload: dict[str, Any], model: str) -> str | None:
    """POST a chat completion, retrying transient transport failures.

    Local runtimes (Ollama) restart after an OOM kill or a model swap, which shows up
    as ConnectError / RemoteProtocolError. Retrying with backoff rides that out.
    """
    attempts = env_int("MODEL_RETRIES", 3, minimum=1, maximum=10)
    delay = env_float("MODEL_RETRY_DELAY", 3.0, minimum=0.0)
    timeout = env_float("MODEL_TIMEOUT", 120.0, minimum=1.0)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{base_url}/v1/chat/completions", json=payload, headers=_headers())
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


def _build_payload(
    system_prompt: str,
    user_prompt: str,
    model: str,
    max_tokens: int | None,
) -> dict[str, Any]:
    """Assemble an OpenAI-compatible chat request."""
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": env_float("MODEL_TEMPERATURE", 0.2, minimum=0.0, maximum=2.0),
        "max_tokens": _max_tokens(max_tokens),
    }


async def complete(
    system_prompt: str,
    user_prompt: str,
    default_model: str,
    max_tokens: int | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> str | None:
    """Run one chat completion, or return ``None`` when no backend answered.

    ``base_url``/``model`` are explicit overrides. The router used to set
    ``os.environ`` around this call to reach a different backend, which raced
    with any concurrent request reading the same variables.
    """
    resolved_url = (base_url if base_url is not None else os.getenv("MODEL_BASE_URL", "")).rstrip("/")
    if not resolved_url:
        _LOG.info("no model backend configured - using offline stub for %s", default_model)
        return None

    resolved_model = model or os.getenv("MODEL_NAME", default_model)
    payload = _build_payload(system_prompt, user_prompt, resolved_model, max_tokens)
    return await _post_chat(resolved_url, payload, resolved_model)


def _load_image_bytes(uri: str) -> bytes:
    """Blocking read of one image, sized so a bad URI cannot exhaust memory."""
    if len(uri) > MAX_IMAGE_BYTES * 2:  # base64 is ~4/3 of the binary size
        raise ValueError(f"attachment exceeds {MAX_IMAGE_BYTES} bytes")
    if uri.startswith(("http://", "https://")):
        with urlopen(uri, timeout=FETCH_TIMEOUT) as response:  # noqa: S310 - scheme checked above
            return response.read(MAX_IMAGE_BYTES)
    return Path(uri).read_bytes()[:MAX_IMAGE_BYTES]


async def complete_with_images(
    system_prompt: str,
    user_prompt: str,
    images: list[tuple[str, str]],
    default_model: str,
) -> str | None:
    """Multimodal completion; ``images`` is a list of ``(uri, content_type)``."""
    base_url = os.getenv("MODEL_BASE_URL", "").rstrip("/")
    if not base_url:
        return None
    content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
    try:
        for uri, content_type in images:
            if uri.startswith("data:"):
                data_uri = uri
            else:
                # urlopen/read_bytes block, so keep them off the event loop.
                raw = await asyncio.to_thread(_load_image_bytes, uri)
                encoded = base64.b64encode(raw).decode("ascii")
                data_uri = f"data:{content_type};base64,{encoded}"
            content.append({"type": "image_url", "image_url": {"url": data_uri}})
    except (OSError, ValueError) as exc:
        _LOG.warning("could not load image attachment: %r", exc)
        return None

    model = os.getenv("MODEL_NAME", default_model)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        "max_tokens": _max_tokens(),
    }
    return await _post_chat(base_url, payload, model)
