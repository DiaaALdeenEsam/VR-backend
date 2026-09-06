"""Composes a scenario's human-readable `case_text` from its normalized
clinical-section child tables (see app/infrastructure/db/models.py and
docs/scenario-clinical-schema-mapping.md).

`case_text` on `scenarios` itself never changes shape or meaning -- it is
still just "the free-text clinical case body" as far as every use case,
repository consumer, and API response is concerned (see ScenarioRepository
in app/domain/repositories.py). This module only changes *where that string
comes from*: SqlScenarioRepository calls compose_case_text() on read and
uses its result instead of the stored column whenever a scenario has any
normalized clinical data, falling back to the stored `case_text` column
otherwise (e.g. scenarios seeded before this migration, or via the .docx/JSON
import scripts, which still write directly to `scenarios.case_text` and never
touch these child tables).

Read-time composition (rather than write-time, materialized into the column)
was chosen because there is currently no application-layer write path for
scenario data at all -- ScenarioRepository only exposes list_all()/get()
(see app/domain/repositories.py) -- so there is nothing to hook a write-time
step into without inventing a new endpoint, which the task explicitly rules
out. Composing on read keeps this entirely inside the existing repository
and leaves every endpoint's contract untouched.
"""

from __future__ import annotations

from enum import Enum

from app.infrastructure.db.models import ScenarioModel


def _section(title: str, lines: list[str]) -> str | None:
    """Joins a section's non-empty lines under a heading, or None if empty."""

    body = [line for line in lines if line]
    if not body:
        return None
    return f"{title}\n" + "\n".join(body)


def _labeled(label: str, value: str | None) -> str:
    return f"{label}: {value}" if value else ""


def _enum_text(value: object, default: str = "-") -> str:
    """Renders an optional str-Enum column (priority/timing/frequency) as its
    plain value, e.g. 'essential' rather than 'InvestigationPriority.essential'.

    Needed because on Python 3.11, f-string formatting of a `class X(str, Enum)`
    member calls Enum.__format__, which renders as 'ClassName.member' instead of
    the plain string -- even though the member *is* a str. `.value` (or an
    explicit str(...) on the underlying value) sidesteps that; a bare `{member}`
    in an f-string does not.
    """

    if value is None:
        return default
    return value.value if isinstance(value, Enum) else str(value)


def _bulleted(label: str, values: list[str]) -> str:
    if not values:
        return ""
    return f"{label}: " + "; ".join(values)


def has_clinical_data(row: ScenarioModel) -> bool:
    """True if any of the 21 normalized clinical-section child tables has a row
    for this scenario. Relationships must already be loaded (eagerly, e.g. via
    selectinload) -- this never triggers lazy I/O."""

    return any(
        [
            row.pathophysiology is not None,
            row.risk_factors is not None,
            bool(row.risk_factor_items),
            row.symptoms_signs is not None,
            bool(row.symptom_items),
            bool(row.clinical_sign_items),
            bool(row.atypical_presentation_items),
            bool(row.lab_investigations),
            bool(row.radiological_investigations),
            bool(row.differential_diagnoses),
            row.diagnostic_criteria is not None,
            bool(row.diagnostic_criteria_items),
            bool(row.complications),
            row.prevention is not None,
            bool(row.prevention_items),
            row.non_pharma_treatment is not None,
            bool(row.non_pharma_items),
            row.pharma_treatment is not None,
            bool(row.pharma_drug_items),
            bool(row.drugs_to_avoid_items),
            bool(row.interventional_treatments),
        ]
    )


