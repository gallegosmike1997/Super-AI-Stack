"""Tests for the session store, router, and shared schemas."""

import asyncio
import sqlite3
from pathlib import Path

import pytest

from common.schemas import LLMRequest
from common.session_store import append, clear, connection, history, list_sessions


class TestSessionStore:
    def test_round_trip_is_chronological(self):
        append("s1", "user", "hello")
        append("s1", "assistant", "hi")
        assert history("s1") == ["user: hello", "assistant: hi"]

    def test_history_respects_limit_and_order(self):
        for i in range(5):
            append("s2", "user", f"m{i}")
        # Most recent turns, still oldest-first inside the window.
        assert history("s2", limit=2) == ["user: m3", "user: m4"]

    def test_empty_turns_are_not_stored(self):
        append("s3", "user", "")
        append("", "user", "orphan")
        assert history("s3") == []
        assert history("") == []

    def test_clear_removes_only_the_target_session(self):
        append("keep", "user", "a")
        append("drop", "user", "b")
        assert clear("drop") == 1
        assert history("drop") == []
        assert history("keep") == ["user: a"]
        assert clear("missing") == 0

    def test_list_sessions_reports_turn_counts(self):
        append("a", "user", "1")
        append("a", "user", "2")
        append("b", "user", "1")
        listed = {item["session_id"]: item for item in list_sessions()}
        assert listed["a"]["turns"] == 2
        assert listed["b"]["turns"] == 1

    def test_connection_closes_the_handle(self):
        """The old ``with sqlite3.connect(...)`` pattern leaked a handle per call."""
        with connection() as db:
            db.execute("SELECT 1")
        # A closed connection raises ProgrammingError on use.
        with pytest.raises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")

    def test_schema_is_created_once_and_reused(self, monkeypatch):
        """CREATE TABLE/INDEX ran on every connection before this was cached."""
        from common import session_store

        executed: list[str] = []
        real_connect = session_store.sqlite3.connect

        class TrackingConnection:
            """Proxy that records SQL; sqlite3.Connection.execute is read-only."""

            def __init__(self, db):
                self._db = db

            def execute(self, sql, *args, **kwargs):
                executed.append(sql)
                return self._db.execute(sql, *args, **kwargs)

            def commit(self):
                return self._db.commit()

            def rollback(self):
                return self._db.rollback()

            def close(self):
                return self._db.close()

        monkeypatch.setattr(session_store.sqlite3, "connect", lambda *a, **k: TrackingConnection(real_connect(*a, **k)))
        session_store._SCHEMA_READY = False
        append("schema", "user", "1")
        append("schema", "user", "2")
        history("schema")

        creates = [sql for sql in executed if sql.strip().upper().startswith("CREATE")]
        # Table + index on the first connection, nothing on the later ones.
        assert len(creates) == 2

    def test_schema_flag_resets_when_the_path_changes(self, monkeypatch, tmp_path):
        from common import session_store

        append("path-a", "user", "1")
        assert session_store._SCHEMA_READY is True
        # A different database needs its own schema pass.
        monkeypatch.setattr(session_store, "DB_PATH", tmp_path / "other.db")
        session_store.resolved_path()
        assert session_store._SCHEMA_READY is False

    def test_falls_back_to_temp_dir_when_path_unwritable(self, monkeypatch):
        from common import session_store

        monkeypatch.setattr(session_store, "DB_PATH", Path("/proc/sas/unwritable/sessions.db"))
        session_store._SCHEMA_READY = False
        resolved = session_store.resolved_path()
        assert resolved.parent.exists()
        assert resolved.name == "sessions.db"


class TestRequestSchema:
    def test_oversized_message_is_rejected(self):
        from pydantic import ValidationError

        from common.schemas import MAX_MESSAGE_CHARS

        with pytest.raises(ValidationError):
            LLMRequest(message="x" * (MAX_MESSAGE_CHARS + 1))

    def test_too_many_attachments_is_rejected(self):
        from pydantic import ValidationError

        from common.schemas import MAX_ATTACHMENTS

        attachments = [{"kind": "image", "uri": "data:image/png;base64,AA"}] * (MAX_ATTACHMENTS + 1)
        with pytest.raises(ValidationError):
            LLMRequest(message="hi", attachments=attachments)

    def test_empty_message_is_allowed_for_attachment_only_requests(self):
        request = LLMRequest(
            message="",
            attachments=[{"kind": "image", "uri": "data:image/png;base64,AA", "content_type": "image/png"}],
        )
        assert request.attachments[0].kind == "image"


