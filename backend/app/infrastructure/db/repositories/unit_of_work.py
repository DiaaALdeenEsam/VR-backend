from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domain.repositories import AbstractUnitOfWork
from app.infrastructure.db.repositories.answer_repository import SqlAnswerRepository
from app.infrastructure.db.repositories.message_repository import SqlMessageRepository
from app.infrastructure.db.repositories.ordered_test_repository import SqlOrderedTestRepository
from app.infrastructure.db.repositories.question_repository import SqlQuestionRepository
from app.infrastructure.db.repositories.scenario_repository import SqlScenarioRepository
from app.infrastructure.db.repositories.session_repository import SqlSessionRepository
from app.infrastructure.db.repositories.test_category_repository import SqlTestCategoryRepository
from app.infrastructure.db.repositories.test_repository import SqlTestRepository


class SqlAlchemyUnitOfWork(AbstractUnitOfWork):
    """SQLAlchemy/SQLModel-backed Unit of Work.

    A fresh AsyncSession is opened on __aenter__ and closed on __aexit__, so
    each `async with SqlAlchemyUnitOfWork(...):` block is exactly one
    transaction against exactly one connection.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        self._session = self._session_factory()
        self.scenarios = SqlScenarioRepository(self._session)
        self.test_categories = SqlTestCategoryRepository(self._session)
        self.tests = SqlTestRepository(self._session)
        self.questions = SqlQuestionRepository(self._session)
        self.sessions = SqlSessionRepository(self._session)
        self.messages = SqlMessageRepository(self._session)
        self.ordered_tests = SqlOrderedTestRepository(self._session)
        self.answers = SqlAnswerRepository(self._session)
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await super().__aexit__(exc_type, exc, tb)
        assert self._session is not None
        await self._session.close()
        self._session = None

    async def commit(self) -> None:
        assert self._session is not None
        await self._session.commit()

    async def rollback(self) -> None:
        if self._session is not None:
            await self._session.rollback()
