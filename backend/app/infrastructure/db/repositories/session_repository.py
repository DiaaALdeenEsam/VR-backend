from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.domain.entities import Session
from app.domain.repositories import SessionRepository
from app.infrastructure.db.models import SessionModel


def _to_entity(row: SessionModel) -> Session:
    return Session(id=row.id, scenario_id=row.scenario_id, created_at=row.created_at)


class SqlSessionRepository(SessionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, session: Session) -> Session:
        row = SessionModel(id=session.id, scenario_id=session.scenario_id, created_at=session.created_at)
        self._session.add(row)
        await self._session.flush()
        return _to_entity(row)

    async def get(self, session_id: str) -> Session | None:
        row = await self._session.get(SessionModel, session_id)
        return _to_entity(row) if row is not None else None
