from __future__ import annotations

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domain.entities import Scenario
from app.domain.repositories import ScenarioRepository
from app.infrastructure.db.models import ScenarioModel


def _to_entity(row: ScenarioModel) -> Scenario:
    assert row.id is not None
    return Scenario(id=row.id, name=row.name, case_text=row.case_text, gold_standard=row.gold_standard)


class SqlScenarioRepository(ScenarioRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[Scenario]:
        result = await self._session.exec(select(ScenarioModel).order_by(ScenarioModel.id))
        return [_to_entity(row) for row in result.all()]

    async def get(self, scenario_id: int) -> Scenario | None:
        row = await self._session.get(ScenarioModel, scenario_id)
        return _to_entity(row) if row is not None else None
