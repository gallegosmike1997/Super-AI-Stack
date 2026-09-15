import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI
from pydantic import BaseModel, Field

from common.schemas import ExpertResponse


app = FastAPI(title="Super AI Stack - Agent")
ALLOWED_TOOLS = set(filter(None, os.getenv("ALLOWED_TOOLS", "").split(",")))
AUDIT_PATH = Path(os.getenv("AUDIT_LOG_PATH", "/data/agent-audit.jsonl"))
TOOL_REGISTRY = {
    "echo": {"description": "Return arguments without side effects", "risk": "low"},
    "http_request": {"description": "Call an external HTTP endpoint", "risk": "high"},
    "run_script": {"description": "Run an approved script", "risk": "critical"},
}


def audit(event: dict) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    event["timestamp"] = datetime.now(timezone.utc).isoformat()
    with AUDIT_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event) + "\n")


class ToolRequest(BaseModel):
    tool: str
    arguments: dict = Field(default_factory=dict)
    approved: bool = False


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "agent", "mode": "approval-required"}


@app.get("/tools")
async def tools() -> dict[str, list[dict[str, str | bool]]]:
    return {"tools": [{"name": name, **details, "allowlisted": name in ALLOWED_TOOLS} for name, details in TOOL_REGISTRY.items()]}


@app.post("/execute", response_model=ExpertResponse)
async def execute(request: ToolRequest) -> ExpertResponse:
    job_id = str(uuid4())
    if request.tool not in ALLOWED_TOOLS:
        audit({"job_id": job_id, "status": "denied", "tool": request.tool, "arguments": request.arguments})
        return ExpertResponse(text="Tool is not allowlisted.", expert="agent", model="approval-gated", artifacts=[{"job_id": job_id, "status": "denied", "tool": request.tool}])
    if not request.approved:
        audit({"job_id": job_id, "status": "approval_required", "tool": request.tool, "arguments": request.arguments})
        return ExpertResponse(
            text="Tool execution requires explicit approval.",
            expert="agent",
            model="approval-gated",
            artifacts=[{"job_id": job_id, "status": "approval_required", "tool": request.tool, "arguments": request.arguments}],
        )
    audit({"job_id": job_id, "status": "queued", "tool": request.tool, "arguments": request.arguments})
    return ExpertResponse(
        text="Tool request queued; connect an allowlisted adapter before execution.",
        expert="agent",
        model="approval-gated",
        artifacts=[{"job_id": job_id, "status": "queued", "tool": request.tool, "arguments": request.arguments}],
    )
