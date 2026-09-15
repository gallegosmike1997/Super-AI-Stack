import os

from common.model_client import complete_with_images
from common.schemas import LLMRequest
from common.utils import create_expert_app, offline_notice


async def respond(request: LLMRequest) -> tuple[str, list[dict]]:
	images = [
		(item.uri, item.content_type or "image/jpeg")
		for item in request.attachments
		if item.kind == "image" or (item.content_type and item.content_type.startswith("image/"))
	]
	if not images:
		return "No image attachment supplied.", []
	generated = await complete_with_images(
		"You are the vision expert. Describe images accurately and note uncertainty.",
		request.message or "Describe the supplied image.",
		images,
		os.getenv("MODEL_NAME", "qwen2.5vl:3b"),
	)
	return generated or offline_notice("vision", os.getenv("MODEL_NAME", "qwen2.5vl:3b")), [{"type": "image_analysis", "count": len(images)}]


app = create_expert_app("vision", os.getenv("MODEL_NAME", "qwen2.5vl:3b"), respond)
