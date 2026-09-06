"""DEPRECATED -- superseded by app/infrastructure/colab_tts_adapter.py (calls
a self-hosted leva-tts model over HTTP; see colab/leva_stt_tts_notebook.py).

Kept only for rollback: set TTS_BACKEND=gtts to switch app/api/deps.py's
get_text_to_speech_port() back to this adapter. Not otherwise imported by
anything. Do not build new features against this -- ask before deleting it.

Local TTS adapter backed by gTTS (a tiny client for Google Translate's TTS endpoint).

Honest caveat despite the "Local" name (matching this project's adapter-naming
convention, e.g. LocalWhisperSTTAdapter): gTTS makes a network call per
request -- it is not an offline engine. It's used here because it's a tiny,
pure-Python dependency with no native/system requirements, unlike piper-tts
(a genuinely offline engine considered for this slot, but skipped for now --
it needs espeak-ng, a system dependency that doesn't install via pip alone
and would add real cross-platform setup risk). Swap this for piper-tts,
Coqui, or another real offline engine later without touching
ProcessVoiceChatUseCase or anything above it -- same one-file-per-adapter
pattern as the STT side.

gTTS itself only emits MP3. That's re-encoded to WAV here via miniaudio (a
small C-extension MP3 decoder with prebuilt wheels, no system ffmpeg
required) so every TextToSpeechPort implementation in this codebase returns
the same container format -- see get_tts_content_type() in app/api/deps.py,
which no longer needs to branch on which adapter is active as a result.

Failure handling mirrors local_whisper_stt_adapter.py: nothing here ever
raises out to the use case. If gTTS is unreachable (no network), synthesis
otherwise fails, or the MP3-to-WAV re-encode fails, this falls back to
StubTTSAdapter's silent placeholder WAV.
"""

from __future__ import annotations

import asyncio
import io
import logging
import wave

import miniaudio

from app.domain.repositories import TextToSpeechPort
from app.infrastructure.stub_tts import StubTTSAdapter

logger = logging.getLogger(__name__)

LANGUAGE = "ar"
SAMPLE_RATE = 24_000  # typical speech rate; matches gTTS's own output closely enough to avoid audible artifacts


def _mp3_to_wav_bytes(mp3_bytes: bytes) -> bytes:
    decoded = miniaudio.decode(
        mp3_bytes,
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=1,
        sample_rate=SAMPLE_RATE,
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(decoded.nchannels)
        wav_file.setsampwidth(2)  # SIGNED16 == 16-bit PCM
        wav_file.setframerate(decoded.sample_rate)
        wav_file.writeframes(decoded.samples.tobytes())
    return buffer.getvalue()


def _synthesize_sync(text: str) -> bytes | None:
    """Blocking network call + decode -- always run this via asyncio.to_thread, never awaited directly."""

    try:
        from gtts import gTTS

        buffer = io.BytesIO()
        gTTS(text=text, lang=LANGUAGE).write_to_fp(buffer)
        mp3_bytes = buffer.getvalue()
        if not mp3_bytes:
            return None
        return _mp3_to_wav_bytes(mp3_bytes)
    except Exception:
        logger.exception("gTTS synthesis (or MP3->WAV re-encode) failed -- falling back to the stub TTS adapter.")
        return None


class LocalTTSAdapter(TextToSpeechPort):
    """TTS backed by gTTS, re-encoded to WAV bytes (see module docstring)."""

    def __init__(self) -> None:
        self._fallback = StubTTSAdapter()

    async def synthesize(self, text: str) -> bytes:
        audio = await asyncio.to_thread(_synthesize_sync, text)
        if audio:
            return audio
        return await self._fallback.synthesize(text)
