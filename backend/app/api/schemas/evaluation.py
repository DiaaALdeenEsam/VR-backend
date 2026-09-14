from __future__ import annotations

from pydantic import BaseModel, Field


class EvaluationCriterionRead(BaseModel):
    name: str
    passed: bool
    feedback: str


class QuizAnswerResultRead(BaseModel):
    """One post-session-quiz question's outcome within a session evaluation.

    Deliberately excludes correct_choice_id -- same discipline as
    QuestionRead/QuizQuestionRead (app/api/schemas/question.py): a session
    evaluation must never leak the answer key either. `answered=False` means
    `choice_id`/`is_correct` are both null -- the doctor hasn't answered this
    question at all, distinct from an answered-but-wrong question
    (`is_correct=false`).
    """

    question_id: int
    category: str
    text: str
    answered: bool
    choice_id: int | None = None
    is_correct: bool | None = None


class PostSessionQuizResultRead(BaseModel):
    """Aggregate post-session-quiz outcome, embedded in
    SessionEvaluationResponse.quiz. `score` is null (not 0.0) when
    `total_questions` is 0 -- this scenario has no post-session quiz seeded
    yet, distinct from a seeded-but-unanswered quiz (score 0.0). Computed
    entirely from local DB lookups against each question's stored
    correct_choice_id -- see gather_quiz_result(), never routed through the
    RAG API/EvaluationGenerator, unlike score/summary/criteria_breakdown
    above.
    """

    total_questions: int
    answered_count: int
    correct_count: int
    score: float | None = None
    questions: list[QuizAnswerResultRead] = Field(default_factory=list)


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

    `quiz` is unrelated to `status` above -- it's the post-session MCQ quiz
    result (disease name, attack-severity classification, management plan
    across its 3 stages -- see PostSessionQuizResultRead), computed locally
    and attached to this same payload alongside the RAG-judged conversation
    evaluation. Unlike score/summary/criteria_breakdown, `quiz` is already
    fully populated even while `status == "pending"` -- quiz scoring is an
    instant DB lookup, not something that needs to wait for the RAG call --
    and stays populated on `status == "failed"` too, since a failed/timed-out
    conversation evaluation has no bearing on the quiz result. `quiz` is null
    only if the session itself couldn't be resolved, which never happens
    here without the whole call already having raised NotFoundError instead.
    """

    status: str = "complete"
    score: float | None = None
    summary: str | None = None
    criteria_breakdown: list[EvaluationCriterionRead] = Field(default_factory=list)
    quiz: PostSessionQuizResultRead | None = None
