import asyncio
import hashlib
import io
import json
import logging
import tempfile
from pathlib import Path
from urllib.request import urlopen

import faiss
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from common.env import env_int, env_str
from common.logging_utils import configure_logging

configure_logging("memory")
_LOG = logging.getLogger("memory")

DIMENSION = 256
DATA_DIR = Path(env_str("MEMORY_DATA_DIR", "/data"))
# Bound the index so a long-running store cannot grow without limit; oldest
# entries are evicted once the cap is hit.
MAX_DOCUMENTS = env_int("MEMORY_MAX_DOCUMENTS", 50_000, minimum=1)
MAX_TEXT_CHARS = env_int("MEMORY_MAX_TEXT_CHARS", 200_000, minimum=1)
SOURCE_TIMEOUT = 30.0


def _fallback_dir() -> Path:
    """A writable directory when the container volume path is unavailable.

    Import previously called ``DATA_DIR.mkdir(...)`` unguarded, so running the
    service on a host without ``/data`` crashed at import time.
    """
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe = DATA_DIR / ".sas-write-probe"
        probe.touch()
        probe.unlink()
        return DATA_DIR
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "sas-memory"
        fallback.mkdir(parents=True, exist_ok=True)
        _LOG.warning("memory dir %s is not writable; using %s", DATA_DIR, fallback)
        return fallback


STORE_DIR = _fallback_dir()
INDEX_PATH = STORE_DIR / "memory.faiss"
DOCUMENTS_PATH = STORE_DIR / "documents.json"

# Serialises read-modify-write on the index; FAISS itself is not thread-safe.
_LOCK = asyncio.Lock()


def embed(text: str) -> np.ndarray:
    """Hashed bag-of-words embedding - deterministic and dependency-free."""
    vector = np.zeros(DIMENSION, dtype="float32")
    for token in text.lower().split():
        digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % DIMENSION
        vector[index] += 1.0 if int.from_bytes(digest[4:], "little") % 2 else -1.0
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


def load_store() -> tuple[faiss.Index, list[dict[str, str]]]:
    try:
        index = faiss.read_index(str(INDEX_PATH)) if INDEX_PATH.exists() else faiss.IndexFlatIP(DIMENSION)
        documents = json.loads(DOCUMENTS_PATH.read_text(encoding="utf-8")) if DOCUMENTS_PATH.exists() else []
    except (OSError, ValueError) as exc:
        # A corrupt index must not stop the service from starting.
        _LOG.error("could not load memory store (%r); starting empty", exc)
        index = faiss.IndexFlatIP(DIMENSION)
        documents = []
    if not isinstance(documents, list):
        documents = []
    return index, documents


INDEX, DOCUMENTS = load_store()
app = FastAPI(title="Super AI Stack - Memory")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10_000)
    limit: int = Field(default=5, ge=1, le=50)
    min_score: float = Field(default=0.0, ge=-1.0, le=1.0)


class AddRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    user_id: str | None = Field(default=None, max_length=255)
    metadata: dict[str, str] = Field(default_factory=dict)


class IngestRequest(BaseModel):
    uri: str = Field(min_length=1, max_length=4_000)
    user_id: str | None = Field(default=None, max_length=255)
    metadata: dict[str, str] = Field(default_factory=dict)


def persist() -> None:
    faiss.write_index(INDEX, str(INDEX_PATH))
    DOCUMENTS_PATH.write_text(json.dumps(DOCUMENTS), encoding="utf-8")


def _trim_to_cap() -> None:
    """Evict oldest documents so the index stays within MAX_DOCUMENTS."""
    overflow = len(DOCUMENTS) - MAX_DOCUMENTS
    if overflow <= 0:
        return
    del DOCUMENTS[:overflow]
    # The FAISS index has no way to drop leading vectors, so re-add the
    # survivors from a fresh index; it must stay in step with DOCUMENTS.
    survivors = np.vstack([embed(doc["text"]) for doc in DOCUMENTS]) if DOCUMENTS else None
    INDEX.reset()
    if survivors is not None:
        INDEX.add(survivors)


@app.get("/health")
async def health() -> dict[str, str | int]:
    return {
        "status": "ok",
        "service": "memory",
        "backend": "faiss",
        "documents": len(DOCUMENTS),
        "dimension": DIMENSION,
        "path": str(STORE_DIR),
    }


@app.post("/add")
async def add(request: AddRequest) -> dict[str, int | str]:
    async with _LOCK:
        DOCUMENTS.append(
            {
                "text": request.text,
                "user_id": request.user_id or "",
                "metadata": json.dumps(request.metadata),
            }
        )
        INDEX.add(embed(request.text).reshape(1, -1))
        _trim_to_cap()
        persist()
        return {"status": "stored", "id": len(DOCUMENTS) - 1, "documents": len(DOCUMENTS)}


@app.post("/ingest")
async def ingest(request: IngestRequest) -> dict[str, int | str]:
    """Load a document from a URL or local path and store it."""
    try:
        if request.uri.startswith(("http://", "https://")):
            with urlopen(request.uri, timeout=SOURCE_TIMEOUT) as response:  # noqa: S310 - scheme checked
                content = response.read(MAX_TEXT_CHARS * 4)
        else:
            content = Path(request.uri).read_bytes()[: MAX_TEXT_CHARS * 4]
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"could not read {request.uri}: {exc}") from exc

    if request.uri.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise HTTPException(status_code=501, detail="Install pypdf to ingest PDF files") from exc
        try:
            extracted = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
        except Exception as exc:  # noqa: BLE001 - pypdf raises several unrelated types
            raise HTTPException(status_code=400, detail=f"could not parse PDF: {exc}") from exc
    else:
        extracted = content.decode("utf-8", errors="replace")

    if not extracted.strip():
        raise HTTPException(status_code=400, detail="document contained no text")
    return await add(
        AddRequest(
            text=extracted[:MAX_TEXT_CHARS],
            user_id=request.user_id,
            metadata=request.metadata | {"source": request.uri},
        )
    )


@app.post("/search")
async def search(request: SearchRequest) -> dict[str, object]:
    """Nearest-neighbour lookup, optionally filtered by a similarity floor."""
    async with _LOCK:
        if not DOCUMENTS:
            return {"results": [], "scores": [], "documents": 0}
        count = min(request.limit, len(DOCUMENTS))
        scores, indices = INDEX.search(embed(request.query).reshape(1, -1), count)
        results: list[str] = []
        matched: list[float] = []
        for score, index in zip(scores[0], indices[0], strict=False):
            if index < 0 or score < request.min_score:
                continue
            results.append(DOCUMENTS[index]["text"])
            matched.append(round(float(score), 4))
        return {"results": results, "scores": matched, "documents": len(DOCUMENTS)}


@app.delete("/documents")
async def clear() -> dict[str, str | int]:
    """Drop every stored document."""
    async with _LOCK:
        DOCUMENTS.clear()
        INDEX.reset()
        persist()
    return {"status": "cleared", "documents": len(DOCUMENTS)}
