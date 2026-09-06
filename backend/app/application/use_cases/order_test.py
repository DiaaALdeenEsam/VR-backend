from __future__ import annotations

import logging

from app.domain.entities import OrderedTest, Test, utcnow
from app.domain.exceptions import NotFoundError
from app.domain.repositories import AbstractUnitOfWork

logger = logging.getLogger(__name__)


class OrderTestUseCase:
    """Records that a doctor ordered an investigation, and returns its result.

    This is the only place a Test.result value is allowed to leave the domain --
    it is never included in the /test-categories/{id}/tests listing.

    The result returned is scenario-specific whenever the active session's
    scenario has recorded one (see ScenarioTestResultModel, migration 0004):
    the same test (e.g. "Abdominal ultrasound") is shared across every
    scenario's session -- reusing the same investigation across cases is
    clinically normal -- but the finding it reveals should not be. Falling
    back to the generic Test.result when no override exists keeps every test
    orderable everywhere (nothing 404s just because a scenario hasn't been
    annotated yet), but that fallback is logged at WARNING so a scenario
    silently missing case-specific findings for a test it plausibly needs
    stays visible in dev/test output rather than passing unnoticed.
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

            override = await self._uow.tests.get_scenario_override(session.scenario_id, test_id)
            if override is not None:
                test.result = override
            else:
                logger.warning(
                    "No scenario_test_results override for scenario_id=%s, test_id=%s (%r) -- "
                    "returning the generic/default result instead of a case-specific finding.",
                    session.scenario_id,
                    test_id,
                    test.name,
                )

            ordered = OrderedTest(session_id=session_id, test_id=test_id, ordered_at=utcnow())
            ordered = await self._uow.ordered_tests.add(ordered)

            await self._uow.commit()
            return test, ordered
