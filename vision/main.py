import logging

from common.env import env_str
from common.logging_utils import configure_logging
from common.model_client import complete_with_images
from common.schemas import LLMRequest
from common.utils import create_expert_app, offline_notice

configure_logging("vision")
_LOG = logging.getLogger("vision")

MODEL_NAME = env_str("MODEL_NAME", "qwen2.5vl:3b")


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
        MODEL_NAME,
    )
    if generated:
        return generated, [{"type": "image_analysis", "count": len(images)}]
    return offline_notice("vision", MODEL_NAME), [{"type": "image_analysis", "count": len(images)}]


app = create_expert_app("vision", MODEL_NAME, respond)
