"""One-off script to import a single clinical-case JSON (matching the
reference disease-template structure -- top-level disease_name/source/
chapter/pages/fields.1_pathophysiology.../fields.12_interventional_treatment/
extraction_notes/missing_fields) into the database.

Creates:
  - one `scenarios` row (name = disease_name; case_text is left as a short
    placeholder -- the existing read-time composer in scenario_case_text.py
    regenerates it from the normalized child rows below on every read, see
    SqlScenarioRepository; gold_standard = NULL, since no template field maps
    to it unambiguously)
  - the corresponding child rows across the 21 clinical-section tables from
    migration 0002 (see docs/scenario-clinical-schema-mapping.md for the
    exact field mapping)
  - one `scenario_patient_profile` row (migration 0003), parsed out of
    section 2's (risk_factors.epidemiology) age_group / sex_predominance free
    text -- see _parse_age / _parse_sex below for the parsing rules

THIS IS A ONE-OFF MANUAL TESTING TOOL, NOT A BATCH IMPORTER: point it at
exactly one JSON file and review the printed summary (particularly which
patient-profile fields were parsed to concrete values vs. left NULL for
manual review) before trusting the same approach for a bulk importer.

Usage:
    python scripts/import_scenario_from_json.py path/to/disease.json

Never modifies or deletes the source JSON file. Never touches
test_categories, tests, questions, choices, sessions, messages,
ordered_tests, or answers -- and does not touch any router, schema, or
use-case code; this only writes rows via the SQLModel table models directly,
the same way scripts/import_scenario_docx.py and app/infrastructure/seed.py
do.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.infrastructure.db.engine import create_engine_from_settings, create_session_factory
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
    ScenarioModel,
    ScenarioNonPharmaItemModel,
    ScenarioNonPharmaTreatmentModel,
    ScenarioPathophysiologyModel,
    ScenarioPatientProfileModel,
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

# --- text-cleaning helpers ---------------------------------------------------
#
# Extracted templates are full of "Not specified in source" / "Not
# specifically specified" placeholders standing in for missing data. Treating
# those as real values would fill the DB with junk rows (e.g. a lab
# investigation whose test_name, expected_finding, and clinical_significance
# are all "Not specified in source"), so every string is run through _clean()
# and every list-of-dicts entry through _meaningful_dict() before a row gets
# created for it.

_PLACEHOLDER_MARKERS = {
    "",
    "n/a",
    "na",
    "none",
    "unspecified",
    "unknown",
    "not specified",
    "not specified in source",
    "not specifically specified",
    "not specifically specified in this chapter",
    "not available",
    "not stated",
    "not applicable",
}


def _clean(value: Any) -> str | None:
    """Trims a string and treats common 'no data' placeholders as None."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.rstrip(".").lower() in _PLACEHOLDER_MARKERS:
        return None
    return text


def _clean_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [cleaned for v in values if (cleaned := _clean(v))]


def _meaningful_dict(d: Any, keys: list[str]) -> bool:
    return isinstance(d, dict) and any(_clean(d.get(k)) for k in keys)


def _normalize_token(text: Any, allowed: set[str]) -> str | None:
    """Picks the first word in `text` that's a member of `allowed`.

    Word-based (not substring) matching matters here: source text like
    'Subacute/chronic' would false-positive a naive `"acute" in text.lower()`
    check, since "acute" is a substring of "subacute".
    """

    if not isinstance(text, str):
        return None
    for token in re.split(r"[^a-zA-Z]+", text.lower()):
        if token in allowed:
            return token
    return None


def _drug_to_avoid_text(item: Any) -> str | None:
    """`drugs_to_avoid` entries are plain strings in the reference template,
    but some extractions instead give the same drug-row-shaped dict as
    first_line/second_line/adjunct_therapy. Handle both."""

    if isinstance(item, str):
        return _clean(item)
    if isinstance(item, dict):
        parts = [c for k in ("drug_class", "drug_name", "dose", "route", "duration") if (c := _clean(item.get(k)))]
        notes = _clean(item.get("notes"))
        if not parts and not notes:
            return None
        text = " / ".join(parts) if parts else "(unspecified drug)"
        return f"{text} -- {notes}" if notes else text
    return None


