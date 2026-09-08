"""Async evaluation-generation path for the LLM-judge EvaluationGenerator
(today, only "rag_llm" -- see app/infrastructure/rag_llm_evaluator.py, tens
of seconds observed per call against the real /v1/rag/chat API), mirroring
app/application/use_cases/post_message_async.py's shape as closely as the
two problems allow.

Replaces the single blocking `await evaluation_generator.evaluate(...)` call
EvaluateSessionUseCase makes with: validate the session/scenario/gold-standard
and gather the grading inputs immediately (still inline, before returning --
these are fast DB reads, and a 404/400 must reach the client synchronously,
not as a delayed WS failure), return a "pending" SessionEvaluation right
away, then run the real (slow) evaluation in a background asyncio task and
push the result to the session's live WS connection
(app/infrastructure/ws_hub.py) once it's ready.

One notable simplification versus PostMessageAsyncUseCase: there is no
persisted "evaluations" row to create a placeholder for and later update --
SessionEvaluation was never a persisted aggregate (see its docstring in
app/domain/entities.py), so the background phase here needs no uow/DB access
at all, only the plain EvaluationInputs already gathered in the foreground
phase. That's also why this class takes `uow_factory` but only ever calls it
once, up front -- kept as a factory (rather than a single injected `uow`)
for symmetry with PostMessageAsyncUseCase and because Depends(get_uow_factory)
is what app/api/deps.py already wires for this router, not because a second
transaction is ever needed here.

Concurrency policy: reject a second evaluate() call for the same session
while one is already generating, with SessionBusyError(session_id,
operation="evaluation") -- a 409, same as a second in-flight message (see
PostMessageAsyncUseCase's module docstring for the queueing/cancelling
alternatives this rejects and why). Tracked by a *separate*
InMemorySessionConcurrencyGuard instance from the one messages use (see
app/infrastructure/inflight_sessions.py's get_evaluation_singleton()) --
message generation and evaluation generation are unrelated background jobs
that happen to share a session_id, and coupling their guards would block one
operation on the other for no reason either actually depends on.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from app.application.use_cases.evaluate_session import EvaluationInputs, gather_evaluation_inputs
from app.config import Settings
from app.domain.entities import SessionEvaluation
from app.domain.exceptions import RagServiceUnavailableError, SessionBusyError
from app.domain.repositories import AbstractUnitOfWork, EvaluationGenerator, ReplyPushPort, SessionConcurrencyGuard

logger = logging.getLogger(__name__)


class EvaluateSessionAsyncUseCase:
    def __init__(
        self,
        uow_factory: Callable[[], AbstractUnitOfWork],
        evaluation_generator: EvaluationGenerator,
        settings: Settings,
        concurrency_guard: SessionConcurrencyGuard,
        push_port: ReplyPushPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._evaluation_generator = evaluation_generator
        self._settings = settings
        self._concurrency_guard = concurrency_guard
        self._push_port = push_port

    async def execute(self, session_id: str) -> SessionEvaluation:
        if self._concurrency_guard.is_busy(session_id):
            raise SessionBusyError(session_id, operation="evaluation")

        async with self._uow_factory() as uow:
            # NotFoundError/MissingGoldStandardError raise straight out of
            # here -- synchronously, before any "pending" response is
            # returned, same as PostMessageAsyncUseCase's own NotFoundError
            # checks in its execute().
            inputs = await gather_evaluation_inputs(uow, session_id)

        # Registered *before* returning, so a second call arriving the
        # instant after this returns still sees is_busy() == True -- no
        # window where two calls could both pass the check above (same
        # ordering guarantee as PostMessageAsyncUseCase.execute()).
        self._concurrency_guard.start(session_id, self._generate_and_push(session_id, inputs))
        return SessionEvaluation(status="pending")

    async def _generate_and_push(self, session_id: str, inputs: EvaluationInputs) -> None:
        try:
            evaluation = await asyncio.wait_for(
                self._evaluation_generator.evaluate(
                    case_text=inputs.case_text,
                    gold_standard=inputs.gold_standard,
                    messages=inputs.messages,
                    ordered_tests=inputs.ordered_tests,
                    answers=inputs.answers,
                    relevant_test_ids=inputs.relevant_test_ids,
                    total_questions=inputs.total_questions,
                ),
                timeout=self._settings.rag_evaluation_hard_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "evaluation for session_id=%s exceeded %.0fs -- reporting failure (no fallback score)",
                session_id,
                self._settings.rag_evaluation_hard_timeout_seconds,
            )
            await self._push_failed(
                session_id, f"evaluation timed out after {self._settings.rag_evaluation_hard_timeout_seconds:.0f}s"
            )
            return
        except RagServiceUnavailableError as exc:
            logger.warning(
                "evaluation for session_id=%s failed: %s -- reporting failure (no fallback score)",
                session_id,
                exc,
            )
            await self._push_failed(session_id, str(exc))
            return

        await self._push_port.push(
            session_id,
            {
                "type": "evaluation",
                "status": "complete",
                "score": evaluation.score,
                "summary": evaluation.summary,
                "criteria_breakdown": [
                    {"name": c.name, "passed": c.passed, "feedback": c.feedback}
                    for c in evaluation.criteria_breakdown
                ],
            },
        )

    async def _push_failed(self, session_id: str, detail: str) -> None:
        # No fallback score of any kind (see this module's and
        # rag_llm_evaluator.py's docstrings) -- score/summary are None and
        # criteria_breakdown is empty, `detail` carries the reason, matching
        # WS /ws/voice's own `{"type": "error", "detail": ...}` field name
        # (app/api/routers/voice.py) for the same kind of information.
        await self._push_port.push(
            session_id,
            {
                "type": "evaluation",
                "status": "failed",
                "score": None,
                "summary": None,
                "criteria_breakdown": [],
                "detail": detail,
            },
        )
