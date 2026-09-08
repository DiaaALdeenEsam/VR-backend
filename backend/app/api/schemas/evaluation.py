from __future__ import annotations

from pydantic import BaseModel, Field


class EvaluationCriterionRead(BaseModel):
    name: str
    passed: bool
    feedback: str


class SessionEvaluationResponse(BaseModel):
    """`status` mirrors MessageRead.status's pending/generating/complete/failed
    vocabulary (see app/api/schemas/message.py) -- POST /sessions/{id}/evaluate
    now acks immediately with status="pending" (score/summary null, an empty
    criteria_breakdown) while the real LLM-judge call runs in the background
    (app/infrastructure/rag_llm_evaluator.py, tens of seconds per call); the
    real result (or a "failed" status with no score at all -- there is no
    rule-based fallback score) arrives later over the session's WS connection
    as a `{"type": "evaluation", ...}` frame, same field names as this
    response (see EvaluateSessionAsyncUseCase). A client that doesn't care
    about async generation can still treat "complete" the same way it always
    read this response.
    """

    status: str = "complete"
    score: float | None = None
    summary: str | None = None
    criteria_breakdown: list[EvaluationCriterionRead] = Field(default_factory=list)
