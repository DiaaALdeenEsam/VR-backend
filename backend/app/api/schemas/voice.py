from __future__ import annotations

from pydantic import BaseModel

from app.api.schemas.message import MessageRead


class TranscribeResponse(BaseModel):
    text: str


class ChatVoiceResponse(BaseModel):
    transcribed_text: str
    reply: MessageRead
    reply_audio_base64: str
    reply_audio_content_type: str
