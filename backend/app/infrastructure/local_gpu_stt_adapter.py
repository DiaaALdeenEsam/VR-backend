"""STT adapter backed by a locally GPU-loaded openai-whisper "small" model --
the exact same model/size/language the Colab notebook
(colab/leva_stt_tts_notebook.py) runs, now in-process instead of over HTTP.
Backs Settings.stt_backend's "local_gpu"/"auto" values -- see
app/infrastructure/voice_models.py for the startup detection/loading that
constructs one of these once per process (this class never loads or reloads
the model itself).

Deliberately a NEW file, not an in-place upgrade of
legacy/local_whisper_stt_adapter.py: that file is a genuinely different thing
-- faster-whisper "tiny", CPU-only, explicitly documented as a deprecated
rollback path (STT_BACKEND=whisper) kept for a reason unrelated to this one
(a fast, dependency-light CPU fallback with no CUDA/VRAM story at all), not a
GPU-accelerated production-equivalent of the Colab backend. Overwriting its
"tiny"/CPU behavior in place would silently break whatever
STT_BACKEND=whisper rollback currently depends on it. This adapter is a
third, independent STT_BACKEND option instead, sitting alongside it.

Implements SpeechToTextPort with the same effective contract
colab_stt_adapter.py's HTTP calls have against the Colab notebook's /stt
route (see that module and colab/leva_stt_tts_notebook.py CELL 3): empty
input, no-text-produced, and any other failure all become
VoiceServiceUnavailableError -- the single exception type this port's
callers already handle -- collapsing the notebook's 400/422/500 HTTP status
distinction into one exception, exactly like colab_stt_adapter.py already
does for its own HTTP responses. Callers (TranscribeAudioUseCase,
ProcessVoiceChatAsyncUseCase) never know which adapter is behind the port.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any

from app.domain.exceptions import VoiceServiceUnavailableError
from app.domain.repositories import SpeechToTextPort

logger = logging.getLogger(__name__)

# Matches colab/leva_stt_tts_notebook.py CELL 2's WHISPER_MODEL_SIZE/WHISPER_LANGUAGE
# constants exactly -- this adapter exists to replicate the production Colab model
# locally, not to pick a different size/accuracy tradeoff.
WHISPER_MODEL_SIZE = "small"
WHISPER_LANGUAGE = "ar"

_SERVICE_NAME = "local GPU STT (whisper small)"


def _transcribe_sync(model: Any, audio_bytes: bytes, filename: str | None) -> str:
    """Blocking inference call -- always run via asyncio.to_thread, never awaited
    directly. Whisper's transcribe() decodes audio via ffmpeg from a file path, not
    raw bytes -- the same constraint the Colab notebook's /stt handler works around
    (see CELL 3's docstring there), so this writes a temp file the same way."""

    suffix = (Path(filename).suffix if filename else "") or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
        tmp_file.write(audio_bytes)
        tmp_path = tmp_file.name

    try:
        result = model.transcribe(tmp_path, language=WHISPER_LANGUAGE)
        return (result.get("text") or "").strip()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


class LocalGpuSTTAdapter(SpeechToTextPort):
    """STT backed by a locally-loaded openai-whisper "small" model on CUDA.

    `model` is loaded once by app/infrastructure/voice_models.py at process
    startup and passed in here -- this class only runs inference on it.
    """

    def __init__(self, model: Any) -> None:
        self._model = model

    async def transcribe(self, audio_bytes: bytes, filename: str | None = None) -> str:
        if not audio_bytes:
            raise VoiceServiceUnavailableError(_SERVICE_NAME, "uploaded audio file is empty")

        try:
            transcript = await asyncio.to_thread(_transcribe_sync, self._model, audio_bytes, filename)
        except Exception as exc:
            logger.exception("local whisper transcription failed")
            raise VoiceServiceUnavailableError(_SERVICE_NAME, f"transcription failed: {exc}") from exc

        if not transcript:
            raise VoiceServiceUnavailableError(
                _SERVICE_NAME, "transcription produced no text (silent or unrecognizable audio)"
            )

        return transcript
