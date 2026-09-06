"""scenario patient profile

Adds one new table, `scenario_patient_profile`: a derived/default virtual
patient profile per scenario (age/sex/geographical context), parsed out of
section 2's (risk_factors.epidemiology) free text by
scripts/import_scenario_from_json.py.

Purely additive: does not touch any of the 21 clinical-section tables added
in 0002, or any other existing table/column.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-02

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scenario_patient_profile",
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("age_group_raw", sa.Text(), nullable=True),
        sa.Column("sex", sa.String(length=16), nullable=True),
        sa.Column("sex_predominance_raw", sa.Text(), nullable=True),
        sa.Column("geographical_context", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "sex IS NULL OR sex IN ('male', 'female')", name="ck_scenario_patient_profile_sex"
        ),
    )
    op.create_index(
        "ix_scenario_patient_profile_scenario_id", "scenario_patient_profile", ["scenario_id"]
    )


def downgrade() -> None:
    op.drop_table("scenario_patient_profile")
