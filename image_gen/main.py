import base64
import os

import httpx
from common.schemas import LLMRequest
from common.utils import create_expert_app


async def respond(request: LLMRequest) -> tuple[str, list[dict]]:
	base_url = os.getenv("IMAGE_MODEL_BASE_URL", "").rstrip("/")
	if not base_url:
		return "Image generation is queued; configure IMAGE_MODEL_BASE_URL to enable it.", [{"type": "image", "status": "queued", "prompt": request.message}]
	payload = {
		"prompt": request.message,
		"steps": int(os.getenv("IMAGE_STEPS", "20")),
		"width": int(os.getenv("IMAGE_WIDTH", "512")),
		"height": int(os.getenv("IMAGE_HEIGHT", "512")),
	}
	try:
		async with httpx.AsyncClient(timeout=float(os.getenv("IMAGE_TIMEOUT", "300"))) as client:
			response = await client.post(f"{base_url}/sdapi/v1/txt2img", json=payload)
			response.raise_for_status()
		data = response.json()
		encoded = data["images"][0]
		artifact = {"type": "image", "status": "complete", "mime_type": "image/png", "data": base64.b64encode(base64.b64decode(encoded)).decode("ascii")}
		return "Image generated successfully.", [artifact]
	except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
		return "Image generation is temporarily unavailable.", [{"type": "image", "status": "unavailable", "prompt": request.message}]


app = create_expert_app("image_gen", os.getenv("IMAGE_MODEL_NAME", "stable-diffusion-3"), respond)
