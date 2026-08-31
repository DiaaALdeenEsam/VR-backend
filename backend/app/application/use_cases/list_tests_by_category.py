from __future__ import annotations

from app.domain.entities import Test
from app.domain.exceptions import NotFoundError
from app.domain.repositories import AbstractUnitOfWork


class ListTestsByCategoryUseCase:
    """Lists the tests (investigations) available under a category.

    Only the menu (id/name) is meaningful here -- Test.result is never returned
    by this use case; it is only revealed by OrderTestUseCase once ordered.
    """

    def __init__(self, uow: AbstractUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, category_id: int) -> list[Test]:
        async with self._uow:
            category = await self._uow.test_categories.get(category_id)
            if category is None:
                raise NotFoundError("TestCategory", category_id)
            return await self._uow.tests.list_by_category(category_id)
