import os

from common.model_client import complete
from common.schemas import LLMRequest
from common.utils import create_expert_app, offline_notice


async def respond(request: LLMRequest) -> tuple[str, list[dict]]:
    prompt = build_prompt(request.message, request.memory_context)
    generated = await complete(
        "You are the general language expert for Super AI Stack.",
        prompt,
        os.getenv("MODEL_NAME", "llama3.1"),
    )
    return generated or offline_notice("general", os.getenv("MODEL_NAME", "llama3.1")), []


app = create_expert_app("general", os.getenv("MODEL_NAME", "llama3.1"), respond)


def build_prompt(message: str, memory_context: list[str]) -> str:
    context_block = ""
    if memory_context:
        context_block = "\n\nRelevant context from memory:\n" + "\n".join(
            f"- {item}" for item in memory_context
        )

    return (
        "You are the General LLM for the Super AI Stack.\n"
        "Use the memory context when it is relevant.\n"
        "If memory is irrelevant, ignore it.\n"
        "Respond clearly and concisely.\n\n"
        f"User message:\n{message}"
        f"{context_block}\n\n"
        "Final answer:"
    )
