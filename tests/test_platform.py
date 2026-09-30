import asyncio

from starlette.requests import Request

from agent.main import ToolRequest, execute
from common.session_store import append, history
from router.main import heuristic_route


def make_request(path: str = "/chat", headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "path": path,
        "headers": [(key.encode(), value.encode()) for key, value in (headers or {}).items()],
    }
    return Request(scope)


def test_router_classifies_core_tasks() -> None:
    assert heuristic_route("write a Python script")["task_type"] == "code"
    assert heuristic_route("create a simple blue square icon")["task_type"] == "image_gen"
    assert heuristic_route("what stepdown do we use", {"use_memory": True})["needs_memory"] is True


def test_session_history_is_chronological(tmp_path, monkeypatch) -> None:
    from common import session_store

    monkeypatch.setattr(session_store, "DB_PATH", tmp_path / "sessions.db")
    append("test", "user", "hello")
    append("test", "assistant", "world")
    assert history("test") == ["user: hello", "assistant: world"]


def test_agent_denies_unallowlisted_tools(tmp_path, monkeypatch) -> None:
    from agent import main

    monkeypatch.setattr(main, "ALLOWED_TOOLS", set())
    monkeypatch.setattr(main, "AUDIT_PATH", tmp_path / "audit.jsonl")
    result = asyncio.run(execute(ToolRequest(tool="shell", approved=True)))
    assert result.artifacts[0]["status"] == "denied"


def test_gateway_policy_rejects_invalid_key(monkeypatch) -> None:
    from gateway import main

    monkeypatch.setattr(main, "API_KEY", "secret")
    called = False

    async def next_handler(_request):
        nonlocal called
        called = True

    result = asyncio.run(main.policy(make_request(headers={}), next_handler))
    assert result.status_code == 401
    assert not called


def test_router_task_types_map_to_real_experts() -> None:
    from common.constants import SERVICE_URLS
    from gateway.main import TASK_TO_EXPERT

    assert set(TASK_TO_EXPERT) == {"chat", "code", "reasoning", "vision", "speech", "image_gen", "agent"}
    # Every router task type must resolve to an expert that actually exists.
    assert set(TASK_TO_EXPERT.values()) <= set(SERVICE_URLS)
    # The mismatch that silently disabled code streaming.
    assert TASK_TO_EXPERT["code"] == "coding"


def test_sse_frame_keeps_multiline_token_on_one_data_line() -> None:
    import json

    from gateway.main import sse

    frame = sse("delta", {"text": "line one\nline two"})
    assert frame.startswith("event: delta\n")
    assert frame.endswith("\n\n")
    data_lines = [line for line in frame.split("\n") if line.startswith("data:")]
    assert len(data_lines) == 1
    assert json.loads(data_lines[0][len("data: ") :])["text"] == "line one\nline two"


def test_offline_notice_is_actionable_not_prompt_echo(monkeypatch) -> None:
    from common.utils import offline_notice

    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    notice = offline_notice("general", "llama3.1")
    assert "general" in notice and "llama3.1" in notice
    assert "MODEL_BASE_URL" in notice

    monkeypatch.setenv("MODEL_BASE_URL", "http://127.0.0.1:11434")
    configured = offline_notice("coding", "qwen2.5-coder:7b")
    assert "http://127.0.0.1:11434" in configured
    assert "MODEL_TIMEOUT" in configured


def test_extract_content_handles_openai_shapes() -> None:
    from common.model_client import _extract_content

    assert _extract_content({"choices": [{"message": {"content": "plain"}}]}) == "plain"
    assert _extract_content({"choices": [{"message": {"content": [{"text": "a"}, {"text": "b"}]}}]}) == "ab"
    assert _extract_content({"choices": [{"message": {}}]}) is None
    assert _extract_content({}) is None
    assert _extract_content({"choices": []}) is None


