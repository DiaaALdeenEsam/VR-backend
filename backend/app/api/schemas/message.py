from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MessageCreate(BaseModel):
    content: str


class MessageRead(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime
