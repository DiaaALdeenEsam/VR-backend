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
    session_id: str
    role: str
    content: str
    created_at: datetime
    id: int | None = None


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

    This is raw retrieval output. It must never be handed to a
    PatientReplyGenerator's prompt unfiltered -- see
    app/infrastructure/qwen_patient_generator.py's curation step, which is
    the boundary that keeps this from leaking diagnostic content to the
    simulated patient.
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
    """

    score: float
    summary: str
    criteria_breakdown: list[EvaluationCriterion] = field(default_factory=list)
