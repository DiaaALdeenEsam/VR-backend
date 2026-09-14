from __future__ import annotations

from sqlalchemy.orm import selectinload
from sqlmodel.ext.asyncio.session import AsyncSession

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
from app.domain.repositories import PatientCaseRepository
from app.infrastructure.db.models import (
    CaseAssociatedSymptomModel,
    CaseCurrentMedicationModel,
    CaseDischargePlanModel,
    CaseDispositionCriterionModel,
    CaseFamilySocialHistoryModel,
    CaseInvestigationModel,
    CaseLearningObjectiveModel,
    CaseManagementPhaseModel,
    CasePastMedicalHistoryModel,
    CasePatientIdentityModel,
    CasePhysicalExamFindingModel,
    CasePresentingComplaintModel,
    CaseSeverityCriterionModel,
    CaseTriggerModel,
    CaseVitalSignModel,
    CaseWarningSignModel,
    ScenarioModel,
)

# Eagerly loads every patient-case relationship, the same way
# scenario_repository._CLINICAL_SECTION_OPTIONS does for the disease-
# reference layer -- keeps get() to one extra SELECT per relationship
# rather than triggering lazy I/O against a possibly-closed session.
_PATIENT_CASE_OPTIONS = [
    selectinload(ScenarioModel.patient_identity),
    selectinload(ScenarioModel.presenting_complaint),
    selectinload(ScenarioModel.associated_symptoms),
    selectinload(ScenarioModel.past_medical_history),
    selectinload(ScenarioModel.current_medications),
    selectinload(ScenarioModel.triggers),
    selectinload(ScenarioModel.family_social_history),
    selectinload(ScenarioModel.vital_signs),
    selectinload(ScenarioModel.physical_exam_findings),
    selectinload(ScenarioModel.case_investigations),
    selectinload(ScenarioModel.severity_criteria),
    selectinload(ScenarioModel.warning_signs),
    selectinload(ScenarioModel.management_phases),
    selectinload(ScenarioModel.disposition_criteria),
    selectinload(ScenarioModel.discharge_plan),
    selectinload(ScenarioModel.learning_objectives),
]


def _has_patient_case_data(row: ScenarioModel) -> bool:
    return any(
        [
            row.patient_identity is not None,
            row.presenting_complaint is not None,
            bool(row.associated_symptoms),
            bool(row.past_medical_history),
            bool(row.current_medications),
            bool(row.triggers),
            bool(row.family_social_history),
            bool(row.vital_signs),
            bool(row.physical_exam_findings),
            bool(row.case_investigations),
            bool(row.severity_criteria),
            bool(row.warning_signs),
            bool(row.management_phases),
            bool(row.disposition_criteria),
            bool(row.discharge_plan),
            bool(row.learning_objectives),
        ]
    )


def _to_entity(row: ScenarioModel) -> PatientCase:
    assert row.id is not None
    return PatientCase(
        scenario_id=row.id,
        identity=(
            PatientIdentity(
                name=row.patient_identity.name,
                age=row.patient_identity.age,
                sex=row.patient_identity.sex.value if row.patient_identity.sex else None,
                occupation=row.patient_identity.occupation,
                nationality=row.patient_identity.nationality,
                marital_status=row.patient_identity.marital_status,
            )
            if row.patient_identity is not None
            else None
        ),
        presenting_complaint=(
            PresentingComplaint(
                chief_complaint=row.presenting_complaint.chief_complaint,
                hpi_narrative=row.presenting_complaint.hpi_narrative,
            )
            if row.presenting_complaint is not None
            else None
        ),
        associated_symptoms=[
            AssociatedSymptom(symptom=i.symptom, is_present=i.is_present) for i in row.associated_symptoms
        ],
        past_medical_history=[
            PastMedicalHistoryItem(item=i.item, note=i.note) for i in row.past_medical_history
        ],
        current_medications=[
            CurrentMedication(drug_name=i.drug_name, dose=i.dose, note=i.note) for i in row.current_medications
        ],
        triggers=[Trigger(trigger=i.trigger, is_primary=i.is_primary) for i in row.triggers],
        family_social_history=[
            FamilySocialHistoryItem(category=i.category.value, item=i.item) for i in row.family_social_history
        ],
        vital_signs=[
            VitalSign(parameter=i.parameter, value=i.value, interpretation=i.interpretation)
            for i in row.vital_signs
        ],
        physical_exam_findings=[
            PhysicalExamFinding(method=i.method.value, finding=i.finding) for i in row.physical_exam_findings
        ],
        investigations=[
            CaseInvestigation(
                category=i.category.value, test_name=i.test_name, result=i.result, interpretation=i.interpretation
            )
            for i in row.case_investigations
        ],
        severity_criteria=[
            SeverityCriterion(criterion=i.criterion, patient_value=i.patient_value, classification=i.classification)
            for i in row.severity_criteria
        ],
        warning_signs=[WarningSign(sign=i.sign, is_present=i.is_present) for i in row.warning_signs],
        management_phases=[
            ManagementPhaseItem(
                phase=i.phase.value,
                treatment=i.treatment,
                sequence_order=i.sequence_order,
                dose_route=i.dose_route,
                goal=i.goal,
            )
            for i in row.management_phases
        ],
        disposition_criteria=[
            DispositionCriterion(type=i.type.value, criterion=i.criterion) for i in row.disposition_criteria
        ],
        discharge_plan=[DischargePlanItem(category=i.category.value, detail=i.detail) for i in row.discharge_plan],
        learning_objectives=[
            LearningObjective(objective_number=i.objective_number, objective_text=i.objective_text)
            for i in row.learning_objectives
        ],
    )


