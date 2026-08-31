from __future__ import annotations

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domain.entities import TestCategory
from app.domain.repositories import TestCategoryRepository
from app.infrastructure.db.models import TestCategoryModel


def _to_entity(row: TestCategoryModel) -> TestCategory:
    assert row.id is not None
    return TestCategory(id=row.id, name=row.name)


class SqlTestCategoryRepository(TestCategoryRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[TestCategory]:
        result = await self._session.exec(select(TestCategoryModel).order_by(TestCategoryModel.id))
        return [_to_entity(row) for row in result.all()]

    async def get(self, category_id: int) -> TestCategory | None:
        row = await self._session.get(TestCategoryModel, category_id)
        return _to_entity(row) if row is not None else None
