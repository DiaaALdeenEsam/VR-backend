from __future__ import annotations

from pydantic import BaseModel

from app.api.schemas.message import MessageRead


class TranscribeResponse(BaseModel):
    text: str


class ChatVoiceResponse(BaseModel):
    """reply_audio_base64/reply_audio_content_type are null when `reply` is
    still pending (production -- see ProcessVoiceChatAsyncUseCase): there is
    no audio to send yet, since there is no finished reply text yet. The real
    reply text and its audio are pushed later over the session's WS
    connection (see app/infrastructure/ws_hub.py) once generation finishes.
    With the synchronous "stub" test double, `reply` is already "complete"
    and both audio fields are always populated, exactly as before this field
    existed."""

    transcribed_text: str
    reply: MessageRead
    reply_audio_base64: str | None = None
    reply_audio_content_type: str | None = None
