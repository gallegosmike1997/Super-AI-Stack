from typing import Any

from pydantic import BaseModel, Field

# A request larger than this is either a mistake or an attempt to exhaust the
# gateway, and it would be forwarded verbatim to every downstream service.
MAX_MESSAGE_CHARS = 32_000
MAX_ATTACHMENTS = 8
MAX_MEMORY_CONTEXT_ITEMS = 50


class Attachment(BaseModel):
    kind: str = Field(default="other", max_length=32, description="image, audio, document, or other")
    uri: str = Field(max_length=8_000_000)
    content_type: str | None = Field(default=None, max_length=255)


class LLMRequest(BaseModel):
    # Empty is allowed: a request can be attachment-only (transcribe, describe).
    message: str = Field(default="", max_length=MAX_MESSAGE_CHARS)
    user_id: str | None = Field(default=None, max_length=255)
    session_id: str | None = Field(default=None, max_length=255)
    request_id: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)
    attachments: list[Attachment] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)
    task_type: str | None = Field(default=None, max_length=64)
    memory_context: list[str] = Field(default_factory=list, max_length=MAX_MEMORY_CONTEXT_ITEMS)


class RouterDecision(BaseModel):
    task_type: str = "chat"
    needs_memory: bool = False
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    experts: list[str] = Field(default_factory=list)
    notes: str = ""


class ExpertResponse(BaseModel):
    text: str
    expert: str
    model: str
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class UnifiedResponse(BaseModel):
    request_id: str
    task_type: str
    response: str
    expert: str
    model: str
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    routing: RouterDecision