def _section(fields: dict[str, Any], key: str) -> dict[str, Any]:
    value = fields.get(key)
    return value if isinstance(value, dict) else {}


# --- patient-profile parsing --------------------------------------------------
#
# Deliberately conservative: only convert age_group/sex_predominance text
# into a concrete value when it's unambiguous. Anything else is left NULL
# with the original text preserved in the matching *_raw column so a human
# can review it later -- see the module docstring and the printed summary.

_VAGUE_AGE_MARKERS = (
    "all ages",
    "any age",
    "no specific age",
    "not age-specific",
    "not age specific",
)

_VAGUE_SEX_MARKERS = (
    "no predominance",
    "no sex predominance",
    "equal",
    "not sex-specific",
    "not sex specific",
)

_SEX_PREDOMINANCE_WORDS = (
    "predomina",
    "more common",
    "more frequent",
    "higher incidence",
    "greater risk",
    "twice as common",
    "more prevalent",
)


def _parse_age(raw: str | None) -> int | None:
    if not raw:
        return None
    lower = raw.lower()
    if any(marker in lower for marker in _VAGUE_AGE_MARKERS):
        return None

    if m := re.search(r"(\d{1,3})\s*[-–—]\s*(\d{1,3})", raw):
        return round((int(m.group(1)) + int(m.group(2))) / 2)
    if m := re.search(r"[<>≥≤]\s*(\d{1,3})", raw):
        return int(m.group(1))
    if m := re.search(r"\bunder\s+(\d{1,3})\b", lower):
        return max(int(m.group(1)) - 1, 0)
    if m := re.search(r"\bover\s+(\d{1,3})\b", lower):
        return int(m.group(1)) + 1
    if "middle-aged" in lower or "middle aged" in lower:
        return 50
    if "elderly" in lower or "older adult" in lower:
        return 70
    if "neonate" in lower:
        return 0
    if "infant" in lower:
        return 1
    if "child" in lower or "paediatric" in lower or "pediatric" in lower:
        return 8
    if m := re.search(r"\b(\d{1,3})\b", raw):
        return int(m.group(1))
    return None


def _parse_sex(raw: str | None) -> str | None:
    if not raw:
        return None
    lower = raw.lower()
    if any(marker in lower for marker in _VAGUE_SEX_MARKERS):
        return None

    if re.search(r"\bf\s*>\s*m\b", lower):
        return "female"
    if re.search(r"\bm\s*>\s*f\b", lower):
        return "male"

    mentions_predominance = any(w in lower for w in _SEX_PREDOMINANCE_WORDS)
    has_male = bool(re.search(r"\bmale\b|\bmen\b", lower))
    has_female = bool(re.search(r"\bfemale\b|\bwomen\b", lower))
    if mentions_predominance and has_male and not has_female:
        return "male"
    if mentions_predominance and has_female and not has_male:
        return "female"
    return None


