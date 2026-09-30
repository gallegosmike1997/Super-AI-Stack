import logging

from common.env import env_str
from common.logging_utils import configure_logging
from common.model_client import complete
from common.schemas import LLMRequest
from common.utils import create_expert_app, offline_notice

configure_logging("llm_coding")
_LOG = logging.getLogger("llm_coding")

# Local MoE default: Qwen2.5-Coder 7B runs on commodity hardware.
# Set MODEL_NAME=mistral-large (Ollama: mistral-large, vLLM, or hosted)
# to use Mistral Large as listed in the Super AI Stack table.
MODEL_NAME = env_str("MODEL_NAME", "qwen2.5-coder:7b")

SYSTEM_PROMPT = (
    "You are the coding and tools expert for Super AI Stack "
    "(Mistral Large / Qwen2.5-Coder). Return practical, correct code."
)


async def respond(request: LLMRequest) -> tuple[str, list[dict]]:
    prompt = (
        "Coding / tools task. Return practical, correct code with brief explanation.\n\n"
        f"User request:\n{request.message}"
    )
    if request.memory_context:
        prompt += "\n\nRelevant memory:\n" + "\n".join(f"- {item}" for item in request.memory_context)
    generated = await complete(SYSTEM_PROMPT, prompt, MODEL_NAME)
    return generated or offline_notice("coding", MODEL_NAME), []


app = create_expert_app("coding", MODEL_NAME, respond)
