"""SQLModel table models -- the actual ORM/database schema.

These mirror the schema created by the Alembic migrations under alembic/versions/.
Migrations are the source of truth for the schema (there is no create_all()
anywhere in this project); these classes exist only so the repositories in
app/infrastructure/db/repositories/ have something to read/write at runtime.

Do not import these (or anything from sqlmodel/sqlalchemy) outside of
app/infrastructure -- domain and application code only ever see
app/domain/entities.py.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class TestCategoryModel(SQLModel, table=True):
    __tablename__ = "test_categories"

    id: int | None = Field(default=None, primary_key=True)
    name: str


class TestModel(SQLModel, table=True):
    __tablename__ = "tests"

    id: int | None = Field(default=None, primary_key=True)
    category_id: int = Field(foreign_key="test_categories.id", index=True)
    name: str
    result: str


class ScenarioModel(SQLModel, table=True):
    __tablename__ = "scenarios"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    case_text: str
    gold_standard: str | None = None


class QuestionModel(SQLModel, table=True):
    __tablename__ = "questions"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", index=True)
    text: str
    # Soft reference to choices.id (the correct answer). Deliberately NOT a DB-level
    # foreign key: choices.question_id already points at questions.id, so a hard FK
    # here would create a circular dependency between the two tables. Integrity is
    # enforced in the application layer (AnswerQuestionUseCase validates choice
    # membership) and by the seed script (which resolves this only after choices
    # exist).
    correct_choice_id: int


class ChoiceModel(SQLModel, table=True):
    __tablename__ = "choices"

    id: int | None = Field(default=None, primary_key=True)
    question_id: int = Field(foreign_key="questions.id", index=True)
    text: str


class SessionModel(SQLModel, table=True):
    __tablename__ = "sessions"

    id: str = Field(primary_key=True)
    scenario_id: int = Field(foreign_key="scenarios.id", index=True)
    created_at: datetime


class MessageModel(SQLModel, table=True):
    __tablename__ = "messages"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="sessions.id", index=True)
    role: str
    content: str
    created_at: datetime


class OrderedTestModel(SQLModel, table=True):
    __tablename__ = "ordered_tests"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="sessions.id", index=True)
    test_id: int = Field(foreign_key="tests.id")
    ordered_at: datetime


class AnswerModel(SQLModel, table=True):
    __tablename__ = "answers"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="sessions.id", index=True)
    question_id: int = Field(foreign_key="questions.id")
    choice_id: int = Field(foreign_key="choices.id")
    is_correct: bool
    answered_at: datetime