def _build_patient_profile(
    scenario_id: int,
    age_group_raw: str | None,
    sex_predominance_raw: str | None,
    geographical_context: str | None,
) -> tuple[ScenarioPatientProfileModel, list[str], list[str]]:
    age = _parse_age(age_group_raw)
    sex = _parse_sex(sex_predominance_raw)

    profile = ScenarioPatientProfileModel(
        scenario_id=scenario_id,
        age=age,
        age_group_raw=age_group_raw,
        sex=sex,
        sex_predominance_raw=sex_predominance_raw,
        geographical_context=geographical_context,
    )

    parsed: list[str] = []
    null_for_review: list[str] = []

    if age is not None:
        parsed.append(f"age = {age}  (parsed from age_group: {age_group_raw!r})")
    elif age_group_raw:
        null_for_review.append(f"age  (age_group text too vague/unparseable: {age_group_raw!r})")
    else:
        null_for_review.append("age  (no age_group text in source)")

    if sex is not None:
        parsed.append(f"sex = {sex!r}  (parsed from sex_predominance: {sex_predominance_raw!r})")
    elif sex_predominance_raw:
        null_for_review.append(f"sex  (sex_predominance text too vague/unparseable: {sex_predominance_raw!r})")
    else:
        null_for_review.append("sex  (no sex_predominance text in source)")

    if geographical_context is not None:
        parsed.append(f"geographical_context = {geographical_context!r}  (copied verbatim)")
    else:
        null_for_review.append("geographical_context  (no geographical_prevalence text in source)")

    return profile, parsed, null_for_review


# --- main import ---------------------------------------------------------------


