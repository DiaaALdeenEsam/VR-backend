from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import (
    get_answer_question_use_case,
    get_evaluate_session_use_case,
    get_order_test_use_case,
    get_session_review_use_case,
    get_start_session_use_case,
)
from app.api.schemas.evaluation import EvaluationCriterionRead, SessionEvaluationResponse
from app.api.schemas.message import MessageRead
from app.api.schemas.session import (
    AnswerCreate,
    AnswerCreateResponse,
    AnswerReviewItem,
    OrderedTestReviewItem,
    SessionCreate,
    SessionCreateResponse,
    SessionReviewResponse,
)
from app.api.schemas.test import TestOrderCreate, TestOrderResponse
from app.application.use_cases.answer_question import AnswerQuestionUseCase
from app.application.use_cases.evaluate_session import EvaluateSessionUseCase
from app.application.use_cases.evaluate_session_async import EvaluateSessionAsyncUseCase
from app.application.use_cases.get_session_review import GetSessionReviewUseCase
from app.application.use_cases.order_test import OrderTestUseCase
from app.application.use_cases.start_session import StartSessionUseCase

router = APIRouter(tags=["sessions"])


@router.post("/sessions", response_model=SessionCreateResponse, status_code=201)
async def create_session(
    payload: SessionCreate,
    use_case: StartSessionUseCase = Depends(get_start_session_use_case),
) -> SessionCreateResponse:
    session = await use_case.execute(payload.scenario_id)
    return SessionCreateResponse(session_id=session.id)


@router.get("/sessions/{session_id}", response_model=SessionReviewResponse)
async def get_session_review(
    session_id: str,
    use_case: GetSessionReviewUseCase = Depends(get_session_review_use_case),
) -> SessionReviewResponse:
    review = await use_case.execute(session_id)
    return SessionReviewResponse(
        session_id=review.session.id,
        scenario_id=review.session.scenario_id,
        created_at=review.session.created_at,
        messages=[
            MessageRead(
                id=m.id,  # type: ignore[arg-type]
                role=m.role,
                content=m.content,
                created_at=m.created_at,
                status=m.status,
            )
            for m in review.messages
        ],
        ordered_tests=[
            OrderedTestReviewItem(id=ot.id, test_id=t.id, name=t.name, ordered_at=ot.ordered_at)  # type: ignore[arg-type]
            for ot, t in review.ordered_tests
        ],
        answers=[
            AnswerReviewItem(
                id=a.id,  # type: ignore[arg-type]
                question_id=a.question_id,
                choice_id=a.choice_id,
                is_correct=a.is_correct,
                answered_at=a.answered_at,
            )
            for a, _q in review.answers
        ],
    )


@router.post("/sessions/{session_id}/tests", response_model=TestOrderResponse, status_code=201)
async def order_test(
    session_id: str,
    payload: TestOrderCreate,
    use_case: OrderTestUseCase = Depends(get_order_test_use_case),
) -> TestOrderResponse:
    test, ordered = await use_case.execute(session_id, payload.test_id)
    assert ordered.id is not None
    return TestOrderResponse(
        ordered_test_id=ordered.id,
        test_id=test.id,
        name=test.name,
        result=test.result,
        ordered_at=ordered.ordered_at,
    )


@router.post("/sessions/{session_id}/answers", response_model=AnswerCreateResponse, status_code=201)
async def submit_answer(
    session_id: str,
    payload: AnswerCreate,
    use_case: AnswerQuestionUseCase = Depends(get_answer_question_use_case),
) -> AnswerCreateResponse:
    answer = await use_case.execute(session_id, payload.question_id, payload.choice_id)
    assert answer.id is not None
    return AnswerCreateResponse(
        id=answer.id,
        question_id=answer.question_id,
        choice_id=answer.choice_id,
        answered_at=answer.answered_at,
    )


@router.post("/sessions/{session_id}/evaluate", response_model=SessionEvaluationResponse)
async def evaluate_session(
    session_id: str,
    use_case: EvaluateSessionUseCase | EvaluateSessionAsyncUseCase = Depends(get_evaluate_session_use_case),
) -> SessionEvaluationResponse:
    """OSCE-style evaluation of the session so far, generated on demand -- not persisted.

    `use_case` is typed as a Union even though app/api/deps.py's
    get_evaluate_session_use_case unconditionally builds the async one in
    production, the same reasoning as voice.py's chat_voice/voice_websocket
    routes: tests override that dependency back to the synchronous
    EvaluateSessionUseCase (wired with the fast/deterministic stub generator)
    for fixtures that don't want to exercise the async orchestration layer --
    see tests/conftest.py. Both branches are real, live code.

    In production this always responds status="pending" immediately (score/
    summary null, empty criteria_breakdown) -- the real LLM-judge result (or
    a "failed" status with no score) is pushed later over the session's WS
    connection (WS /ws/voice?session_id=..., see app/infrastructure/ws_hub.py)
    as a `{"type": "evaluation", ...}` frame with the same field names as
    this response. A 409 (SessionBusyError) means an evaluation for this
    session is already being generated -- see EvaluateSessionAsyncUseCase's
    module docstring for the concurrency policy.

    404 if the session or its scenario doesn't exist; 400 if the scenario has
    no gold_standard set (see MissingGoldStandardError, handled centrally by
    app/api/error_handlers.py's generic DomainError -> 400 mapping) -- both
    raised synchronously, before any "pending" response goes out.
    """

    evaluation = await use_case.execute(session_id)
    return SessionEvaluationResponse(
        status=evaluation.status,
        score=evaluation.score,
        summary=evaluation.summary,
        criteria_breakdown=[
            EvaluationCriterionRead(name=c.name, passed=c.passed, feedback=c.feedback)
            for c in evaluation.criteria_breakdown
        ],
    )