def compose_case_text(row: ScenarioModel) -> str | None:
    """Builds a human-readable case_text from row's clinical-section relationships.

    Returns None if the scenario has no normalized clinical data at all, so
    callers can fall back to the stored `scenarios.case_text` column.
    """

    if not has_clinical_data(row):
        return None

    sections: list[str] = []

    # 1. Pathophysiology
    if row.pathophysiology is not None:
        s = _section(
            "1. Pathophysiology",
            [
                _labeled("Definition", row.pathophysiology.definition),
                _labeled("Mechanism", row.pathophysiology.mechanism),
                _labeled("Classification", row.pathophysiology.classification),
            ],
        )
        if s:
            sections.append(s)

    # 2. Risk Factors
    modifiable = [i.value for i in row.risk_factor_items if i.type == "modifiable"]
    non_modifiable = [i.value for i in row.risk_factor_items if i.type == "non_modifiable"]
    rf = row.risk_factors
    s = _section(
        "2. Risk Factors",
        [
            _bulleted("Modifiable", modifiable),
            _bulleted("Non-modifiable", non_modifiable),
            _labeled("Age group", rf.age_group if rf else None),
            _labeled("Sex predominance", rf.sex_predominance if rf else None),
            _labeled("Geographical prevalence", rf.geographical_prevalence if rf else None),
        ],
    )
    if s:
        sections.append(s)

    # 3. Symptoms and Signs
    cardinal = [i.value for i in row.symptom_items if i.type == "cardinal"]
    other_symptoms = [i.value for i in row.symptom_items if i.type == "other"]
    inspection = [i.value for i in row.clinical_sign_items if i.category == "inspection"]
    palpation = [i.value for i in row.clinical_sign_items if i.category == "palpation"]
    percussion = [i.value for i in row.clinical_sign_items if i.category == "percussion"]
    auscultation = [i.value for i in row.clinical_sign_items if i.category == "auscultation"]
    atypical = [i.value for i in row.atypical_presentation_items]
    s = _section(
        "3. Symptoms and Signs",
        [
            _bulleted("Cardinal symptoms", cardinal),
            _bulleted("Other symptoms", other_symptoms),
            _bulleted("Inspection", inspection),
            _bulleted("Palpation", palpation),
            _bulleted("Percussion", percussion),
            _bulleted("Auscultation", auscultation),
            _bulleted("Atypical presentations", atypical),
        ],
    )
    if s:
        sections.append(s)

    # 4. Laboratory Investigations
    lab_lines = [
        f"{li.test_name or 'Unnamed test'} ({_enum_text(li.priority)}): {li.expected_finding or '-'}"
        f"{f' -- {li.clinical_significance}' if li.clinical_significance else ''}"
        for li in row.lab_investigations
    ]
    s = _section("4. Laboratory Investigations", lab_lines)
    if s:
        sections.append(s)

    # 5. Radiological Investigations
    rad_lines = [
        f"{ri.modality or 'Unnamed modality'} ({_enum_text(ri.priority)}): {ri.expected_finding or '-'}"
        f"{f' -- {ri.clinical_significance}' if ri.clinical_significance else ''}"
        for ri in row.radiological_investigations
    ]
    s = _section("5. Radiological Investigations", rad_lines)
    if s:
        sections.append(s)

    # 6. Differential Diagnosis
    ddx_lines = [
        f"{d.condition or 'Unnamed condition'}: {d.distinguishing_feature or '-'}"
        for d in row.differential_diagnoses
    ]
    s = _section("6. Differential Diagnosis", ddx_lines)
    if s:
        sections.append(s)

    # 7. Diagnostic Criteria
    dc = row.diagnostic_criteria
    criteria_list = [i.criterion_text for i in row.diagnostic_criteria_items]
    s = _section(
        "7. Diagnostic Criteria",
        [
            _labeled("Criteria name", dc.criteria_name if dc else None),
            _bulleted("Criteria", criteria_list),
            _labeled("Scoring system", dc.scoring_system if dc else None),
        ],
    )
    if s:
        sections.append(s)

    # 8. Complications
    complication_lines = [
        f"{c.complication} ({_enum_text(c.timing)}, {_enum_text(c.frequency)})" for c in row.complications
    ]
    s = _section("8. Complications", complication_lines)
    if s:
        sections.append(s)

    # 9. Prevention
    primary_prevention = [i.value for i in row.prevention_items if i.type == "primary"]
    secondary_prevention = [i.value for i in row.prevention_items if i.type == "secondary"]
    prevention = row.prevention
    s = _section(
        "9. Prevention",
        [
            _bulleted("Primary prevention", primary_prevention),
            _bulleted("Secondary prevention", secondary_prevention),
            _labeled("Screening", prevention.screening if prevention else None),
            _labeled("Vaccination", prevention.vaccination if prevention else None),
        ],
    )
    if s:
        sections.append(s)

    # 10. Non-pharmacological Treatment
    lifestyle = [i.value for i in row.non_pharma_items if i.type == "lifestyle"]
    dietary = [i.value for i in row.non_pharma_items if i.type == "dietary"]
    other_non_pharma = [i.value for i in row.non_pharma_items if i.type == "other"]
    npt = row.non_pharma_treatment
    s = _section(
        "10. Non-pharmacological Treatment",
        [
            _bulleted("Lifestyle modifications", lifestyle),
            _bulleted("Dietary changes", dietary),
            _bulleted("Other", other_non_pharma),
            _labeled("Physical activity", npt.physical_activity if npt else None),
        ],
    )
    if s:
        sections.append(s)

    # 11. Pharmacological Treatment
    def _drug_line(d) -> str:
        parts = [p for p in (d.drug_class, d.drug_name, d.dose, d.route, d.duration) if p]
        text = " / ".join(parts) if parts else "(unspecified drug)"
        return f"{text} -- {d.notes}" if d.notes else text

    first_line = [_drug_line(d) for d in row.pharma_drug_items if d.line == "first_line"]
    second_line = [_drug_line(d) for d in row.pharma_drug_items if d.line == "second_line"]
    adjunct = [_drug_line(d) for d in row.pharma_drug_items if d.line == "adjunct_therapy"]
    drugs_to_avoid = [i.value for i in row.drugs_to_avoid_items]
    s = _section(
        "11. Pharmacological Treatment",
        [
            _bulleted("First line", first_line),
            _bulleted("Second line", second_line),
            _bulleted("Adjunct therapy", adjunct),
            _bulleted("Drugs to avoid", drugs_to_avoid),
        ],
    )
    if s:
        sections.append(s)

    # 12. Interventional Treatment
    intervention_lines = [
        f"{t.procedure or 'Unnamed procedure'}"
        f"{f' (indication: {t.indication})' if t.indication else ''}"
        f"{f' (timing: {t.timing})' if t.timing else ''}"
        f"{f' -- {t.notes}' if t.notes else ''}"
        for t in row.interventional_treatments
    ]
    s = _section("12. Interventional Treatment", intervention_lines)
    if s:
        sections.append(s)

    return "\n\n".join(sections)
