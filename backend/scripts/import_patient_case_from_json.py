"""One-off script to import a single ER-style patient-case vignette JSON
(the patient-case simulation layer added in migration 0006 -- see
docs/scenario-clinical-schema-mapping.md) into the database.

Unlike scripts/import_scenario_from_json.py (which writes SQLModel rows
directly, matching its own module docstring's stated convention), this
script goes through the new repository layer added alongside migration 0006
(PatientCaseRepository / SqlPatientCaseRepository -- the same class
SqlAlchemyUnitOfWork.patient_cases wires up for app code) -- deliberately
exercising that layer's create() end-to-end, in one transaction, the way a
future real caller (e.g. an application use case, going through the UoW)
would.

Expected JSON shape (every key besides scenario_name/scenario_id is
optional -- omit whatever the source case doesn't specify):

{
  "scenario_id": null,                 // attach to an existing scenario instead of creating one
  "scenario_name": "Acute Appendicitis -- ER Case 1",  // used only when scenario_id is omitted
  "gold_standard": null,               // used only when scenario_id is omitted
  "patient_identity": {"name": null, "age": 24, "sex": "male", "occupation": null,
                        "nationality": null, "marital_status": null},
  "presenting_complaint": {"chief_complaint": "...", "hpi_narrative": "..."},
  "associated_symptoms": [{"symptom": "Nausea", "is_present": true}],
  "past_medical_history": [{"item": "Appendectomy", "note": null}],
  "current_medications": [{"drug_name": "Ibuprofen", "dose": "400mg PRN", "note": null}],
  "triggers": [{"trigger": "Movement", "is_primary": true}],
  "family_social_history": [{"category": "family", "item": "..."}],
  "vital_signs": [{"parameter": "Temp", "value": "38.5C", "interpretation": "Fever"}],
  "physical_exam_findings": [{"method": "palpation", "finding": "RLQ tenderness"}],
  "investigations": [{"category": "laboratory", "test_name": "CBC", "result": "...", "interpretation": "..."}],
  "severity_criteria": [{"criterion": "...", "patient_value": "...", "classification": "..."}],
  "warning_signs": [{"sign": "Rebound tenderness", "is_present": true}],
  "management_phases": [{"phase": "immediate", "treatment": "IV fluids", "dose_route": null,
                          "goal": null, "sequence_order": 1}],
  "disposition_criteria": [{"type": "admission", "criterion": "..."}],
  "discharge_plan": [{"category": "follow_up", "detail": "..."}],
  "learning_objectives": [{"objective_number": 1, "objective_text": "..."}]
}

`category`/`type`/`phase`/`method` values must match the CHECK-constrained
vocabulary on the corresponding table (see
alembic/versions/0006_patient_case_simulation.py) -- this script does not
normalize or guess at those the way import_scenario_from_json.py's
_normalize_token does for the disease-reference layer; a mismatched value
surfaces as a CHECK-constraint IntegrityError from SQLite on commit.

THIS IS A ONE-OFF MANUAL TESTING TOOL, NOT A BATCH IMPORTER: point it at
exactly one JSON file and review the printed summary.

Usage:
    python scripts/import_patient_case_from_json.py path/to/patient_case.json

Never modifies or deletes the source JSON file. Never touches
test_categories, tests, questions, choices, sessions, messages,
ordered_tests, answers, or any of the disease-reference tables from
migrations 0002-0004.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.domain.entities import (
    AssociatedSymptom,
    CaseInvestigation,
    CurrentMedication,
    DischargePlanItem,
    DispositionCriterion,
    FamilySocialHistoryItem,
    LearningObjective,
    ManagementPhaseItem,
    PastMedicalHistoryItem,
    PatientCase,
    PatientIdentity,
    PhysicalExamFinding,
    PresentingComplaint,
    SeverityCriterion,
    Trigger,
    VitalSign,
    WarningSign,
)
from app.infrastructure.db.engine import create_engine_from_settings, create_session_factory
from app.infrastructure.db.models import ScenarioModel
from app.infrastructure.db.repositories.patient_case_repository import SqlPatientCaseRepository


def _build_patient_case(scenario_id: int, data: dict[str, Any]) -> PatientCase:
    identity_raw = data.get("patient_identity")
    identity = PatientIdentity(**identity_raw) if identity_raw else None

    complaint_raw = data.get("presenting_complaint")
    presenting_complaint = PresentingComplaint(**complaint_raw) if complaint_raw else None

    return PatientCase(
        scenario_id=scenario_id,
        identity=identity,
        presenting_complaint=presenting_complaint,
        associated_symptoms=[AssociatedSymptom(**i) for i in data.get("associated_symptoms") or []],
        past_medical_history=[PastMedicalHistoryItem(**i) for i in data.get("past_medical_history") or []],
        current_medications=[CurrentMedication(**i) for i in data.get("current_medications") or []],
        triggers=[Trigger(**i) for i in data.get("triggers") or []],
        family_social_history=[FamilySocialHistoryItem(**i) for i in data.get("family_social_history") or []],
        vital_signs=[VitalSign(**i) for i in data.get("vital_signs") or []],
        physical_exam_findings=[PhysicalExamFinding(**i) for i in data.get("physical_exam_findings") or []],
        investigations=[CaseInvestigation(**i) for i in data.get("investigations") or []],
        severity_criteria=[SeverityCriterion(**i) for i in data.get("severity_criteria") or []],
        warning_signs=[WarningSign(**i) for i in data.get("warning_signs") or []],
        management_phases=[ManagementPhaseItem(**i) for i in data.get("management_phases") or []],
        disposition_criteria=[DispositionCriterion(**i) for i in data.get("disposition_criteria") or []],
        discharge_plan=[DischargePlanItem(**i) for i in data.get("discharge_plan") or []],
        learning_objectives=[LearningObjective(**i) for i in data.get("learning_objectives") or []],
    )


def _section_counts(case: PatientCase) -> list[str]:
    lines = [
        f"  - patient_identity: {'yes' if case.identity else 'no'}",
        f"  - presenting_complaint: {'yes' if case.presenting_complaint else 'no'}",
    ]
    list_sections = {
        "associated_symptoms": case.associated_symptoms,
        "past_medical_history": case.past_medical_history,
        "current_medications": case.current_medications,
        "triggers": case.triggers,
        "family_social_history": case.family_social_history,
        "vital_signs": case.vital_signs,
        "physical_exam_findings": case.physical_exam_findings,
        "investigations": case.investigations,
        "severity_criteria": case.severity_criteria,
        "warning_signs": case.warning_signs,
        "management_phases": case.management_phases,
        "disposition_criteria": case.disposition_criteria,
        "discharge_plan": case.discharge_plan,
        "learning_objectives": case.learning_objectives,
    }
    lines.extend(f"  - {name}: {len(items)} row(s)" for name, items in list_sections.items())
    return lines


async def _resolve_scenario(session: AsyncSession, data: dict[str, Any]) -> tuple[int, str]:
    """Returns (scenario_id, scenario_name), creating a new scenario row (and
    flushing it) if `data` doesn't reference an existing one."""

    scenario_id = data.get("scenario_id")
    if scenario_id is not None:
        existing = await session.get(ScenarioModel, scenario_id)
        if existing is None:
            print(f"No scenario with id={scenario_id} exists -- aborting, nothing written.")
            raise SystemExit(1)
        return existing.id, existing.name  # type: ignore[return-value]

    scenario = ScenarioModel(
        name=data.get("scenario_name") or "Untitled patient case",
        case_text=(
            "(placeholder -- see the case_patient_identity/case_presenting_complaint/... "
            "tables added in migration 0006 for this scenario's actual patient-case data)"
        ),
        gold_standard=data.get("gold_standard"),
    )
    session.add(scenario)
    await session.flush()
    assert scenario.id is not None
    return scenario.id, scenario.name


