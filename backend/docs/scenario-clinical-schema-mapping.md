# Scenario clinical schema: template JSON → database mapping

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
