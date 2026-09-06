from __future__ import annotations

from app.domain.entities import Test
from app.domain.exceptions import NotFoundError
from app.domain.repositories import AbstractUnitOfWork


class ListRelevantTestsUseCase:
    """Lists the tests a scenario has a case-specific finding for (see
    ScenarioTestResultModel, migration 0004) -- narrower than the full global
    test catalog, and meant as a hint for the frontend/doctor about which
    investigations this particular case's data actually speaks to.

    Deliberately does not restrict what can be ordered: POST
    /sessions/{id}/tests still accepts any test_id from the global catalog
    (see OrderTestUseCase) -- a doctor ordering an investigation that turns
    out irrelevant is itself clinically realistic. This list is additive
    guidance, not a filter on what's orderable.

    Only the menu (id/name) is meaningful here -- Test.result is never
    returned by this use case, the same restriction ListTestsByCategoryUseCase
    applies to the full catalog.
    """

    def __init__(self, uow: AbstractUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, scenario_id: int) -> list[Test]:
        async with self._uow:
            scenario = await self._uow.scenarios.get(scenario_id)
            if scenario is None:
                raise NotFoundError("Scenario", scenario_id)
            return await self._uow.tests.list_scenario_relevant(scenario_id)
