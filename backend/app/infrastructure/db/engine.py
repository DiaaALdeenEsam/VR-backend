"""Async engine + session factory.

A module-level engine/session factory is created once at app startup (see
app/main.py's lifespan) and torn down at shutdown. Tests never touch these
globals -- they build their own engine bound to an in-memory SQLite DB and
override the FastAPI dependencies in app/api/deps.py directly.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine_from_settings(settings: Settings | None = None) -> AsyncEngine:
    settings = settings or get_settings()
    connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    return create_async_engine(settings.database_url, echo=settings.db_echo, connect_args=connect_args)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def init_engine(settings: Settings | None = None) -> None:
    """Create the process-wide engine/session factory. Called from app startup."""

    global _engine, _session_factory
    _engine = create_engine_from_settings(settings)
    _session_factory = create_session_factory(_engine)


async def dispose_engine() -> None:
    """Dispose the process-wide engine. Called from app shutdown."""

    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Returns the process-wide session factory, lazily initializing it if needed.

    Lazy init matters for one-off scripts (e.g. the seed script) that use the
    engine without going through the FastAPI lifespan.
    """

    if _session_factory is None:
        init_engine()
    assert _session_factory is not None
    return _session_factory
