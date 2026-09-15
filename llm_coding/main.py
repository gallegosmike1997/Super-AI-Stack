import os

from common.model_client import complete
from common.schemas import LLMRequest
from common.utils import create_expert_app, offline_notice


# Local MoE default: Qwen2.5-Coder 7B runs on commodity hardware.
# Set MODEL_NAME=mistral-large (Ollama: mistral-large, vLLM, or hosted)
# to use Mistral Large as listed in the Super AI Stack table.
async def respond(request: LLMRequest) -> tuple[str, list[dict]]:
    prompt = (
        "Coding / tools task. Return practical, correct code with brief explanation.\n\n"
        f"User request:\n{request.message}"
    )
    if request.memory_context:
        prompt += "\n\nRelevant memory:\n" + "\n".join(f"- {item}" for item in request.memory_context)
    generated = await complete(
        "You are the coding and tools expert for Super AI Stack (Mistral Large / Qwen2.5-Coder). Return practical, correct code.",
        prompt,
        os.getenv("MODEL_NAME", "qwen2.5-coder"),
    )
    return generated or offline_notice("coding", os.getenv("MODEL_NAME", "qwen2.5-coder")), []


app = create_expert_app("coding", os.getenv("MODEL_NAME", "qwen2.5-coder"), respond)
