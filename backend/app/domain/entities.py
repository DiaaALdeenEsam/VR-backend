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
    id: int
    scenario_id: int
    text: str
    correct_choice_id: int
    choices: list[Choice] = field(default_factory=list)


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
    """

    status: str = "complete"
    score: float | None = None
    summary: str | None = None
    criteria_breakdown: list[EvaluationCriterion] = field(default_factory=list)
