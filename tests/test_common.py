"""Tests for the shared infrastructure: env parsing, logging and health probes."""

import asyncio
import logging

import httpx

from common import env
from common.health import probe_many, probe_many_with_latency
from common.logging_utils import RequestIdFilter, request_context


class TestEnv:
    def test_env_str_treats_blank_as_unset(self, monkeypatch):
        monkeypatch.setenv("SAS_T_STR", "   ")
        assert env.env_str("SAS_T_STR", "fallback") == "fallback"
        monkeypatch.setenv("SAS_T_STR", " value ")
        assert env.env_str("SAS_T_STR", "fallback") == "value"

    def test_env_int_falls_back_on_garbage(self, monkeypatch):
        # A typo in .env must not raise inside a request handler.
        monkeypatch.setenv("SAS_T_INT", "fast")
        assert env.env_int("SAS_T_INT", 7) == 7
        monkeypatch.setenv("SAS_T_INT", "42")
        assert env.env_int("SAS_T_INT", 7) == 42

    def test_env_int_clamps_to_bounds(self, monkeypatch):
        monkeypatch.setenv("SAS_T_INT", "9999")
        assert env.env_int("SAS_T_INT", 10, maximum=100) == 100
        monkeypatch.setenv("SAS_T_INT", "-5")
        assert env.env_int("SAS_T_INT", 10, minimum=1) == 1

    def test_env_float_rejects_nan_and_garbage(self, monkeypatch):
        monkeypatch.setenv("SAS_T_FLOAT", "nan")
        assert env.env_float("SAS_T_FLOAT", 1.5) == 1.5
        monkeypatch.setenv("SAS_T_FLOAT", "oops")
        assert env.env_float("SAS_T_FLOAT", 1.5) == 1.5
        monkeypatch.setenv("SAS_T_FLOAT", "2.5")
        assert env.env_float("SAS_T_FLOAT", 1.5) == 2.5

    def test_env_bool_parses_common_spellings(self, monkeypatch):
        for truthy in ("1", "true", "TRUE", "yes", "on"):
            monkeypatch.setenv("SAS_T_BOOL", truthy)
            assert env.env_bool("SAS_T_BOOL") is True
        for falsy in ("0", "false", "no", "off"):
            monkeypatch.setenv("SAS_T_BOOL", falsy)
            assert env.env_bool("SAS_T_BOOL", default=True) is False
        monkeypatch.setenv("SAS_T_BOOL", "maybe")
        assert env.env_bool("SAS_T_BOOL", default=True) is True

    def test_env_list_drops_blanks_and_duplicates(self, monkeypatch):
        monkeypatch.setenv("SAS_T_LIST", " a, b ,, a ,c ")
        assert env.env_list("SAS_T_LIST") == ("a", "b", "c")
        monkeypatch.delenv("SAS_T_LIST")
        assert env.env_list("SAS_T_LIST", ("x",)) == ("x",)


class TestRequestId:
    def test_request_context_binds_and_restores(self):
        from common.logging_utils import current_request_id

        assert current_request_id() == "-"
        with request_context("abc-123") as rid:
            assert rid == "abc-123"
            assert current_request_id() == "abc-123"
        assert current_request_id() == "-"

    def test_request_context_generates_an_id_when_missing(self):
        with request_context() as rid:
            assert len(rid) == 36  # uuid4 string

    def test_filter_stamps_records(self):
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
        with request_context("rid-9"):
            assert RequestIdFilter().filter(record) is True
        assert record.request_id == "rid-9"


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestHealth:
    def test_probe_many_returns_bodies(self):
        def handler(request):
            return httpx.Response(200, json={"status": "ok", "service": request.url.host})

        async def go():
            async with _client(handler) as client:
                return await probe_many({"a": "http://a", "b": "http://b"}, client, timeout=1.0)

        out = asyncio.run(go())
        assert out["a"]["status"] == "ok"
        assert out["a"]["url"] == "http://a"
        assert out["b"]["status"] == "ok"

    def test_unreachable_service_is_marked_not_raised(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        async def go():
            async with _client(handler) as client:
                return await probe_many({"a": "http://a"}, client, timeout=1.0)

        out = asyncio.run(go())
        assert out["a"]["status"] == "unavailable"

    def test_non_json_body_is_unavailable(self):
        async def go():
            async with _client(lambda r: httpx.Response(200, text="not json")) as client:
                return await probe_many({"a": "http://a"}, client, timeout=1.0)

        assert asyncio.run(go())["a"]["status"] == "unavailable"

    def test_latency_is_measured(self):
        async def go():
            async with _client(lambda r: httpx.Response(200, json={"status": "ok"})) as client:
                return await probe_many_with_latency({"a": "http://a"}, client, timeout=1.0)

        assert "latency_ms" in asyncio.run(go())["a"]

    def test_empty_service_map_is_safe(self):
        async def go():
            async with _client(lambda r: httpx.Response(200, json={})) as client:
                return await probe_many({}, client)

        assert asyncio.run(go()) == {}

    def test_probes_run_concurrently(self):
        """A slow peer must not serialise the others behind it."""
        import time

        def handler(request):
            if request.url.host == "slow":
                time.sleep(0.3)
            return httpx.Response(200, json={"status": "ok"})

        async def go():
            async with _client(handler) as client:
                started = time.monotonic()
                await probe_many({"slow": "http://slow", "a": "http://a", "b": "http://b"}, client, timeout=2.0)
                return time.monotonic() - started

        # Sequential would be >= 0.3s; concurrent is a single slow window.
        assert asyncio.run(go()) < 0.6
