"""Covers the normalized scenario clinical-section tables added alongside
`scenarios` (see app/infrastructure/db/models.py and
docs/scenario-clinical-schema-mapping.md).

Two things matter here:
  1. Every existing scenario-related endpoint's response shape is completely
     unaffected by this change -- same fields, same absence of case_text/
     gold_standard (see test_scenarios.py for the baseline these mirror).
  2. SqlScenarioRepository composes a rich case_text from the new tables when
     they have data, and falls back to the plain stored column otherwise.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.infrastructure.db.models import (
    ScenarioAtypicalPresentationItemModel,
    ScenarioClinicalSignItemModel,
    ScenarioComplicationModel,
    ScenarioDiagnosticCriteriaItemModel,
    ScenarioDiagnosticCriteriaModel,
    ScenarioDifferentialDiagnosisModel,
    ScenarioDrugsToAvoidItemModel,
    ScenarioInterventionalTreatmentModel,
    ScenarioLabInvestigationModel,
    ScenarioNonPharmaItemModel,
    ScenarioNonPharmaTreatmentModel,
    ScenarioPathophysiologyModel,
    ScenarioPharmaDrugItemModel,
    ScenarioPharmaTreatmentModel,
    ScenarioPreventionItemModel,
    ScenarioPreventionModel,
    ScenarioRadiologicalInvestigationModel,
    ScenarioRiskFactorItemModel,
    ScenarioRiskFactorsModel,
    ScenarioSymptomItemModel,
    ScenarioSymptomsSignsModel,
)
from app.infrastructure.db.repositories.scenario_repository import SqlScenarioRepository


async def _seed_full_clinical_data(session_factory: async_sessionmaker[AsyncSession], scenario_id: int) -> None:
    """Populates one row (or a couple) in every one of the 21 child tables."""

    async with session_factory() as session:
        session.add_all(
            [
                ScenarioPathophysiologyModel(
                    scenario_id=scenario_id,
                    definition="Gallstone disease definition",
                    mechanism="Cholesterol supersaturation",
                    classification="Cholesterol vs pigment stones",
                    source_reference="Kumar and Clark 11th Ed",
                ),
                ScenarioRiskFactorsModel(
                    scenario_id=scenario_id,
                    age_group="40+",
                    sex_predominance="Female",
                    geographical_prevalence="Higher in Western countries",
                ),
                ScenarioRiskFactorItemModel(scenario_id=scenario_id, type="modifiable", value="Obesity"),
                ScenarioRiskFactorItemModel(scenario_id=scenario_id, type="non_modifiable", value="Female sex"),
                ScenarioSymptomsSignsModel(scenario_id=scenario_id, source_reference="Kumar and Clark"),
                ScenarioSymptomItemModel(scenario_id=scenario_id, type="cardinal", value="RUQ pain"),
                ScenarioSymptomItemModel(scenario_id=scenario_id, type="other", value="Nausea"),
                ScenarioClinicalSignItemModel(
                    scenario_id=scenario_id, category="palpation", value="Murphy's sign positive"
                ),
                ScenarioAtypicalPresentationItemModel(scenario_id=scenario_id, value="Epigastric pain only"),
                ScenarioLabInvestigationModel(
                    scenario_id=scenario_id,
                    test_name="LFTs",
                    expected_finding="Mildly elevated",
                    priority="essential",
                ),
                ScenarioRadiologicalInvestigationModel(
                    scenario_id=scenario_id,
                    modality="Abdominal ultrasound",
                    expected_finding="Echogenic foci with acoustic shadowing",
                    priority="essential",
                ),
                ScenarioDifferentialDiagnosisModel(
                    scenario_id=scenario_id, condition="Peptic ulcer disease", distinguishing_feature="No RUQ tenderness"
                ),
                ScenarioDiagnosticCriteriaModel(
                    scenario_id=scenario_id, criteria_name="Clinical + ultrasound", scoring_system=None
                ),
                ScenarioDiagnosticCriteriaItemModel(
                    scenario_id=scenario_id, criterion_text="RUQ pain + ultrasound-confirmed stones"
                ),
                ScenarioComplicationModel(
                    scenario_id=scenario_id, complication="Acute cholecystitis", timing="acute", frequency="common"
                ),
                ScenarioPreventionModel(scenario_id=scenario_id, screening="None routine"),
                ScenarioPreventionItemModel(scenario_id=scenario_id, type="primary", value="Maintain healthy weight"),
                ScenarioNonPharmaTreatmentModel(scenario_id=scenario_id, physical_activity="Regular exercise"),
                ScenarioNonPharmaItemModel(scenario_id=scenario_id, type="dietary", value="Low-fat diet"),
                ScenarioPharmaTreatmentModel(scenario_id=scenario_id, source_reference="Kumar and Clark"),
                ScenarioPharmaDrugItemModel(
                    scenario_id=scenario_id,
                    line="first_line",
                    drug_class="Analgesic",
                    drug_name="Diclofenac",
                    dose="75mg",
                    route="IM",
                ),
                ScenarioDrugsToAvoidItemModel(scenario_id=scenario_id, value="Opioids (mask perforation signs)"),
                ScenarioInterventionalTreatmentModel(
                    scenario_id=scenario_id,
                    procedure="Laparoscopic cholecystectomy",
                    indication="Symptomatic gallstones",
                    timing="Elective",
                ),
            ]
        )
        await session.commit()


@pytest.mark.usefixtures("seed_ids")
async def test_list_scenarios_endpoint_shape_unchanged_with_rich_data(client, seed_ids, session_factory):
    """Same assertions as test_scenarios.py::test_list_scenarios_returns_seeded_scenario,
    but run *after* the scenario has been given full normalized clinical data --
    the endpoint's response shape must be byte-for-byte the same either way."""

    await _seed_full_clinical_data(session_factory, seed_ids["scenario_id"])

    response = await client.get("/scenarios")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert set(body[0].keys()) == {"id", "name"}
    assert body[0]["id"] == seed_ids["scenario_id"]
    assert body[0]["name"] == "حالة اختبار"
    assert "case_text" not in body[0]
    assert "gold_standard" not in body[0]


