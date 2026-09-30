"""Shared health probing.

The gateway, the diagnostics endpoint, and the activity feed all need the same
thing: "is each service up, and what does it say?". Probing them one at a time
made ``/ready`` cost the sum of every timeout (eight services x 2 s = 16 s when
the stack is down), so everything here is concurrent and bounded.
"""

import asyncio
import logging
from collections.abc import Mapping

import httpx

_LOG = logging.getLogger("sas.health")

UNAVAILABLE: dict[str, object] = {"status": "unavailable"}


def _merge(base_url: str, name: str, body: dict[str, object] | None) -> dict[str, object]:
    """Attach the service name so a bare body is still self-describing."""
    return {**(body or UNAVAILABLE), "service": name, "url": base_url}


async def probe(url: str, client: httpx.AsyncClient, timeout: float = 2.0) -> dict[str, object]:
    """GET ``url/health`` and return the parsed body, or an unavailable marker."""
    try:
        response = await client.get(f"{url}/health", timeout=timeout)
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
        _LOG.debug("health probe failed for %s: %r", url, exc)
        return dict(UNAVAILABLE)
    except ValueError:
        _LOG.warning("health probe for %s returned non-JSON", url)
        return dict(UNAVAILABLE)
    if not isinstance(body, dict):
        return dict(UNAVAILABLE)
    return body


async def probe_many(
    services: Mapping[str, str],
    client: httpx.AsyncClient,
    timeout: float = 2.0,
) -> dict[str, dict[str, object]]:
    """Probe every service concurrently; one slow peer cannot delay the rest."""
    if not services:
        return {}

    async def one(name: str, url: str) -> tuple[str, dict[str, object]]:
        return name, _merge(url, name, await probe(url, client, timeout))

    results = await asyncio.gather(*(one(name, url) for name, url in services.items()))
    return dict(results)


async def probe_many_with_latency(
    services: Mapping[str, str],
    client: httpx.AsyncClient,
    timeout: float = 2.0,
) -> dict[str, dict[str, object]]:
    """``probe_many`` plus a measured ``latency_ms`` for the diagnostics table."""
    if not services:
        return {}

    async def one(name: str, url: str) -> tuple[str, dict[str, object]]:
        loop = asyncio.get_running_loop()
        started = loop.time()
        body = _merge(url, name, await probe(url, client, timeout))
        body["latency_ms"] = int((loop.time() - started) * 1000)
        return name, body

    results = await asyncio.gather(*(one(name, url) for name, url in services.items()))
    return dict(results)
