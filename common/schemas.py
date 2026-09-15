from typing import Any

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    kind: str = Field(description="image, audio, document, or other")
    uri: str
    content_type: str | None = None


class LLMRequest(BaseModel):
    message: str
    user_id: str | None = None
    session_id: str | None = None
    request_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    attachments: list[Attachment] = Field(default_factory=list)
    task_type: str | None = None
    memory_context: list[str] = Field(default_factory=list)


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
