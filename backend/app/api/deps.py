"""FastAPI Depends() providers.

get_db_session is used directly only by the health check (a plain connectivity
probe, not a use case). Every use case is built from get_uow, so all reads and
writes go through the same AbstractUnitOfWork / single-transaction pattern.

Tests override get_db_session and get_uow via app.dependency_overrides to point
at a throwaway in-memory database (see tests/conftest.py) -- nothing here needs
to change for that to work.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.application.use_cases.answer_question import AnswerQuestionUseCase
from app.application.use_cases.evaluate_session import EvaluateSessionUseCase
from app.application.use_cases.get_session_review import GetSessionReviewUseCase
from app.application.use_cases.list_questions import ListQuestionsUseCase
from app.application.use_cases.list_scenarios import ListScenariosUseCase
from app.application.use_cases.list_test_categories import ListTestCategoriesUseCase
from app.application.use_cases.list_tests_by_category import ListTestsByCategoryUseCase
from app.application.use_cases.order_test import OrderTestUseCase
from app.application.use_cases.post_message import PostMessageUseCase
from app.application.use_cases.process_voice_chat import ProcessVoiceChatUseCase
from app.application.use_cases.start_session import StartSessionUseCase
from app.application.use_cases.transcribe_audio import TranscribeAudioUseCase
from app.config import get_settings
from app.domain.repositories import (
    AbstractUnitOfWork,
    EvaluationGenerator,
    PatientReplyGenerator,
    SpeechToTextPort,
    TextToSpeechPort,
)
from app.infrastructure.db.engine import get_session_factory
from app.infrastructure.db.repositories.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.local_tts_adapter import LocalTTSAdapter
from app.infrastructure.local_whisper_stt_adapter import LocalWhisperSTTAdapter
from app.infrastructure.patient_reply import StubPatientReplyGenerator
from app.infrastructure.qwen_patient_generator import QwenPatientReplyGenerator
from app.infrastructure.stub_evaluator import StubEvaluationGenerator
from app.infrastructure.stub_stt import StubSTTAdapter
from app.infrastructure.stub_tts import StubTTSAdapter


async def get_db_session() -> AsyncIterator[AsyncSession]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session


def get_uow() -> AbstractUnitOfWork:
    return SqlAlchemyUnitOfWork(get_session_factory())


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


def get_start_session_use_case(uow: AbstractUnitOfWork = Depends(get_uow)) -> StartSessionUseCase:
    return StartSessionUseCase(uow)


def get_patient_reply_generator() -> PatientReplyGenerator:
    """Picks the PatientReplyGenerator implementation via Settings.patient_reply_backend.

    Constructing either implementation here is cheap either way: the stub is
    stateless, and QwenPatientReplyGenerator only triggers its (cached,
    module-level, load-once-per-process) model load on first actual use.
    """

    backend = get_settings().patient_reply_backend
    if backend == "stub":
        return StubPatientReplyGenerator()
    return QwenPatientReplyGenerator()


def get_post_message_use_case(
    uow: AbstractUnitOfWork = Depends(get_uow),
    reply_generator: PatientReplyGenerator = Depends(get_patient_reply_generator),
) -> PostMessageUseCase:
    return PostMessageUseCase(uow, reply_generator)


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
    """Picks the SpeechToTextPort implementation via Settings.stt_backend."""

    backend = get_settings().stt_backend
    if backend == "stub":
        return StubSTTAdapter()
    return LocalWhisperSTTAdapter()


def get_text_to_speech_port() -> TextToSpeechPort:
    """Picks the TextToSpeechPort implementation via Settings.tts_backend."""

    backend = get_settings().tts_backend
    if backend == "stub":
        return StubTTSAdapter()
    return LocalTTSAdapter()


def get_tts_content_type(
    text_to_speech: TextToSpeechPort = Depends(get_text_to_speech_port),
) -> str:
    """MIME type of the bytes TextToSpeechPort.synthesize() returns. Not part
    of the port itself (its interface is audio bytes only). Every adapter
    (StubTTSAdapter, LocalTTSAdapter) returns WAV -- LocalTTSAdapter
    re-encodes gTTS's native MP3 output to WAV itself (see its module
    docstring) specifically so this stays a single constant instead of
    branching on which concrete adapter get_text_to_speech_port returned.
    Kept as a Depends()-resolved function rather than a bare constant so a
    future adapter that returns a different format only has to change this
    one place.
    """

    del text_to_speech  # unused now that every adapter returns the same format; kept for signature stability
    return "audio/wav"


def get_transcribe_audio_use_case(
    speech_to_text: SpeechToTextPort = Depends(get_speech_to_text_port),
) -> TranscribeAudioUseCase:
    return TranscribeAudioUseCase(speech_to_text)


def get_process_voice_chat_use_case(
    speech_to_text: SpeechToTextPort = Depends(get_speech_to_text_port),
    post_message_use_case: PostMessageUseCase = Depends(get_post_message_use_case),
    text_to_speech: TextToSpeechPort = Depends(get_text_to_speech_port),
) -> ProcessVoiceChatUseCase:
    return ProcessVoiceChatUseCase(speech_to_text, post_message_use_case, text_to_speech)
