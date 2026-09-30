"""Logging setup shared by every service.

Two things matter operationally: every line identifies the service that wrote
it (the Compose logs interleave eight processes), and a request can be followed
across services. ``request_context`` binds a correlation id to the current task
so log lines carry the same id the client was handed.
"""

import contextlib
import contextvars
import logging
import os
import sys
import uuid
from collections.abc import Iterator

_CONFIGURED = False

# Bound to the running task, so concurrent requests never share an id.
REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("sas_request_id", default="-")


class RequestIdFilter(logging.Filter):
    """Stamp every record with the active request id."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = REQUEST_ID.get()
        return True


def configure_logging(service: str, level: str | None = None) -> None:
    """Install a single stdout handler for ``service`` (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    resolved = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt=f"%(asctime)s {service} %(levelname)-7s [%(request_id)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    # Replace rather than append: uvicorn may have installed its own handlers,
    # which would otherwise duplicate every line.
    root.handlers[:] = [handler]
    root.setLevel(resolved)
    # httpx logs every request at INFO, which drowns out our own lines.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


@contextlib.contextmanager
def request_context(request_id: str | None = None) -> Iterator[str]:
    """Bind ``request_id`` (or a fresh one) for the duration of the block."""
    rid = request_id or str(uuid.uuid4())
    token = REQUEST_ID.set(rid)
    try:
        yield rid
    finally:
        REQUEST_ID.reset(token)


def current_request_id() -> str:
    """The request id bound to this task, or ``-`` outside a request."""
    return REQUEST_ID.get()


def new_request_id() -> str:
    """Generate a correlation id for a new request."""
    return str(uuid.uuid4())