class TestRouting:
    def test_keyword_routing_covers_the_core_tasks(self):
        from router.main import heuristic_route

        cases = {
            "write a Python script": "code",
            "debug this function": "code",
            "see this screenshot": "vision",
            "transcribe this audio": "speech",
            "prove that sqrt2 is irrational": "reasoning",
            "draw a cat": "image_gen",
            "create a simple blue square icon": "image_gen",
            "make me a logo for my app": "image_gen",
            "what is the weather": "chat",
        }
        for message, expected in cases.items():
            assert heuristic_route(message)["task_type"] == expected, message

    def test_keywords_match_on_word_boundaries(self):
        """Substring matching used to send these to the wrong expert."""
        from router.main import heuristic_route

        for message in (
            "I saw my therapist today",  # contains "api"
            "give me an explanation of gravity",  # contains "plan"
            "the capital of France is Paris",  # contains "api"
            "summarise this article",  # must not become image_gen
        ):
            assert heuristic_route(message)["task_type"] == "chat", message

    def test_explicit_task_type_wins(self):
        from router.main import heuristic_route

        assert heuristic_route("draw a cat", {"task_type": "code"})["task_type"] == "code"

    def test_use_memory_flag_is_honoured(self):
        from router.main import heuristic_route

        assert heuristic_route("what stepdown", {"use_memory": True})["needs_memory"] is True
        assert heuristic_route("what stepdown")["needs_memory"] is False

    def test_extract_json_handles_fences_and_prose(self):
        from router.main import extract_json

        assert extract_json('```json\n{"task_type": "code"}\n```') == {"task_type": "code"}
        assert extract_json('sure! {"task_type": "vision"} done') == {"task_type": "vision"}
        assert extract_json("no json here") is None
        assert extract_json("[1, 2]") is None

    def test_router_falls_back_when_the_model_backend_is_down(self, monkeypatch):
        """Routing must never be the reason a user gets no answer."""
        import httpx

        from router import main as router

        monkeypatch.setenv("ROUTER_MODEL_BASE_URL", "http://127.0.0.1:9")
        monkeypatch.setattr(router, "PHI3_URL", "")

        async def failing_post(*args, **kwargs):
            raise httpx.ConnectError("backend down")

        monkeypatch.setattr(router.httpx.AsyncClient, "post", failing_post)
        decision = asyncio.run(router.call_phi3_router("write a python script"))
        assert decision["task_type"] == "code"
        assert decision["notes"] == "local fallback"

    def test_memory_lookup_failure_degrades_to_no_context(self, monkeypatch):
        import httpx

        from router import main as router

        async def failing_post(*args, **kwargs):
            raise httpx.ConnectError("memory down")

        monkeypatch.setattr(router.httpx.AsyncClient, "post", failing_post)
        assert asyncio.run(router.call_memory("anything")) == []

    def test_attachment_type_drives_routing(self):
        from router.main import decide_route

        image = LLMRequest(message="what is this", attachments=[{"kind": "image", "uri": "data:image/png;base64,AA"}])
        task, decision = asyncio.run(decide_route(image))
        assert task == "vision"
        assert decision.notes == "attachment-driven routing"

        audio = LLMRequest(message="what is this", attachments=[{"kind": "audio", "uri": "data:audio/wav;base64,AA"}])
        assert asyncio.run(decide_route(audio))[0] == "speech"

    def test_explicit_task_type_is_not_overridden_by_attachments(self):
        from router.main import decide_route

        req = LLMRequest(
            message="describe",
            task_type="chat",
            attachments=[{"kind": "image", "uri": "data:image/png;base64,AA"}],
        )
        assert asyncio.run(decide_route(req))[0] == "chat"


