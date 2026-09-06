"""FastAPI Depends() providers.

get_db_session is used directly only by the health check (a plain connectivity
probe, not a use case). Every use case is built from get_uow, so all reads and
writes go through the same AbstractUnitOfWork / single-transaction pattern.

Tests override get_db_session and get_uow via app.dependency_overrides to point
at a throwaway in-memory database (see tests/conftest.py) -- nothing here needs
to change for that to work.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.application.use_cases.answer_question import AnswerQuestionUseCase
from app.application.use_cases.evaluate_session import EvaluateSessionUseCase
from app.application.use_cases.get_session_review import GetSessionReviewUseCase
from app.application.use_cases.list_questions import ListQuestionsUseCase
from app.application.use_cases.list_relevant_tests import ListRelevantTestsUseCase
from app.application.use_cases.list_scenarios import ListScenariosUseCase
from app.application.use_cases.list_test_categories import ListTestCategoriesUseCase
from app.application.use_cases.list_tests_by_category import ListTestsByCategoryUseCase
from app.application.use_cases.order_test import OrderTestUseCase
from app.application.use_cases.post_message_async import PostMessageAsyncUseCase
from app.application.use_cases.process_voice_chat_async import ProcessVoiceChatAsyncUseCase
from app.application.use_cases.start_session import StartSessionUseCase
from app.application.use_cases.transcribe_audio import TranscribeAudioUseCase
from app.config import get_settings
from app.domain.repositories import (
    AbstractUnitOfWork,
    EvaluationGenerator,
    EvidenceRetriever,
    PatientReplyGenerator,
    ReplyPushPort,
    SessionConcurrencyGuard,
    SpeechToTextPort,
    TextToSpeechPort,
)
from app.infrastructure import inflight_sessions, ws_hub
from app.infrastructure.colab_stt_adapter import ColabSTTAdapter
from app.infrastructure.colab_tts_adapter import ColabTTSAdapter
from app.infrastructure.db.engine import get_session_factory
from app.infrastructure.db.repositories.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.legacy.local_tts_adapter import LocalTTSAdapter
from app.infrastructure.legacy.local_whisper_stt_adapter import LocalWhisperSTTAdapter
from app.infrastructure.rag_client_adapter import RagClientAdapter
from app.infrastructure.rag_patient_generator import RagPatientReplyGenerator
from app.infrastructure.stub_evaluator import StubEvaluationGenerator
from app.infrastructure.stub_stt import StubSTTAdapter
from app.infrastructure.stub_tts import StubTTSAdapter


async def get_db_session() -> AsyncIterator[AsyncSession]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session


def get_uow() -> AbstractUnitOfWork:
    return SqlAlchemyUnitOfWork(get_session_factory())


def get_uow_factory() -> Callable[[], AbstractUnitOfWork]:
    """A callable that builds a *fresh* AbstractUnitOfWork every time it's
    invoked -- for PostMessageAsyncUseCase, which needs more than one
    independent transaction across a single request's lifetime (including
    one from a background task that outlives the request itself).
    Depends(get_uow) alone only ever provides a single per-request instance,
    which isn't enough here.

    A direct peer of get_uow (both close over get_session_factory()), not a
    wrapper around it -- app.dependency_overrides only intercepts calls that
    go through FastAPI's own Depends() resolution, so overriding get_uow
    alone would not affect a plain Python reference to it held elsewhere.
    Tests that need this path (see tests/test_post_message_async.py) override
    get_uow_factory itself, the same way tests/conftest.py already overrides
    get_uow, both closing over the same test session_factory.
    """

    session_factory = get_session_factory()
    return lambda: SqlAlchemyUnitOfWork(session_factory)


# NOTE: every use-case provider below takes its UnitOfWork via `Depends(get_uow)`
# rather than calling get_uow() directly. That keeps get_uow part of FastAPI's
# dependency-resolution tree, so tests/conftest.py's
# `app.dependency_overrides[get_uow] = ...` actually takes effect for every use
# case, however deeply nested.


def get_list_scenarios_use_case(uow: AbstractUnitOfWork = Depends(get_uow)) -> ListScenariosUseCase:
    return ListScenariosUseCase(uow)


def get_list_test_categories_use_case(
    uow: AbstractUnitOfWork = Depends(get_uow),
) -> ListTestCategoriesUseCase:
    return ListTestCategoriesUseCase(uow)


def get_list_tests_by_category_use_case(
    uow: AbstractUnitOfWork = Depends(get_uow),
) -> ListTestsByCategoryUseCase:
    return ListTestsByCategoryUseCase(uow)


def get_list_questions_use_case(uow: AbstractUnitOfWork = Depends(get_uow)) -> ListQuestionsUseCase:
    return ListQuestionsUseCase(uow)


def get_list_relevant_tests_use_case(
    uow: AbstractUnitOfWork = Depends(get_uow),
) -> ListRelevantTestsUseCase:
    return ListRelevantTestsUseCase(uow)


def get_start_session_use_case(uow: AbstractUnitOfWork = Depends(get_uow)) -> StartSessionUseCase:
    return StartSessionUseCase(uow)


def get_patient_reply_generator() -> PatientReplyGenerator:
    """The sole PatientReplyGenerator implementation: the external RAG
    patient-chat API (app/infrastructure/rag_patient_generator.py).

    Used to also pick between this, a local Qwen2.5-0.5B-Instruct model, and
    a fixed-echo stub via a Settings.patient_reply_backend toggle -- removed
    once live reproduction confirmed Qwen hallucinates/breaks character
    systematically (see the diagnosis this deletion followed from) and the
    RAG API's anti-leak boundary was separately verified to hold cleanly.
    This is now a deliberate, permanent choice, not a config default -- there
    is no other backend to fall back to. (tests still construct
    StubPatientReplyGenerator directly for fast/deterministic test doubles --
    see tests/conftest.py -- entirely via dependency_overrides, never through
    this function or Settings.)
    """

    return RagPatientReplyGenerator()


def get_session_concurrency_guard() -> SessionConcurrencyGuard:
    """Process-wide singleton (see app/infrastructure/inflight_sessions.py) --
    NOT constructed fresh per call the way get_uow() is: is_busy()/start()
    must see the same state across separate HTTP requests for the
    reject-a-second-in-flight-message policy to mean anything."""

    return inflight_sessions.get_singleton()


def get_reply_push_port() -> ReplyPushPort:
    """Process-wide singleton (see app/infrastructure/ws_hub.py) -- same
    reasoning as get_session_concurrency_guard(): a WS connection registered
    by one request must be visible to a background task spawned by a
    different one."""

    return ws_hub.get_singleton()


def get_ws_hub() -> ws_hub.WebSocketPushHub:
    """The concrete WebSocketPushHub (not the ReplyPushPort abstraction) --
    only for app/api/routers/voice.py's WS handler, which needs
    register()/unregister() (connection-registry bookkeeping, not part of
    the ReplyPushPort domain port -- that port only covers push()/
    push_bytes(), which is all any use case ever needs). Routers depending on
    a concrete infrastructure type directly is fine -- the API layer is where
    this project's wiring lives (see this whole module); the Clean
    Architecture boundary this project draws is domain/application not
    depending on infrastructure, which this isn't."""

    return ws_hub.get_singleton()


