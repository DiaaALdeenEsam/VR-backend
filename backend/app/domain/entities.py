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
class SessionEvaluation:
    """Result of evaluating a completed session against a scenario's gold standard.

    Not a persisted aggregate (there is no `evaluations` table) -- it's
    generated on demand by an EvaluationGenerator port (see
    app/domain/repositories.py) and returned straight through the API.
    """

    score: float
    summary: str
    criteria_breakdown: list[EvaluationCriterion] = field(default_factory=list)