class TestMemory:
    def _fresh(self, monkeypatch):
        import faiss

        from memory import main as memory

        monkeypatch.setattr(memory, "DOCUMENTS", [])
        monkeypatch.setattr(memory, "INDEX", faiss.IndexFlatIP(memory.DIMENSION))
        monkeypatch.setattr(memory, "persist", lambda: None)
        return memory

    def test_add_and_search_round_trip(self, monkeypatch):
        memory = self._fresh(monkeypatch)
        asyncio.run(memory.add(memory.AddRequest(text="the quick brown fox jumps")))
        out = asyncio.run(memory.search(memory.SearchRequest(query="fox", limit=3)))
        assert out["results"] == ["the quick brown fox jumps"]
        assert len(out["scores"]) == 1

    def test_min_score_filters_weak_matches(self, monkeypatch):
        memory = self._fresh(monkeypatch)
        asyncio.run(memory.add(memory.AddRequest(text="alpha beta gamma")))
        asyncio.run(memory.add(memory.AddRequest(text="completely different topic")))
        loose = asyncio.run(memory.search(memory.SearchRequest(query="alpha", limit=5)))
        strict = asyncio.run(memory.search(memory.SearchRequest(query="alpha", limit=5, min_score=0.99)))
        assert len(loose["results"]) == 2
        assert strict["results"] == []

    def test_empty_store_returns_no_results(self, monkeypatch):
        memory = self._fresh(monkeypatch)
        assert asyncio.run(memory.search(memory.SearchRequest(query="anything")))["results"] == []

    def test_document_cap_evicts_oldest(self, monkeypatch):
        memory = self._fresh(monkeypatch)
        monkeypatch.setattr(memory, "MAX_DOCUMENTS", 3)
        for i in range(5):
            asyncio.run(memory.add(memory.AddRequest(text=f"document number {i}")))
        assert len(memory.DOCUMENTS) == 3
        assert memory.DOCUMENTS[0]["text"] == "document number 2"
        # The rebuilt index must stay in step with the document list.
        assert memory.INDEX.ntotal == 3

    def test_clear_empties_the_store(self, monkeypatch):
        memory = self._fresh(monkeypatch)
        asyncio.run(memory.add(memory.AddRequest(text="something")))
        asyncio.run(memory.clear())
        assert memory.DOCUMENTS == []
        assert memory.INDEX.ntotal == 0

    def test_ingest_rejects_an_unreadable_source(self, monkeypatch):
        from fastapi import HTTPException

        memory = self._fresh(monkeypatch)
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(memory.ingest(memory.IngestRequest(uri="/definitely/not/here.txt")))
        assert excinfo.value.status_code == 400

    def test_ingest_reads_a_local_text_file(self, tmp_path, monkeypatch):
        memory = self._fresh(monkeypatch)
        source = tmp_path / "doc.txt"
        source.write_text("ingested knowledge about widgets", encoding="utf-8")
        result = asyncio.run(memory.ingest(memory.IngestRequest(uri=str(source))))
        assert result["status"] == "stored"
        assert "widgets" in memory.DOCUMENTS[0]["text"]

    def test_oversized_document_is_rejected(self):
        """An unbounded ingest would let one request exhaust the store."""
        from pydantic import ValidationError

        from memory.main import MAX_TEXT_CHARS, AddRequest

        with pytest.raises(ValidationError):
            AddRequest(text="x" * (MAX_TEXT_CHARS + 1))

    def test_embed_is_deterministic_and_normalised(self, monkeypatch):
        memory = self._fresh(monkeypatch)
        first = memory.embed("hello world")
        assert bool((first == memory.embed("hello world")).all())
        assert abs(float((first**2).sum()) - 1.0) < 1e-5

    def test_health_reports_the_document_count(self, monkeypatch):
        memory = self._fresh(monkeypatch)
        asyncio.run(memory.add(memory.AddRequest(text="one")))
        body = asyncio.run(memory.health())
        assert body["documents"] == 1
        assert body["backend"] == "faiss"


