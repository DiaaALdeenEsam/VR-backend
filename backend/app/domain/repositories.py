"""Abstract repository interfaces (one per aggregate) plus the Unit of Work.

Implementations live in app/infrastructure/db/repositories/. Use cases in
app/application/use_cases/ depend only on these interfaces, injected via their
constructors -- never on the concrete SQLModel-backed classes.

PatientReplyGenerator, EvaluationGenerator, SpeechToTextPort, and TextToSpeechPort
are not persistence repositories but "ports" to an external service (a real LLM,
a real speech model, eventually). They are declared here rather than in a fourth
file so the domain package stays exactly the three modules laid out for this
project; treat them as ports, not aggregate repositories.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Coroutine
from typing import Any

from app.domain import entities as e


class ScenarioRepository(ABC):
    @abstractmethod
    async def list_all(self) -> list[e.Scenario]: ...

    @abstractmethod
    async def get(self, scenario_id: int) -> e.Scenario | None: ...


class TestCategoryRepository(ABC):
    @abstractmethod
    async def list_all(self) -> list[e.TestCategory]: ...

    @abstractmethod
    async def get(self, category_id: int) -> e.TestCategory | None: ...


class TestRepository(ABC):
    @abstractmethod
    async def list_by_category(self, category_id: int) -> list[e.Test]: ...

    @abstractmethod
    async def get(self, test_id: int) -> e.Test | None: ...

    @abstractmethod
    async def get_scenario_override(self, scenario_id: int, test_id: int) -> str | None:
        """The scenario-specific result for this test, if one has been
        recorded (see ScenarioTestResultModel, migration 0004), else None.

        Returns a bare string, not a domain entity -- there's no standalone
        "ScenarioTestResult" concept used anywhere else; this is purely an
        override lookup on the way to resolving one Test's result for one
        scenario, the same way EvaluationGenerator's port takes primitives
        rather than entities for a similarly narrow, non-reused shape.
        """

    @abstractmethod
    async def list_scenario_relevant(self, scenario_id: int) -> list[e.Test]:
        """Tests that have a scenario-specific override for this scenario --
        i.e. what this scenario's case data actually says is clinically
        relevant, as opposed to the full global catalog. See
        GET /scenarios/{scenario_id}/relevant-tests."""


class QuestionRepository(ABC):
    @abstractmethod
    async def list_by_scenario(self, scenario_id: int) -> list[e.Question]: ...

    @abstractmethod
    async def get(self, question_id: int) -> e.Question | None: ...


class SessionRepository(ABC):
    @abstractmethod
    async def add(self, session: e.Session) -> e.Session: ...

    @abstractmethod
    async def get(self, session_id: str) -> e.Session | None: ...


class MessageRepository(ABC):
    @abstractmethod
    async def add(self, message: e.Message) -> e.Message: ...

    @abstractmethod
    async def list_by_session(self, session_id: str) -> list[e.Message]: ...

    @abstractmethod
    async def update_content(self, message_id: int, content: str, status: str) -> e.Message:
        """Overwrites an existing message row's `content`/`status` in place.

        Used only by the async reply-generation path (PostMessageAsyncUseCase)
        to fill in a placeholder ("pending"/"generating") row once the real
        reply is ready, or with fallback text once it's given up ("failed").
        The synchronous path never calls this -- it only ever creates rows
        that are "complete" from the start, via `add()`.
        """


class OrderedTestRepository(ABC):
    @abstractmethod
    async def add(self, ordered_test: e.OrderedTest) -> e.OrderedTest: ...

    @abstractmethod
    async def list_by_session(self, session_id: str) -> list[e.OrderedTest]: ...


class AnswerRepository(ABC):
    @abstractmethod
    async def upsert(self, answer: e.Answer) -> e.Answer:
        """Insert a new answer, or replace the prior answer for (session_id, question_id)."""

    @abstractmethod
    async def list_by_session(self, session_id: str) -> list[e.Answer]: ...


class PatientReplyGenerator(ABC):
    """Port for generating the simulated patient's reply to a chat message.

    The real implementation (an LLM call) is out of scope here; only the
    interface + a stub infrastructure implementation exist, so the DB flow is
    provable without wiring a real model.
    """

    @abstractmethod
    async def generate_reply(
        self,
        scenario: e.Scenario,
        history: list[e.Message],
        user_message: str,
        evidence: list[e.Evidence] | None = None,
    ) -> str:
        """`evidence` is RAG-retrieved context for `user_message` (see
        EvidenceRetriever below), or None/empty when retrieval wasn't
        attempted or failed. Optional with a default so this stays
        backward-compatible with callers that don't pass it. An
        implementation that does fold this into a prompt MUST NOT pass
        `evidence[i].text` through unfiltered -- see the curation-related note
        on the Evidence entity itself (app/domain/entities.py). The current
        sole implementation, RagPatientReplyGenerator
        (app/infrastructure/rag_patient_generator.py), ignores this parameter
        entirely -- the external RAG API's patient route does its own
        internal retrieval already.
        """


class EvaluationGenerator(ABC):
    """Port for generating an OSCE-style evaluation of a completed session.

    Deliberately takes primitives (case_text/gold_standard/messages-as-dicts/
    ordered_tests-as-dicts/answers-as-dicts/relevant_test_ids/total_questions)
    rather than domain entities directly -- keeps this port trivially
    serializable for a future real LLM-backed implementation (e.g. dropping
    these straight into a prompt or an API payload) without coupling it to
    this project's entity shapes. EvaluateSessionUseCase does the translation
    from domain entities to these primitives.
    """

    @abstractmethod
    async def evaluate(
        self,
        case_text: str,
        gold_standard: str,
        messages: list[dict[str, str]],
        ordered_tests: list[dict],
        answers: list[dict],
        relevant_test_ids: list[int],
        total_questions: int,
    ) -> e.SessionEvaluation:
        """Grades a session against a scenario's gold standard and its
        investigation-ordering / quiz activity.

        ordered_tests: one dict per test the doctor ordered this session, in
            order, each `{"test_id": int}` -- deliberately minimal, matching
            `messages`' own minimal shape; nothing here needs `ordered_at` or
            the test's name/result.
        answers: one dict per quiz question the doctor has answered this
            session, each `{"question_id": int, "choice_id": int,
            "is_correct": bool}`. Correctness is precomputed by
            AnswerQuestionUseCase, not recomputed here.
        relevant_test_ids: the scenario's clinically-relevant test ids (see
            ScenarioTestResultModel / GET /scenarios/{id}/relevant-tests) --
            what `ordered_tests` should be judged against for appropriateness.
        total_questions: how many quiz questions exist for the scenario in
            total, not just how many are in `answers` -- an implementation
            grading quiz accuracy out of this (rather than out of
            `len(answers)`) is expected to penalize unanswered questions,
            not just wrong ones.
        """


class SpeechToTextPort(ABC):
    """Port for transcribing spoken audio into text.

    Takes raw audio bytes rather than a domain entity -- there's no "Audio"
    domain concept here, just an external capability the application layer
    calls into, the same shape as PatientReplyGenerator/EvaluationGenerator.
    """

    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, filename: str | None = None) -> str: ...


class EvidenceRetriever(ABC):
    """Port for retrieving ranked clinical evidence from a RAG (retrieval-only)
    API, keyed on a doctor's chat message. Same shape as
    PatientReplyGenerator/SpeechToTextPort/TextToSpeechPort -- an external
    capability the application layer calls into, not a persistence
    repository.

    Retrieval failure is expected to be non-fatal to a chat turn (see
    PostMessageUseCase.execute()): callers should be prepared to catch
    RagServiceUnavailableError (app/domain/exceptions.py) around this and
    proceed with no evidence rather than let a RAG outage break the
    conversation.
    """

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        content_types: list[str] | None = None,
    ) -> list[e.Evidence]: ...


class TextToSpeechPort(ABC):
    """Port for synthesizing spoken audio from text.

    Returns raw audio bytes. Every adapter returns WAV -- the gTTS-backed
    adapter re-encodes gTTS's native MP3 output to WAV before returning it
    (see local_tts_adapter.py) -- so app/api/deps.py's get_tts_content_type()
    is a fixed "audio/wav" rather than something resolved per-adapter, even
    though the port signature itself carries no format metadata.
    """

    @abstractmethod
    async def synthesize(self, text: str) -> bytes: ...


class SessionConcurrencyGuard(ABC):
    """Port for tracking whether a session already has a reply being
    generated in the background -- backs PostMessageAsyncUseCase's
    concurrency policy (reject a second message with SessionBusyError while
    one is in flight; see that use case's module docstring for why).

    Declared here, not just written directly against
    app/infrastructure/inflight_sessions.py's module-level dict, for the same
    reason every other external capability in this file is a port: so
    PostMessageAsyncUseCase depends only on this interface, not on how
    "in flight" is actually tracked (today, an in-process dict -- see that
    module's docstring for why a real task queue isn't warranted yet).
    """

    @abstractmethod
    def is_busy(self, session_id: str) -> bool: ...

    @abstractmethod
    def start(self, session_id: str, coro: Coroutine[Any, Any, None]) -> None:
        """Schedules `coro` to run in the background for this session and
        marks the session busy until it finishes. Callers must have already
        checked is_busy(session_id) is False; this does not itself enforce
        the one-in-flight-per-session rule -- see the note on
        PostMessageAsyncUseCase.execute()."""


class ReplyPushPort(ABC):
    """Port for pushing an async-generated reply (and, for voice, its audio)
    to whatever live connection a session has -- see
    app/infrastructure/ws_hub.py (backed by WS /ws/voice today).

    A push with nothing listening is expected to be a silent no-op (the
    caller has no way to know whether a client happens to be connected right
    now), not an error -- see PostMessageAsyncUseCase, which never checks the
    return value for control flow, only for its own diagnostics/tests.
    """

    @abstractmethod
    async def push(self, session_id: str, payload: dict) -> bool: ...

    @abstractmethod
    async def push_bytes(self, session_id: str, data: bytes) -> bool: ...


class AbstractUnitOfWork(ABC):
    """Groups all repositories behind a single transaction boundary.

    Every mutating use case does its work inside `async with uow:` and calls
    `await uow.commit()` exactly once at the end. If the block exits without a
    commit (an exception, or simply returning early), __aexit__ rolls back --
    so there is never a partial write.
    """

    scenarios: ScenarioRepository
    test_categories: TestCategoryRepository
    tests: TestRepository
    questions: QuestionRepository
    sessions: SessionRepository
    messages: MessageRepository
    ordered_tests: OrderedTestRepository
    answers: AnswerRepository

    async def __aenter__(self) -> AbstractUnitOfWork:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        # Always attempt a rollback on exit. If commit() already ran, this is a
        # no-op (there is nothing left in the transaction to undo).
        await self.rollback()

    @abstractmethod
    async def commit(self) -> None: ...

    @abstractmethod
    async def rollback(self) -> None: ...
