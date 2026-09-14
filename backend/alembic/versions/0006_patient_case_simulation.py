"""patient case simulation

Adds 16 new tables under `scenarios` for a full ER-style patient-case
vignette layer -- name/demographics, presenting complaint, associated
symptoms, PMH, current medications, triggers, family/social history, vital
signs, physical exam findings, investigations, severity criteria, warning
signs, management phases, disposition criteria, discharge plan, and learning
objectives. See docs/scenario-clinical-schema-mapping.md for how this layer
relates to the 21 disease-reference tables added in 0002-0004.

Same two shapes as 0002: 1:1 "header" tables (PK = scenario_id, doubling as
the FK) for singular per-scenario data (identity, presenting complaint), and
1:N "item" tables (auto-increment id + indexed scenario_id FK) for
repeating data. Every FK is `ondelete="CASCADE"`, matching every other
scenario-child table.

Purely additive: does not touch test_categories, tests, scenarios' existing
columns, questions, choices, sessions, messages, ordered_tests, answers, or
any of the 21 clinical-section / patient-profile / test-result tables from
0002-0004, or messages.status from 0005.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-14

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Patient identity (1:1) ---------------------------------------------
    op.create_table(
        "case_patient_identity",
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("sex", sa.String(length=16), nullable=True),
        sa.Column("occupation", sa.Text(), nullable=True),
        sa.Column("nationality", sa.Text(), nullable=True),
        sa.Column("marital_status", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "sex IS NULL OR sex IN ('male', 'female')", name="ck_case_patient_identity_sex"
        ),
    )
    op.create_index(
        "ix_case_patient_identity_scenario_id", "case_patient_identity", ["scenario_id"]
    )

    # --- Presenting complaint (1:1) -----------------------------------------
    op.create_table(
        "case_presenting_complaint",
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("chief_complaint", sa.Text(), nullable=True),
        sa.Column("hpi_narrative", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_case_presenting_complaint_scenario_id", "case_presenting_complaint", ["scenario_id"]
    )

    # --- Associated symptoms (1:N) ------------------------------------------
    op.create_table(
        "case_associated_symptoms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("symptom", sa.Text(), nullable=False),
        sa.Column("is_present", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "ix_case_associated_symptoms_scenario_id", "case_associated_symptoms", ["scenario_id"]
    )

    # --- Past medical history (1:N) -----------------------------------------
    op.create_table(
        "case_past_medical_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_case_past_medical_history_scenario_id", "case_past_medical_history", ["scenario_id"]
    )

    # --- Current medications (1:N) ------------------------------------------
    op.create_table(
        "case_current_medications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("drug_name", sa.Text(), nullable=False),
        sa.Column("dose", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_case_current_medications_scenario_id", "case_current_medications", ["scenario_id"]
    )

    # --- Triggers (1:N) -------------------------------------------------------
    op.create_table(
        "case_triggers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_case_triggers_scenario_id", "case_triggers", ["scenario_id"])

    # --- Family/social history (1:N) ----------------------------------------
    op.create_table(
        "case_family_social_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("item", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "category IN ('family', 'social')", name="ck_case_family_social_history_category"
        ),
    )
    op.create_index(
        "ix_case_family_social_history_scenario_id", "case_family_social_history", ["scenario_id"]
    )

    # --- Vital signs (1:N) ----------------------------------------------------
    op.create_table(
        "case_vital_signs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("parameter", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("interpretation", sa.Text(), nullable=True),
    )
    op.create_index("ix_case_vital_signs_scenario_id", "case_vital_signs", ["scenario_id"])

    # --- Physical exam findings (1:N) ----------------------------------------
    op.create_table(
        "case_physical_exam_findings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("finding", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "method IN ('inspection', 'palpation', 'percussion', 'auscultation')",
            name="ck_case_physical_exam_findings_method",
        ),
    )
    op.create_index(
        "ix_case_physical_exam_findings_scenario_id", "case_physical_exam_findings", ["scenario_id"]
    )

    # --- Investigations (1:N) --------------------------------------------------
    op.create_table(
        "case_investigations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("test_name", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("interpretation", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "category IN ('immediate', 'laboratory', 'imaging')", name="ck_case_investigations_category"
        ),
    )
    op.create_index("ix_case_investigations_scenario_id", "case_investigations", ["scenario_id"])

    # --- Severity criteria (1:N) ----------------------------------------------
    op.create_table(
        "case_severity_criteria",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("criterion", sa.Text(), nullable=False),
        sa.Column("patient_value", sa.Text(), nullable=False),
        sa.Column("classification", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_case_severity_criteria_scenario_id", "case_severity_criteria", ["scenario_id"]
    )

    # --- Warning signs (1:N) ----------------------------------------------------
    op.create_table(
        "case_warning_signs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sign", sa.Text(), nullable=False),
        sa.Column("is_present", sa.Boolean(), nullable=True),
    )
    op.create_index("ix_case_warning_signs_scenario_id", "case_warning_signs", ["scenario_id"])

    # --- Management phases (1:N) -------------------------------------------
    op.create_table(
        "case_management_phases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("treatment", sa.Text(), nullable=False),
        sa.Column("dose_route", sa.Text(), nullable=True),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("sequence_order", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "phase IN ('immediate', 'monitoring', 'disposition')", name="ck_case_management_phases_phase"
        ),
    )
    op.create_index(
        "ix_case_management_phases_scenario_id", "case_management_phases", ["scenario_id"]
    )

    # --- Disposition criteria (1:N) -----------------------------------------
    op.create_table(
        "case_disposition_criteria",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("criterion", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "type IN ('admission', 'discharge')", name="ck_case_disposition_criteria_type"
        ),
    )
    op.create_index(
        "ix_case_disposition_criteria_scenario_id", "case_disposition_criteria", ["scenario_id"]
    )

    # --- Discharge plan (1:N) --------------------------------------------------
    op.create_table(
        "case_discharge_plan",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "category IN ('medication', 'education', 'follow_up', 'referral')",
            name="ck_case_discharge_plan_category",
        ),
    )
    op.create_index("ix_case_discharge_plan_scenario_id", "case_discharge_plan", ["scenario_id"])

    # --- Learning objectives (1:N) -------------------------------------------
    op.create_table(
        "case_learning_objectives",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("objective_number", sa.Integer(), nullable=False),
        sa.Column("objective_text", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_case_learning_objectives_scenario_id", "case_learning_objectives", ["scenario_id"]
    )


def downgrade() -> None:
    op.drop_table("case_learning_objectives")
    op.drop_table("case_discharge_plan")
    op.drop_table("case_disposition_criteria")
    op.drop_table("case_management_phases")
    op.drop_table("case_warning_signs")
    op.drop_table("case_severity_criteria")
    op.drop_table("case_investigations")
    op.drop_table("case_physical_exam_findings")
    op.drop_table("case_vital_signs")
    op.drop_table("case_family_social_history")
    op.drop_table("case_triggers")
    op.drop_table("case_current_medications")
    op.drop_table("case_past_medical_history")
    op.drop_table("case_associated_symptoms")
    op.drop_table("case_presenting_complaint")
    op.drop_table("case_patient_identity")
