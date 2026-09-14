# Scenario clinical schema: template JSON → database mapping

`scenarios` carries two independent, optional layers of normalized clinical
data, either or both of which may be empty for a given scenario:

1. **Disease-reference layer** (12 sections, 21 tables, migrations
   0002-0004) -- reference facts about the disease in general (typical
   pathophysiology, typical risk factors, typical management, ...),
   composed at read time into `scenarios.case_text`. This is what the rest
   of this document maps.
2. **Patient-case simulation layer** (16 tables, migration 0006) -- one
   concrete ER-style patient vignette (this particular patient's identity,
   HPI, vitals, exam findings, actual investigation results, and management
   plan). See "Patient-case simulation layer" below.

They are independent because they answer different questions ("what is
typically true of this disease" vs. "what happened with this one patient")
and are consumed differently: the disease-reference layer only ever feeds
`Scenario.case_text` (no domain entity of its own -- see "case_text
composition" below); the patient-case layer has its own domain entity
(`PatientCase`) and repository (`PatientCaseRepository`) and is read/written
as a structured aggregate, never flattened into `case_text`. A scenario can
have disease-reference data, patient-case data, both, or neither -- nothing
in either layer requires the other to exist.

## Disease-reference layer

This maps the 12-section reference disease-template JSON (see the
`fields` object in a template like `template.json`) onto the normalized
child tables added under `scenarios` by
[`alembic/versions/0002_scenario_clinical_sections.py`](../alembic/versions/0002_scenario_clinical_sections.py)
and modeled in
[`app/infrastructure/db/models.py`](../app/infrastructure/db/models.py).

Every section is optional -- a scenario may have rows for only some of these
tables, or none at all, in which case the plain `scenarios.case_text` column
is used as-is (see "case_text composition" below).

Cardinality key: **1:1** = at most one row per scenario, table's primary key
*is* `scenario_id`. **1:N** = zero or more rows per scenario, auto-increment
`id` primary key plus an indexed `scenario_id` FK.

## 1. `pathophysiology`

| Template field | Table.column | Cardinality |
|---|---|---|
| `definition` | `scenario_pathophysiology.definition` | 1:1 |
| `mechanism` | `scenario_pathophysiology.mechanism` | 1:1 |
| `classification` | `scenario_pathophysiology.classification` | 1:1 |
| `source_reference` | `scenario_pathophysiology.source_reference` | 1:1 |

## 2. `risk_factors`

| Template field | Table.column | Cardinality |
|---|---|---|
| `modifiable[]` | `scenario_risk_factor_items.value` where `type='modifiable'` | 1:N |
| `non_modifiable[]` | `scenario_risk_factor_items.value` where `type='non_modifiable'` | 1:N |
| `epidemiology.age_group` | `scenario_risk_factors.age_group` | 1:1 |
| `epidemiology.sex_predominance` | `scenario_risk_factors.sex_predominance` | 1:1 |
| `epidemiology.geographical_prevalence` | `scenario_risk_factors.geographical_prevalence` | 1:1 |
| `source_reference` | `scenario_risk_factors.source_reference` | 1:1 |

`scenario_risk_factor_items.type` is CHECK-constrained to
`modifiable | non_modifiable` (mirrored as `RiskFactorType`).

## 3. `symptoms_and_signs`

| Template field | Table.column | Cardinality |
|---|---|---|
| `cardinal_symptoms[]` | `scenario_symptom_items.value` where `type='cardinal'` | 1:N |
| `other_symptoms[]` | `scenario_symptom_items.value` where `type='other'` | 1:N |
| `clinical_signs.inspection[]` | `scenario_clinical_sign_items.value` where `category='inspection'` | 1:N |
| `clinical_signs.palpation[]` | `scenario_clinical_sign_items.value` where `category='palpation'` | 1:N |
| `clinical_signs.percussion[]` | `scenario_clinical_sign_items.value` where `category='percussion'` | 1:N |
| `clinical_signs.auscultation[]` | `scenario_clinical_sign_items.value` where `category='auscultation'` | 1:N |
| `atypical_presentations[]` | `scenario_atypical_presentation_items.value` | 1:N |
| `source_reference` | `scenario_symptoms_signs.source_reference` | 1:1 |

`scenario_symptom_items.type` is CHECK-constrained to `cardinal | other`
(mirrored as `SymptomItemType`). `scenario_clinical_sign_items.category` is
CHECK-constrained to `inspection | palpation | percussion | auscultation`
(mirrored as `ClinicalSignCategory`).

## 4. `laboratory_investigations[]`

| Template field | Table.column | Cardinality |
|---|---|---|
| `test_name` | `scenario_lab_investigations.test_name` | 1:N |
| `expected_finding` | `scenario_lab_investigations.expected_finding` | 1:N |
| `clinical_significance` | `scenario_lab_investigations.clinical_significance` | 1:N |
| `priority` | `scenario_lab_investigations.priority` | 1:N |

`priority` is CHECK-constrained to `essential | confirmatory | optional`
(mirrored as `InvestigationPriority`).

## 5. `radiological_investigations[]`

| Template field | Table.column | Cardinality |
|---|---|---|
| `modality` | `scenario_radiological_investigations.modality` | 1:N |
| `expected_finding` | `scenario_radiological_investigations.expected_finding` | 1:N |
| `clinical_significance` | `scenario_radiological_investigations.clinical_significance` | 1:N |
| `priority` | `scenario_radiological_investigations.priority` | 1:N |

Same `InvestigationPriority` CHECK as section 4.

## 6. `differential_diagnosis[]`

| Template field | Table.column | Cardinality |
|---|---|---|
| `condition` | `scenario_differential_diagnoses.condition` | 1:N |
| `distinguishing_feature` | `scenario_differential_diagnoses.distinguishing_feature` | 1:N |

## 7. `diagnostic_criteria`

| Template field | Table.column | Cardinality |
|---|---|---|
| `criteria_name` | `scenario_diagnostic_criteria.criteria_name` | 1:1 |
| `criteria_list[]` | `scenario_diagnostic_criteria_items.criterion_text` | 1:N |
| `scoring_system` | `scenario_diagnostic_criteria.scoring_system` | 1:1 |
| `source_reference` | `scenario_diagnostic_criteria.source_reference` | 1:1 |

## 8. `complications[]`

| Template field | Table.column | Cardinality |
|---|---|---|
| `complication` | `scenario_complications.complication` | 1:N |
| `timing` | `scenario_complications.timing` | 1:N |
| `frequency` | `scenario_complications.frequency` | 1:N |

`timing` is CHECK-constrained to `acute | subacute | chronic` (mirrored as
`ComplicationTiming`). `frequency` is CHECK-constrained to
`common | uncommon | rare` (mirrored as `ComplicationFrequency`).

## 9. `prevention`

| Template field | Table.column | Cardinality |
|---|---|---|
| `primary_prevention[]` | `scenario_prevention_items.value` where `type='primary'` | 1:N |
| `secondary_prevention[]` | `scenario_prevention_items.value` where `type='secondary'` | 1:N |
| `screening` | `scenario_prevention.screening` | 1:1 |
| `vaccination` | `scenario_prevention.vaccination` | 1:1 |
| `source_reference` | `scenario_prevention.source_reference` | 1:1 |

`scenario_prevention_items.type` is CHECK-constrained to
`primary | secondary` (mirrored as `PreventionItemType`).

## 10. `non_pharmacological_treatment`

| Template field | Table.column | Cardinality |
|---|---|---|
| `lifestyle_modifications[]` | `scenario_non_pharma_items.value` where `type='lifestyle'` | 1:N |
| `dietary_changes[]` | `scenario_non_pharma_items.value` where `type='dietary'` | 1:N |
| `other[]` | `scenario_non_pharma_items.value` where `type='other'` | 1:N |
| `physical_activity` | `scenario_non_pharma_treatment.physical_activity` | 1:1 |
| `source_reference` | `scenario_non_pharma_treatment.source_reference` | 1:1 |

`scenario_non_pharma_items.type` is CHECK-constrained to
`lifestyle | dietary | other` (mirrored as `NonPharmaItemType`).

## 11. `pharmacological_treatment`

| Template field | Table.column | Cardinality |
|---|---|---|
| `first_line[]` (each `{drug_class, drug_name, dose, route, duration, notes}`) | `scenario_pharma_drug_items.*` where `line='first_line'` | 1:N |
| `second_line[]` | `scenario_pharma_drug_items.*` where `line='second_line'` | 1:N |
| `adjunct_therapy[]` | `scenario_pharma_drug_items.*` where `line='adjunct_therapy'` | 1:N |
| `drugs_to_avoid[]` | `scenario_drugs_to_avoid_items.value` | 1:N |
| `source_reference` | `scenario_pharma_treatment.source_reference` | 1:1 |

`scenario_pharma_drug_items.line` is CHECK-constrained to
`first_line | second_line | adjunct_therapy` (mirrored as `PharmaDrugLine`).
`drug_class`, `drug_name`, `dose`, `route`, `duration`, `notes` map 1:1 to
the identically-named columns on `scenario_pharma_drug_items`.

## 12. `interventional_treatment[]`

| Template field | Table.column | Cardinality |
|---|---|---|
| `procedure` | `scenario_interventional_treatments.procedure` | 1:N |
| `indication` | `scenario_interventional_treatments.indication` | 1:N |
| `timing` | `scenario_interventional_treatments.timing` | 1:N |
| `notes` | `scenario_interventional_treatments.notes` | 1:N |

`timing` here is free text (unlike `complications.timing`), matching the
template's own field, which carries no enum.

## Fields intentionally not mapped

`disease_name`, `source`, `chapter`, `pages`, `extraction_notes`, and
`missing_fields` are template/import metadata, not clinical content -- they
have no corresponding column. `scenarios.name` already plays the role of
`disease_name`.

## `case_text` composition

`scenarios.case_text` and `scenarios.gold_standard` keep their existing
meaning and shape everywhere they're used (LLM prompt context, evaluation
input) -- no endpoint or DTO changes. What changed is only *where the string
comes from*: `SqlScenarioRepository` (in
[`app/infrastructure/db/repositories/scenario_repository.py`](../app/infrastructure/db/repositories/scenario_repository.py))
eagerly loads every relationship above and, on read, calls
`compose_case_text()` (in
[`app/infrastructure/db/scenario_case_text.py`](../app/infrastructure/db/scenario_case_text.py))
to build a human-readable case description from whatever normalized data
exists. If a scenario has no rows in any of the 21 tables above (e.g. it was
seeded via the legacy `seed_data/scenarios.json` importer or the `.docx`
importer, which only ever write the plain `case_text` column), the stored
column is returned unchanged.

The patient-case layer below plays no part in this composition -- it is
never folded into `case_text`, regardless of whether a scenario has one.

## Patient-case simulation layer

Added by
[`alembic/versions/0006_patient_case_simulation.py`](../alembic/versions/0006_patient_case_simulation.py)
and modeled in
[`app/infrastructure/db/models.py`](../app/infrastructure/db/models.py).
16 tables under `scenarios`, same two shapes as the disease-reference layer
above (1:1 header tables keyed on `scenario_id`; 1:N item tables with an
auto-increment `id` + indexed `scenario_id` FK), every FK `ondelete=CASCADE`.

Unlike the disease-reference layer, this one has its own domain entity
(`PatientCase`, in
[`app/domain/entities.py`](../app/domain/entities.py)) and repository
(`PatientCaseRepository` / `SqlPatientCaseRepository`, wired into
`SqlAlchemyUnitOfWork.patient_cases` -- see
[`app/infrastructure/db/repositories/patient_case_repository.py`](../app/infrastructure/db/repositories/patient_case_repository.py)),
so it is read and written as a structured aggregate rather than composed
into a string. `PatientCaseRepository.create()` writes every section in one
transaction; `get()` returns `None` for a scenario with no patient-case data
at all (it never falls back to anything, unlike `ScenarioRepository.get()`).

| Table | Cardinality | Columns | Notes |
|---|---|---|---|
| `case_patient_identity` | 1:1 | `name`, `age`, `sex`, `occupation`, `nationality`, `marital_status` | `sex` CHECK `male \| female` (mirrored as `PatientSex`, reused from the disease-reference layer's `ScenarioPatientProfileModel.sex`) |
| `case_presenting_complaint` | 1:1 | `chief_complaint`, `hpi_narrative` | |
| `case_associated_symptoms` | 1:N | `symptom`, `is_present` (NOT NULL) | Records pertinent negatives, not just positives |
| `case_past_medical_history` | 1:N | `item`, `note` | |
| `case_current_medications` | 1:N | `drug_name`, `dose`, `note` | |
| `case_triggers` | 1:N | `trigger`, `is_primary` (default `false`) | |
| `case_family_social_history` | 1:N | `category`, `item` | `category` CHECK `family \| social` (`FamilySocialCategory`) |
| `case_vital_signs` | 1:N | `parameter`, `value`, `interpretation` | |
| `case_physical_exam_findings` | 1:N | `method`, `finding` | `method` CHECK `inspection \| palpation \| percussion \| auscultation` -- reuses `ClinicalSignCategory` from the disease-reference layer (same value set) |
| `case_investigations` | 1:N | `category`, `test_name`, `result`, `interpretation` | `category` CHECK `immediate \| laboratory \| imaging` (`CaseInvestigationCategory` -- distinct from the disease-reference layer's `InvestigationPriority`, which is `essential \| confirmatory \| optional`); this table records what was actually done/found for this patient, unlike `scenario_lab_investigations`/`scenario_radiological_investigations`, which describe what's typically expected for the disease |
| `case_severity_criteria` | 1:N | `criterion`, `patient_value`, `classification` | This patient's actual values against a severity score, e.g. Alvarado/CURB-65 |
| `case_warning_signs` | 1:N | `sign`, `is_present` (nullable) | `is_present` is nullable, unlike `case_associated_symptoms.is_present` -- `NULL` means not assessed in this vignette, distinct from an explicit "absent" |
| `case_management_phases` | 1:N | `phase`, `treatment`, `dose_route`, `goal`, `sequence_order` (NOT NULL) | `phase` CHECK `immediate \| monitoring \| disposition` (`ManagementPhase`); `sequence_order` orders items within the same phase |
| `case_disposition_criteria` | 1:N | `type`, `criterion` | `type` CHECK `admission \| discharge` (`DispositionType`) |
| `case_discharge_plan` | 1:N | `category`, `detail` | `category` CHECK `medication \| education \| follow_up \| referral` (`DischargePlanCategory`) |
| `case_learning_objectives` | 1:N | `objective_number` (NOT NULL), `objective_text` | |

Import path: `scripts/import_patient_case_from_json.py` (parallel to, but
independent of, `scripts/import_scenario_from_json.py` for the
disease-reference layer -- see that script's own docstring for its JSON
shape and usage).
