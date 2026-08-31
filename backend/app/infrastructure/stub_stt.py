"""Stub implementation of the SpeechToTextPort port.

Returns a fixed, clearly-labeled placeholder transcript without any ML
dependency at all -- proves the voice flow end to end. Swap this out for a
real implementation (see local_whisper_stt_adapter.py) without touching
TranscribeAudioUseCase, ProcessVoiceChatUseCase, or anything above them.
Also used as the automatic fallback target by LocalWhisperSTTAdapter when the
real model can't be loaded or a transcription attempt fails.
"""

from __future__ import annotations

from app.domain.repositories import SpeechToTextPort


class StubSTTAdapter(SpeechToTextPort):
    async def transcribe(self, audio_bytes: bytes, filename: str | None = None) -> str:
        label = filename or "audio"
        return f"[نص تجريبي] تم استقبال ملف صوتي ({label}, {len(audio_bytes)} بايت) -- هذا تفريغ وهمي وليس تفريغًا حقيقيًا."
