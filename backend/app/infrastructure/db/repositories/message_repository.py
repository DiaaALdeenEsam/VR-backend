from __future__ import annotations

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domain.entities import Message
from app.domain.repositories import MessageRepository
from app.infrastructure.db.models import MessageModel


def _to_entity(row: MessageModel) -> Message:
    return Message(
        id=row.id,
        session_id=row.session_id,
        role=row.role,
        content=row.content,
        created_at=row.created_at,
        status=row.status,
    )


class SqlMessageRepository(MessageRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, message: Message) -> Message:
        row = MessageModel(
            session_id=message.session_id,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
            status=message.status,
        )
        self._session.add(row)
        await self._session.flush()
        return _to_entity(row)

    async def list_by_session(self, session_id: str) -> list[Message]:
        result = await self._session.exec(
            select(MessageModel).where(MessageModel.session_id == session_id).order_by(MessageModel.id)
        )
        return [_to_entity(row) for row in result.all()]

    async def update_content(self, message_id: int, content: str, status: str) -> Message:
        row = await self._session.get(MessageModel, message_id)
        if row is None:
            raise ValueError(f"message {message_id!r} does not exist")
        row.content = content
        row.status = status
        self._session.add(row)
        await self._session.flush()
        return _to_entity(row)
