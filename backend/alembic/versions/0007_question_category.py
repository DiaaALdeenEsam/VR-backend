"""question category

Adds a nullable `category` column to `questions`, used to tag a small subset
of a scenario's questions as belonging to the post-session multiple-choice
quiz (triggered client-side after a chat session ends -- disease name,
attack-severity classification, and management plan across its three stages:
immediate 0-30min, monitoring 30-60min, disposition decision). See
GetPostSessionQuizUseCase (app/application/use_cases/get_post_session_quiz.py)
and GET /sessions/{id}/quiz.

Deliberately reuses the existing questions/choices/answers tables rather than
adding a parallel quiz schema -- MCQ storage/scoring/upsert mechanics are
scenario-agnostic (the same precedent as ScenarioTestResultModel reusing the
global `tests` catalog instead of a per-scenario test table, migration 0004).
`category IS NULL` (the default -- every question written before this
migration, and any future plain OSCE question with no post-session-quiz role)
means "ordinary question, not part of the post-session quiz"; AnswerQuestionUseCase
and GetSessionReviewUseCase are completely unaffected -- correctness scoring
and review already work identically for any question regardless of category.

Same batch-mode ALTER as 0005_message_status.py (SQLite can't add a
CHECK-constrained column to an existing table outside batch mode).

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-14

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("questions") as batch_op:
        batch_op.add_column(sa.Column("category", sa.String(length=32), nullable=True))
        batch_op.create_check_constraint(
            "ck_questions_category",
            "category IS NULL OR category IN "
            "('diagnosis', 'severity', 'management_immediate', 'management_monitoring', "
            "'management_disposition')",
        )


def downgrade() -> None:
    with op.batch_alter_table("questions") as batch_op:
        batch_op.drop_constraint("ck_questions_category", type_="check")
        batch_op.drop_column("category")
