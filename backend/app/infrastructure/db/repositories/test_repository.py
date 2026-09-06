from __future__ import annotations

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domain.entities import Test
from app.domain.repositories import TestRepository
from app.infrastructure.db.models import ScenarioTestResultModel, TestModel


def _to_entity(row: TestModel) -> Test:
    assert row.id is not None
    return Test(id=row.id, category_id=row.category_id, name=row.name, result=row.result)


class SqlTestRepository(TestRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_category(self, category_id: int) -> list[Test]:
        result = await self._session.exec(
            select(TestModel).where(TestModel.category_id == category_id).order_by(TestModel.id)
        )
        return [_to_entity(row) for row in result.all()]

    async def get(self, test_id: int) -> Test | None:
        row = await self._session.get(TestModel, test_id)
        return _to_entity(row) if row is not None else None

    async def get_scenario_override(self, scenario_id: int, test_id: int) -> str | None:
        result = await self._session.exec(
            select(ScenarioTestResultModel.result).where(
                ScenarioTestResultModel.scenario_id == scenario_id,
                ScenarioTestResultModel.test_id == test_id,
            )
        )
        return result.one_or_none()

    async def list_scenario_relevant(self, scenario_id: int) -> list[Test]:
        result = await self._session.exec(
            select(TestModel)
            .join(ScenarioTestResultModel, ScenarioTestResultModel.test_id == TestModel.id)
            .where(ScenarioTestResultModel.scenario_id == scenario_id)
            .order_by(TestModel.id)
        )
        return [_to_entity(row) for row in result.all()]
