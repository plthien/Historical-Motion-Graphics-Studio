"""add durable Voice workflow ownership fields

Revision ID: 028_durable_voice_workflow
Revises: 027_visual_reference_candidate_task_fence
Create Date: 2026-08-18 19:58:00

Voice remains on its existing physical domain tables. This migration adds only
provider-neutral durable scheduling, reconcile fencing, cancellation, submission,
and execution-attempt identity required by the shared Durable Media Workflow Host.
"""

from alembic import op
import sqlalchemy as sa


revision = "028_durable_voice_workflow"
down_revision = "027_visual_reference_candidate_task_fence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("voice_generation_operations") as batch:
        batch.add_column(sa.Column("next_reconcile_at", sa.String(), nullable=True))
        batch.add_column(sa.Column("reconcile_owner", sa.String(), nullable=True))
        batch.add_column(sa.Column("reconcile_token", sa.String(), nullable=True))
        batch.add_column(sa.Column("reconcile_lease_expires_at", sa.String(), nullable=True))
        batch.add_column(sa.Column("cancel_requested_at", sa.String(), nullable=True))
        batch.add_column(sa.Column("cancelled_at", sa.String(), nullable=True))
        batch.add_column(sa.Column("state_revision", sa.Integer(), nullable=False, server_default="0"))

    op.create_index(
        "ix_voice_generation_operations_due",
        "voice_generation_operations",
        ["status", "next_reconcile_at"],
        unique=False,
    )
    op.create_index(
        "ix_voice_generation_operations_reconcile_lease",
        "voice_generation_operations",
        ["reconcile_lease_expires_at"],
        unique=False,
    )

    with op.batch_alter_table("voice_segment_tasks") as batch:
        batch.add_column(sa.Column("next_reconcile_at", sa.String(), nullable=True))
        batch.add_column(sa.Column("submit_attempt_token", sa.String(), nullable=True))
        batch.add_column(sa.Column("submit_started_at", sa.String(), nullable=True))
        batch.add_column(sa.Column("execution_attempt_token", sa.String(), nullable=True))
        batch.add_column(sa.Column("execution_started_at", sa.String(), nullable=True))
        batch.add_column(sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"))
        batch.add_column(sa.Column("last_retry_at", sa.String(), nullable=True))
        batch.add_column(sa.Column("provider_handle_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("state_revision", sa.Integer(), nullable=False, server_default="0"))

    op.create_index(
        "ix_voice_segment_tasks_due",
        "voice_segment_tasks",
        ["operation_id", "status", "next_reconcile_at"],
        unique=False,
    )
    op.create_index(
        "ix_voice_segment_tasks_submit_started",
        "voice_segment_tasks",
        ["status", "submit_started_at"],
        unique=False,
    )
    op.create_index(
        "ix_voice_segment_tasks_execution_token",
        "voice_segment_tasks",
        ["execution_attempt_token"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_voice_segment_tasks_execution_token", table_name="voice_segment_tasks")
    op.drop_index("ix_voice_segment_tasks_submit_started", table_name="voice_segment_tasks")
    op.drop_index("ix_voice_segment_tasks_due", table_name="voice_segment_tasks")
    with op.batch_alter_table("voice_segment_tasks") as batch:
        batch.drop_column("state_revision")
        batch.drop_column("provider_handle_json")
        batch.drop_column("last_retry_at")
        batch.drop_column("max_attempts")
        batch.drop_column("retry_count")
        batch.drop_column("execution_started_at")
        batch.drop_column("execution_attempt_token")
        batch.drop_column("submit_started_at")
        batch.drop_column("submit_attempt_token")
        batch.drop_column("next_reconcile_at")

    op.drop_index("ix_voice_generation_operations_reconcile_lease", table_name="voice_generation_operations")
    op.drop_index("ix_voice_generation_operations_due", table_name="voice_generation_operations")
    with op.batch_alter_table("voice_generation_operations") as batch:
        batch.drop_column("state_revision")
        batch.drop_column("cancelled_at")
        batch.drop_column("cancel_requested_at")
        batch.drop_column("reconcile_lease_expires_at")
        batch.drop_column("reconcile_token")
        batch.drop_column("reconcile_owner")
        batch.drop_column("next_reconcile_at")
