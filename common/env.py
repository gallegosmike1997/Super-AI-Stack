"""Typed, forgiving environment-variable helpers.

Configuration is environment-driven, so a typo such as ``MODEL_TIMEOUT=fast``
must not become an opaque 500 from inside a request handler. Every helper
logs the rejected value once and falls back to the documented default, which
keeps a service runnable with a partially-broken ``.env``.
"""

import logging
import os

_LOG = logging.getLogger("sas.env")

TRUTHY = frozenset({"1", "true", "t", "yes", "y", "on"})
FALSY = frozenset({"0", "false", "f", "no", "n", "off"})


def env_str(name: str, default: str = "") -> str:
    """Read a string, treating an empty/whitespace value as unset."""
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def env_int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    """Read an int, clamped to ``[minimum, maximum]`` when supplied."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(float(raw.strip()))
    except ValueError:
        _LOG.warning("%s=%r is not a number; using %s", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        _LOG.warning("%s=%s below minimum %s; clamping", name, value, minimum)
        value = minimum
    if maximum is not None and value > maximum:
        _LOG.warning("%s=%s above maximum %s; clamping", name, value, maximum)
        value = maximum
    return value


def env_float(name: str, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    """Read a float, clamped to ``[minimum, maximum]`` when supplied."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        _LOG.warning("%s=%r is not a number; using %s", name, raw, default)
        return default
    if value != value:  # NaN compares unequal to itself.
        _LOG.warning("%s=%r is not a number; using %s", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        _LOG.warning("%s=%s below minimum %s; clamping", name, value, minimum)
        value = minimum
    if maximum is not None and value > maximum:
        _LOG.warning("%s=%s above maximum %s; clamping", name, value, maximum)
        value = maximum
    return value


def env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean flag (``1/true/yes/on`` and ``0/false/no/off``)."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    lowered = raw.strip().lower()
    if lowered in TRUTHY:
        return True
    if lowered in FALSY:
        return False
    _LOG.warning("%s=%r is not a boolean; using %s", name, raw, default)
    return default


def env_list(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Read a comma-separated list, dropping blanks and duplicates."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    seen: dict[str, None] = {}
    for item in raw.split(","):
        cleaned = item.strip()
        if cleaned:
            seen.setdefault(cleaned, None)
    return tuple(seen)