async def run(json_path: Path) -> None:
    data = json.loads(json_path.read_text(encoding="utf-8"))

    settings = get_settings()
    engine = create_engine_from_settings(settings)
    session_factory = create_session_factory(engine)

    # One session/transaction for the (optional) scenario row plus every
    # patient-case table -- SqlPatientCaseRepository.create() (the same
    # repository SqlAlchemyUnitOfWork.patient_cases wires up in the app
    # itself) does the 16-table write; this script commits it here rather
    # than through the UoW context manager, matching the raw-session
    # convention import_scenario_from_json.py uses for its own one-off writes.
    async with session_factory() as session:
        scenario_id, scenario_name = await _resolve_scenario(session, data)
        case = _build_patient_case(scenario_id, data)
        await SqlPatientCaseRepository(session).create(case)
        await session.commit()

    await engine.dispose()

    print(f"\nImported patient case for scenario id={scenario_id} name={scenario_name!r} from {json_path.name}")
    print(f"  database: {settings.database_url}")
    print("\nSections written:")
    for line in _section_counts(case):
        print(line)
    print(f"\nSource file {json_path} was not modified.")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Arabic/unicode text prints correctly on Windows consoles

    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(1)

    json_path = Path(sys.argv[1])
    if not json_path.is_file():
        print(f"Not a file: {json_path}")
        raise SystemExit(1)

    asyncio.run(run(json_path))


if __name__ == "__main__":
    main()
