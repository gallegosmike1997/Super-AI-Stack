import asyncio
import logging
import os
import tempfile

from fastapi import HTTPException

from common.env import env_str
from common.logging_utils import configure_logging
from common.schemas import ExpertResponse, LLMRequest
from common.utils import create_expert_app
from speech.audio_utils import AudioSourceError, read_audio_source, suffix_for

configure_logging("speech")
_LOG = logging.getLogger("speech")

MODEL_NAME = env_str("MODEL_NAME", env_str("WHISPER_MODEL", "small"))

# Loading Whisper weights takes seconds to minutes; the previous code did it on
# every single request, so each transcription paid a full model load.
_MODEL_CACHE: dict[tuple[str, str, str], object] = {}
_CACHE_LOCK = asyncio.Lock()


def _load_whisper() -> object | None:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    return WhisperModel


async def get_model() -> object | None:
    """Return the cached WhisperModel, loading it at most once per config."""
    key = (
        env_str("WHISPER_MODEL", MODEL_NAME),
        env_str("WHISPER_DEVICE", "cpu"),
        env_str("WHISPER_COMPUTE_TYPE", "int8"),
    )
    async with _CACHE_LOCK:
        if key in _MODEL_CACHE:
            return _MODEL_CACHE[key]
        factory = await asyncio.to_thread(_load_whisper)
        if factory is None:
            _LOG.info("faster-whisper is not installed; speech stays unavailable")
            _MODEL_CACHE[key] = None
            return None
        _LOG.info("loading whisper model %s on %s/%s", *key)
        model = await asyncio.to_thread(factory, key[0], device=key[1], compute_type=key[2])
        _MODEL_CACHE[key] = model
        return model


def audio_attachment(request: LLMRequest):
    """First attachment that looks like audio, or ``None``."""
    for attachment in request.attachments:
        if attachment.kind == "audio" or (attachment.content_type and attachment.content_type.startswith("audio/")):
            return attachment
    return None


async def transcribe_audio(path: str) -> tuple[str, str | None]:
    """Transcribe a local file, off the event loop (model inference is blocking)."""
    model = await get_model()
    if model is None:
        return "", "faster-whisper is not installed"

    def run() -> tuple[str, str | None]:
        segments, info = model.transcribe(path, beam_size=5)
        return " ".join(segment.text.strip() for segment in segments).strip(), info.language

    return await asyncio.to_thread(run)


async def respond(request: LLMRequest) -> tuple[str, list[dict]]:
    """Transcribe an audio attachment into text."""
    attachment = audio_attachment(request)
    if attachment is None:
        return "No audio attachment supplied.", []
    try:
        payload = await asyncio.to_thread(read_audio_source, attachment.uri)
    except AudioSourceError as exc:
        _LOG.warning("audio attachment rejected: %s", exc)
        return f"Could not read audio attachment: {exc}", [{"status": "unavailable"}]

    suffix = suffix_for(attachment.content_type)
    path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
            temporary.write(payload)
            path = temporary.name
        text, language = await transcribe_audio(path)
    finally:
        if path and os.path.exists(path):
            os.unlink(path)
    if not text:
        return "Speech transcription is unavailable.", [{"status": "unavailable", "language": language}]
    return text, [{"type": "transcript", "language": language}]


app = create_expert_app("speech", f"whisper-{MODEL_NAME}", respond)


@app.post("/transcribe", response_model=ExpertResponse)
async def transcribe(request: LLMRequest) -> ExpertResponse:
    if audio_attachment(request) is None:
        raise HTTPException(status_code=400, detail="An audio attachment is required")
    text, artifacts = await respond(request)
    return ExpertResponse(text=text, expert="speech", model=f"whisper-{MODEL_NAME}", artifacts=artifacts)
