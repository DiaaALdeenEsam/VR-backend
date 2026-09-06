"""message status

Adds `status` to `messages`: distinguishes an immediately-available reply
(every message ever created before this migration, and every one the
synchronous PatientReplyGenerator backends -- "qwen"/"stub" -- still create)
from one generated asynchronously in the background by a slow backend (e.g.
"rag_api", 6-48s per call observed) -- see PostMessageAsyncUseCase in
app/application/use_cases/post_message_async.py.

Backfilled to 'complete' for every existing row via server_default, so this
is non-breaking for data written before this migration.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-06

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite can't ALTER a constraint onto an existing table outside of
    # "batch mode" (a copy-and-move strategy Alembic implements for exactly
    # this) -- unlike 0001's ck_messages_role, which was added at
    # op.create_table() time, this column+constraint are landing on a table
    # that already exists.
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(
            sa.Column("status", sa.String(length=16), nullable=False, server_default="complete")
        )
        batch_op.create_check_constraint(
            "ck_messages_status",
            "status IN ('pending', 'generating', 'complete', 'failed')",
        )


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_constraint("ck_messages_status", type_="check")
        batch_op.drop_column("status")