def test_post_chat_retries_transient_transport_errors(monkeypatch) -> None:
    import asyncio

    import httpx

    from common import model_client

    calls = {"n": 0}

    class FakeResponse:
        status_code = 200
        text = ""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "recovered"}}]}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> bool:
            return False

        async def post(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("ollama restarting")
            return FakeResponse()

    monkeypatch.setattr(model_client.httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("MODEL_RETRIES", "3")
    monkeypatch.setenv("MODEL_RETRY_DELAY", "0")

    assert asyncio.run(model_client._post_chat("http://x", {"model": "m"}, "m")) == "recovered"
    assert calls["n"] == 2


def test_post_chat_gives_up_after_max_attempts(monkeypatch) -> None:
    import asyncio

    import httpx

    from common import model_client

    calls = {"n": 0}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> bool:
            return False

        async def post(self, *args, **kwargs):
            calls["n"] += 1
            raise httpx.RemoteProtocolError("server disconnected")

    monkeypatch.setattr(model_client.httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("MODEL_RETRIES", "2")
    monkeypatch.setenv("MODEL_RETRY_DELAY", "0")

    assert asyncio.run(model_client._post_chat("http://x", {"model": "m"}, "m")) is None
    assert calls["n"] == 2, "should not retry forever"


def test_post_chat_does_not_retry_bad_model(monkeypatch) -> None:
    import asyncio

    import httpx

    from common import model_client

    calls = {"n": 0}

    class FakeResponse:
        status_code = 404
        text = '{"error":"model not found"}'

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError("404", request=httpx.Request("POST", "http://x"), response=self)

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> bool:
            return False

        async def post(self, *args, **kwargs):
            calls["n"] += 1
            return FakeResponse()

    monkeypatch.setattr(model_client.httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("MODEL_RETRIES", "3")
    monkeypatch.setenv("MODEL_RETRY_DELAY", "0")

    assert asyncio.run(model_client._post_chat("http://x", {"model": "nope"}, "nope")) is None
    assert calls["n"] == 1, "a 404 model tag should fail fast, not retry"


def test_expert_health_reports_backend_mode(monkeypatch) -> None:
    from common.utils import model_backend

    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    assert model_backend() == "offline"
    monkeypatch.setenv("MODEL_BASE_URL", "http://127.0.0.1:11434")
    assert model_backend() == "configured"


def test_gateway_policy_rate_limits_client(monkeypatch) -> None:
    from gateway import main

    monkeypatch.setattr(main, "API_KEY", "")
    monkeypatch.setattr(main, "RATE_LIMIT", 1)
    main.REQUESTS.clear()

    async def next_handler(_request):
        return None

    asyncio.run(main.policy(make_request(), next_handler))
    result = asyncio.run(main.policy(make_request(), next_handler))
    assert result.status_code == 429


def test_session_store_falls_back_when_path_unwritable(monkeypatch) -> None:
    """The /data default is not writable outside Docker; degrade, do not crash."""
    import uuid
    from pathlib import Path

    from common import session_store

    monkeypatch.setattr(session_store, "DB_PATH", Path("/proc/sas/unwritable/sessions.db"))
    # The fallback store lives in a temp dir, so use a unique id to stay idempotent.
    sid = f"fallback-{uuid.uuid4()}"
    session_store.append(sid, "user", "hi")
    assert session_store.history(sid) == ["user: hi"]


def test_session_store_prefers_the_configured_path(tmp_path, monkeypatch) -> None:
    """The temp fallback must only kick in when the configured path is unusable."""
    from common import session_store

    monkeypatch.setattr(session_store, "DB_PATH", tmp_path / "sessions.db")
    session_store.append("configured", "user", "x")
    assert (tmp_path / "sessions.db").exists()
    # export_session renders the stored turns as JSON text.
    assert "user: x" in session_store.export_session("configured")


def test_complete_sends_a_token_cap(monkeypatch) -> None:
    """Local CPU inference needs a cap, or generation runs to the context limit."""
    import asyncio

    from common import model_client

    seen: dict = {}

    async def fake_post(base_url, payload, model):
        seen.update(payload)
        return "ok"

    monkeypatch.setenv("MODEL_BASE_URL", "http://x")
    monkeypatch.setenv("MODEL_MAX_TOKENS", "256")
    monkeypatch.setattr(model_client, "_post_chat", fake_post)

    assert asyncio.run(model_client.complete("sys", "hi", "m")) == "ok"
    assert seen["max_tokens"] == 256


def test_complete_accepts_a_per_call_cap(monkeypatch) -> None:
    import asyncio

    from common import model_client

    seen: dict = {}

    async def fake_post(base_url, payload, model):
        seen.update(payload)
        return "ok"

    monkeypatch.setenv("MODEL_BASE_URL", "http://x")
    monkeypatch.setenv("MODEL_MAX_TOKENS", "1024")
    monkeypatch.setattr(model_client, "_post_chat", fake_post)

    # The router needs only a small JSON blob, so it overrides the global cap.
    assert asyncio.run(model_client.complete("sys", "hi", "m", max_tokens=160)) == "ok"
    assert seen["max_tokens"] == 160


def test_offline_notice_is_actionable_not_a_prompt_echo() -> None:
    from common.utils import offline_notice

    notice = offline_notice("general", "llama3.1")
    assert "general" in notice
    assert "MODEL_BASE_URL" in notice
    # Must not leak the internal prompt template to the user.
    assert "Final answer:" not in notice
