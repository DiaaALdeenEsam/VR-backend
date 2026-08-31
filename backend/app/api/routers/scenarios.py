from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_list_scenarios_use_case
from app.api.schemas.scenario import ScenarioListItem
from app.application.use_cases.list_scenarios import ListScenariosUseCase

router = APIRouter(tags=["scenarios"])


@router.get("/scenarios", response_model=list[ScenarioListItem])
async def list_scenarios(
    use_case: ListScenariosUseCase = Depends(get_list_scenarios_use_case),
) -> list[ScenarioListItem]:
    scenarios = await use_case.execute()
    return [ScenarioListItem(id=s.id, name=s.name) for s in scenarios]
