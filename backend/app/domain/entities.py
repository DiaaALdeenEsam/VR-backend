"""Plain domain entities.

These are pure Python objects with no dependency on SQLModel, SQLAlchemy, Pydantic,
or any other infrastructure/framework concern. The rest of the domain and application
layers speak only in terms of these types; translation to/from ORM rows happens in
app/infrastructure/db/repositories/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current UTC time, kept timezone-naive for consistent SQLite storage/comparison."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class Scenario:
    id: int
    name: str
    case_text: str
    gold_standard: str | None = None


@dataclass
class TestCategory:
    id: int
    name: str


@dataclass
class Test:
    id: int
    category_id: int
    name: str
    result: str


@dataclass
class Choice:
    id: int
    question_id: int
    text: str


@dataclass
class Question:
    """`category` is None for an ordinary OSCE quiz question (the original
    shape, unchanged) and one of 'diagnosis'/'severity'/'management_immediate'/
    'management_monitoring'/'management_disposition' (see QuestionCategory,
    infra-side enum, migration 0007) for a question that belongs to a
    scenario's post-session multiple-choice quiz -- see
    GetPostSessionQuizUseCase. Plain str, not the infra enum, for the same
    reason PatientIdentity.sex is a plain str: domain entities never depend
    on app.infrastructure.
    """

    id: int
    scenario_id: int
    text: str
    correct_choice_id: int
    choices: list[Choice] = field(default_factory=list)
    category: str | None = None


@dataclass
class Session:
    id: str
    scenario_id: int
    created_at: datetime


@dataclass
class Message:
    """`status` distinguishes an immediately-available reply (still true for
    the "stub" PatientReplyGenerator test double -- see
    app/infrastructure/patient_reply.py) from one generated asynchronously in
    the background by the real (RAG-API-backed) production backend, 6-48s per
    call -- see PostMessageAsyncUseCase in
    app/application/use_cases/post_message_async.py. "pending"/"generating"
    rows are placeholders whose `content` gets overwritten in place once
    generation finishes (or fails, landing on "failed" with fallback text).
    Every message created by the synchronous path is "complete" immediately,
    same as before this field existed.
    """

    session_id: str
    role: str
    content: str
    created_at: datetime
    id: int | None = None
    status: str = "complete"


@dataclass
class OrderedTest:
    session_id: str
    test_id: int
    ordered_at: datetime
    id: int | None = None


@dataclass
class Answer:
    session_id: str
    question_id: int
    choice_id: int
    is_correct: bool
    answered_at: datetime
    id: int | None = None


@dataclass
class PatientIdentity:
    """1:1 header for a PatientCase -- see CasePatientIdentityModel (migration 0006).

    `sex` is a plain str ('male'/'female') rather than the infrastructure
    PatientSex enum -- domain entities never depend on app.infrastructure
    (see this module's docstring); the CHECK constraint enforcing the value
    set lives in the DB, mirrored by that enum only on the infra side.
    """

    name: str | None = None
    age: int | None = None
    sex: str | None = None
    occupation: str | None = None
    nationality: str | None = None
    marital_status: str | None = None


@dataclass
class PresentingComplaint:
    """1:1 header for a PatientCase -- see CasePresentingComplaintModel."""

    chief_complaint: str | None = None
    hpi_narrative: str | None = None


@dataclass
class AssociatedSymptom:
    symptom: str
    is_present: bool


@dataclass
class PastMedicalHistoryItem:
    item: str
    note: str | None = None


@dataclass
class CurrentMedication:
    drug_name: str
    dose: str
    note: str | None = None


@dataclass
class Trigger:
    trigger: str
    is_primary: bool = False


@dataclass
class FamilySocialHistoryItem:
    """`category` is 'family' or 'social' -- see FamilySocialCategory (infra-side enum)."""

    category: str
    item: str


@dataclass
class VitalSign:
    parameter: str
    value: str
    interpretation: str | None = None


@dataclass
class PhysicalExamFinding:
    """`method` is 'inspection'/'palpation'/'percussion'/'auscultation'."""

    method: str
    finding: str


@dataclass
class CaseInvestigation:
    """One investigation actually performed for this patient, as opposed to
    the disease-reference LabInvestigation/RadiologicalInvestigation shape
    (which has no domain entity of its own -- see scenario_case_text.py).
    `category` is 'immediate'/'laboratory'/'imaging'.
    """

    category: str
    test_name: str
    result: str
    interpretation: str | None = None


@dataclass
class SeverityCriterion:
    criterion: str
    patient_value: str
    classification: str


@dataclass
class WarningSign:
    """`is_present` is optional -- None means not assessed in this vignette,
    distinct from explicitly False (assessed and absent)."""

    sign: str
    is_present: bool | None = None


@dataclass
class ManagementPhaseItem:
    """`phase` is 'immediate'/'monitoring'/'disposition'; `sequence_order`
    orders items within the same phase."""

    phase: str
    treatment: str
    sequence_order: int
    dose_route: str | None = None
    goal: str | None = None


@dataclass
class DispositionCriterion:
    """`type` is 'admission' or 'discharge'."""

    type: str
    criterion: str


@dataclass
class DischargePlanItem:
    """`category` is 'medication'/'education'/'follow_up'/'referral'."""

    category: str
    detail: str


@dataclass
class LearningObjective:
    objective_number: int
    objective_text: str


@dataclass
class PatientCase:
    """A full ER-style patient-case vignette for one scenario -- a second,
    parallel layer alongside the disease-reference clinical sections (see
    docs/scenario-clinical-schema-mapping.md). A scenario may have a
    PatientCase, disease-reference data, both, or neither.

    Unlike the disease-reference sections (which have no domain entity and
    are composed straight from ORM rows into `Scenario.case_text` -- see
    scenario_case_text.py), this aggregate is a plain domain object: built
    and returned by PatientCaseRepository (app/domain/repositories.py) /
    SqlPatientCaseRepository, the same nested-list shape as Question.choices.
    """

    scenario_id: int
    identity: PatientIdentity | None = None
    presenting_complaint: PresentingComplaint | None = None
    associated_symptoms: list[AssociatedSymptom] = field(default_factory=list)
    past_medical_history: list[PastMedicalHistoryItem] = field(default_factory=list)
    current_medications: list[CurrentMedication] = field(default_factory=list)
    triggers: list[Trigger] = field(default_factory=list)
    family_social_history: list[FamilySocialHistoryItem] = field(default_factory=list)
    vital_signs: list[VitalSign] = field(default_factory=list)
    physical_exam_findings: list[PhysicalExamFinding] = field(default_factory=list)
    investigations: list[CaseInvestigation] = field(default_factory=list)
    severity_criteria: list[SeverityCriterion] = field(default_factory=list)
    warning_signs: list[WarningSign] = field(default_factory=list)
    management_phases: list[ManagementPhaseItem] = field(default_factory=list)
    disposition_criteria: list[DispositionCriterion] = field(default_factory=list)
    discharge_plan: list[DischargePlanItem] = field(default_factory=list)
    learning_objectives: list[LearningObjective] = field(default_factory=list)


@dataclass
class EvaluationCriterion:
    """One graded criterion within an OSCE-style session evaluation."""

    name: str
    passed: bool
    feedback: str


@dataclass
class Evidence:
    """One ranked retrieval result from the RAG API (see EvidenceRetriever port
    in app/domain/repositories.py, and app/infrastructure/rag_client_adapter.py).

    Metadata fields (chapter/section/content_type/source_file/title) are
    flattened directly onto this entity rather than a nested sub-object --
    matching every other entity in this module, all of which are flat.

    `distance` is a vector-search distance from the RAG API, not a confidence
    score -- lower means a better match. Do not treat it as a probability or
    invert it into one.

    This is raw retrieval output. A PatientReplyGenerator that does fold this
    into a prompt must curate it first (filter to patient-safe content types,
    cap length) rather than pass it through unfiltered, to avoid leaking
    diagnostic content to the simulated patient -- a local Qwen-backed
    implementation used to do exactly that (removed after it proved
    unreliable at holding character regardless). The current sole
    implementation, RagPatientReplyGenerator
    (app/infrastructure/rag_patient_generator.py), sidesteps the question
    entirely: it ignores this parameter, since the RAG API's own patient
    route does its own internal retrieval already scoped to patient-safe
    content. PostMessageUseCase/PostMessageAsyncUseCase still retrieve this
    (via EvidenceRetriever) and pass it to generate_reply() regardless -- it's
    currently unused output on the only path that consumes it, worth knowing
    before adding a second PatientReplyGenerator implementation that *does*
    read it.
    """

    id: str
    rank: int
    text: str
    distance: float
    chapter: str | None = None
    section: str | None = None
    content_type: str | None = None
    source_file: str | None = None
    title: str | None = None


@dataclass
class QuizAnswerResult:
    """One post-session-quiz question's outcome, as seen from within a
    session evaluation -- see QuizResult and Question.category (migration
    0007). `category` is never None here (only categorized quiz questions
    ever appear in a QuizResult). `answered`/`choice_id`/`is_correct` are
    None/False when the doctor hasn't answered this question at all yet --
    distinct from an answered-but-wrong state (`is_correct=False`).
    Deliberately excludes correct_choice_id -- same discipline as
    QuestionRead/QuizQuestionRead (app/api/schemas/question.py): a session
    evaluation must never leak the answer key either.
    """

    question_id: int
    category: str
    text: str
    answered: bool
    choice_id: int | None = None
    is_correct: bool | None = None


@dataclass
class QuizResult:
    """Aggregate post-session-quiz outcome for one session, attached to
    SessionEvaluation.quiz -- see gather_quiz_result()
    (app/application/use_cases/evaluate_session.py).

    Not a persisted aggregate, same as SessionEvaluation itself: computed
    fresh, synchronously, from the existing `questions`/`choices`/`answers`
    tables every time an evaluation is generated -- a plain local DB lookup
    against Question.correct_choice_id, never routed through
    EvaluationGenerator/the RAG API. `questions` is ordered diagnosis ->
    severity -> management_immediate -> management_monitoring ->
    management_disposition (the same order QuestionRepository.
    list_post_session_quiz already returns), so each entry's `category`
    already doubles as a per-category breakdown -- there is exactly one
    question per category by construction (see seed_data/questions.json's
    Asthma quiz), so no separate category->score map is needed.

    `score` is None (not 0.0) when total_questions == 0 -- a scenario with no
    post-session quiz seeded yet has no quiz score to report, distinct from a
    seeded-but-unanswered quiz (score 0.0, every question `answered=False`).
    """

    total_questions: int
    answered_count: int
    correct_count: int
    score: float | None
    questions: list[QuizAnswerResult] = field(default_factory=list)


@dataclass
class SessionEvaluation:
    """Result of evaluating a completed session against a scenario's gold standard.

    Not a persisted aggregate (there is no `evaluations` table) -- it's
    generated on demand by an EvaluationGenerator port (see
    app/domain/repositories.py) and returned straight through the API.

    `status` mirrors Message.status's pending/generating/complete/failed
    vocabulary (see that field's docstring above) for the same reason:
    EvaluateSessionAsyncUseCase (app/application/use_cases/evaluate_session_async.py)
    returns a "pending" instance immediately (score/summary None, no
    criteria yet) while the real LLM-judge call
    (app/infrastructure/rag_llm_evaluator.py,
    POST /v1/rag/chat, tens of seconds per call) runs in the background, then
    pushes a "complete" (or "failed", on an unrecoverable RAG-API error/
    timeout/malformed response -- see that adapter's module docstring for why
    there is deliberately no rule-based fallback score) instance over the
    session's WS channel. EvaluateSessionUseCase (the synchronous path, still
    real code -- used directly by fast/deterministic tests, same relationship
    PostMessageUseCase has to PostMessageAsyncUseCase) always returns
    "complete" immediately, same as before this field existed.

    `quiz` is unrelated to `status`/`score`/`summary`/`criteria_breakdown`
    above -- those four describe the RAG-judged conversation only, computed
    by EvaluationGenerator exactly as before this field existed (that port's
    signature/behavior is untouched). `quiz` is attached separately by the
    use case (EvaluateSessionUseCase/EvaluateSessionAsyncUseCase), after the
    EvaluationGenerator call returns, from a plain local DB lookup -- see
    gather_quiz_result(). It is populated even while `status == "pending"`
    (quiz scoring costs nothing, unlike the RAG call) and stays populated on
    `status == "failed"` too (a failed conversation evaluation doesn't
    invalidate an already-computed quiz result).
    """

    status: str = "complete"
    score: float | None = None
    summary: str | None = None
    criteria_breakdown: list[EvaluationCriterion] = field(default_factory=list)
    quiz: QuizResult | None = None
