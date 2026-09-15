import json
import os
import sqlite3
import tempfile
from pathlib import Path


DB_PATH = Path(os.getenv("SESSION_DB_PATH", "/data/sessions.db"))
_FALLBACK_DIR = Path(tempfile.gettempdir()) / "sas-sessions"


def resolved_path() -> Path:
	"""Return a writable database path.

	The /data default only exists inside the container. Running the stack directly on
	a host would raise PermissionError, so degrade to a temp store instead of crashing.
	"""
	try:
		DB_PATH.parent.mkdir(parents=True, exist_ok=True)
		probe = DB_PATH.parent / ".sas-write-probe"
		probe.touch()
		probe.unlink()
		return DB_PATH
	except OSError:
		_FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
		return _FALLBACK_DIR / "sessions.db"


def connection() -> sqlite3.Connection:
	db_path = resolved_path()
	db = sqlite3.connect(db_path)
	db.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
	return db


def history(session_id: str, limit: int = 10) -> list[str]:
	with connection() as db:
		rows = db.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY rowid DESC LIMIT ?", (session_id, limit)).fetchall()
	return [f"{role}: {content}" for role, content in reversed(rows)]


def append(session_id: str, role: str, content: str) -> None:
	with connection() as db:
		db.execute("INSERT INTO messages(session_id, role, content) VALUES (?, ?, ?)", (session_id, role, content))


def export_session(session_id: str) -> str:
	return json.dumps(history(session_id, 100), indent=2)