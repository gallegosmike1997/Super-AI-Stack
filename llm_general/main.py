import logging

from common.env import env_str
from common.logging_utils import configure_logging
from common.model_client import complete
from common.schemas import LLMRequest
from common.utils import create_expert_app, offline_notice

configure_logging("llm_general")
_LOG = logging.getLogger("llm_general")

MODEL_NAME = env_str("MODEL_NAME", "llama3.1")

SYSTEM_PROMPT = "You are the general language expert for Super AI Stack."


def build_prompt(message: str, memory_context: list[str]) -> str:
    context_block = ""
    if memory_context:
        context_block = "\n\nRelevant context from memory:\n" + "\n".join(f"- {item}" for item in memory_context)

    return (
        "You are the General LLM for the Super AI Stack.\n"
        "Use the memory context when it is relevant.\n"
        "If memory is irrelevant, ignore it.\n"
        "Respond clearly and concisely.\n\n"
        f"User message:\n{message}"
        f"{context_block}\n\n"
        "Final answer:"
    )


async def respond(request: LLMRequest) -> tuple[str, list[dict]]:
    prompt = build_prompt(request.message, request.memory_context)
    generated = await complete(SYSTEM_PROMPT, prompt, MODEL_NAME)
    return generated or offline_notice("general", MODEL_NAME), []


app = create_expert_app("general", MODEL_NAME, respond)
