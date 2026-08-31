from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.api.schemas.message import MessageRead


class SessionCreate(BaseModel):
    scenario_id: int


class SessionCreateResponse(BaseModel):
    session_id: str


class AnswerCreate(BaseModel):
    question_id: int
    choice_id: int


class AnswerCreateResponse(BaseModel):
    """No `is_correct` here -- submitting an answer must never leak correctness."""

    id: int
    question_id: int
    choice_id: int
    answered_at: datetime


class OrderedTestReviewItem(BaseModel):
    id: int
    test_id: int
    name: str
    ordered_at: datetime


class AnswerReviewItem(BaseModel):
    """Correctness is only ever exposed here, in the post-hoc session review."""

    id: int
    question_id: int
    choice_id: int
    is_correct: bool
    answered_at: datetime


class SessionReviewResponse(BaseModel):
    session_id: str
    scenario_id: int
    created_at: datetime
    messages: list[MessageRead]
    ordered_tests: list[OrderedTestReviewItem]
    answers: list[AnswerReviewItem]
