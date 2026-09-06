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
        backward-compatible with callers that don't pass it. Implementations
        MUST NOT fold `evidence[i].text` into the model prompt unfiltered --
        see app/infrastructure/qwen_patient_generator.py for the curation
        step this port's real implementation applies before that happens.
        """


class EvaluationGenerator(ABC):
    """Port for generating an OSCE-style evaluation of a completed session.

    Deliberately takes primitives (case_text/gold_standard/messages-as-dicts)
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
    ) -> e.SessionEvaluation: ...


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
