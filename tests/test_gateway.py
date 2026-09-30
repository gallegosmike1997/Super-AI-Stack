"""Tests for the gateway: auth policy, metrics, health aggregation, streaming."""

import asyncio

import httpx
import pytest
from starlette.requests import Request

from common.schemas import LLMRequest


def make_request(path: str = "/chat", headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "path": path,
        "headers": [(key.encode(), value.encode()) for key, value in (headers or {}).items()],
    }
    return Request(scope)


async def _ok(_request):
    return None


class TestAuthPolicy:
    def test_rejects_a_missing_or_wrong_key(self, monkeypatch):
        from gateway import main

        monkeypatch.setattr(main, "API_KEY", "secret")
        result = asyncio.run(main.policy(make_request(headers={}), _ok))
        assert result.status_code == 401

    def test_accepts_the_correct_key(self, monkeypatch):
        from gateway import main

        monkeypatch.setattr(main, "API_KEY", "secret")
        assert asyncio.run(main.policy(make_request(headers={"x-api-key": "secret"}), _ok)) is None

    def test_401_carries_a_request_id_and_counts(self, monkeypatch):
        from gateway import main

        monkeypatch.setattr(main, "API_KEY", "secret")
        result = asyncio.run(main.policy(make_request(headers={}), _ok))
        assert result.headers["X-Request-ID"]
        assert main.METRICS["unauthorized_total"] == 1

    def test_health_and_console_stay_public(self, monkeypatch):
        from gateway import main

        monkeypatch.setattr(main, "API_KEY", "secret")
        for path in ("/health", "/ready", "/metrics", "/", "/assets/logo.png", "/api/stack"):
            assert asyncio.run(main.policy(make_request(path=path, headers={}), _ok)) is None, path

    def test_no_key_configured_means_open(self, monkeypatch):
        from gateway import main

        monkeypatch.setattr(main, "API_KEY", "")
        assert asyncio.run(main.policy(make_request(headers={}), _ok)) is None

    def test_near_miss_key_is_rejected(self, monkeypatch):
        from gateway import main

        monkeypatch.setattr(main, "API_KEY", "secret")
        assert asyncio.run(main.policy(make_request(headers={"x-api-key": "secreT"}), _ok)).status_code == 401


class TestRateLimit:
    def test_limits_repeat_requests_from_one_client(self, monkeypatch):
        from gateway import main

        monkeypatch.setattr(main, "API_KEY", "")
        monkeypatch.setattr(main, "RATE_LIMIT", 1)
        main.REQUESTS.clear()

        asyncio.run(main.policy(make_request(), _ok))
        result = asyncio.run(main.policy(make_request(), _ok))
        assert result.status_code == 429
        assert main.METRICS["rate_limited_total"] == 1

    def test_429_suggests_retry_after(self, monkeypatch):
        from gateway import main

        monkeypatch.setattr(main, "API_KEY", "")
        monkeypatch.setattr(main, "RATE_LIMIT", 1)
        main.REQUESTS.clear()
        asyncio.run(main.policy(make_request(), _ok))
        result = asyncio.run(main.policy(make_request(), _ok))
        assert result.headers["Retry-After"] == "60"

    def test_zero_disables_limiting(self, monkeypatch):
        from gateway import main

        monkeypatch.setattr(main, "API_KEY", "")
        monkeypatch.setattr(main, "RATE_LIMIT", 0)
        main.REQUESTS.clear()
        for _ in range(5):
            assert asyncio.run(main.policy(make_request(), _ok)) is None
        assert main.METRICS["rate_limited_total"] == 0

    def test_stale_clients_are_pruned(self):
        """The client map previously grew without bound."""
        from collections import deque

        from gateway import main

        main.REQUESTS.clear()
        main.REQUESTS["old"] = deque([0.0])
        main._prune_clients(10_000.0)
        assert "old" not in main.REQUESTS

    def test_client_map_is_capped(self, monkeypatch):
        from collections import deque

        from gateway import main

        monkeypatch.setattr(main, "MAX_TRACKED_CLIENTS", 100)
        main.REQUESTS.clear()
        for i in range(150):
            main.REQUESTS[f"c{i}"] = deque([float(i)])
        main._prune_clients(10_000.0)
        assert len(main.REQUESTS) <= 100


