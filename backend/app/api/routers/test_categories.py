from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import (
    get_list_relevant_tests_use_case,
    get_list_test_categories_use_case,
    get_list_tests_by_category_use_case,
)
from app.api.schemas.test import TestCategoryRead, TestSummary
from app.application.use_cases.list_relevant_tests import ListRelevantTestsUseCase
from app.application.use_cases.list_test_categories import ListTestCategoriesUseCase
from app.application.use_cases.list_tests_by_category import ListTestsByCategoryUseCase

router = APIRouter(tags=["test-categories"])


@router.get("/test-categories", response_model=list[TestCategoryRead])
async def list_test_categories(
    use_case: ListTestCategoriesUseCase = Depends(get_list_test_categories_use_case),
) -> list[TestCategoryRead]:
    categories = await use_case.execute()
    return [TestCategoryRead(id=c.id, name=c.name) for c in categories]


@router.get("/test-categories/{category_id}/tests", response_model=list[TestSummary])
async def list_tests_by_category(
    category_id: int,
    use_case: ListTestsByCategoryUseCase = Depends(get_list_tests_by_category_use_case),
) -> list[TestSummary]:
    tests = await use_case.execute(category_id)
    return [TestSummary(id=t.id, category_id=t.category_id, name=t.name) for t in tests]


@router.get("/scenarios/{scenario_id}/relevant-tests", response_model=list[TestSummary])
async def list_relevant_tests(
    scenario_id: int,
    use_case: ListRelevantTestsUseCase = Depends(get_list_relevant_tests_use_case),
) -> list[TestSummary]:
    """Tests this scenario has a case-specific finding for (see migration 0004)
    -- a hint for which investigations are actually relevant to the active
    case, narrower than the full /test-categories/{id}/tests catalog.

    Purely additive guidance: POST /sessions/{id}/tests still accepts any
    test_id from the global catalog regardless of what this returns.
    """

    tests = await use_case.execute(scenario_id)
    return [TestSummary(id=t.id, category_id=t.category_id, name=t.name) for t in tests]
