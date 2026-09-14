"""SQLModel table models -- the actual ORM/database schema.

These mirror the schema created by the Alembic migrations under alembic/versions/.
Migrations are the source of truth for the schema (there is no create_all()
anywhere in this project); these classes exist only so the repositories in
app/infrastructure/db/repositories/ have something to read/write at runtime.

Do not import these (or anything from sqlmodel/sqlalchemy) outside of
app/infrastructure -- domain and application code only ever see
app/domain/entities.py.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy.orm import relationship
from sqlmodel import Field, Relationship, SQLModel

# --- Enums for the scenario clinical-section child tables ------------------
#
# Each mirrors a CHECK constraint on the corresponding VARCHAR column, added
# in the alembic migration the same way messages.role is (see ck_messages_role
# in alembic/versions/0001_initial_schema.py). Member name == member value so
# the string stored in the DB and the enum's Python value are identical.


class RiskFactorType(str, Enum):
    modifiable = "modifiable"
    non_modifiable = "non_modifiable"


class SymptomItemType(str, Enum):
    cardinal = "cardinal"
    other = "other"


class ClinicalSignCategory(str, Enum):
    inspection = "inspection"
    palpation = "palpation"
    percussion = "percussion"
    auscultation = "auscultation"


class InvestigationPriority(str, Enum):
    essential = "essential"
    confirmatory = "confirmatory"
    optional = "optional"


class ComplicationTiming(str, Enum):
    acute = "acute"
    subacute = "subacute"
    chronic = "chronic"


class ComplicationFrequency(str, Enum):
    common = "common"
    uncommon = "uncommon"
    rare = "rare"


class PreventionItemType(str, Enum):
    primary = "primary"
    secondary = "secondary"


class NonPharmaItemType(str, Enum):
    lifestyle = "lifestyle"
    dietary = "dietary"
    other = "other"


class PharmaDrugLine(str, Enum):
    first_line = "first_line"
    second_line = "second_line"
    adjunct_therapy = "adjunct_therapy"


class PatientSex(str, Enum):
    male = "male"
    female = "female"


# --- Enums for the patient-case simulation child tables (migration 0006) ---
#
# Same convention as above: mirrors a CHECK constraint on the corresponding
# VARCHAR column (see ck_messages_role). PatientSex and ClinicalSignCategory
# above are reused where a patient-case column shares the exact same value
# set (case_patient_identity.sex, case_physical_exam_findings.method) rather
# than redeclaring an identical enum under a new name.


class FamilySocialCategory(str, Enum):
    family = "family"
    social = "social"


class CaseInvestigationCategory(str, Enum):
    immediate = "immediate"
    laboratory = "laboratory"
    imaging = "imaging"


class ManagementPhase(str, Enum):
    immediate = "immediate"
    monitoring = "monitoring"
    disposition = "disposition"


class DispositionType(str, Enum):
    admission = "admission"
    discharge = "discharge"


class DischargePlanCategory(str, Enum):
    medication = "medication"
    education = "education"
    follow_up = "follow_up"
    referral = "referral"


class QuestionCategory(str, Enum):
    """Tags a question as belonging to a scenario's post-session MCQ quiz
    (migration 0007) -- disease name, attack-severity classification, and
    management plan across its three stages. QuestionModel.category is
    nullable; an ordinary OSCE question (list_by_scenario's original
    audience) simply has no category at all, distinct from any of these."""

    diagnosis = "diagnosis"
    severity = "severity"
    management_immediate = "management_immediate"
    management_monitoring = "management_monitoring"
    management_disposition = "management_disposition"


class TestCategoryModel(SQLModel, table=True):
    __tablename__ = "test_categories"

    id: int | None = Field(default=None, primary_key=True)
    name: str


class TestModel(SQLModel, table=True):
    __tablename__ = "tests"

    id: int | None = Field(default=None, primary_key=True)
    category_id: int = Field(foreign_key="test_categories.id", index=True)
    name: str
    result: str


class ScenarioModel(SQLModel, table=True):
    __tablename__ = "scenarios"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    case_text: str
    gold_standard: str | None = None

    # Normalized clinical-section child rows (see scenario_case_text.py for how
    # these compose into case_text). Every one of these is optional: a scenario
    # may have data for only some of the 12 template sections, or none at all
    # (in which case the stored case_text above is used as-is).
    #
    # Built via sa_relationship=relationship(...) (the class passed as a plain
    # string, resolved by SQLAlchemy's own class registry) rather than through
    # SQLModel's usual annotation-based Relationship(back_populates=...): this
    # module has `from __future__ import annotations`, which turns annotations
    # like `list[ScenarioXModel]` or `ScenarioXModel | None` into plain strings
    # before SQLModel ever sees them, and SQLModel's annotation resolver (as of
    # 0.0.22) cannot parse those generic forms back out -- only a bare class
    # name string works. Going through sa_relationship directly sidesteps that
    # entirely, at the cost of restating the target class name as a string.
    #
    # Every list-typed (1:N) relationship below also passes collection_class=
    # list explicitly. Without it, SQLAlchemy's own declarative annotation
    # scan -- which still runs against the raw (stringified, un-Mapped)
    # `list[ScenarioXModel]` annotation left behind on the class even though
    # sa_relationship bypasses SQLModel's handling of it -- can't tell it's a
    # collection from that opaque string, so it silently resets uselist back
    # to False regardless of the uselist=True passed here. Giving it an
    # explicit collection_class short-circuits that scan.
    pathophysiology: ScenarioPathophysiologyModel = Relationship(
        sa_relationship=relationship(
            "ScenarioPathophysiologyModel",
            back_populates="scenario",
            uselist=False,
            cascade="all, delete-orphan",
        )
    )
    risk_factors: ScenarioRiskFactorsModel = Relationship(
        sa_relationship=relationship(
            "ScenarioRiskFactorsModel", back_populates="scenario", uselist=False, cascade="all, delete-orphan"
        )
    )
    risk_factor_items: list[ScenarioRiskFactorItemModel] = Relationship(
        sa_relationship=relationship(
            "ScenarioRiskFactorItemModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    symptoms_signs: ScenarioSymptomsSignsModel = Relationship(
        sa_relationship=relationship(
            "ScenarioSymptomsSignsModel", back_populates="scenario", uselist=False, cascade="all, delete-orphan"
        )
    )
    symptom_items: list[ScenarioSymptomItemModel] = Relationship(
        sa_relationship=relationship(
            "ScenarioSymptomItemModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    clinical_sign_items: list[ScenarioClinicalSignItemModel] = Relationship(
        sa_relationship=relationship(
            "ScenarioClinicalSignItemModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    atypical_presentation_items: list[ScenarioAtypicalPresentationItemModel] = Relationship(
        sa_relationship=relationship(
            "ScenarioAtypicalPresentationItemModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    lab_investigations: list[ScenarioLabInvestigationModel] = Relationship(
        sa_relationship=relationship(
            "ScenarioLabInvestigationModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    radiological_investigations: list[ScenarioRadiologicalInvestigationModel] = Relationship(
        sa_relationship=relationship(
            "ScenarioRadiologicalInvestigationModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    differential_diagnoses: list[ScenarioDifferentialDiagnosisModel] = Relationship(
        sa_relationship=relationship(
            "ScenarioDifferentialDiagnosisModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    diagnostic_criteria: ScenarioDiagnosticCriteriaModel = Relationship(
        sa_relationship=relationship(
            "ScenarioDiagnosticCriteriaModel",
            back_populates="scenario",
            uselist=False,
            cascade="all, delete-orphan",
        )
    )
    diagnostic_criteria_items: list[ScenarioDiagnosticCriteriaItemModel] = Relationship(
        sa_relationship=relationship(
            "ScenarioDiagnosticCriteriaItemModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    complications: list[ScenarioComplicationModel] = Relationship(
        sa_relationship=relationship(
            "ScenarioComplicationModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    prevention: ScenarioPreventionModel = Relationship(
        sa_relationship=relationship(
            "ScenarioPreventionModel", back_populates="scenario", uselist=False, cascade="all, delete-orphan"
        )
    )
    prevention_items: list[ScenarioPreventionItemModel] = Relationship(
        sa_relationship=relationship(
            "ScenarioPreventionItemModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    non_pharma_treatment: ScenarioNonPharmaTreatmentModel = Relationship(
        sa_relationship=relationship(
            "ScenarioNonPharmaTreatmentModel",
            back_populates="scenario",
            uselist=False,
            cascade="all, delete-orphan",
        )
    )
    non_pharma_items: list[ScenarioNonPharmaItemModel] = Relationship(
        sa_relationship=relationship(
            "ScenarioNonPharmaItemModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    pharma_treatment: ScenarioPharmaTreatmentModel = Relationship(
        sa_relationship=relationship(
            "ScenarioPharmaTreatmentModel", back_populates="scenario", uselist=False, cascade="all, delete-orphan"
        )
    )
    pharma_drug_items: list[ScenarioPharmaDrugItemModel] = Relationship(
        sa_relationship=relationship(
            "ScenarioPharmaDrugItemModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    drugs_to_avoid_items: list[ScenarioDrugsToAvoidItemModel] = Relationship(
        sa_relationship=relationship(
            "ScenarioDrugsToAvoidItemModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    interventional_treatments: list[ScenarioInterventionalTreatmentModel] = Relationship(
        sa_relationship=relationship(
            "ScenarioInterventionalTreatmentModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    # Not one of the 12 template sections -- a derived/default patient profile
    # per scenario (see migration 0003), parsed from section 2's epidemiology
    # text by scripts/import_scenario_from_json.py. Same 1:1-optional shape as
    # the section header tables above.
    patient_profile: ScenarioPatientProfileModel = Relationship(
        sa_relationship=relationship(
            "ScenarioPatientProfileModel", back_populates="scenario", uselist=False, cascade="all, delete-orphan"
        )
    )
    # Not one of the 12 template sections either -- per-scenario overrides of
    # the shared tests.result value (see migration 0004 and OrderTestUseCase
    # in app/application/use_cases/order_test.py). 1:N like the section list
    # tables above, but the "many" side (ScenarioTestResultModel) points at
    # TestModel too, not just at ScenarioModel -- it's a join row between the
    # scenario and the global test catalog, not a normalized piece of this
    # scenario's own clinical narrative.
    test_results: list[ScenarioTestResultModel] = Relationship(
        sa_relationship=relationship(
            "ScenarioTestResultModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    # --- Patient-case simulation child tables (migration 0006) -------------
    # Not one of the 12 disease-reference template sections above -- a
    # separate, parallel layer describing one concrete ER-style patient
    # vignette (identity, HPI, exam findings, management plan, ...) rather
    # than reference facts about the disease in general. See
    # docs/scenario-clinical-schema-mapping.md for how the two layers relate.
    # Same relationship-declaration approach as the disease-reference section
    # above and for the same reason (this module's `from __future__ import
    # annotations` plus SQLModel 0.0.22's annotation resolver not handling
    # generic forms -- see the long comment above pathophysiology).
    patient_identity: CasePatientIdentityModel = Relationship(
        sa_relationship=relationship(
            "CasePatientIdentityModel", back_populates="scenario", uselist=False, cascade="all, delete-orphan"
        )
    )
    presenting_complaint: CasePresentingComplaintModel = Relationship(
        sa_relationship=relationship(
            "CasePresentingComplaintModel", back_populates="scenario", uselist=False, cascade="all, delete-orphan"
        )
    )
    associated_symptoms: list[CaseAssociatedSymptomModel] = Relationship(
        sa_relationship=relationship(
            "CaseAssociatedSymptomModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    past_medical_history: list[CasePastMedicalHistoryModel] = Relationship(
        sa_relationship=relationship(
            "CasePastMedicalHistoryModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    current_medications: list[CaseCurrentMedicationModel] = Relationship(
        sa_relationship=relationship(
            "CaseCurrentMedicationModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    triggers: list[CaseTriggerModel] = Relationship(
        sa_relationship=relationship(
            "CaseTriggerModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    family_social_history: list[CaseFamilySocialHistoryModel] = Relationship(
        sa_relationship=relationship(
            "CaseFamilySocialHistoryModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    vital_signs: list[CaseVitalSignModel] = Relationship(
        sa_relationship=relationship(
            "CaseVitalSignModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    physical_exam_findings: list[CasePhysicalExamFindingModel] = Relationship(
        sa_relationship=relationship(
            "CasePhysicalExamFindingModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    case_investigations: list[CaseInvestigationModel] = Relationship(
        sa_relationship=relationship(
            "CaseInvestigationModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    severity_criteria: list[CaseSeverityCriterionModel] = Relationship(
        sa_relationship=relationship(
            "CaseSeverityCriterionModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    warning_signs: list[CaseWarningSignModel] = Relationship(
        sa_relationship=relationship(
            "CaseWarningSignModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    management_phases: list[CaseManagementPhaseModel] = Relationship(
        sa_relationship=relationship(
            "CaseManagementPhaseModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    disposition_criteria: list[CaseDispositionCriterionModel] = Relationship(
        sa_relationship=relationship(
            "CaseDispositionCriterionModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    discharge_plan: list[CaseDischargePlanModel] = Relationship(
        sa_relationship=relationship(
            "CaseDischargePlanModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )
    learning_objectives: list[CaseLearningObjectiveModel] = Relationship(
        sa_relationship=relationship(
            "CaseLearningObjectiveModel",
            back_populates="scenario",
            uselist=True,
            collection_class=list,
            cascade="all, delete-orphan",
        )
    )


# --- Scenario clinical-section child tables ---------------------------------
#
# One set of tables per numbered section of the reference disease-template
# JSON (see the mapping doc at docs/scenario-clinical-schema-mapping.md).
# Singular sections (1:1 with a scenario) use scenario_id as their own primary
# key -- there is at most one row per scenario, and it doubles as the unique
# FK index required for every child table here. List sections (1:N) use a
# normal auto-incrementing id and an indexed scenario_id FK.
#
# The CHECK constraints backing every *Type/*Priority/*Timing/*Frequency/*Line
# enum column below live in the alembic migration (mirroring
# ck_messages_role in 0001_initial_schema.py) -- not here, since migrations
# are this project's source of truth for the schema.


class ScenarioPathophysiologyModel(SQLModel, table=True):
    """Section 1: pathophysiology. 1:1 with scenarios."""

    __tablename__ = "scenario_pathophysiology"

    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", primary_key=True)
    definition: str | None = None
    mechanism: str | None = None
    classification: str | None = None
    source_reference: str | None = None

    scenario: ScenarioModel = Relationship(back_populates="pathophysiology")


class ScenarioRiskFactorsModel(SQLModel, table=True):
    """Section 2: risk_factors epidemiology + source_reference. 1:1 with scenarios.

    The modifiable/non_modifiable lists themselves live in
    ScenarioRiskFactorItemModel (1:N).
    """

    __tablename__ = "scenario_risk_factors"

    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", primary_key=True)
    age_group: str | None = None
    sex_predominance: str | None = None
    geographical_prevalence: str | None = None
    source_reference: str | None = None

    scenario: ScenarioModel = Relationship(back_populates="risk_factors")


class ScenarioRiskFactorItemModel(SQLModel, table=True):
    """Section 2: risk_factors.modifiable / risk_factors.non_modifiable list items."""

    __tablename__ = "scenario_risk_factor_items"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    type: RiskFactorType
    value: str

    scenario: ScenarioModel = Relationship(back_populates="risk_factor_items")


class ScenarioSymptomsSignsModel(SQLModel, table=True):
    """Section 3: symptoms_and_signs source_reference. 1:1 with scenarios.

    cardinal/other symptoms, clinical signs, and atypical presentations live in
    the three item tables below (all 1:N).
    """

    __tablename__ = "scenario_symptoms_signs"

    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", primary_key=True)
    source_reference: str | None = None

    scenario: ScenarioModel = Relationship(back_populates="symptoms_signs")


class ScenarioSymptomItemModel(SQLModel, table=True):
    """Section 3: symptoms_and_signs.cardinal_symptoms / .other_symptoms list items."""

    __tablename__ = "scenario_symptom_items"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    type: SymptomItemType
    value: str

    scenario: ScenarioModel = Relationship(back_populates="symptom_items")


class ScenarioClinicalSignItemModel(SQLModel, table=True):
    """Section 3: symptoms_and_signs.clinical_signs.{inspection,palpation,percussion,auscultation}."""

    __tablename__ = "scenario_clinical_sign_items"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    category: ClinicalSignCategory
    value: str

    scenario: ScenarioModel = Relationship(back_populates="clinical_sign_items")


class ScenarioAtypicalPresentationItemModel(SQLModel, table=True):
    """Section 3: symptoms_and_signs.atypical_presentations list items."""

    __tablename__ = "scenario_atypical_presentation_items"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    value: str

    scenario: ScenarioModel = Relationship(back_populates="atypical_presentation_items")


class ScenarioLabInvestigationModel(SQLModel, table=True):
    """Section 4: laboratory_investigations list items."""

    __tablename__ = "scenario_lab_investigations"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    test_name: str | None = None
    expected_finding: str | None = None
    clinical_significance: str | None = None
    priority: InvestigationPriority | None = None

    scenario: ScenarioModel = Relationship(back_populates="lab_investigations")


class ScenarioRadiologicalInvestigationModel(SQLModel, table=True):
    """Section 5: radiological_investigations list items."""

    __tablename__ = "scenario_radiological_investigations"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    modality: str | None = None
    expected_finding: str | None = None
    clinical_significance: str | None = None
    priority: InvestigationPriority | None = None

    scenario: ScenarioModel = Relationship(back_populates="radiological_investigations")


class ScenarioDifferentialDiagnosisModel(SQLModel, table=True):
    """Section 6: differential_diagnosis list items."""

    __tablename__ = "scenario_differential_diagnoses"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    condition: str | None = None
    distinguishing_feature: str | None = None

    scenario: ScenarioModel = Relationship(back_populates="differential_diagnoses")


class ScenarioDiagnosticCriteriaModel(SQLModel, table=True):
    """Section 7: diagnostic_criteria header (name/scoring/source). 1:1 with scenarios.

    criteria_list items live in ScenarioDiagnosticCriteriaItemModel (1:N).
    """

    __tablename__ = "scenario_diagnostic_criteria"

    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", primary_key=True)
    criteria_name: str | None = None
    scoring_system: str | None = None
    source_reference: str | None = None

    scenario: ScenarioModel = Relationship(back_populates="diagnostic_criteria")


class ScenarioDiagnosticCriteriaItemModel(SQLModel, table=True):
    """Section 7: diagnostic_criteria.criteria_list list items."""

    __tablename__ = "scenario_diagnostic_criteria_items"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    criterion_text: str

    scenario: ScenarioModel = Relationship(back_populates="diagnostic_criteria_items")


class ScenarioComplicationModel(SQLModel, table=True):
    """Section 8: complications list items."""

    __tablename__ = "scenario_complications"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    complication: str
    timing: ComplicationTiming | None = None
    frequency: ComplicationFrequency | None = None

    scenario: ScenarioModel = Relationship(back_populates="complications")


class ScenarioPreventionModel(SQLModel, table=True):
    """Section 9: prevention screening/vaccination/source. 1:1 with scenarios.

    primary/secondary prevention lists live in ScenarioPreventionItemModel (1:N).
    """

    __tablename__ = "scenario_prevention"

    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", primary_key=True)
    screening: str | None = None
    vaccination: str | None = None
    source_reference: str | None = None

    scenario: ScenarioModel = Relationship(back_populates="prevention")


class ScenarioPreventionItemModel(SQLModel, table=True):
    """Section 9: prevention.primary_prevention / .secondary_prevention list items."""

    __tablename__ = "scenario_prevention_items"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    type: PreventionItemType
    value: str

    scenario: ScenarioModel = Relationship(back_populates="prevention_items")


class ScenarioNonPharmaTreatmentModel(SQLModel, table=True):
    """Section 10: non_pharmacological_treatment physical_activity/source. 1:1 with scenarios.

    lifestyle/dietary/other lists live in ScenarioNonPharmaItemModel (1:N).
    """

    __tablename__ = "scenario_non_pharma_treatment"

    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", primary_key=True)
    physical_activity: str | None = None
    source_reference: str | None = None

    scenario: ScenarioModel = Relationship(back_populates="non_pharma_treatment")


class ScenarioNonPharmaItemModel(SQLModel, table=True):
    """Section 10: non_pharmacological_treatment.{lifestyle_modifications,dietary_changes,other}."""

    __tablename__ = "scenario_non_pharma_items"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    type: NonPharmaItemType
    value: str

    scenario: ScenarioModel = Relationship(back_populates="non_pharma_items")


class ScenarioPharmaTreatmentModel(SQLModel, table=True):
    """Section 11: pharmacological_treatment source_reference. 1:1 with scenarios.

    first_line/second_line/adjunct_therapy drugs live in ScenarioPharmaDrugItemModel
    (1:N); drugs_to_avoid lives in ScenarioDrugsToAvoidItemModel (1:N).
    """

    __tablename__ = "scenario_pharma_treatment"

    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", primary_key=True)
    source_reference: str | None = None

    scenario: ScenarioModel = Relationship(back_populates="pharma_treatment")


class ScenarioPharmaDrugItemModel(SQLModel, table=True):
    """Section 11: pharmacological_treatment.{first_line,second_line,adjunct_therapy} drug rows."""

    __tablename__ = "scenario_pharma_drug_items"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    line: PharmaDrugLine
    drug_class: str | None = None
    drug_name: str | None = None
    dose: str | None = None
    route: str | None = None
    duration: str | None = None
    notes: str | None = None

    scenario: ScenarioModel = Relationship(back_populates="pharma_drug_items")


class ScenarioDrugsToAvoidItemModel(SQLModel, table=True):
    """Section 11: pharmacological_treatment.drugs_to_avoid list items."""

    __tablename__ = "scenario_drugs_to_avoid_items"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    value: str

    scenario: ScenarioModel = Relationship(back_populates="drugs_to_avoid_items")


class ScenarioInterventionalTreatmentModel(SQLModel, table=True):
    """Section 12: interventional_treatment list items."""

    __tablename__ = "scenario_interventional_treatments"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    procedure: str | None = None
    indication: str | None = None
    timing: str | None = None
    notes: str | None = None

    scenario: ScenarioModel = Relationship(back_populates="interventional_treatments")


class ScenarioPatientProfileModel(SQLModel, table=True):
    """A derived/default virtual patient profile per scenario. 1:1 with scenarios.

    Not one of the 12 template sections -- parsed out of section 2's
    (risk_factors.epidemiology) age_group/sex_predominance free text by
    scripts/import_scenario_from_json.py, added in migration 0003. age/sex
    hold a concrete parsed value when the source text was specific enough;
    otherwise they stay NULL and the *_raw column keeps the original text for
    manual review.
    """

    __tablename__ = "scenario_patient_profile"

    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", primary_key=True)
    age: int | None = None
    age_group_raw: str | None = None
    sex: PatientSex | None = None
    sex_predominance_raw: str | None = None
    geographical_context: str | None = None

    scenario: ScenarioModel = Relationship(back_populates="patient_profile")


class ScenarioTestResultModel(SQLModel, table=True):
    """A scenario-specific override of tests.result (migration 0004).

    The global test catalog (TestCategoryModel/TestModel, defined above) is
    shared across every scenario -- the same "Abdominal ultrasound" row is
    orderable regardless of which case is active, which is clinically
    correct (the same test exists everywhere). What differs per case is the
    *finding* it returns. This table holds that per-(scenario, test) finding;
    OrderTestUseCase (app/application/use_cases/order_test.py) looks here
    first and falls back to the generic TestModel.result -- logging a warning
    when it does, since a scenario missing an override for a test it plausibly
    needs is a data gap worth noticing in dev/test output, not a silent one.

    Unlike every table in the "Scenario clinical-section child tables"
    section below, this is not a normalized piece of this scenario's own
    narrative -- it's a join row between a scenario and the pre-existing,
    scenario-independent test catalog, so it relates to TestModel too, not
    only to ScenarioModel.
    """

    __tablename__ = "scenario_test_results"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    test_id: int = Field(foreign_key="tests.id", index=True)
    result: str

    scenario: ScenarioModel = Relationship(back_populates="test_results")
    test: TestModel = Relationship(sa_relationship=relationship("TestModel"))


# --- Patient-case simulation child tables (migration 0006) ------------------
#
# A second, parallel layer under `scenarios`: one concrete ER-style patient
# vignette (a specific patient's identity, history, exam findings, and
# management plan) rather than reference facts about a disease in general.
# See docs/scenario-clinical-schema-mapping.md for how this relates to the
# 12-section disease-reference layer above. Same two shapes as that layer:
# 1:1 header tables (PK = scenario_id) for singular fields, 1:N item tables
# (auto-increment id + indexed scenario_id FK) for repeating fields. CHECK
# constraints backing every enum column below live in the alembic migration
# (0006_patient_case_simulation.py), mirroring ck_messages_role.


class CasePatientIdentityModel(SQLModel, table=True):
    """Patient identity/demographics for one case vignette. 1:1 with scenarios."""

    __tablename__ = "case_patient_identity"

    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", primary_key=True)
    name: str | None = None
    age: int | None = None
    sex: PatientSex | None = None
    occupation: str | None = None
    nationality: str | None = None
    marital_status: str | None = None

    scenario: ScenarioModel = Relationship(back_populates="patient_identity")


class CasePresentingComplaintModel(SQLModel, table=True):
    """Chief complaint + history-of-presenting-illness narrative. 1:1 with scenarios."""

    __tablename__ = "case_presenting_complaint"

    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", primary_key=True)
    chief_complaint: str | None = None
    hpi_narrative: str | None = None

    scenario: ScenarioModel = Relationship(back_populates="presenting_complaint")


class CaseAssociatedSymptomModel(SQLModel, table=True):
    """Symptoms explicitly present or explicitly absent (pertinent negatives)."""

    __tablename__ = "case_associated_symptoms"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    symptom: str
    is_present: bool

    scenario: ScenarioModel = Relationship(back_populates="associated_symptoms")


class CasePastMedicalHistoryModel(SQLModel, table=True):
    """Past medical history list items."""

    __tablename__ = "case_past_medical_history"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    item: str
    note: str | None = None

    scenario: ScenarioModel = Relationship(back_populates="past_medical_history")


class CaseCurrentMedicationModel(SQLModel, table=True):
    """Medications the patient is already taking at presentation."""

    __tablename__ = "case_current_medications"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    drug_name: str
    dose: str
    note: str | None = None

    scenario: ScenarioModel = Relationship(back_populates="current_medications")


class CaseTriggerModel(SQLModel, table=True):
    """Precipitating/aggravating triggers for this presentation."""

    __tablename__ = "case_triggers"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    trigger: str
    is_primary: bool = False

    scenario: ScenarioModel = Relationship(back_populates="triggers")


class CaseFamilySocialHistoryModel(SQLModel, table=True):
    """Family history and social history list items, distinguished by `category`."""

    __tablename__ = "case_family_social_history"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    category: FamilySocialCategory
    item: str

    scenario: ScenarioModel = Relationship(back_populates="family_social_history")


class CaseVitalSignModel(SQLModel, table=True):
    """One vital-sign reading (parameter/value pair) at presentation."""

    __tablename__ = "case_vital_signs"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    parameter: str
    value: str
    interpretation: str | None = None

    scenario: ScenarioModel = Relationship(back_populates="vital_signs")


class CasePhysicalExamFindingModel(SQLModel, table=True):
    """One physical-exam finding, grouped by examination `method`."""

    __tablename__ = "case_physical_exam_findings"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    method: ClinicalSignCategory
    finding: str

    scenario: ScenarioModel = Relationship(back_populates="physical_exam_findings")


class CaseInvestigationModel(SQLModel, table=True):
    """One investigation actually performed/resulted for this patient (as
    opposed to ScenarioLabInvestigationModel/ScenarioRadiologicalInvestigationModel,
    which describe what's expected for the disease in general)."""

    __tablename__ = "case_investigations"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    category: CaseInvestigationCategory
    test_name: str
    result: str
    interpretation: str | None = None

    scenario: ScenarioModel = Relationship(back_populates="case_investigations")


class CaseSeverityCriterionModel(SQLModel, table=True):
    """One severity-scoring criterion applied to this patient's actual values."""

    __tablename__ = "case_severity_criteria"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    criterion: str
    patient_value: str
    classification: str

    scenario: ScenarioModel = Relationship(back_populates="severity_criteria")


class CaseWarningSignModel(SQLModel, table=True):
    """A red-flag/warning sign, present, absent, or not assessed (`is_present`
    is nullable -- unlike CaseAssociatedSymptomModel.is_present, which is
    always recorded true/false, a warning sign may simply not have been
    checked for in this vignette)."""

    __tablename__ = "case_warning_signs"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    sign: str
    is_present: bool | None = None

    scenario: ScenarioModel = Relationship(back_populates="warning_signs")


class CaseManagementPhaseModel(SQLModel, table=True):
    """One management step, grouped by `phase` and ordered by `sequence_order`
    within that phase."""

    __tablename__ = "case_management_phases"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    phase: ManagementPhase
    treatment: str
    dose_route: str | None = None
    goal: str | None = None
    sequence_order: int

    scenario: ScenarioModel = Relationship(back_populates="management_phases")


class CaseDispositionCriterionModel(SQLModel, table=True):
    """One admission-vs-discharge disposition criterion."""

    __tablename__ = "case_disposition_criteria"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    type: DispositionType
    criterion: str

    scenario: ScenarioModel = Relationship(back_populates="disposition_criteria")


class CaseDischargePlanModel(SQLModel, table=True):
    """One discharge-plan item, grouped by `category`."""

    __tablename__ = "case_discharge_plan"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    category: DischargePlanCategory
    detail: str

    scenario: ScenarioModel = Relationship(back_populates="discharge_plan")


class CaseLearningObjectiveModel(SQLModel, table=True):
    """One numbered learning objective for this case vignette."""

    __tablename__ = "case_learning_objectives"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", ondelete="CASCADE", index=True)
    objective_number: int
    objective_text: str

    scenario: ScenarioModel = Relationship(back_populates="learning_objectives")


class QuestionModel(SQLModel, table=True):
    __tablename__ = "questions"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", index=True)
    text: str
    # Soft reference to choices.id (the correct answer). Deliberately NOT a DB-level
    # foreign key: choices.question_id already points at questions.id, so a hard FK
    # here would create a circular dependency between the two tables. Integrity is
    # enforced in the application layer (AnswerQuestionUseCase validates choice
    # membership) and by the seed script (which resolves this only after choices
    # exist).
    correct_choice_id: int
    # NULL for an ordinary OSCE question (migration 0007's default, and every
    # row written before it) -- set only for the small subset of a scenario's
    # questions that make up its post-session quiz. See QuestionCategory above
    # and GetPostSessionQuizUseCase.
    category: QuestionCategory | None = None


class ChoiceModel(SQLModel, table=True):
    __tablename__ = "choices"

    id: int | None = Field(default=None, primary_key=True)
    question_id: int = Field(foreign_key="questions.id", index=True)
    text: str


class SessionModel(SQLModel, table=True):
    __tablename__ = "sessions"

    id: str = Field(primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", index=True)
    created_at: datetime


class MessageModel(SQLModel, table=True):
    """`status` (migration 0005) is 'complete' for every message the
    synchronous PatientReplyGenerator backends create; 'pending'/'generating'/
    'failed' only ever appear on assistant rows created by the async
    reply-generation path (see PostMessageAsyncUseCase). The CHECK constraint
    backing this (ck_messages_status) lives in the migration, mirroring
    ck_messages_role."""

    __tablename__ = "messages"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="sessions.id", index=True)
    role: str
    content: str
    created_at: datetime
    status: str = "complete"


class OrderedTestModel(SQLModel, table=True):
    __tablename__ = "ordered_tests"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="sessions.id", index=True)
    test_id: int = Field(foreign_key="tests.id")
    ordered_at: datetime


class AnswerModel(SQLModel, table=True):
    __tablename__ = "answers"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="sessions.id", index=True)
    question_id: int = Field(foreign_key="questions.id")
    choice_id: int = Field(foreign_key="choices.id")
    is_correct: bool
    answered_at: datetime