class TestAgent:
    @pytest.fixture(autouse=True)
    def _audit_to_tmp(self, tmp_path, monkeypatch):
        """Keep every audit write inside the test's temp directory."""
        from agent import main as agent

        monkeypatch.setattr(agent, "AUDIT_PATH", tmp_path / "audit.jsonl")
        monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))

    def test_denies_tools_that_are_not_allowlisted(self, monkeypatch):
        from agent import main as agent

        monkeypatch.setattr(agent, "ALLOWED_TOOLS", set())
        result = asyncio.run(agent.execute(agent.ToolRequest(tool="shell", approved=True)))
        assert result.artifacts[0]["status"] == "denied"

    def test_allowlisted_tool_still_requires_approval(self, monkeypatch):
        from agent import main as agent

        monkeypatch.setattr(agent, "ALLOWED_TOOLS", {"echo"})
        result = asyncio.run(agent.execute(agent.ToolRequest(tool="echo", arguments={"a": 1})))
        assert result.artifacts[0]["status"] == "approval_required"

    def test_approved_allowlisted_tool_is_queued(self, monkeypatch):
        from agent import main as agent

        monkeypatch.setattr(agent, "ALLOWED_TOOLS", {"echo"})
        result = asyncio.run(agent.execute(agent.ToolRequest(tool="echo", approved=True)))
        assert result.artifacts[0]["status"] == "queued"

    def test_every_decision_is_audited(self, tmp_path, monkeypatch):
        import json

        from agent import main as agent

        audit_file = agent.AUDIT_PATH
        monkeypatch.setattr(agent, "ALLOWED_TOOLS", {"echo"})
        monkeypatch.setattr(agent, "AUDIT_PATH", audit_file)
        asyncio.run(agent.execute(agent.ToolRequest(tool="echo")))
        asyncio.run(agent.execute(agent.ToolRequest(tool="nope", approved=True)))
        statuses = [json.loads(line)["status"] for line in audit_file.read_text().splitlines()]
        assert statuses == ["approval_required", "denied"]

    def test_audit_write_failure_does_not_break_the_decision(self, monkeypatch):
        """An unwritable audit sink must not turn a decision into a 500."""
        from agent import main as agent

        monkeypatch.setattr(agent, "ALLOWED_TOOLS", set())
        real_open = agent.Path.open

        def failing_open(self, *args, **kwargs):
            if self.name.endswith(".jsonl"):
                raise OSError("disk full")
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(agent.Path, "open", failing_open)
        result = asyncio.run(agent.execute(agent.ToolRequest(tool="shell")))
        assert result.artifacts[0]["status"] == "denied"

    def test_audit_path_falls_back_when_data_is_unwritable(self, tmp_path, monkeypatch):
        """Writing to /data used to raise on a host install, failing every call."""
        import tempfile

        from agent import main as agent

        # A path whose parent is an existing *file* can never be created.
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("", encoding="utf-8")
        monkeypatch.setenv("AUDIT_LOG_PATH", str(blocker / "sub" / "audit.jsonl"))

        resolved = agent._audit_path()
        assert resolved.parent.exists()
        assert resolved.parent == Path(tempfile.gettempdir()) / "sas-agent"

    def test_tools_endpoint_flags_the_allowlist(self, monkeypatch):
        from agent import main as agent

        monkeypatch.setattr(agent, "ALLOWED_TOOLS", {"echo"})
        tools = {item["name"]: item for item in asyncio.run(agent.tools())["tools"]}
        assert tools["echo"]["allowlisted"] is True
        assert tools["run_script"]["allowlisted"] is False