class TestMetrics:
    def test_exposition_includes_help_and_type(self):
        """Without HELP/TYPE the series are unlabelled in Grafana."""
        from gateway import main

        body = asyncio.run(main.metrics()).body.decode()
        assert "# HELP super_ai_requests_total" in body
        assert "# TYPE super_ai_requests_total counter" in body
        assert "# TYPE super_ai_inflight gauge" in body

    def test_every_counter_is_exported(self, monkeypatch):
        from gateway import main

        monkeypatch.setattr(main, "METRICS", dict(main.METRICS))
        body = asyncio.run(main.metrics()).body.decode()
        for name in main.METRICS:
            assert f"super_ai_{name} " in body

    def test_stats_report_percentiles(self):
        from gateway import main

        main.LATENCIES.clear()
        main.LATENCIES.extend([0.01, 0.02, 0.5])
        stats = main.gateway_stats()
        assert stats["latency_p50_ms"] is not None
        assert stats["latency_p95_ms"] >= stats["latency_p50_ms"]

    def test_success_rate_is_none_before_any_request(self):
        from gateway.main import gateway_stats

        assert gateway_stats()["success_rate"] is None


class TestSystemMetrics:
    def test_returns_real_values_on_this_platform(self):
        """It read /proc only, so every panel showed zeros on Windows."""
        from gateway import main

        info = asyncio.run(main.read_system())
        assert set(info) == {"cpu_pct", "load_1", "mem_total_gb", "mem_used_gb", "cores"}
        assert info["cores"], "cpu count should always be available"
        assert info["mem_total_gb"], "total memory should be readable"

    def test_unreadable_metrics_degrade_to_none(self, monkeypatch):
        from gateway import main

        def boom():
            raise OSError("no /proc here")

        monkeypatch.setattr(main, "_windows_system", boom)
        monkeypatch.setattr(main, "_proc_system", boom)
        info = asyncio.run(main.read_system())
        assert info["cpu_pct"] is None
        assert info["mem_total_gb"] is None


class TestHealthEndpoints:
    def _patch_client(self, monkeypatch, handler):
        from gateway import main

        real_client = httpx.AsyncClient

        def factory(*args, **kwargs):
            # main.httpx IS the global httpx module, so capture the real class
            # first or the factory would call itself.
            kwargs.pop("transport", None)
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(main.httpx, "AsyncClient", factory)

    def test_stack_reports_every_expert(self, monkeypatch):
        from gateway import main

        def handler(request):
            return httpx.Response(200, json={"status": "ok", "service": "x"})

        self._patch_client(monkeypatch, handler)
        body = asyncio.run(main.stack())
        assert set(body["services"]) == set(main.EXPERT_PORTS)
        assert body["services"]["general"]["status"] == "ok"
        assert body["catalog"], "the MoE catalog should be published"

    def test_stack_marks_a_down_expert(self, monkeypatch):
        from gateway import main

        def handler(request):
            if str(request.url).endswith(":8002/health"):
                raise httpx.ConnectError("down")
            return httpx.Response(200, json={"status": "ok"})

        self._patch_client(monkeypatch, handler)
        body = asyncio.run(main.stack())
        assert body["services"]["general"]["status"] == "unavailable"
        assert body["services"]["coding"]["status"] == "ok"

    def test_ready_is_503_when_the_router_is_down(self, monkeypatch):
        from fastapi import HTTPException

        from gateway import main

        def handler(request):
            raise httpx.ConnectError("down")

        self._patch_client(monkeypatch, handler)
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(main.ready())
        assert excinfo.value.status_code == 503

    def test_ready_passes_when_the_router_is_up(self, monkeypatch):
        from gateway import main

        def handler(request):
            return httpx.Response(200, json={"status": "ok"})

        self._patch_client(monkeypatch, handler)
        body = asyncio.run(main.ready())
        assert body["status"] == "ready"
        assert body["dependencies"]["router"] == "ok"

    def test_activity_flags_an_unreachable_backend(self, monkeypatch):
        from gateway import main

        def handler(request):
            if request.url.host == "127.0.0.1":
                # The inference backend is down, the experts are up.
                raise httpx.ConnectError("down")
            return httpx.Response(200, json={"status": "ok"})

        self._patch_client(monkeypatch, handler)
        body = asyncio.run(main.activity())
        assert any("Inference backend unreachable" in a["text"] for a in body["alerts"])
        assert not any("Experts down" in a["text"] for a in body["alerts"])

    def test_activity_flags_down_experts(self, monkeypatch):
        from gateway import main

        def handler(request):
            if str(request.url).endswith(":8002/health"):
                raise httpx.ConnectError("down")
            return httpx.Response(200, json={"status": "ok", "models": []})

        self._patch_client(monkeypatch, handler)
        body = asyncio.run(main.activity())
        assert any("Experts down" in a["text"] and "general" in a["text"] for a in body["alerts"])


