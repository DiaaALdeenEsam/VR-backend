from __future__ import annotations

from pydantic import BaseModel


class EvaluationCriterionRead(BaseModel):
    name: str
    passed: bool
    feedback: str


class SessionEvaluationResponse(BaseModel):
    score: float
    summary: str
    criteria_breakdown: list[EvaluationCriterionRead]