class TestSpeechAudio:
    def test_rejects_an_empty_data_uri(self):
        from speech.audio_utils import AudioSourceError, read_audio_source

        with pytest.raises(AudioSourceError):
            read_audio_source("data:audio/wav;base64,")

    def test_rejects_a_payload_that_decodes_to_nothing(self):
        from speech.audio_utils import AudioSourceError, read_audio_source

        with pytest.raises(AudioSourceError):
            read_audio_source("data:audio/wav;base64,####")

    def test_reads_a_local_file(self, tmp_path):
        from speech.audio_utils import read_audio_source

        source = tmp_path / "clip.wav"
        source.write_bytes(b"RIFFfake")
        assert read_audio_source(str(source)) == b"RIFFfake"

    def test_reports_a_missing_file_clearly(self, tmp_path):
        from speech.audio_utils import AudioSourceError, read_audio_source

        with pytest.raises(AudioSourceError):
            read_audio_source(str(tmp_path / "missing.wav"))

    def test_oversized_file_is_refused(self, tmp_path, monkeypatch):
        from speech import audio_utils

        monkeypatch.setattr(audio_utils, "MAX_AUDIO_BYTES", 4)
        source = tmp_path / "big.wav"
        source.write_bytes(b"x" * 64)
        with pytest.raises(audio_utils.AudioSourceError):
            audio_utils.read_audio_source(str(source))

    def test_suffix_follows_the_content_type(self):
        from speech.audio_utils import suffix_for

        assert suffix_for("audio/mpeg") == ".mp3"
        assert suffix_for("audio/wav; codecs=1") == ".wav"
        assert suffix_for(None) == ".audio"

    def test_respond_without_audio_is_graceful(self):
        from speech.main import respond

        text, artifacts = asyncio.run(respond(LLMRequest(message="hello")))
        assert "No audio attachment" in text
        assert artifacts == []

    def test_respond_reports_a_bad_attachment(self, tmp_path):
        from speech.main import respond

        req = LLMRequest(message="", attachments=[{"kind": "audio", "uri": str(tmp_path / "missing.wav")}])
        text, artifacts = asyncio.run(respond(req))
        assert "Could not read" in text
        assert artifacts[0]["status"] == "unavailable"

    def test_whisper_model_is_loaded_at_most_once(self, monkeypatch):
        """Re-loading Whisper weights on every request was a multi-second cost."""
        from speech import main as speech

        speech._MODEL_CACHE.clear()
        loads = {"n": 0}

        class FakeModel:
            def transcribe(self, path, beam_size=5):
                segment = type("S", (), {"text": " hello there "})()
                info = type("I", (), {"language": "en"})()
                return [segment], info

        def factory(name, device=None, compute_type=None):
            loads["n"] += 1
            return FakeModel()

        monkeypatch.setattr(speech, "_load_whisper", lambda: factory)
        monkeypatch.setenv("WHISPER_MODEL", "tiny")

        async def go():
            await speech.get_model()
            await speech.get_model()

        asyncio.run(go())
        assert loads["n"] == 1
        speech._MODEL_CACHE.clear()

    def test_missing_whisper_degrades_instead_of_raising(self, monkeypatch):
        from speech import main as speech

        speech._MODEL_CACHE.clear()
        monkeypatch.setattr(speech, "_load_whisper", lambda: None)
        assert asyncio.run(speech.get_model()) is None
        text, language = asyncio.run(speech.transcribe_audio("ignored.wav"))
        assert text == ""
        assert "not installed" in language
        speech._MODEL_CACHE.clear()

    def test_transcribe_requires_an_audio_attachment(self):
        from fastapi import HTTPException

        from speech.main import transcribe

        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(transcribe(LLMRequest(message="no audio here")))
        assert excinfo.value.status_code == 400


class TestModelManager:
    def test_big_models_are_detected_by_prefix(self, monkeypatch):
        from model_manager import main as manager

        monkeypatch.setattr(manager, "BIG_MODELS", ("llama3.1", "deepseek-r1"))
        assert manager.is_big("llama3.1:8b") is True
        assert manager.is_big("phi3:mini") is False

    def test_health_reports_the_service(self):
        from model_manager.main import health

        assert asyncio.run(health())["service"] == "model_manager"

    def test_a_failed_load_is_reported_not_swallowed(self, monkeypatch):
        """A discarded response previously reported a successful swap."""
        import httpx
        from fastapi import HTTPException

        from model_manager import main as manager

        async def loaded():
            return ["phi3:mini"]

        async def failing_post(*args, **kwargs):
            raise httpx.ConnectError("ollama down")

        monkeypatch.setattr(manager, "loaded_models", loaded)
        monkeypatch.setattr(manager.httpx.AsyncClient, "post", failing_post)
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(manager.ensure(manager.EnsureRequest(model="llama3.1")))
        assert excinfo.value.status_code == 502

    def test_unreachable_backend_reports_503(self, monkeypatch):
        import httpx
        from fastapi import HTTPException

        from model_manager import main as manager

        async def failing_get(*args, **kwargs):
            raise httpx.ConnectError("down")

        monkeypatch.setattr(manager.httpx.AsyncClient, "get", failing_get)
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(manager.status())
        assert excinfo.value.status_code == 503

    def test_resident_model_needs_no_swap(self, monkeypatch):
        from model_manager import main as manager

        async def loaded():
            return ["phi3:mini"]

        monkeypatch.setattr(manager, "loaded_models", loaded)
        monkeypatch.setattr(manager, "BIG_MODELS", ("llama3.1",))
        result = asyncio.run(manager.ensure(manager.EnsureRequest(model="phi3:mini")))
        assert result["status"] == "resident"
