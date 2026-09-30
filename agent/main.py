import json
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel, Field

from common.env import env_list, env_str
from common.logging_utils import configure_logging
from common.schemas import ExpertResponse

configure_logging("agent")
_LOG = logging.getLogger("agent")

app = FastAPI(title="Super AI Stack - Agent")

# Tool execution is deny-by-default: nothing runs until it is explicitly
# allowlisted, and even then it waits for per-request approval.
ALLOWED_TOOLS = set(env_list("ALLOWED_TOOLS"))
TOOL_REGISTRY = {
    "echo": {"description": "Return arguments without side effects", "risk": "low"},
    "http_request": {"description": "Call an external HTTP endpoint", "risk": "high"},
    "run_script": {"description": "Run an approved script", "risk": "critical"},
}


def _audit_path() -> Path:
    """Writable audit log path, falling back to a temp dir outside Docker.

    Writing to ``/data`` used to raise on a host install, which turned every
    tool decision into a 500 instead of a recorded decision.
    """
    configured = Path(env_str("AUDIT_LOG_PATH", "/data/agent-audit.jsonl"))
    try:
        configured.parent.mkdir(parents=True, exist_ok=True)
        probe = configured.parent / ".sas-write-probe"
        probe.touch()
        probe.unlink()
        return configured
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "sas-agent"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback / "agent-audit.jsonl"


AUDIT_PATH = _audit_path()


def audit(event: dict) -> None:
    """Append a decision to the audit log; never fail the request over it."""
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        event["timestamp"] = datetime.now(UTC).isoformat()
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, default=str) + "\n")
    except OSError:
        # An unwritable audit sink must not deny or approve a tool call silently.
        _LOG.error("could not write agent audit log for %s", event.get("job_id"), exc_info=True)


class ToolRequest(BaseModel):
    tool: str = Field(min_length=1, max_length=64)
    arguments: dict = Field(default_factory=dict)
    approved: bool = False


@app.get("/health")
async def health() -> dict[str, str | int]:
    return {
        "status": "ok",
        "service": "agent",
        "mode": "approval-required",
        "allowlisted": len(ALLOWED_TOOLS),
    }


@app.get("/tools")
async def tools() -> dict[str, list[dict[str, str | bool]]]:
    return {
        "tools": [
            {"name": name, **details, "allowlisted": name in ALLOWED_TOOLS} for name, details in TOOL_REGISTRY.items()
        ]
    }


@app.post("/execute", response_model=ExpertResponse)
async def execute(request: ToolRequest) -> ExpertResponse:
    """Gate a tool call: deny-by-default, then require explicit approval."""
    job_id = str(uuid4())
    if request.tool not in ALLOWED_TOOLS:
        _LOG.warning("denied tool %s (job %s)", request.tool, job_id)
        audit({"job_id": job_id, "status": "denied", "tool": request.tool, "arguments": request.arguments})
        return ExpertResponse(
            text="Tool is not allowlisted.",
            expert="agent",
            model="approval-gated",
            artifacts=[{"job_id": job_id, "status": "denied", "tool": request.tool}],
        )
    if not request.approved:
        audit({"job_id": job_id, "status": "approval_required", "tool": request.tool, "arguments": request.arguments})
        return ExpertResponse(
            text="Tool execution requires explicit approval.",
            expert="agent",
            model="approval-gated",
            artifacts=[
                {
                    "job_id": job_id,
                    "status": "approval_required",
                    "tool": request.tool,
                    "arguments": request.arguments,
                }
            ],
        )
    audit({"job_id": job_id, "status": "queued", "tool": request.tool, "arguments": request.arguments})
    return ExpertResponse(
        text="Tool request queued; connect an allowlisted adapter before execution.",
        expert="agent",
        model="approval-gated",
        artifacts=[{"job_id": job_id, "status": "queued", "tool": request.tool, "arguments": request.arguments}],
    )
