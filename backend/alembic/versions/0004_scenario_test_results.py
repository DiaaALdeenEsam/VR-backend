"""scenario test results

Adds one new table, `scenario_test_results`: a per-scenario override of a
global `tests.result` value, keyed on (scenario_id, test_id). Lets the same
shared test catalog (test_categories/tests) be reused across scenarios while
each scenario states its own case-specific finding for whichever tests are
clinically relevant to it -- see OrderTestUseCase in
app/application/use_cases/order_test.py, which now checks this table before
falling back to the generic tests.result.

Purely additive: does not touch test_categories, tests, scenarios, or any of
the 21 clinical-section tables from 0002 / scenario_patient_profile from 0003.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-06

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scenario_test_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("test_id", sa.Integer(), sa.ForeignKey("tests.id"), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.UniqueConstraint("scenario_id", "test_id", name="uq_scenario_test_results_scenario_test"),
    )
    op.create_index(
        "ix_scenario_test_results_scenario_id", "scenario_test_results", ["scenario_id"]
    )
    op.create_index("ix_scenario_test_results_test_id", "scenario_test_results", ["test_id"])


def downgrade() -> None:
    op.drop_table("scenario_test_results")
