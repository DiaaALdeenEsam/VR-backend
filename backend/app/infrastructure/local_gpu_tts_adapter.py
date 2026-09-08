"""TTS adapter backed by a locally GPU-loaded leva-tts model -- the exact same
model the Colab notebook (colab/leva_stt_tts_notebook.py) runs, now
in-process instead of over HTTP. Backs Settings.tts_backend's
"local_gpu"/"auto" values -- see app/infrastructure/voice_models.py for the
startup detection/loading that constructs one of these once per process
(this class never loads or reloads the model itself).

Returns WAV bytes encoded via soundfile (PCM_16) -- identical encoding to
the Colab notebook's /tts handler (CELL 3), so app/api/deps.py's
get_tts_content_type() stays a fixed "audio/wav" with no changes needed
here.

Implements TextToSpeechPort with the same effective contract
colab_tts_adapter.py's HTTP calls have against the Colab notebook's /tts
route: empty text and any synthesis failure both become
VoiceServiceUnavailableError -- the single exception type this port's
callers already handle, collapsing the notebook's 400/500 HTTP status
distinction into one exception, exactly like colab_tts_adapter.py already
does. Speaker selection is a construction-time choice (DEFAULT_SPEAKER), not
a per-call parameter -- TextToSpeechPort.synthesize(text) takes no speaker
argument (see app/domain/repositories.py), the same limitation
colab_tts_adapter.py already has (it never sends a "speaker" field in its
/tts request body either, so the Colab server's own default -- also
"Amina" -- is what every existing call already gets).
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

from app.domain.exceptions import VoiceServiceUnavailableError
from app.domain.repositories import TextToSpeechPort

logger = logging.getLogger(__name__)

# Matches colab/leva_stt_tts_notebook.py CELL 2's LEVA_DEFAULT_SPEAKER exactly.
DEFAULT_SPEAKER = "Amina"

_SERVICE_NAME = "local GPU TTS (leva-tts)"


def _synthesize_sync(engine: Any, text: str, speaker: str) -> bytes:
    """Blocking inference call -- always run via asyncio.to_thread, never awaited
    directly. leva-tts returns (numpy.ndarray float32, sample_rate); encoded to a
    WAV container with soundfile before returning, matching the Colab notebook's
    /tts handler (CELL 3) byte-for-byte in approach.

    `soundfile` is imported here, not at module level -- same lazy-import
    convention every heavy/optional dependency in this codebase's local ML
    adapters follows (see e.g. legacy/local_whisper_stt_adapter.py's
    `from faster_whisper import WhisperModel` inside _ensure_loaded()), so
    that importing this module -- which app/infrastructure/voice_models.py
    and app/api/deps.py do unconditionally at startup -- never requires
    soundfile to be installed on a machine that isn't using this backend.
    """

    import soundfile as sf

    wav, sample_rate = engine.synthesize(text, speaker=speaker)
    buffer = io.BytesIO()
    sf.write(buffer, wav, sample_rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


class LocalGpuTTSAdapter(TextToSpeechPort):
    """TTS backed by a locally-loaded leva-tts LevaTTS engine on CUDA.

    `engine`/`speakers` are loaded/read once by
    app/infrastructure/voice_models.py at process startup and passed in here
    -- this class only runs inference on them.
    """

    def __init__(self, engine: Any, speakers: list[str], speaker: str = DEFAULT_SPEAKER) -> None:
        self._engine = engine
        # Falls back to DEFAULT_SPEAKER if an invalid speaker were ever passed in --
        # defensive only; voice_models.py always passes DEFAULT_SPEAKER today, already
        # validated against `speakers` (SPEAKERS from leva_tts) at load time.
        self._speaker = speaker if speaker in speakers else DEFAULT_SPEAKER

    async def synthesize(self, text: str) -> bytes:
        text = text.strip()
        if not text:
            raise VoiceServiceUnavailableError(_SERVICE_NAME, "'text' must not be empty")

        try:
            audio_bytes = await asyncio.to_thread(_synthesize_sync, self._engine, text, self._speaker)
        except Exception as exc:
            logger.exception("local leva-tts synthesis failed")
            raise VoiceServiceUnavailableError(_SERVICE_NAME, f"synthesis failed: {exc}") from exc

        if not audio_bytes:
            raise VoiceServiceUnavailableError(_SERVICE_NAME, "returned an empty audio response")

        return audio_bytes
