from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MessageCreate(BaseModel):
    content: str


class MessageRead(BaseModel):
    """`status` is "complete" immediately for the synchronous "stub"
    PatientReplyGenerator test double -- unchanged behavior from before this
    field existed. In production, an assistant reply starts "pending" and is
    filled in asynchronously by the RAG-API-backed generator (6-48s observed
    per call) -- see PostMessageAsyncUseCase. A client that doesn't care about
    async generation can ignore this field entirely."""

    id: int
    role: str
    content: str
    created_at: datetime
    status: str = "complete"
