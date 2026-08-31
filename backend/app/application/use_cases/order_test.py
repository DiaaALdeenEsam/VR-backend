from __future__ import annotations

from app.domain.entities import OrderedTest, Test, utcnow
from app.domain.exceptions import NotFoundError
from app.domain.repositories import AbstractUnitOfWork


class OrderTestUseCase:
    """Records that a doctor ordered an investigation, and returns its result.

    This is the only place a Test.result value is allowed to leave the domain --
    it is never included in the /test-categories/{id}/tests listing.
    """

    def __init__(self, uow: AbstractUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, session_id: str, test_id: int) -> tuple[Test, OrderedTest]:
        async with self._uow:
            session = await self._uow.sessions.get(session_id)
            if session is None:
                raise NotFoundError("Session", session_id)

            test = await self._uow.tests.get(test_id)
            if test is None:
                raise NotFoundError("Test", test_id)

            ordered = OrderedTest(session_id=session_id, test_id=test_id, ordered_at=utcnow())
            ordered = await self._uow.ordered_tests.add(ordered)

            await self._uow.commit()
            return test, ordered
