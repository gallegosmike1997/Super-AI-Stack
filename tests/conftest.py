"""Shared test fixtures.

Every test runs against an isolated session database and a cleared gateway
metric/event state, so ordering can never leak between tests.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_session_store(tmp_path, monkeypatch):
    """Point the session store at a per-test database."""
    from common import session_store

    monkeypatch.setattr(session_store, "DB_PATH", tmp_path / "sessions.db")
    session_store._SCHEMA_READY = False
    session_store._ACTIVE_PATH = None
    session_store._PROBE_CACHE.clear()
    yield
    session_store._SCHEMA_READY = False
    session_store._ACTIVE_PATH = None
    session_store._PROBE_CACHE.clear()


@pytest.fixture(autouse=True)
def clean_gateway_state():
    """Reset gateway counters, rate-limit windows and the event feed."""
    from gateway import main as gateway

    gateway.REQUESTS.clear()
    gateway.EVENTS.clear()
    gateway.LATENCIES.clear()
    for key in gateway.METRICS:
        gateway.METRICS[key] = 0
    gateway.INFLIGHT = 0
    yield
    gateway.REQUESTS.clear()
    gateway.EVENTS.clear()