class SqlPatientCaseRepository(PatientCaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, case: PatientCase) -> PatientCase:
        """Adds every section of `case` to the session and flushes once.

        Does not commit -- that's the surrounding AbstractUnitOfWork's job
        (see SqlAlchemyUnitOfWork), so this create() participates in
        whatever transaction the caller's `async with uow:` block owns.
        """

        scenario_id = case.scenario_id

        if case.identity is not None:
            self._session.add(
                CasePatientIdentityModel(
                    scenario_id=scenario_id,
                    name=case.identity.name,
                    age=case.identity.age,
                    sex=case.identity.sex,
                    occupation=case.identity.occupation,
                    nationality=case.identity.nationality,
                    marital_status=case.identity.marital_status,
                )
            )

        if case.presenting_complaint is not None:
            self._session.add(
                CasePresentingComplaintModel(
                    scenario_id=scenario_id,
                    chief_complaint=case.presenting_complaint.chief_complaint,
                    hpi_narrative=case.presenting_complaint.hpi_narrative,
                )
            )

        for s in case.associated_symptoms:
            self._session.add(
                CaseAssociatedSymptomModel(scenario_id=scenario_id, symptom=s.symptom, is_present=s.is_present)
            )

        for h in case.past_medical_history:
            self._session.add(CasePastMedicalHistoryModel(scenario_id=scenario_id, item=h.item, note=h.note))

        for m in case.current_medications:
            self._session.add(
                CaseCurrentMedicationModel(
                    scenario_id=scenario_id, drug_name=m.drug_name, dose=m.dose, note=m.note
                )
            )

        for t in case.triggers:
            self._session.add(
                CaseTriggerModel(scenario_id=scenario_id, trigger=t.trigger, is_primary=t.is_primary)
            )

        for f in case.family_social_history:
            self._session.add(
                CaseFamilySocialHistoryModel(scenario_id=scenario_id, category=f.category, item=f.item)
            )

        for v in case.vital_signs:
            self._session.add(
                CaseVitalSignModel(
                    scenario_id=scenario_id, parameter=v.parameter, value=v.value, interpretation=v.interpretation
                )
            )

        for p in case.physical_exam_findings:
            self._session.add(
                CasePhysicalExamFindingModel(scenario_id=scenario_id, method=p.method, finding=p.finding)
            )

        for inv in case.investigations:
            self._session.add(
                CaseInvestigationModel(
                    scenario_id=scenario_id,
                    category=inv.category,
                    test_name=inv.test_name,
                    result=inv.result,
                    interpretation=inv.interpretation,
                )
            )

        for c in case.severity_criteria:
            self._session.add(
                CaseSeverityCriterionModel(
                    scenario_id=scenario_id,
                    criterion=c.criterion,
                    patient_value=c.patient_value,
                    classification=c.classification,
                )
            )

        for w in case.warning_signs:
            self._session.add(CaseWarningSignModel(scenario_id=scenario_id, sign=w.sign, is_present=w.is_present))

        for phase in case.management_phases:
            self._session.add(
                CaseManagementPhaseModel(
                    scenario_id=scenario_id,
                    phase=phase.phase,
                    treatment=phase.treatment,
                    dose_route=phase.dose_route,
                    goal=phase.goal,
                    sequence_order=phase.sequence_order,
                )
            )

        for d in case.disposition_criteria:
            self._session.add(
                CaseDispositionCriterionModel(scenario_id=scenario_id, type=d.type, criterion=d.criterion)
            )

        for d in case.discharge_plan:
            self._session.add(
                CaseDischargePlanModel(scenario_id=scenario_id, category=d.category, detail=d.detail)
            )

        for o in case.learning_objectives:
            self._session.add(
                CaseLearningObjectiveModel(
                    scenario_id=scenario_id, objective_number=o.objective_number, objective_text=o.objective_text
                )
            )

        await self._session.flush()
        return case

    async def get(self, scenario_id: int) -> PatientCase | None:
        row = await self._session.get(ScenarioModel, scenario_id, options=_PATIENT_CASE_OPTIONS)
        if row is None or not _has_patient_case_data(row):
            return None
        return _to_entity(row)
