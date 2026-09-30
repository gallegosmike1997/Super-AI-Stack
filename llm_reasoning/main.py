import logging

from common.env import env_str
from common.logging_utils import configure_logging
from common.model_client import complete
from common.schemas import LLMRequest
from common.utils import create_expert_app, offline_notice

configure_logging("llm_reasoning")
_LOG = logging.getLogger("llm_reasoning")

MODEL_NAME = env_str("MODEL_NAME", "deepseek-r1:8b")

SYSTEM_PROMPT = (
    "You are the reasoning core for Super AI Stack (DeepSeek R1). "
    "Handle logic, planning, math, and chain-of-thought. Be precise."
)


async def respond(request: LLMRequest) -> tuple[str, list[dict]]:
    prompt = (
        "Reasoning task for DeepSeek-R1. Think step by step, "
        "show chain-of-thought, then give the final answer.\n\n"
        f"User request:\n{request.message}"
    )
    if request.memory_context:
        prompt += "\n\nRelevant memory:\n" + "\n".join(f"- {item}" for item in request.memory_context)
    generated = await complete(SYSTEM_PROMPT, prompt, MODEL_NAME)
    return generated or offline_notice("reasoning", MODEL_NAME), []


app = create_expert_app("reasoning", MODEL_NAME, respond)