class TestStreaming:
    def _body_text(self, response):
        async def collect():
            chunks = [chunk async for chunk in response.body_iterator]
            return "".join(c if isinstance(c, str) else c.decode() for c in chunks)

        return asyncio.run(collect())

    def test_stream_falls_back_to_the_buffered_path(self, monkeypatch):
        from gateway import main

        async def no_target(_req):
            return None

        async def fake_chat(_req):
            return type(
                "R",
                (),
                {
                    "task_type": "chat",
                    "expert": "general",
                    "model": "llama3.1",
                    "response": "buffered",
                    "request_id": "r1",
                },
            )()

        monkeypatch.setattr(main, "resolve_stream_target", no_target)
        monkeypatch.setattr(main, "chat", fake_chat)
        text = self._body_text(asyncio.run(main.chat_stream(LLMRequest(message="hi"))))
        assert "buffered" in text
        assert text.rstrip().endswith("data: [DONE]")

    def test_stream_reports_a_terminal_error_frame(self, monkeypatch):
        """A failed fallback used to truncate the stream with no explanation."""
        from fastapi import HTTPException

        from gateway import main

        async def no_target(_req):
            return None

        async def failing_chat(_req):
            raise HTTPException(status_code=503, detail="Router unavailable")

        monkeypatch.setattr(main, "resolve_stream_target", no_target)
        monkeypatch.setattr(main, "chat", failing_chat)
        text = self._body_text(asyncio.run(main.chat_stream(LLMRequest(message="hi"))))
        assert "event: error" in text
        assert "Router unavailable" in text
        assert "data: [DONE]" in text
        assert main.METRICS["errors_total"] == 1

    def test_sse_frames_a_multiline_token_on_one_data_line(self):
        from gateway.main import sse

        frame = sse("delta", {"text": "one\ntwo"})
        assert [line for line in frame.split("\n") if line.startswith("data:")] == ['data: {"text": "one\\ntwo"}']


class TestSessions:
    def test_sessions_endpoint_lists_recent(self):
        from common.session_store import append
        from gateway import main

        append("listed", "user", "hello")
        body = asyncio.run(main.sessions())
        assert any(item["session_id"] == "listed" for item in body["sessions"])

    def test_delete_session_clears_history(self):
        from common.session_store import append, history
        from gateway import main

        append("doomed", "user", "bye")
        result = asyncio.run(main.delete_session("doomed"))
        assert result["deleted"] == 1
        assert history("doomed") == []