def get_evidence_retriever() -> EvidenceRetriever:
    """Provides the EvidenceRetriever implementation for RAG-backed evidence
    retrieval. Only one implementation exists today (unlike
    get_patient_reply_generator/get_speech_to_text_port, there's no
    Settings-driven backend selector here -- not asked for, and there's no
    stub to fall back to yet). Cheap to construct: no request is made until
    retrieve() is actually called.
    """

    return RagClientAdapter(get_settings())


def get_post_message_use_case(
    uow_factory: Callable[[], AbstractUnitOfWork] = Depends(get_uow_factory),
    reply_generator: PatientReplyGenerator = Depends(get_patient_reply_generator),
    evidence_retriever: EvidenceRetriever = Depends(get_evidence_retriever),
    concurrency_guard: SessionConcurrencyGuard = Depends(get_session_concurrency_guard),
    push_port: ReplyPushPort = Depends(get_reply_push_port),
) -> PostMessageAsyncUseCase:
    """What app/api/routers/chat.py depends on for POST /sessions/{id}/messages.

    Unconditionally PostMessageAsyncUseCase now: the sole PatientReplyGenerator
    (get_patient_reply_generator, above) is the RAG API, 6-48s observed per
    call -- there is no fast synchronous backend left in production to make a
    PostMessageUseCase (still used directly by tests -- see
    tests/conftest.py's dependency_overrides for this exact function)
    worthwhile as the default here. This function used to also pick between
    PostMessageUseCase and PostMessageAsyncUseCase via
    Settings.patient_reply_backend when "qwen" was still an option; that
    branch is gone along with the setting itself.

    No text_to_speech here (see get_post_message_use_case_with_tts below for
    the voice path's equivalent) -- deliberately not just an unused optional
    param on this one: FastAPI introspects every parameter of a function used
    as a Depends() target to build its request-validation model, and a bare
    `TextToSpeechPort | None = None` parameter (not itself wrapped in
    Depends(...)) fails that introspection, since the port type isn't a valid
    Pydantic field. Two small functions avoids that entirely.
    """

    return PostMessageAsyncUseCase(
        uow_factory=uow_factory,
        reply_generator=reply_generator,
        evidence_retriever=evidence_retriever,
        settings=get_settings(),
        concurrency_guard=concurrency_guard,
        push_port=push_port,
    )


def get_order_test_use_case(uow: AbstractUnitOfWork = Depends(get_uow)) -> OrderTestUseCase:
    return OrderTestUseCase(uow)


def get_answer_question_use_case(
    uow: AbstractUnitOfWork = Depends(get_uow),
) -> AnswerQuestionUseCase:
    return AnswerQuestionUseCase(uow)


def get_session_review_use_case(
    uow: AbstractUnitOfWork = Depends(get_uow),
) -> GetSessionReviewUseCase:
    return GetSessionReviewUseCase(uow)


