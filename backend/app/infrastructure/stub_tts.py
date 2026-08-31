"""Stub implementation of the TextToSpeechPort port.

Returns a short, valid, silent WAV clip -- real playable audio, just silent --
built with the stdlib `wave` module, no ML/network dependency at all. Swap
this out for a real implementation (see local_tts_adapter.py) without
touching ProcessVoiceChatUseCase or anything above it. Also used as the
automatic fallback target by LocalTTSAdapter when gTTS is unreachable or
synthesis otherwise fails.
"""

from __future__ import annotations

import io
import wave

from app.domain.repositories import TextToSpeechPort

SAMPLE_RATE = 16_000
SILENCE_DURATION_SECONDS = 0.3


def _silent_wav_bytes(duration_seconds: float = SILENCE_DURATION_SECONDS) -> bytes:
    frame_count = int(duration_seconds * SAMPLE_RATE)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # 16-bit PCM
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(b"\x00\x00" * frame_count)
    return buffer.getvalue()


class StubTTSAdapter(TextToSpeechPort):
    async def synthesize(self, text: str) -> bytes:
        return _silent_wav_bytes()
