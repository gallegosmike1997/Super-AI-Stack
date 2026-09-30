import base64
import binascii
import logging

import httpx

from common.env import env_float, env_int, env_str
from common.logging_utils import configure_logging
from common.schemas import LLMRequest
from common.utils import create_expert_app

configure_logging("image_gen")
_LOG = logging.getLogger("image_gen")

MAX_IMAGE_BYTES = env_int("IMAGE_MAX_BYTES", 16 * 1024 * 1024, minimum=1024)


async def respond(request: LLMRequest) -> tuple[str, list[dict]]:
    """Generate an image, or report honestly that it is not configured."""
    base_url = env_str("IMAGE_MODEL_BASE_URL").rstrip("/")
    if not base_url:
        # Queued, not generated - the console must not claim an image exists.
        return "Image generation is queued; configure IMAGE_MODEL_BASE_URL to enable it.", [
            {"type": "image", "status": "queued", "prompt": request.message}
        ]
    payload = {
        "prompt": request.message,
        "steps": env_int("IMAGE_STEPS", 20, minimum=1, maximum=150),
        "width": env_int("IMAGE_WIDTH", 512, minimum=64, maximum=2048),
        "height": env_int("IMAGE_HEIGHT", 512, minimum=64, maximum=2048),
    }
    try:
        async with httpx.AsyncClient(timeout=env_float("IMAGE_TIMEOUT", 300.0, minimum=1.0)) as client:
            response = await client.post(f"{base_url}/sdapi/v1/txt2img", json=payload)
            response.raise_for_status()
        encoded = response.json()["images"][0]
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        _LOG.warning("image generation failed: %r", exc)
        return "Image generation is temporarily unavailable.", [
            {"type": "image", "status": "unavailable", "prompt": request.message}
        ]

    # The backend returns a base64 PNG; the previous code decoded and re-encoded
    # it to the identical string, which only cost memory on large images.
    if not isinstance(encoded, str) or not encoded:
        return "Image generation returned an unexpected payload.", [
            {"type": "image", "status": "unavailable", "prompt": request.message}
        ]
    if len(encoded) > MAX_IMAGE_BYTES * 2:
        _LOG.warning("generated image exceeds %s bytes; not returning it inline", MAX_IMAGE_BYTES)
        return "The generated image is too large to return inline.", [
            {"type": "image", "status": "too_large", "prompt": request.message}
        ]
    try:
        base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return "Image generation returned malformed image data.", [
            {"type": "image", "status": "unavailable", "prompt": request.message}
        ]
    return "Image generated successfully.", [
        {"type": "image", "status": "complete", "mime_type": "image/png", "data": encoded}
    ]


app = create_expert_app("image_gen", env_str("IMAGE_MODEL_NAME", "stable-diffusion-3"), respond)
