from __future__ import annotations

from dataclasses import dataclass, replace

from app.domain.entities import QuizAnswerResult, QuizResult, SessionEvaluation
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

    Chat-only assessment model (2026-09-14): used to also carry
    ordered_tests/answers/relevant_test_ids/total_questions for
    investigation-ordering/quiz scoring -- removed along with that scoring
    (see EvaluationGenerator's docstring). gather_evaluation_inputs() below
    no longer queries uow.ordered_tests/uow.answers/uow.tests/uow.questions
    for this purpose; those repositories and their underlying tables are
    otherwise untouched.
    """

    case_text: str
    gold_standard: str
    messages: list[dict[str, str]]


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

    return EvaluationInputs(
        case_text=scenario.case_text,
        gold_standard=scenario.gold_standard,
        messages=message_dicts,
    )


async def gather_quiz_result(uow: AbstractUnitOfWork, session_id: str) -> QuizResult:
    """Computes this session's post-session-quiz outcome -- disease name,
    attack-severity classification, and management plan across its three
    stages (see Question.category, migration 0007) -- entirely from local
    DB reads (uow.questions.list_post_session_quiz + uow.answers.list_by_session).

    Deliberately separate from EvaluationGenerator/gather_evaluation_inputs
    above: quiz correctness is a plain lookup against each question's stored
    correct_choice_id (already computed and stored by AnswerQuestionUseCase
    at answer-submission time -- see Answer.is_correct), never an LLM call.
    Called from both EvaluateSessionUseCase.execute() and
    EvaluateSessionAsyncUseCase (both its synchronous validation phase and,
    via the value computed there, its background push) so a session
    evaluation's quiz section is computed exactly once per /evaluate call and
    attached to whatever SessionEvaluation the EvaluationGenerator call
    produces -- see SessionEvaluation.quiz's own docstring.

    Raises NotFoundError for an unknown session_id, same as
    gather_evaluation_inputs -- meant to run inside the same uow/transaction
    right alongside it, so this is only ever reached once the session is
    already known to exist in practice, but stays self-contained (a second,
    cheap uow.sessions.get()) rather than depending on EvaluationInputs
    carrying a scenario_id it has no other use for.
    """

    session = await uow.sessions.get(session_id)
    if session is None:
        raise NotFoundError("Session", session_id)

    # Already returned in the fixed diagnosis -> severity -> management_immediate
    # -> management_monitoring -> management_disposition clinical-stage order
    # by SqlQuestionRepository.list_post_session_quiz -- nothing to re-sort here.
    questions = await uow.questions.list_post_session_quiz(session.scenario_id)
    answers = await uow.answers.list_by_session(session_id)
    answer_by_question_id = {a.question_id: a for a in answers}

    results: list[QuizAnswerResult] = []
    correct_count = 0
    answered_count = 0
    for question in questions:
        answer = answer_by_question_id.get(question.id)
        if answer is not None:
            answered_count += 1
            if answer.is_correct:
                correct_count += 1
        results.append(
            QuizAnswerResult(
                question_id=question.id,
                category=question.category,  # never None -- list_post_session_quiz filters to categorized only
                text=question.text,
                answered=answer is not None,
                choice_id=answer.choice_id if answer is not None else None,
                is_correct=answer.is_correct if answer is not None else None,
            )
        )

    total = len(questions)
    score = (correct_count / total * 100) if total > 0 else None
    return QuizResult(
        total_questions=total,
        answered_count=answered_count,
        correct_count=correct_count,
        score=score,
        questions=results,
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
            evaluation = await self._evaluation_generator.evaluate(
                case_text=inputs.case_text,
                gold_standard=inputs.gold_standard,
                messages=inputs.messages,
            )
            # Attached AFTER the EvaluationGenerator call returns, from a
            # separate local-only DB lookup -- see gather_quiz_result's and
            # SessionEvaluation.quiz's docstrings. evaluation_generator itself
            # never sees or produces quiz data; its behavior is unchanged.
            quiz = await gather_quiz_result(self._uow, session_id)
            return replace(evaluation, quiz=quiz)
