import hashlib
import io
import json
import os
from pathlib import Path
from urllib.request import urlopen

import faiss
import numpy as np
from fastapi import FastAPI
from pydantic import BaseModel, Field


DIMENSION = 256
DATA_DIR = Path(os.getenv("MEMORY_DATA_DIR", "/data"))
INDEX_PATH = DATA_DIR / "memory.faiss"
DOCUMENTS_PATH = DATA_DIR / "documents.json"


def embed(text: str) -> np.ndarray:
	vector = np.zeros(DIMENSION, dtype="float32")
	for token in text.lower().split():
		digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
		index = int.from_bytes(digest[:4], "little") % DIMENSION
		vector[index] += 1.0 if int.from_bytes(digest[4:], "little") % 2 else -1.0
	norm = np.linalg.norm(vector)
	return vector / norm if norm else vector


def load_store() -> tuple[faiss.Index, list[dict[str, str]]]:
	DATA_DIR.mkdir(parents=True, exist_ok=True)
	index = faiss.read_index(str(INDEX_PATH)) if INDEX_PATH.exists() else faiss.IndexFlatIP(DIMENSION)
	documents = json.loads(DOCUMENTS_PATH.read_text()) if DOCUMENTS_PATH.exists() else []
	return index, documents


INDEX, DOCUMENTS = load_store()
app = FastAPI(title="Super AI Stack - Memory")


class SearchRequest(BaseModel):
	query: str
	limit: int = Field(default=5, ge=1, le=50)


class AddRequest(BaseModel):
	text: str = Field(min_length=1)
	user_id: str | None = None
	metadata: dict[str, str] = Field(default_factory=dict)


class IngestRequest(BaseModel):
	uri: str
	user_id: str | None = None
	metadata: dict[str, str] = Field(default_factory=dict)


def persist() -> None:
	faiss.write_index(INDEX, str(INDEX_PATH))
	DOCUMENTS_PATH.write_text(json.dumps(DOCUMENTS, indent=2))


@app.get("/health")
async def health() -> dict[str, str | int]:
	return {"status": "ok", "service": "memory", "backend": "faiss", "documents": len(DOCUMENTS)}


@app.post("/add")
async def add(request: AddRequest) -> dict[str, int | str]:
	DOCUMENTS.append({"text": request.text, "user_id": request.user_id or "", "metadata": json.dumps(request.metadata)})
	INDEX.add(embed(request.text).reshape(1, -1))
	persist()
	return {"status": "stored", "id": len(DOCUMENTS) - 1}


@app.post("/ingest")
async def ingest(request: IngestRequest) -> dict[str, int | str]:
	if request.uri.startswith(("http://", "https://")):
		with urlopen(request.uri, timeout=30) as response:
			content = response.read()
	else:
		content = Path(request.uri).read_bytes()
	if request.uri.lower().endswith(".pdf"):
		try:
			from pypdf import PdfReader
			extracted = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
		except ImportError as exc:
			raise ValueError("Install pypdf to ingest PDF files") from exc
	else:
		extracted = content.decode("utf-8", errors="replace")
	return await add(AddRequest(text=extracted, user_id=request.user_id, metadata=request.metadata | {"source": request.uri}))


@app.post("/search")
async def search(request: SearchRequest) -> dict[str, list[str]]:
	if not DOCUMENTS:
		return {"results": []}
	_, indices = INDEX.search(embed(request.query).reshape(1, -1), min(request.limit, len(DOCUMENTS)))
	results = [DOCUMENTS[index]["text"] for index in indices[0] if index >= 0]
	return {"results": results}