async def import_scenario(session: AsyncSession, data: dict[str, Any]) -> tuple[ScenarioModel, list[str], list[str]]:
    fields = data.get("fields") or {}
    disease_name = _clean(data.get("disease_name")) or "Untitled scenario"

    scenario = ScenarioModel(
        name=disease_name,
        case_text=(
            "(placeholder -- composed from normalized clinical sections at read "
            "time; see app/infrastructure/db/scenario_case_text.py)"
        ),
        gold_standard=None,
    )
    session.add(scenario)
    await session.flush()
    assert scenario.id is not None
    scenario_id = scenario.id

    # 1. Pathophysiology
    patho = _section(fields, "1_pathophysiology")
    if _meaningful_dict(patho, ["definition", "mechanism", "classification", "source_reference"]):
        session.add(
            ScenarioPathophysiologyModel(
                scenario_id=scenario_id,
                definition=_clean(patho.get("definition")),
                mechanism=_clean(patho.get("mechanism")),
                classification=_clean(patho.get("classification")),
                source_reference=_clean(patho.get("source_reference")),
            )
        )

    # 2. Risk factors (+ the epidemiology text the patient profile is parsed from)
    rf = _section(fields, "2_risk_factors")
    epidemiology = rf.get("epidemiology") or {}
    age_group_raw = _clean(epidemiology.get("age_group"))
    sex_predominance_raw = _clean(epidemiology.get("sex_predominance"))
    geographical_prevalence = _clean(epidemiology.get("geographical_prevalence"))
    rf_source_reference = _clean(rf.get("source_reference"))
    if age_group_raw or sex_predominance_raw or geographical_prevalence or rf_source_reference:
        session.add(
            ScenarioRiskFactorsModel(
                scenario_id=scenario_id,
                age_group=age_group_raw,
                sex_predominance=sex_predominance_raw,
                geographical_prevalence=geographical_prevalence,
                source_reference=rf_source_reference,
            )
        )
    for value in _clean_list(rf.get("modifiable")):
        session.add(ScenarioRiskFactorItemModel(scenario_id=scenario_id, type="modifiable", value=value))
    for value in _clean_list(rf.get("non_modifiable")):
        session.add(ScenarioRiskFactorItemModel(scenario_id=scenario_id, type="non_modifiable", value=value))

    # 3. Symptoms and signs
    ss = _section(fields, "3_symptoms_and_signs")
    ss_source_reference = _clean(ss.get("source_reference"))
    if ss_source_reference:
        session.add(ScenarioSymptomsSignsModel(scenario_id=scenario_id, source_reference=ss_source_reference))
    for value in _clean_list(ss.get("cardinal_symptoms")):
        session.add(ScenarioSymptomItemModel(scenario_id=scenario_id, type="cardinal", value=value))
    for value in _clean_list(ss.get("other_symptoms")):
        session.add(ScenarioSymptomItemModel(scenario_id=scenario_id, type="other", value=value))
    clinical_signs = ss.get("clinical_signs") or {}
    for category in ("inspection", "palpation", "percussion", "auscultation"):
        for value in _clean_list(clinical_signs.get(category)):
            session.add(ScenarioClinicalSignItemModel(scenario_id=scenario_id, category=category, value=value))
    for value in _clean_list(ss.get("atypical_presentations")):
        session.add(ScenarioAtypicalPresentationItemModel(scenario_id=scenario_id, value=value))

    # 4. Laboratory investigations
    for item in fields.get("4_laboratory_investigations") or []:
        if not _meaningful_dict(item, ["test_name", "expected_finding", "clinical_significance"]):
            continue
        session.add(
            ScenarioLabInvestigationModel(
                scenario_id=scenario_id,
                test_name=_clean(item.get("test_name")),
                expected_finding=_clean(item.get("expected_finding")),
                clinical_significance=_clean(item.get("clinical_significance")),
                priority=_normalize_token(item.get("priority"), {"essential", "confirmatory", "optional"}),
            )
        )

    # 5. Radiological investigations
    for item in fields.get("5_radiological_investigations") or []:
        if not _meaningful_dict(item, ["modality", "expected_finding", "clinical_significance"]):
            continue
        session.add(
            ScenarioRadiologicalInvestigationModel(
                scenario_id=scenario_id,
                modality=_clean(item.get("modality")),
                expected_finding=_clean(item.get("expected_finding")),
                clinical_significance=_clean(item.get("clinical_significance")),
                priority=_normalize_token(item.get("priority"), {"essential", "confirmatory", "optional"}),
            )
        )

    # 6. Differential diagnosis
    for item in fields.get("6_differential_diagnosis") or []:
        if not _meaningful_dict(item, ["condition", "distinguishing_feature"]):
            continue
        session.add(
            ScenarioDifferentialDiagnosisModel(
                scenario_id=scenario_id,
                condition=_clean(item.get("condition")),
                distinguishing_feature=_clean(item.get("distinguishing_feature")),
            )
        )

    # 7. Diagnostic criteria
    dc = _section(fields, "7_diagnostic_criteria")
    if _meaningful_dict(dc, ["criteria_name", "scoring_system", "source_reference"]):
        session.add(
            ScenarioDiagnosticCriteriaModel(
                scenario_id=scenario_id,
                criteria_name=_clean(dc.get("criteria_name")),
                scoring_system=_clean(dc.get("scoring_system")),
                source_reference=_clean(dc.get("source_reference")),
            )
        )
    for value in _clean_list(dc.get("criteria_list")):
        session.add(ScenarioDiagnosticCriteriaItemModel(scenario_id=scenario_id, criterion_text=value))

    # 8. Complications
    for item in fields.get("8_complications") or []:
        if not isinstance(item, dict):
            continue
        complication = _clean(item.get("complication"))
        if not complication:
            continue
        session.add(
            ScenarioComplicationModel(
                scenario_id=scenario_id,
                complication=complication,
                timing=_normalize_token(item.get("timing"), {"acute", "subacute", "chronic"}),
                frequency=_normalize_token(item.get("frequency"), {"common", "uncommon", "rare"}),
            )
        )

    # 9. Prevention
    prevention = _section(fields, "9_prevention")
    if _meaningful_dict(prevention, ["screening", "vaccination", "source_reference"]):
        session.add(
            ScenarioPreventionModel(
                scenario_id=scenario_id,
                screening=_clean(prevention.get("screening")),
                vaccination=_clean(prevention.get("vaccination")),
                source_reference=_clean(prevention.get("source_reference")),
            )
        )
    for value in _clean_list(prevention.get("primary_prevention")):
        session.add(ScenarioPreventionItemModel(scenario_id=scenario_id, type="primary", value=value))
    for value in _clean_list(prevention.get("secondary_prevention")):
        session.add(ScenarioPreventionItemModel(scenario_id=scenario_id, type="secondary", value=value))

    # 10. Non-pharmacological treatment
    npt = _section(fields, "10_non_pharmacological_treatment")
    if _meaningful_dict(npt, ["physical_activity", "source_reference"]):
        session.add(
            ScenarioNonPharmaTreatmentModel(
                scenario_id=scenario_id,
                physical_activity=_clean(npt.get("physical_activity")),
                source_reference=_clean(npt.get("source_reference")),
            )
        )
    for value in _clean_list(npt.get("lifestyle_modifications")):
        session.add(ScenarioNonPharmaItemModel(scenario_id=scenario_id, type="lifestyle", value=value))
    for value in _clean_list(npt.get("dietary_changes")):
        session.add(ScenarioNonPharmaItemModel(scenario_id=scenario_id, type="dietary", value=value))
    for value in _clean_list(npt.get("other")):
        session.add(ScenarioNonPharmaItemModel(scenario_id=scenario_id, type="other", value=value))

    # 11. Pharmacological treatment
    pt = _section(fields, "11_pharmacological_treatment")
    pt_source_reference = _clean(pt.get("source_reference"))
    if pt_source_reference:
        session.add(ScenarioPharmaTreatmentModel(scenario_id=scenario_id, source_reference=pt_source_reference))
    drug_keys = ["drug_class", "drug_name", "dose", "route", "duration", "notes"]
    for line in ("first_line", "second_line", "adjunct_therapy"):
        for item in pt.get(line) or []:
            if not _meaningful_dict(item, drug_keys):
                continue
            session.add(
                ScenarioPharmaDrugItemModel(
                    scenario_id=scenario_id,
                    line=line,
                    drug_class=_clean(item.get("drug_class")),
                    drug_name=_clean(item.get("drug_name")),
                    dose=_clean(item.get("dose")),
                    route=_clean(item.get("route")),
                    duration=_clean(item.get("duration")),
                    notes=_clean(item.get("notes")),
                )
            )
    for item in pt.get("drugs_to_avoid") or []:
        value = _drug_to_avoid_text(item)
        if value:
            session.add(ScenarioDrugsToAvoidItemModel(scenario_id=scenario_id, value=value))

    # 12. Interventional treatment
    for item in fields.get("12_interventional_treatment") or []:
        if not _meaningful_dict(item, ["procedure", "indication", "timing", "notes"]):
            continue
        session.add(
            ScenarioInterventionalTreatmentModel(
                scenario_id=scenario_id,
                procedure=_clean(item.get("procedure")),
                indication=_clean(item.get("indication")),
                timing=_clean(item.get("timing")),
                notes=_clean(item.get("notes")),
            )
        )

    # Derived patient profile (not a template section -- parsed from section 2's
    # epidemiology text above; see ScenarioPatientProfileModel's docstring).
    profile, parsed_fields, null_fields = _build_patient_profile(
        scenario_id, age_group_raw, sex_predominance_raw, geographical_prevalence
    )
    session.add(profile)

    await session.flush()
    return scenario, parsed_fields, null_fields


async def run(json_path: Path) -> None:
    data = json.loads(json_path.read_text(encoding="utf-8"))

    settings = get_settings()
    engine = create_engine_from_settings(settings)
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        scenario, parsed_fields, null_fields = await import_scenario(session, data)
        await session.commit()
        scenario_id, scenario_name = scenario.id, scenario.name

    await engine.dispose()

    print(f"\nImported scenario id={scenario_id} name={scenario_name!r} from {json_path.name}")
    print(f"  database: {settings.database_url}")

    print("\nPatient profile (scenario_patient_profile) -- parsed to concrete values:")
    if parsed_fields:
        for line in parsed_fields:
            print(f"  - {line}")
    else:
        print("  (none)")

    print("\nPatient profile -- left NULL for manual review:")
    if null_fields:
        for line in null_fields:
            print(f"  - {line}")
    else:
        print("  (none)")

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
