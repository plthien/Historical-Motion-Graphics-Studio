"""Add minimal external authoring Artifact reservations.

Revision ID: 021_external_authoring_reservations
Revises: 020_script_analysis_project_reference
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "021_external_authoring_reservations"
down_revision = "020_script_analysis_project_reference"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_authoring_reservations",
        sa.Column("primary_artifact_id", sa.String(), primary_key=True),
        sa.Column("paired_artifact_id", sa.String(), nullable=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("source_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("consumed_at", sa.String(), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'consumed')", name="ck_external_authoring_reservations_status"),
        sa.UniqueConstraint("paired_artifact_id", name="ux_external_authoring_reservations_paired_artifact_id"),
    )
    op.create_index("ix_external_authoring_reservations_paired_artifact_id", "external_authoring_reservations", ["paired_artifact_id"])
    op.create_index("ix_external_authoring_reservations_project_id", "external_authoring_reservations", ["project_id"])
    op.create_index("ix_external_authoring_reservations_stage", "external_authoring_reservations", ["stage"])
    op.create_index("ix_external_authoring_reservations_status", "external_authoring_reservations", ["status"])
    op.create_index("ix_external_authoring_reservations_created_at", "external_authoring_reservations", ["created_at"])
    op.create_index("ix_external_authoring_reservations_consumed_at", "external_authoring_reservations", ["consumed_at"])


def downgrade() -> None:
    op.drop_table("external_authoring_reservations")
