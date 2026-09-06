"""DEPRECATED -- superseded by app/infrastructure/colab_stt_adapter.py (calls
a self-hosted Whisper model over HTTP; see colab/leva_stt_tts_notebook.py).

Kept only for rollback: set STT_BACKEND=whisper to switch app/api/deps.py's
get_speech_to_text_port() back to this adapter. Not otherwise imported by
anything. Do not build new features against this -- ask before deleting it.

Local STT adapter backed by faster-whisper (a CTranslate2 reimplementation
of OpenAI's Whisper).

Runs fully offline on CPU once the "tiny" model weights are cached (~75MB,
downloaded from Hugging Face on first use) -- unlike the TTS side, no network
call happens per transcription request. Decoding arbitrary audio containers
(webm/mp3/wav/...) works without a system ffmpeg install: faster-whisper uses
the `av` package, which bundles its own ffmpeg libraries in the wheel.

Language is forced to Arabic (language="ar") rather than auto-detected,
matching this project's Arabic-only domain, and skipping the extra
language-ID pass for speed.

Failure handling: nothing here ever raises out to the use case. If the model
can't be loaded (no network on first run, out of memory, faster-whisper/deps
missing, ...) or a transcription attempt fails, this falls back to
StubSTTAdapter instead.
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading

from app.domain.repositories import SpeechToTextPort
from app.infrastructure.stub_stt import StubSTTAdapter

logger = logging.getLogger(__name__)

MODEL_SIZE = "tiny"
LANGUAGE = "ar"
BEAM_SIZE = 1

_load_lock = threading.Lock()
_model = None
_load_failed = False


def _ensure_loaded() -> bool:
    """Loads the faster-whisper model once per process. Returns True iff ready to use."""

    global _model, _load_failed

    if _model is not None:
        return True
    if _load_failed:
        return False

    with _load_lock:
        if _model is not None:
            return True
        if _load_failed:
            return False

        try:
            from faster_whisper import WhisperModel

            _model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
        except Exception:
            logger.exception(
                "Failed to load faster-whisper model %r -- falling back to the stub STT adapter.",
                MODEL_SIZE,
            )
            _load_failed = True
            return False

        return True


def _transcribe_sync(audio_bytes: bytes) -> str | None:
    """Blocking inference call -- always run this via asyncio.to_thread, never awaited directly."""

    if not _ensure_loaded():
        return None

    assert _model is not None
    try:
        segments, _info = _model.transcribe(io.BytesIO(audio_bytes), language=LANGUAGE, beam_size=BEAM_SIZE)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return text or None
    except Exception:
        logger.exception("faster-whisper transcription failed.")
        return None


class LocalWhisperSTTAdapter(SpeechToTextPort):
    """STT backed by a local faster-whisper "tiny" model (CPU, Arabic-forced)."""

    def __init__(self) -> None:
        self._fallback = StubSTTAdapter()

    async def transcribe(self, audio_bytes: bytes, filename: str | None = None) -> str:
        text = await asyncio.to_thread(_transcribe_sync, audio_bytes)
        if text:
            return text
        return await self._fallback.transcribe(audio_bytes, filename)
