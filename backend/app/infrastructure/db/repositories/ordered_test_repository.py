from __future__ import annotations

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domain.entities import OrderedTest
from app.domain.repositories import OrderedTestRepository
from app.infrastructure.db.models import OrderedTestModel


def _to_entity(row: OrderedTestModel) -> OrderedTest:
    return OrderedTest(id=row.id, session_id=row.session_id, test_id=row.test_id, ordered_at=row.ordered_at)


class SqlOrderedTestRepository(OrderedTestRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, ordered_test: OrderedTest) -> OrderedTest:
        row = OrderedTestModel(
            session_id=ordered_test.session_id,
            test_id=ordered_test.test_id,
            ordered_at=ordered_test.ordered_at,
        )
        self._session.add(row)
        await self._session.flush()
        return _to_entity(row)

    async def list_by_session(self, session_id: str) -> list[OrderedTest]:
        result = await self._session.exec(
            select(OrderedTestModel)
            .where(OrderedTestModel.session_id == session_id)
            .order_by(OrderedTestModel.id)
        )
        return [_to_entity(row) for row in result.all()]
