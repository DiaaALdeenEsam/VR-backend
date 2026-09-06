"""scenario clinical sections

Adds 21 normalized child tables under `scenarios`, one set per numbered
section of the reference disease-template JSON (pathophysiology, risk
factors, symptoms & signs, lab/radiological investigations, differential
diagnosis, diagnostic criteria, complications, prevention, non-pharma
treatment, pharma treatment, interventional treatment). See
docs/scenario-clinical-schema-mapping.md for the full field mapping.

Purely additive: does not touch test_categories, tests, questions, choices,
sessions, messages, ordered_tests, answers, or the existing columns of
scenarios.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-02

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Section 1: pathophysiology (1:1) -----------------------------------
    op.create_table(
        "scenario_pathophysiology",
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("definition", sa.Text(), nullable=True),
        sa.Column("mechanism", sa.Text(), nullable=True),
        sa.Column("classification", sa.Text(), nullable=True),
        sa.Column("source_reference", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_scenario_pathophysiology_scenario_id", "scenario_pathophysiology", ["scenario_id"]
    )

    # --- Section 2: risk_factors (1:1 header + 1:N items) -------------------
    op.create_table(
        "scenario_risk_factors",
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("age_group", sa.Text(), nullable=True),
        sa.Column("sex_predominance", sa.Text(), nullable=True),
        sa.Column("geographical_prevalence", sa.Text(), nullable=True),
        sa.Column("source_reference", sa.Text(), nullable=True),
    )
    op.create_index("ix_scenario_risk_factors_scenario_id", "scenario_risk_factors", ["scenario_id"])

    op.create_table(
        "scenario_risk_factor_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "type IN ('modifiable', 'non_modifiable')", name="ck_scenario_risk_factor_items_type"
        ),
    )
    op.create_index(
        "ix_scenario_risk_factor_items_scenario_id", "scenario_risk_factor_items", ["scenario_id"]
    )

    # --- Section 3: symptoms_and_signs (1:1 header + 3 item tables) ---------
    op.create_table(
        "scenario_symptoms_signs",
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source_reference", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_scenario_symptoms_signs_scenario_id", "scenario_symptoms_signs", ["scenario_id"]
    )

    op.create_table(
        "scenario_symptom_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.CheckConstraint("type IN ('cardinal', 'other')", name="ck_scenario_symptom_items_type"),
    )
    op.create_index("ix_scenario_symptom_items_scenario_id", "scenario_symptom_items", ["scenario_id"])

    op.create_table(
        "scenario_clinical_sign_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "category IN ('inspection', 'palpation', 'percussion', 'auscultation')",
            name="ck_scenario_clinical_sign_items_category",
        ),
    )
    op.create_index(
        "ix_scenario_clinical_sign_items_scenario_id", "scenario_clinical_sign_items", ["scenario_id"]
    )

    op.create_table(
        "scenario_atypical_presentation_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("value", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_scenario_atypical_presentation_items_scenario_id",
        "scenario_atypical_presentation_items",
        ["scenario_id"],
    )

    # --- Section 4: laboratory_investigations (1:N) -------------------------
    op.create_table(
        "scenario_lab_investigations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("test_name", sa.Text(), nullable=True),
        sa.Column("expected_finding", sa.Text(), nullable=True),
        sa.Column("clinical_significance", sa.Text(), nullable=True),
        sa.Column("priority", sa.String(length=16), nullable=True),
        sa.CheckConstraint(
            "priority IS NULL OR priority IN ('essential', 'confirmatory', 'optional')",
            name="ck_scenario_lab_investigations_priority",
        ),
    )
    op.create_index(
        "ix_scenario_lab_investigations_scenario_id", "scenario_lab_investigations", ["scenario_id"]
    )

    # --- Section 5: radiological_investigations (1:N) -----------------------
    op.create_table(
        "scenario_radiological_investigations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("modality", sa.Text(), nullable=True),
        sa.Column("expected_finding", sa.Text(), nullable=True),
        sa.Column("clinical_significance", sa.Text(), nullable=True),
        sa.Column("priority", sa.String(length=16), nullable=True),
        sa.CheckConstraint(
            "priority IS NULL OR priority IN ('essential', 'confirmatory', 'optional')",
            name="ck_scenario_radiological_investigations_priority",
        ),
    )
    op.create_index(
        "ix_scenario_radiological_investigations_scenario_id",
        "scenario_radiological_investigations",
        ["scenario_id"],
    )

    # --- Section 6: differential_diagnosis (1:N) ----------------------------
    op.create_table(
        "scenario_differential_diagnoses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("condition", sa.Text(), nullable=True),
        sa.Column("distinguishing_feature", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_scenario_differential_diagnoses_scenario_id",
        "scenario_differential_diagnoses",
        ["scenario_id"],
    )

    # --- Section 7: diagnostic_criteria (1:1 header + 1:N items) ------------
    op.create_table(
        "scenario_diagnostic_criteria",
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("criteria_name", sa.Text(), nullable=True),
        sa.Column("scoring_system", sa.Text(), nullable=True),
        sa.Column("source_reference", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_scenario_diagnostic_criteria_scenario_id", "scenario_diagnostic_criteria", ["scenario_id"]
    )

    op.create_table(
        "scenario_diagnostic_criteria_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("criterion_text", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_scenario_diagnostic_criteria_items_scenario_id",
        "scenario_diagnostic_criteria_items",
        ["scenario_id"],
    )

    # --- Section 8: complications (1:N) --------------------------------------
    op.create_table(
        "scenario_complications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("complication", sa.Text(), nullable=False),
        sa.Column("timing", sa.String(length=16), nullable=True),
        sa.Column("frequency", sa.String(length=16), nullable=True),
        sa.CheckConstraint(
            "timing IS NULL OR timing IN ('acute', 'subacute', 'chronic')",
            name="ck_scenario_complications_timing",
        ),
        sa.CheckConstraint(
            "frequency IS NULL OR frequency IN ('common', 'uncommon', 'rare')",
            name="ck_scenario_complications_frequency",
        ),
    )
    op.create_index("ix_scenario_complications_scenario_id", "scenario_complications", ["scenario_id"])

    # --- Section 9: prevention (1:1 header + 1:N items) ----------------------
    op.create_table(
        "scenario_prevention",
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("screening", sa.Text(), nullable=True),
        sa.Column("vaccination", sa.Text(), nullable=True),
        sa.Column("source_reference", sa.Text(), nullable=True),
    )
    op.create_index("ix_scenario_prevention_scenario_id", "scenario_prevention", ["scenario_id"])

    op.create_table(
        "scenario_prevention_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "type IN ('primary', 'secondary')", name="ck_scenario_prevention_items_type"
        ),
    )
    op.create_index(
        "ix_scenario_prevention_items_scenario_id", "scenario_prevention_items", ["scenario_id"]
    )

    # --- Section 10: non_pharmacological_treatment (1:1 header + 1:N items) -
    op.create_table(
        "scenario_non_pharma_treatment",
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("physical_activity", sa.Text(), nullable=True),
        sa.Column("source_reference", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_scenario_non_pharma_treatment_scenario_id", "scenario_non_pharma_treatment", ["scenario_id"]
    )

    op.create_table(
        "scenario_non_pharma_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "type IN ('lifestyle', 'dietary', 'other')", name="ck_scenario_non_pharma_items_type"
        ),
    )
    op.create_index(
        "ix_scenario_non_pharma_items_scenario_id", "scenario_non_pharma_items", ["scenario_id"]
    )

    # --- Section 11: pharmacological_treatment (1:1 header + 1:N items) -----
    op.create_table(
        "scenario_pharma_treatment",
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source_reference", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_scenario_pharma_treatment_scenario_id", "scenario_pharma_treatment", ["scenario_id"]
    )

    op.create_table(
        "scenario_pharma_drug_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("line", sa.String(length=32), nullable=False),
        sa.Column("drug_class", sa.Text(), nullable=True),
        sa.Column("drug_name", sa.Text(), nullable=True),
        sa.Column("dose", sa.Text(), nullable=True),
        sa.Column("route", sa.Text(), nullable=True),
        sa.Column("duration", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "line IN ('first_line', 'second_line', 'adjunct_therapy')",
            name="ck_scenario_pharma_drug_items_line",
        ),
    )
    op.create_index(
        "ix_scenario_pharma_drug_items_scenario_id", "scenario_pharma_drug_items", ["scenario_id"]
    )

    op.create_table(
        "scenario_drugs_to_avoid_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("value", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_scenario_drugs_to_avoid_items_scenario_id", "scenario_drugs_to_avoid_items", ["scenario_id"]
    )

    # --- Section 12: interventional_treatment (1:N) --------------------------
    op.create_table(
        "scenario_interventional_treatments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("procedure", sa.Text(), nullable=True),
        sa.Column("indication", sa.Text(), nullable=True),
        sa.Column("timing", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_scenario_interventional_treatments_scenario_id",
        "scenario_interventional_treatments",
        ["scenario_id"],
    )


def downgrade() -> None:
    op.drop_table("scenario_interventional_treatments")
    op.drop_table("scenario_drugs_to_avoid_items")
    op.drop_table("scenario_pharma_drug_items")
    op.drop_table("scenario_pharma_treatment")
    op.drop_table("scenario_non_pharma_items")
    op.drop_table("scenario_non_pharma_treatment")
    op.drop_table("scenario_prevention_items")
    op.drop_table("scenario_prevention")
    op.drop_table("scenario_complications")
    op.drop_table("scenario_diagnostic_criteria_items")
    op.drop_table("scenario_diagnostic_criteria")
    op.drop_table("scenario_differential_diagnoses")
    op.drop_table("scenario_radiological_investigations")
    op.drop_table("scenario_lab_investigations")
    op.drop_table("scenario_atypical_presentation_items")
    op.drop_table("scenario_clinical_sign_items")
    op.drop_table("scenario_symptom_items")
    op.drop_table("scenario_symptoms_signs")
    op.drop_table("scenario_risk_factor_items")
    op.drop_table("scenario_risk_factors")
    op.drop_table("scenario_pathophysiology")