@pytest.mark.usefixtures("seed_ids")
async def test_questions_endpoint_shape_unchanged_with_rich_data(client, seed_ids, session_factory):
    await _seed_full_clinical_data(session_factory, seed_ids["scenario_id"])

    response = await client.get(f"/scenarios/{seed_ids['scenario_id']}/questions")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert set(body[0].keys()) == {"id", "text", "choices"}
    assert "correct_choice_id" not in body[0]


@pytest.mark.usefixtures("seed_ids")
async def test_repository_composes_case_text_from_clinical_sections(seed_ids, session_factory):
    await _seed_full_clinical_data(session_factory, seed_ids["scenario_id"])

    async with session_factory() as session:
        repo = SqlScenarioRepository(session)
        scenario = await repo.get(seed_ids["scenario_id"])

    assert scenario is not None
    # Composed text supersedes the plain stored case_text once clinical data exists.
    assert scenario.case_text != "نص الحالة السريرية الكامل (مخفي عن الطبيب المتدرب)."
    assert "1. Pathophysiology" in scenario.case_text
    assert "Gallstone disease definition" in scenario.case_text
    assert "Murphy's sign positive" in scenario.case_text
    assert "Laparoscopic cholecystectomy" in scenario.case_text
    # gold_standard is untouched by any of this.
    assert scenario.gold_standard == "التشخيص المرجعي"


@pytest.mark.usefixtures("seed_ids")
async def test_repository_falls_back_to_stored_case_text_without_clinical_data(seed_ids, session_factory):
    """A scenario with no rows in any of the 21 child tables (e.g. seeded via
    the legacy JSON/.docx import scripts) keeps returning its stored case_text
    verbatim -- composition only kicks in once there is something to compose."""

    async with session_factory() as session:
        repo = SqlScenarioRepository(session)
        scenario = await repo.get(seed_ids["scenario_id"])

    assert scenario is not None
    assert scenario.case_text == "نص الحالة السريرية الكامل (مخفي عن الطبيب المتدرب)."


@pytest.mark.usefixtures("seed_ids")
async def test_list_all_also_composes_case_text(seed_ids, session_factory):
    await _seed_full_clinical_data(session_factory, seed_ids["scenario_id"])

    async with session_factory() as session:
        repo = SqlScenarioRepository(session)
        scenarios = await repo.list_all()

    assert len(scenarios) == 1
    assert "2. Risk Factors" in scenarios[0].case_text
    assert "Obesity" in scenarios[0].case_text
