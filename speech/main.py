import os
import tempfile

from fastapi import HTTPException

from common.schemas import ExpertResponse, LLMRequest
from common.utils import create_expert_app
from speech.audio_utils import read_audio_source


MODEL_NAME = os.getenv("MODEL_NAME", os.getenv("WHISPER_MODEL", "small"))


def transcribe_file(path: str) -> tuple[str, str | None]:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return "", "faster-whisper is not installed"

    model = WhisperModel(
        os.getenv("WHISPER_MODEL", MODEL_NAME),
        device=os.getenv("WHISPER_DEVICE", "cpu"),
        compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
    )
    segments, info = model.transcribe(path, beam_size=5)
    return " ".join(segment.text.strip() for segment in segments).strip(), info.language


def audio_uri(request: LLMRequest) -> str | None:
    for attachment in request.attachments:
        if attachment.kind == "audio" or (
            attachment.content_type and attachment.content_type.startswith("audio/")
        ):
            return attachment.uri
    return None


def respond(request: LLMRequest) -> tuple[str, list[dict]]:
    uri = audio_uri(request)
    if not uri:
        return "No audio attachment supplied.", []
    path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as temporary:
            temporary.write(read_audio_source(uri))
            path = temporary.name
        text, language = transcribe_file(path)
    finally:
        if path and os.path.exists(path):
            os.unlink(path)
    if not text:
        return "Speech transcription is unavailable.", [{"status": "unavailable", "language": language}]
    return text, [{"type": "transcript", "language": language}]


app = create_expert_app("speech", f"whisper-{MODEL_NAME}", respond)


@app.post("/transcribe", response_model=ExpertResponse)
async def transcribe(request: LLMRequest) -> ExpertResponse:
    if not audio_uri(request):
        raise HTTPException(status_code=400, detail="An audio attachment is required")
    text, artifacts = respond(request)
    return ExpertResponse(text=text, expert="speech", model=f"whisper-{MODEL_NAME}", artifacts=artifacts)

