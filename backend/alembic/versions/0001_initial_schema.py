"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-24

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "test_categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
    )

    op.create_table(
        "tests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "category_id", sa.Integer(), sa.ForeignKey("test_categories.id"), nullable=False
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
    )
    op.create_index("ix_tests_category_id", "tests", ["category_id"])

    op.create_table(
        "scenarios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("case_text", sa.Text(), nullable=False),
        sa.Column("gold_standard", sa.Text(), nullable=True),
    )

    op.create_table(
        "questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scenario_id", sa.Integer(), sa.ForeignKey("scenarios.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        # Soft reference to choices.id -- see the comment on QuestionModel in
        # app/infrastructure/db/models.py for why this is not a DB-level FK.
        sa.Column("correct_choice_id", sa.Integer(), nullable=False),
    )
    op.create_index("ix_questions_scenario_id", "questions", ["scenario_id"])

    op.create_table(
        "choices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
    )
    op.create_index("ix_choices_question_id", "choices", ["question_id"])

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("scenario_id", sa.Integer(), sa.ForeignKey("scenarios.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_sessions_scenario_id", "sessions", ["scenario_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("role IN ('system', 'user', 'assistant')", name="ck_messages_role"),
    )
    op.create_index("ix_messages_session_id", "messages", ["session_id"])

    op.create_table(
        "ordered_tests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("test_id", sa.Integer(), sa.ForeignKey("tests.id"), nullable=False),
        sa.Column("ordered_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ordered_tests_session_id", "ordered_tests", ["session_id"])

    op.create_table(
        "answers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("choice_id", sa.Integer(), sa.ForeignKey("choices.id"), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column("answered_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("session_id", "question_id", name="uq_answers_session_question"),
    )


def downgrade() -> None:
    op.drop_table("answers")
    op.drop_table("ordered_tests")
    op.drop_table("messages")
    op.drop_table("sessions")
    op.drop_table("choices")
    op.drop_table("questions")
    op.drop_table("scenarios")
    op.drop_table("tests")
    op.drop_table("test_categories")
