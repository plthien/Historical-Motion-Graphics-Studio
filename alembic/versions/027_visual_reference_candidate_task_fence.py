"""fence Visual Reference candidate materialization by durable task

Revision ID: 027_visual_reference_candidate_task_fence
Revises: 026_visual_reference_working_state_v2
Create Date: 2026-08-18 08:58:00

A generated durable operation task may materialize at most one immutable Visual
Reference candidate. NULL source_operation_task_id remains available for future
non-generated/upload candidates.
"""

from alembic import op


revision = "027_visual_reference_candidate_task_fence"
down_revision = "026_visual_reference_working_state_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ux_visual_reference_candidates_operation_task",
        "visual_reference_candidates",
        ["source_operation_task_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ux_visual_reference_candidates_operation_task",
        table_name="visual_reference_candidates",
    )