def get_evaluation_generator() -> EvaluationGenerator:
    """Picks the EvaluationGenerator implementation via Settings.evaluation_backend.

    Only "stub" exists today; mirrors get_patient_reply_generator's shape so a
    real/local LLM evaluator backend can be added the same way later (a new
    Literal member on Settings.evaluation_backend plus a branch here).
    """

    backend = get_settings().evaluation_backend
    if backend == "stub":
        return StubEvaluationGenerator()
    raise ValueError(f"unknown evaluation_backend: {backend!r}")


def get_evaluate_session_use_case(
    uow: AbstractUnitOfWork = Depends(get_uow),
    evaluation_generator: EvaluationGenerator = Depends(get_evaluation_generator),
) -> EvaluateSessionUseCase:
    return EvaluateSessionUseCase(uow, evaluation_generator)


def get_speech_to_text_port() -> SpeechToTextPort:
    """Picks the SpeechToTextPort implementation via Settings.stt_backend.

    "colab" (the default) and "whisper" (deprecated, rollback-only -- see
    app/infrastructure/legacy/local_whisper_stt_adapter.py) are both cheap to
    construct: neither loads a model or opens a connection until transcribe()
    is actually called.
    """

    settings = get_settings()
    if settings.stt_backend == "stub":
        return StubSTTAdapter()
    if settings.stt_backend == "whisper":
        return LocalWhisperSTTAdapter()
    return ColabSTTAdapter(settings)


def get_text_to_speech_port() -> TextToSpeechPort:
    """Picks the TextToSpeechPort implementation via Settings.tts_backend.

    Same construction-is-cheap reasoning as get_speech_to_text_port() above.
    """

    settings = get_settings()
    if settings.tts_backend == "stub":
        return StubTTSAdapter()
    if settings.tts_backend == "gtts":
        return LocalTTSAdapter()
    return ColabTTSAdapter(settings)


def get_tts_content_type(
    text_to_speech: TextToSpeechPort = Depends(get_text_to_speech_port),
) -> str:
    """MIME type of the bytes TextToSpeechPort.synthesize() returns. Not part
    of the port itself (its interface is audio bytes only). Every adapter
    (StubTTSAdapter, ColabTTSAdapter, legacy LocalTTSAdapter) returns WAV --
    LocalTTSAdapter re-encodes gTTS's native MP3 output to WAV itself, and the
    Colab notebook's /tts endpoint is written to return WAV directly (see
    each adapter's module docstring) -- specifically so this stays a single
    constant instead of branching on which concrete adapter
    get_text_to_speech_port returned. Kept as a Depends()-resolved function
    rather than a bare constant so a future adapter that returns a different
    format only has to change this one place.
    """

    del text_to_speech  # unused now that every adapter returns the same format; kept for signature stability
    return "audio/wav"


def get_transcribe_audio_use_case(
    speech_to_text: SpeechToTextPort = Depends(get_speech_to_text_port),
) -> TranscribeAudioUseCase:
    return TranscribeAudioUseCase(speech_to_text)


def get_post_message_use_case_with_tts(
    uow_factory: Callable[[], AbstractUnitOfWork] = Depends(get_uow_factory),
    reply_generator: PatientReplyGenerator = Depends(get_patient_reply_generator),
    evidence_retriever: EvidenceRetriever = Depends(get_evidence_retriever),
    concurrency_guard: SessionConcurrencyGuard = Depends(get_session_concurrency_guard),
    push_port: ReplyPushPort = Depends(get_reply_push_port),
    text_to_speech: TextToSpeechPort = Depends(get_text_to_speech_port),
) -> PostMessageAsyncUseCase:
    """Same construction as get_post_message_use_case, but always with
    text_to_speech set -- used only by the voice path (see
    get_process_voice_chat_use_case below), so the background phase also
    synthesizes audio for the finished reply before pushing it."""

    return PostMessageAsyncUseCase(
        uow_factory=uow_factory,
        reply_generator=reply_generator,
        evidence_retriever=evidence_retriever,
        settings=get_settings(),
        concurrency_guard=concurrency_guard,
        push_port=push_port,
        text_to_speech=text_to_speech,
    )


def get_process_voice_chat_use_case(
    speech_to_text: SpeechToTextPort = Depends(get_speech_to_text_port),
    post_message_use_case: PostMessageAsyncUseCase = Depends(get_post_message_use_case_with_tts),
) -> ProcessVoiceChatAsyncUseCase:
    """What app/api/routers/voice.py depends on for POST /sessions/{id}/chat-voice
    and WS /ws/voice. Unconditionally ProcessVoiceChatAsyncUseCase now, same
    reasoning as get_post_message_use_case above -- no fast synchronous
    backend left in production. ProcessVoiceChatUseCase (still used directly
    by tests -- see tests/conftest.py) is the synchronous equivalent, kept
    for exactly that purpose."""

    return ProcessVoiceChatAsyncUseCase(speech_to_text, post_message_use_case)
