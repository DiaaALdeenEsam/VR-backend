from __future__ import annotations

from app.domain.entities import TestCategory
from app.domain.repositories import AbstractUnitOfWork


class ListTestCategoriesUseCase:
    def __init__(self, uow: AbstractUnitOfWork) -> None:
        self._uow = uow

    async def execute(self) -> list[TestCategory]:
        async with self._uow:
            return await self._uow.test_categories.list_all()
