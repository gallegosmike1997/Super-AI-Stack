"""Audio attachment loading.

Reads are size-capped so one oversized file cannot exhaust memory, and each
source is validated before use.
"""

import base64
import binascii
import logging
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname, urlopen

from common.env import env_float, env_int

_LOG = logging.getLogger("speech.audio")

# Whisper needs the whole clip in memory; 25 MB of audio is already minutes.
MAX_AUDIO_BYTES = env_int("SPEECH_MAX_AUDIO_BYTES", 25 * 1024 * 1024, minimum=1024)
FETCH_TIMEOUT = env_float("SPEECH_FETCH_TIMEOUT", 30.0, minimum=1.0)

# Content types Whisper can decode, mapped to a file suffix.
SUFFIX_BY_TYPE = {
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/flac": ".flac",
    "audio/ogg": ".ogg",
    "audio/webm": ".webm",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
}


class AudioSourceError(ValueError):
    """The attachment could not be read safely."""


def suffix_for(content_type: str | None) -> str:
    """Best-effort file suffix; decoders sniff content when the suffix is wrong."""
    if not content_type:
        return ".audio"
    return SUFFIX_BY_TYPE.get(content_type.split(";")[0].strip().lower(), ".audio")


def read_audio_source(uri: str) -> bytes:
    """Read audio bytes from a data URI, an HTTP(S) URL, or a local path."""
    if not uri:
        raise AudioSourceError("empty audio attachment uri")
    if uri.startswith("data:"):
        _, _, encoded = uri.partition(",")
        if not encoded:
            raise AudioSourceError("malformed data URI")
        # Reject before decoding: base64 of a huge blob must not be materialised.
        if len(encoded) > MAX_AUDIO_BYTES * 2:
            raise AudioSourceError(f"audio attachment exceeds {MAX_AUDIO_BYTES} bytes")
        try:
            payload = base64.b64decode(encoded, validate=False)
        except (binascii.Error, ValueError) as exc:
            raise AudioSourceError(f"invalid base64 audio attachment: {exc}") from exc
        if not payload:
            raise AudioSourceError("audio attachment decoded to zero bytes")
        return payload
    if uri.startswith(("http://", "https://")):
        with urlopen(uri, timeout=FETCH_TIMEOUT) as response:  # noqa: S310 - scheme checked above
            return response.read(MAX_AUDIO_BYTES)
    if uri.startswith("file://"):
        # file:///C:/x.wav on Windows, file:///tmp/x.wav elsewhere.
        uri = url2pathname(urlparse(uri).path)
    try:
        path = Path(uri)
        if path.stat().st_size > MAX_AUDIO_BYTES:
            raise AudioSourceError(f"audio file exceeds {MAX_AUDIO_BYTES} bytes")
        return path.read_bytes()
    except OSError as exc:
        raise AudioSourceError(f"could not read audio file: {exc}") from exc
