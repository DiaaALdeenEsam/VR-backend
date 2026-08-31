from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import (
    get_db_session,
    get_patient_reply_generator,
    get_speech_to_text_port,
    get_text_to_speech_port,
    get_uow,
)
from app.infrastructure.db.repositories.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.patient_reply import StubPatientReplyGenerator
from app.infrastructure.stub_stt import StubSTTAdapter
from app.infrastructure.stub_tts import StubTTSAdapter
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
ALEMBIC_SCRIPT_LOCATION = BACKEND_DIR / "alembic"


def _run_upgrade(connection: Connection, alembic_cfg: Config) -> None:
    alembic_cfg.attributes["connection"] = connection
    command.upgrade(alembic_cfg, "head")


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """A fresh in-memory SQLite DB per test, schema created via Alembic migrations."""

    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("script_location", str(ALEMBIC_SCRIPT_LOCATION))

    async with test_engine.begin() as conn:
        await conn.run_sync(_run_upgrade, alembic_cfg)

    yield test_engine

    await test_engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def app(session_factory: async_sessionmaker[AsyncSession]):
    fastapi_app = create_app()

    async def override_get_db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    def override_get_uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    fastapi_app.dependency_overrides[get_db_session] = override_get_db_session
    fastapi_app.dependency_overrides[get_uow] = override_get_uow

    # The default app config wires the real (Qwen) patient-reply generator (see
    # app/api/deps.py::get_patient_reply_generator), but this general-purpose
    # `app`/`client` fixture pair is used by the broad session/DB-flow tests,
    # which care about persistence mechanics, not model output -- so keep them
    # fast and deterministic with the stub here. tests/test_qwen_patient_generator.py
    # overrides this same dependency back to the real generator for the tests
    # that specifically exercise it.
    fastapi_app.dependency_overrides[get_patient_reply_generator] = lambda: StubPatientReplyGenerator()

    # Same reasoning as get_patient_reply_generator above, extended to the voice
    # pipeline: Settings defaults to the real (whisper/gtts) backends, but this
    # shared fixture keeps the general test suite fast/deterministic/offline.
    # tests/test_voice.py overrides these back to the real adapters for the
    # tests that specifically exercise them.
    fastapi_app.dependency_overrides[get_speech_to_text_port] = lambda: StubSTTAdapter()
    fastapi_app.dependency_overrides[get_text_to_speech_port] = lambda: StubTTSAdapter()

    yield fastapi_app


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def seed_ids(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """Inserts one scenario, one test category+test, and one question+2 choices.

    Returns the ids assigned by the DB, keyed by name, for tests to reference.
    """

    from app.infrastructure.db.models import (
        ChoiceModel,
        QuestionModel,
        ScenarioModel,
        TestCategoryModel,
        TestModel,
    )

    async with session_factory() as session:
        scenario = ScenarioModel(
            name="حالة اختبار",
            case_text="نص الحالة السريرية الكامل (مخفي عن الطبيب المتدرب).",
            gold_standard="التشخيص المرجعي",
        )
        session.add(scenario)
        await session.flush()

        category = TestCategoryModel(name="Vital")
        session.add(category)
        await session.flush()

        test = TestModel(category_id=category.id, name="Blood Pressure", result="120/80 mmHg")
        session.add(test)
        await session.flush()

        question = QuestionModel(scenario_id=scenario.id, text="ما هو التشخيص الأرجح؟", correct_choice_id=-1)
        session.add(question)
        await session.flush()

        choice_a = ChoiceModel(question_id=question.id, text="التهاب رئوي")
        choice_b = ChoiceModel(question_id=question.id, text="نوبة ربو")
        session.add(choice_a)
        session.add(choice_b)
        await session.flush()

        question.correct_choice_id = choice_a.id
        session.add(question)
        await session.commit()

        return {
            "scenario_id": scenario.id,
            "category_id": category.id,
            "test_id": test.id,
            "question_id": question.id,
            "correct_choice_id": choice_a.id,
            "wrong_choice_id": choice_b.id,
        }
