from __future__ import annotations

from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domain.entities import Scenario
from app.domain.repositories import ScenarioRepository
from app.infrastructure.db.models import ScenarioModel
from app.infrastructure.db.scenario_case_text import compose_case_text

# Eagerly loads every normalized clinical-section relationship so
# compose_case_text() (see scenario_case_text.py) never triggers lazy I/O --
# selectinload keeps this to one extra SELECT per relationship rather than a
# giant multi-join, which is fine at this table count/row volume.
_CLINICAL_SECTION_OPTIONS = [
    selectinload(ScenarioModel.pathophysiology),
    selectinload(ScenarioModel.risk_factors),
    selectinload(ScenarioModel.risk_factor_items),
    selectinload(ScenarioModel.symptoms_signs),
    selectinload(ScenarioModel.symptom_items),
    selectinload(ScenarioModel.clinical_sign_items),
    selectinload(ScenarioModel.atypical_presentation_items),
    selectinload(ScenarioModel.lab_investigations),
    selectinload(ScenarioModel.radiological_investigations),
    selectinload(ScenarioModel.differential_diagnoses),
    selectinload(ScenarioModel.diagnostic_criteria),
    selectinload(ScenarioModel.diagnostic_criteria_items),
    selectinload(ScenarioModel.complications),
    selectinload(ScenarioModel.prevention),
    selectinload(ScenarioModel.prevention_items),
    selectinload(ScenarioModel.non_pharma_treatment),
    selectinload(ScenarioModel.non_pharma_items),
    selectinload(ScenarioModel.pharma_treatment),
    selectinload(ScenarioModel.pharma_drug_items),
    selectinload(ScenarioModel.drugs_to_avoid_items),
    selectinload(ScenarioModel.interventional_treatments),
]


def _to_entity(row: ScenarioModel) -> Scenario:
    assert row.id is not None
    # Prefer the composed, normalized case_text when a scenario has any rich
    # clinical data; otherwise fall back to the plain stored column (covers
    # scenarios seeded before this schema addition, or via the .docx/legacy
    # JSON import scripts, which only ever write the stored column).
    case_text = compose_case_text(row) or row.case_text
    return Scenario(id=row.id, name=row.name, case_text=case_text, gold_standard=row.gold_standard)


class SqlScenarioRepository(ScenarioRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[Scenario]:
        result = await self._session.exec(
            select(ScenarioModel).options(*_CLINICAL_SECTION_OPTIONS).order_by(ScenarioModel.id)
        )
        return [_to_entity(row) for row in result.all()]

    async def get(self, scenario_id: int) -> Scenario | None:
        row = await self._session.get(ScenarioModel, scenario_id, options=_CLINICAL_SECTION_OPTIONS)
        return _to_entity(row) if row is not None else None
