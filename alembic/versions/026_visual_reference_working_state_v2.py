"""add durable Visual Reference V2 working state

Revision ID: 026_visual_reference_working_state_v2
Revises: 025_scene_asset_prompt_revisions
Create Date: 2026-08-18 08:15:00

No legacy Visual Reference rows are backfilled. The new tables are the live V2
working-state foundation; immutable reference-set artifacts remain publication
snapshots and the old RQ workflow is cut over in later steps of the same change
series rather than dual-written.
"""

from alembic import op
import sqlalchemy as sa

revision = "026_visual_reference_working_state_v2"
down_revision = "025_scene_asset_prompt_revisions"
branch_labels = None
depends_on = None

ANCHOR_TYPES = "'character', 'location', 'prop', 'recurring_visual_subject'"


def upgrade() -> None:
    op.create_table(
        "visual_reference_working_operations",
        sa.Column("operation_id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("operation_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source_style_bible_artifact_id", sa.String(), sa.ForeignKey("artifacts.artifact_id"), nullable=False),
        sa.Column("expected_visual_reference_set_artifact_id", sa.String(), sa.ForeignKey("artifacts.artifact_id"), nullable=True),
        sa.Column("provider_settings_snapshot_json", sa.Text(), nullable=False),
        sa.Column("provider_settings_snapshot_hash", sa.String(), nullable=False),
        sa.Column("total_anchor_count", sa.Integer(), nullable=False),
        sa.Column("completed_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("cancelled_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_reconcile_at", sa.String(), nullable=True),
        sa.Column("last_progress_at", sa.String(), nullable=False),
        sa.Column("reconcile_owner", sa.String(), nullable=True),
        sa.Column("reconcile_token", sa.String(), nullable=True),
        sa.Column("reconcile_lease_expires_at", sa.String(), nullable=True),
        sa.Column("cancel_requested_at", sa.String(), nullable=True),
        sa.Column("cancelled_at", sa.String(), nullable=True),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("error_details_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.UniqueConstraint("operation_id", "project_id", name="ux_visual_reference_working_operations_identity_project"),
        sa.UniqueConstraint("reconcile_token", name="ux_visual_reference_working_operations_reconcile_token"),
        sa.CheckConstraint("operation_type IN ('generate_all','generate_anchor','regenerate_anchor','retry_failed')", name="ck_visual_reference_working_operations_type"),
        sa.CheckConstraint("status IN ('queued','running','finalizing','completed','partial','failed','cancelled','superseded')", name="ck_visual_reference_working_operations_status"),
        sa.CheckConstraint("total_anchor_count >= 1", name="ck_visual_reference_working_operations_total_positive"),
        sa.CheckConstraint("completed_count >= 0 AND failed_count >= 0 AND cancelled_count >= 0", name="ck_visual_reference_working_operations_counts_nonnegative"),
        sa.CheckConstraint("completed_count + failed_count + cancelled_count <= total_anchor_count", name="ck_visual_reference_working_operations_counts_bounded"),
        sa.CheckConstraint("(reconcile_owner IS NULL AND reconcile_token IS NULL AND reconcile_lease_expires_at IS NULL) OR (reconcile_owner IS NOT NULL AND reconcile_token IS NOT NULL AND reconcile_lease_expires_at IS NOT NULL)", name="ck_visual_reference_working_operations_reconcile_lease_complete"),
    )
    op.create_index("ix_visual_reference_working_operations_project_status", "visual_reference_working_operations", ["project_id", "status"])
    op.create_index("ix_visual_reference_working_operations_due", "visual_reference_working_operations", ["status", "next_reconcile_at"])
    op.create_index("ix_visual_reference_working_operations_lease_expiry", "visual_reference_working_operations", ["reconcile_lease_expires_at"])

    op.create_table(
        "visual_reference_prompt_revisions",
        sa.Column("prompt_revision_id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("anchor_type", sa.String(), nullable=False),
        sa.Column("anchor_id", sa.String(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("revision_kind", sa.String(), nullable=False),
        sa.Column("is_current", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("source_style_bible_artifact_id", sa.String(), sa.ForeignKey("artifacts.artifact_id"), nullable=False),
        sa.Column("source_definition_hash", sa.String(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("negative_prompt", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.UniqueConstraint("project_id", "anchor_type", "anchor_id", "revision_no", name="ux_visual_reference_prompt_revisions_anchor_revision"),
        sa.UniqueConstraint("prompt_revision_id", "project_id", "anchor_type", "anchor_id", name="ux_visual_reference_prompt_revisions_identity_owner"),
        sa.CheckConstraint("revision_no >= 1", name="ck_visual_reference_prompt_revisions_revision_positive"),
        sa.CheckConstraint(f"anchor_type IN ({ANCHOR_TYPES})", name="ck_visual_reference_prompt_revisions_anchor_type"),
        sa.CheckConstraint("revision_kind IN ('compiled','manual_edit','restore_compiled')", name="ck_visual_reference_prompt_revisions_kind"),
    )
    op.create_index("ux_visual_reference_prompt_revisions_current", "visual_reference_prompt_revisions", ["project_id", "anchor_type", "anchor_id"], unique=True, sqlite_where=sa.text("is_current = 1"))
    op.create_index("ix_visual_reference_prompt_revisions_style_bible", "visual_reference_prompt_revisions", ["source_style_bible_artifact_id"])

    op.create_table(
        "visual_reference_operation_anchors",
        sa.Column("operation_anchor_id", sa.String(), primary_key=True),
        sa.Column("operation_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("anchor_type", sa.String(), nullable=False),
        sa.Column("anchor_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("prompt_revision_id", sa.String(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("negative_prompt", sa.Text(), nullable=False),
        sa.Column("generation_options_json", sa.Text(), nullable=False),
        sa.Column("generation_options_hash", sa.String(), nullable=False),
        sa.Column("source_definition_hash", sa.String(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.UniqueConstraint("operation_id", "anchor_type", "anchor_id", name="ux_visual_reference_operation_anchors_operation_anchor"),
        sa.UniqueConstraint("operation_anchor_id", "project_id", "anchor_type", "anchor_id", name="ux_visual_reference_operation_anchors_identity_owner"),
        sa.UniqueConstraint("operation_anchor_id", "candidate_count", name="ux_visual_reference_operation_anchors_candidate_count_owner"),
        sa.ForeignKeyConstraint(["operation_id", "project_id"], ["visual_reference_working_operations.operation_id", "visual_reference_working_operations.project_id"], name="fk_visual_reference_operation_anchors_operation_project"),
        sa.ForeignKeyConstraint(["prompt_revision_id", "project_id", "anchor_type", "anchor_id"], ["visual_reference_prompt_revisions.prompt_revision_id", "visual_reference_prompt_revisions.project_id", "visual_reference_prompt_revisions.anchor_type", "visual_reference_prompt_revisions.anchor_id"], name="fk_visual_reference_operation_anchors_prompt_revision"),
        sa.CheckConstraint("version >= 1", name="ck_visual_reference_operation_anchors_version_positive"),
        sa.CheckConstraint(f"anchor_type IN ({ANCHOR_TYPES})", name="ck_visual_reference_operation_anchors_anchor_type"),
        sa.CheckConstraint("status IN ('queued','running','candidate_ready','failed','cancelled')", name="ck_visual_reference_operation_anchors_status"),
        sa.CheckConstraint("candidate_count >= 1 AND candidate_count <= 5", name="ck_visual_reference_operation_anchors_candidate_count"),
    )
    op.create_index("ix_visual_reference_operation_anchors_project_status", "visual_reference_operation_anchors", ["project_id", "status"])
    op.create_index("ix_visual_reference_operation_anchors_project_anchor", "visual_reference_operation_anchors", ["project_id", "anchor_type", "anchor_id"])

    op.create_table(
        "visual_reference_operation_tasks",
        sa.Column("operation_task_id", sa.String(), primary_key=True),
        sa.Column("operation_anchor_id", sa.String(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("candidate_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("media_task_id", sa.String(), sa.ForeignKey("media_tasks.media_task_id"), nullable=True),
        sa.Column("submit_attempt_token", sa.String(), nullable=True),
        sa.Column("submit_started_at", sa.String(), nullable=True),
        sa.Column("next_reconcile_at", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.UniqueConstraint("operation_anchor_id", "candidate_index", name="ux_visual_reference_operation_tasks_anchor_candidate"),
        sa.UniqueConstraint("operation_task_id", "operation_anchor_id", name="ux_visual_reference_operation_tasks_identity_anchor"),
        sa.UniqueConstraint("media_task_id", name="ux_visual_reference_operation_tasks_media_task_id"),
        sa.UniqueConstraint("submit_attempt_token", name="ux_visual_reference_operation_tasks_submit_attempt_token"),
        sa.ForeignKeyConstraint(["operation_anchor_id", "candidate_count"], ["visual_reference_operation_anchors.operation_anchor_id", "visual_reference_operation_anchors.candidate_count"], name="fk_visual_reference_operation_tasks_candidate_count_owner"),
        sa.CheckConstraint("candidate_count >= 1 AND candidate_count <= 5", name="ck_visual_reference_operation_tasks_candidate_count"),
        sa.CheckConstraint("candidate_index >= 1 AND candidate_index <= candidate_count", name="ck_visual_reference_operation_tasks_candidate_index"),
        sa.CheckConstraint("attempt_no >= 1", name="ck_visual_reference_operation_tasks_attempt_positive"),
        sa.CheckConstraint("status IN ('queued','submitting','submit_outcome_unknown','waiting_provider','downloading','materializing','completed','failed','cancelled')", name="ck_visual_reference_operation_tasks_status"),
    )
    op.create_index("ix_visual_reference_operation_tasks_anchor_status", "visual_reference_operation_tasks", ["operation_anchor_id", "status"])
    op.create_index("ix_visual_reference_operation_tasks_due", "visual_reference_operation_tasks", ["status", "next_reconcile_at"])

    op.create_table(
        "visual_reference_candidates",
        sa.Column("candidate_id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("anchor_type", sa.String(), nullable=False),
        sa.Column("anchor_id", sa.String(), nullable=False),
        sa.Column("candidate_index", sa.Integer(), nullable=False),
        sa.Column("source_prompt_revision_id", sa.String(), nullable=False),
        sa.Column("source_operation_task_id", sa.String(), sa.ForeignKey("visual_reference_operation_tasks.operation_task_id"), nullable=True),
        sa.Column("media_artifact_id", sa.String(), sa.ForeignKey("artifacts.artifact_id"), nullable=False),
        sa.Column("media_task_id", sa.String(), sa.ForeignKey("media_tasks.media_task_id"), nullable=True),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("media_sha256", sa.String(), nullable=False),
        sa.Column("provenance_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.UniqueConstraint("candidate_id", "project_id", "anchor_type", "anchor_id", name="ux_visual_reference_candidates_identity_owner"),
        sa.ForeignKeyConstraint(["source_prompt_revision_id", "project_id", "anchor_type", "anchor_id"], ["visual_reference_prompt_revisions.prompt_revision_id", "visual_reference_prompt_revisions.project_id", "visual_reference_prompt_revisions.anchor_type", "visual_reference_prompt_revisions.anchor_id"], name="fk_visual_reference_candidates_prompt_revision"),
        sa.CheckConstraint(f"anchor_type IN ({ANCHOR_TYPES})", name="ck_visual_reference_candidates_anchor_type"),
        sa.CheckConstraint("candidate_index >= 1 AND candidate_index <= 5", name="ck_visual_reference_candidates_candidate_index"),
    )
    op.create_index("ix_visual_reference_candidates_anchor", "visual_reference_candidates", ["project_id", "anchor_type", "anchor_id", "created_at"])

    op.create_table(
        "visual_reference_anchor_states",
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), primary_key=True),
        sa.Column("anchor_type", sa.String(), primary_key=True),
        sa.Column("anchor_id", sa.String(), primary_key=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("resolution_status", sa.String(), nullable=False),
        sa.Column("review_status", sa.String(), nullable=False),
        sa.Column("current_prompt_revision_id", sa.String(), nullable=False),
        sa.Column("selected_candidate_id", sa.String(), nullable=True),
        sa.Column("approved_candidate_id", sa.String(), nullable=True),
        sa.Column("active_operation_anchor_id", sa.String(), nullable=True),
        sa.Column("last_error_json", sa.Text(), nullable=True),
        sa.Column("last_progress_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.UniqueConstraint("project_id", "anchor_type", "anchor_id", name="ux_visual_reference_anchor_states_owner"),
        sa.ForeignKeyConstraint(["current_prompt_revision_id", "project_id", "anchor_type", "anchor_id"], ["visual_reference_prompt_revisions.prompt_revision_id", "visual_reference_prompt_revisions.project_id", "visual_reference_prompt_revisions.anchor_type", "visual_reference_prompt_revisions.anchor_id"], name="fk_visual_reference_anchor_states_current_prompt"),
        sa.ForeignKeyConstraint(["selected_candidate_id", "project_id", "anchor_type", "anchor_id"], ["visual_reference_candidates.candidate_id", "visual_reference_candidates.project_id", "visual_reference_candidates.anchor_type", "visual_reference_candidates.anchor_id"], name="fk_visual_reference_anchor_states_selected_candidate"),
        sa.ForeignKeyConstraint(["approved_candidate_id", "project_id", "anchor_type", "anchor_id"], ["visual_reference_candidates.candidate_id", "visual_reference_candidates.project_id", "visual_reference_candidates.anchor_type", "visual_reference_candidates.anchor_id"], name="fk_visual_reference_anchor_states_approved_candidate"),
        sa.ForeignKeyConstraint(["active_operation_anchor_id", "project_id", "anchor_type", "anchor_id"], ["visual_reference_operation_anchors.operation_anchor_id", "visual_reference_operation_anchors.project_id", "visual_reference_operation_anchors.anchor_type", "visual_reference_operation_anchors.anchor_id"], name="fk_visual_reference_anchor_states_active_operation_anchor"),
        sa.CheckConstraint("version >= 1", name="ck_visual_reference_anchor_states_version_positive"),
        sa.CheckConstraint(f"anchor_type IN ({ANCHOR_TYPES})", name="ck_visual_reference_anchor_states_anchor_type"),
        sa.CheckConstraint("resolution_status IN ('not_generated','candidate_ready','approved','failed')", name="ck_visual_reference_anchor_states_resolution_status"),
        sa.CheckConstraint("review_status IN ('unreviewed','draft_selected','approved','rejected')", name="ck_visual_reference_anchor_states_review_status"),
        sa.CheckConstraint("review_status != 'draft_selected' OR selected_candidate_id IS NOT NULL", name="ck_visual_reference_anchor_states_selected_review"),
        sa.CheckConstraint("(resolution_status != 'approved' OR (review_status = 'approved' AND approved_candidate_id IS NOT NULL)) AND (review_status != 'approved' OR (resolution_status = 'approved' AND approved_candidate_id IS NOT NULL))", name="ck_visual_reference_anchor_states_approved_consistency"),
    )
    op.create_index("ix_visual_reference_anchor_states_project_resolution", "visual_reference_anchor_states", ["project_id", "resolution_status"])
    op.create_index("ix_visual_reference_anchor_states_active_operation_anchor", "visual_reference_anchor_states", ["active_operation_anchor_id"])


def downgrade() -> None:
    op.drop_index("ix_visual_reference_anchor_states_active_operation_anchor", table_name="visual_reference_anchor_states")
    op.drop_index("ix_visual_reference_anchor_states_project_resolution", table_name="visual_reference_anchor_states")
    op.drop_table("visual_reference_anchor_states")
    op.drop_index("ix_visual_reference_candidates_anchor", table_name="visual_reference_candidates")
    op.drop_table("visual_reference_candidates")
    op.drop_index("ix_visual_reference_operation_tasks_due", table_name="visual_reference_operation_tasks")
    op.drop_index("ix_visual_reference_operation_tasks_anchor_status", table_name="visual_reference_operation_tasks")
    op.drop_table("visual_reference_operation_tasks")
    op.drop_index("ix_visual_reference_operation_anchors_project_anchor", table_name="visual_reference_operation_anchors")
    op.drop_index("ix_visual_reference_operation_anchors_project_status", table_name="visual_reference_operation_anchors")
    op.drop_table("visual_reference_operation_anchors")
    op.drop_index("ix_visual_reference_prompt_revisions_style_bible", table_name="visual_reference_prompt_revisions")
    op.drop_index("ux_visual_reference_prompt_revisions_current", table_name="visual_reference_prompt_revisions")
    op.drop_table("visual_reference_prompt_revisions")
    op.drop_index("ix_visual_reference_working_operations_lease_expiry", table_name="visual_reference_working_operations")
    op.drop_index("ix_visual_reference_working_operations_due", table_name="visual_reference_working_operations")
    op.drop_index("ix_visual_reference_working_operations_project_status", table_name="visual_reference_working_operations")
    op.drop_table("visual_reference_working_operations")
