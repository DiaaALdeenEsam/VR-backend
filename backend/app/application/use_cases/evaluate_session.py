from __future__ import annotations

from dataclasses import dataclass

from app.domain.entities import SessionEvaluation
from app.domain.exceptions import MissingGoldStandardError, NotFoundError
from app.domain.repositories import AbstractUnitOfWork, EvaluationGenerator


@dataclass
class EvaluationInputs:
    """Everything EvaluationGenerator.evaluate() needs, already translated
    from domain entities into the port's primitive shapes -- see that port's
    docstring (app/domain/repositories.py) for exactly what each field means
    and why. A plain DTO (not a domain entity: nothing else in the domain
    layer needs "the inputs to an evaluation" as a concept) so
    gather_evaluation_inputs() can be called once and its result handed
    straight to EvaluationGenerator.evaluate() via **vars(...), by both
    EvaluateSessionUseCase (synchronous) and EvaluateSessionAsyncUseCase
    (background phase) without re-deriving it twice.
    """

    case_text: str
    gold_standard: str
    messages: list[dict[str, str]]
    ordered_tests: list[dict]
    answers: list[dict]
    relevant_test_ids: list[int]
    total_questions: int


async def gather_evaluation_inputs(uow: AbstractUnitOfWork, session_id: str) -> EvaluationInputs:
    """Loads and shapes everything EvaluationGenerator.evaluate() needs for
    one session, inside the caller's own `async with uow:`/uow-factory
    transaction. Extracted here (rather than inlined in
    EvaluateSessionUseCase.execute()) so EvaluateSessionAsyncUseCase's
    synchronous validation phase (session/scenario/gold_standard existence
    checks that must fail *before* acking "pending", not inside the
    background task -- see that use case's module docstring) can reuse the
    exact same gathering logic without duplicating it, the same precedent as
    post_message.py's retrieve_evidence_or_empty() being shared by
    PostMessageUseCase and PostMessageAsyncUseCase.

    Raises NotFoundError (unknown session/scenario) or
    MissingGoldStandardError (scenario.gold_standard not set) -- both are
    meant to surface synchronously to the caller, before any "pending"
    response goes out.
    """

    session = await uow.sessions.get(session_id)
    if session is None:
        raise NotFoundError("Session", session_id)

    scenario = await uow.scenarios.get(session.scenario_id)
    if scenario is None:
        raise NotFoundError("Scenario", session.scenario_id)

    if not scenario.gold_standard:
        raise MissingGoldStandardError(scenario.id)

    # Excludes any message still "pending"/"generating" (an assistant reply
    # PostMessageAsyncUseCase hasn't filled in yet -- see Message.status's
    # docstring in app/domain/entities.py): its placeholder content is empty,
    # and grading against an empty assistant turn would misrepresent the
    # conversation rather than just show it as still in progress. This can
    # only ever matter for EvaluateSessionAsyncUseCase in production (the
    # RAG-API-backed PatientReplyGenerator is the only one that ever leaves a
    # message in one of these two statuses) -- a no-op filter for
    # EvaluateSessionUseCase's synchronous callers, whose messages are always
    # already "complete".
    all_messages = await uow.messages.list_by_session(session_id)
    messages = [m for m in all_messages if m.status not in ("pending", "generating")]
    message_dicts = [{"role": m.role, "content": m.content} for m in messages]

    ordered_tests = await uow.ordered_tests.list_by_session(session_id)
    ordered_test_dicts = [{"test_id": ot.test_id} for ot in ordered_tests]

    answers = await uow.answers.list_by_session(session_id)
    answer_dicts = [
        {"question_id": a.question_id, "choice_id": a.choice_id, "is_correct": a.is_correct} for a in answers
    ]

    # What this scenario's own data says is relevant (see
    # ScenarioTestResultModel, migration 0004) -- what ordered_tests is
    # judged against, not what's merely orderable.
    relevant_tests = await uow.tests.list_scenario_relevant(session.scenario_id)
    relevant_test_ids = [t.id for t in relevant_tests]

    # Total, not just attempted: len(uow.questions.list_by_scenario(...))
    # rather than a dedicated count_by_scenario() -- QuestionRepository
    # already has to load full Question entities (with choices) for
    # GET /scenarios/{id}/questions, so a second method that only returns a
    # count would be redundant surface for what's already a cheap, small,
    # per-scenario list in this project's data.
    questions = await uow.questions.list_by_scenario(session.scenario_id)
    total_questions = len(questions)

    return EvaluationInputs(
        case_text=scenario.case_text,
        gold_standard=scenario.gold_standard,
        messages=message_dicts,
        ordered_tests=ordered_test_dicts,
        answers=answer_dicts,
        relevant_test_ids=relevant_test_ids,
        total_questions=total_questions,
    )


class EvaluateSessionUseCase:
    """Generates an OSCE-style evaluation of a completed session, entirely
    within one request (the evaluation_generator call is awaited inline).

    Still real, live code -- not superseded by EvaluateSessionAsyncUseCase
    the way PostMessageUseCase relates to PostMessageAsyncUseCase (see that
    pair's module docstrings): app/api/deps.py's get_evaluate_session_use_case
    builds EvaluateSessionAsyncUseCase for production, and this class is used
    directly by fast/deterministic tests that don't want to exercise the
    async orchestration layer (see tests/conftest.py's dependency_overrides).

    Note on dependencies: the original feature plan named SessionRepository,
    ScenarioRepository, and EvaluationGenerator as this use case's
    constructor dependencies. This project's established convention (every
    other use case in this package) bundles repository access behind a
    single AbstractUnitOfWork instead -- so this takes `uow` (exposing
    `.sessions` and `.scenarios`, among others) plus the EvaluationGenerator
    port, the same (uow, port) shape as PostMessageUseCase. Read-only, so it
    never calls `uow.commit()`.
    """

    def __init__(self, uow: AbstractUnitOfWork, evaluation_generator: EvaluationGenerator) -> None:
        self._uow = uow
        self._evaluation_generator = evaluation_generator

    async def execute(self, session_id: str) -> SessionEvaluation:
        async with self._uow:
            inputs = await gather_evaluation_inputs(self._uow, session_id)
            return await self._evaluation_generator.evaluate(
                case_text=inputs.case_text,
                gold_standard=inputs.gold_standard,
                messages=inputs.messages,
                ordered_tests=inputs.ordered_tests,
                answers=inputs.answers,
                relevant_test_ids=inputs.relevant_test_ids,
                total_questions=inputs.total_questions,
            )
