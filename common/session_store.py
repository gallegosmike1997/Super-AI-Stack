import json
import logging
import os
import sqlite3
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_LOG = logging.getLogger("session_store")

DB_PATH = Path(os.getenv("SESSION_DB_PATH", "/data/sessions.db"))
_FALLBACK_DIR = Path(tempfile.gettempdir()) / "sas-sessions"

# The schema is created once per process rather than on every connection.
_SCHEMA_READY = False
_INIT_LOCK = threading.Lock()


_ACTIVE_PATH: Path | None = None
_PROBE_CACHE: dict[Path, bool] = {}


def _is_writable(directory: Path) -> bool:
    """Whether ``directory`` exists (or can be created) and accepts writes.

    The result is cached per directory: this runs on every read and write, and
    a mkdir/touch/unlink round trip each time is pure overhead.
    """
    cached = _PROBE_CACHE.get(directory)
    if cached is not None:
        return cached
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".sas-write-probe"
        probe.touch()
        probe.unlink()
        writable = True
    except OSError:
        writable = False
    _PROBE_CACHE[directory] = writable
    return writable


def resolved_path() -> Path:
    """Return a writable database path.

    The /data default only exists inside the container. Running the stack directly on
    a host would raise PermissionError, so degrade to a temp store instead of crashing.
    """
    global _SCHEMA_READY, _ACTIVE_PATH

    target = DB_PATH
    if not _is_writable(target.parent):
        _LOG.warning("session store %s is not writable; falling back to %s", DB_PATH, _FALLBACK_DIR)
        if not _is_writable(_FALLBACK_DIR):
            raise OSError(f"no writable directory for the session store ({DB_PATH}, {_FALLBACK_DIR})")
        target = _FALLBACK_DIR / "sessions.db"

    with _INIT_LOCK:
        # Only invalidate the cached schema when the database actually changes;
        # doing it on every call defeated the point of caching it.
        if target != _ACTIVE_PATH:
            _ACTIVE_PATH = target
            _SCHEMA_READY = False
    return target


def _ensure_schema(db: sqlite3.Connection) -> None:
    """Create the table and its index once, guarded for concurrent requests."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _INIT_LOCK:
        if _SCHEMA_READY:
            return
        db.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, "
            "content TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        # Every read filters and orders by session_id; without this index the
        # table is scanned in full on each turn of a long conversation.
        db.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)")
        _SCHEMA_READY = True


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """Yield a connection, committing on success and always closing it.

    The previous ``with sqlite3.connect(...) as db`` pattern committed but never
    closed, leaking one file handle per history/append call.
    """
    db = sqlite3.connect(resolved_path(), timeout=10.0)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        _ensure_schema(db)
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def history(session_id: str, limit: int = 10) -> list[str]:
    """Most recent turns for ``session_id``, oldest first."""
    with connection() as db:
        rows = db.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, max(1, limit)),
        ).fetchall()
    return [f"{role}: {content}" for role, content in reversed(rows)]


def append(session_id: str, role: str, content: str) -> None:
    """Record one turn. Empty turns are dropped rather than stored."""
    if not session_id or not content:
        return
    with connection() as db:
        db.execute(
            "INSERT INTO messages(session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )


def clear(session_id: str) -> int:
    """Delete a session's history; returns the number of turns removed."""
    with connection() as db:
        cursor = db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        return max(0, cursor.rowcount)


def list_sessions(limit: int = 20) -> list[dict[str, object]]:
    """Most recently active sessions, newest first."""
    with connection() as db:
        rows = db.execute(
            "SELECT session_id, COUNT(*) AS turns, MAX(created_at) AS updated "
            "FROM messages GROUP BY session_id ORDER BY updated DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
    return [{"session_id": row[0], "turns": row[1], "updated": row[2]} for row in rows]


def export_session(session_id: str) -> str:
    return json.dumps(history(session_id, 100), indent=2)
