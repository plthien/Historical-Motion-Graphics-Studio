"""Phase 11 scene asset generation.

Revision ID: 016_scene_asset_generation_v3
Revises: 015_style_bible_visual_reference_approvals_v3
Create Date: 2026-07-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "016_scene_asset_generation_v3"
down_revision = "015_style_bible_visual_reference_approvals_v3"
branch_labels = None
depends_on = None


def _add_project_pointer() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            "ALTER TABLE projects ADD COLUMN scene_asset_manifest_artifact_id "
            "VARCHAR REFERENCES artifacts(artifact_id)"
        )
    else:
        op.add_column("projects", sa.Column("scene_asset_manifest_artifact_id", sa.String(), nullable=True))
        op.create_foreign_key(
            "fk_projects_scene_asset_manifest_artifact_id_artifacts",
            "projects",
            "artifacts",
            ["scene_asset_manifest_artifact_id"],
            ["artifact_id"],
        )
    op.create_index(
        "ix_projects_scene_asset_manifest_artifact_id",
        "projects",
        ["scene_asset_manifest_artifact_id"],
        unique=False,
    )


def upgrade() -> None:
    _add_project_pointer()

    op.add_column("media_batches", sa.Column("domain_revision_id", sa.String(), nullable=True))
    op.create_index("ix_media_batches_domain_revision_id", "media_batches", ["domain_revision_id"], unique=False)
    for name, column in (
        ("domain_revision_id", sa.Column("domain_revision_id", sa.String(), nullable=True)),
        ("reference_images_json", sa.Column("reference_images_json", sa.Text(), nullable=True)),
        ("storage_namespace", sa.Column("storage_namespace", sa.String(), nullable=True)),
        ("metadata_json", sa.Column("metadata_json", sa.Text(), nullable=True)),
    ):
        op.add_column("media_tasks", column)
    op.create_index("ix_media_tasks_domain_revision_id", "media_tasks", ["domain_revision_id"], unique=False)

    op.create_table(
        "scene_asset_operations",
        sa.Column("operation_id", sa.String(), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.job_id"), nullable=False),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("operation_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("script_artifact_id", sa.String(), nullable=False),
        sa.Column("script_analysis_artifact_id", sa.String(), nullable=False),
        sa.Column("style_bible_artifact_id", sa.String(), nullable=False),
        sa.Column("scene_plan_artifact_id", sa.String(), nullable=False),
        sa.Column("visual_plan_artifact_id", sa.String(), nullable=False),
        sa.Column("prompt_bundle_artifact_id", sa.String(), nullable=False),
        sa.Column("visual_reference_approval_set_artifact_id", sa.String(), nullable=True),
        sa.Column("expected_scene_asset_manifest_artifact_id", sa.String(), nullable=True),
        sa.Column("media_batch_id", sa.String(), sa.ForeignKey("media_batches.batch_id"), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.UniqueConstraint("job_id", name="ux_scene_asset_operations_job_id"),
    )
    for name in (
        "job_id", "project_id", "operation_type", "status", "scene_plan_artifact_id",
        "visual_plan_artifact_id", "prompt_bundle_artifact_id",
        "visual_reference_approval_set_artifact_id", "expected_scene_asset_manifest_artifact_id",
        "media_batch_id", "created_at", "completed_at",
    ):
        op.create_index(f"ix_scene_asset_operations_{name}", "scene_asset_operations", [name], unique=(name == "job_id"))

    op.create_table(
        "scene_asset_candidate_reviews",
        sa.Column("review_id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("scene_id", sa.String(), nullable=False),
        sa.Column("beat_id", sa.String(), nullable=False),
        sa.Column("candidate_artifact_id", sa.String(), sa.ForeignKey("artifacts.artifact_id"), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("is_current_draft", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.UniqueConstraint(
            "project_id", "scene_id", "beat_id", "candidate_artifact_id",
            name="ux_scene_asset_candidate_reviews_owner",
        ),
    )
    for name in ("project_id", "scene_id", "beat_id", "candidate_artifact_id", "decision"):
        op.create_index(f"ix_scene_asset_candidate_reviews_{name}", "scene_asset_candidate_reviews", [name], unique=False)


def downgrade() -> None:
    for name in ("decision", "candidate_artifact_id", "beat_id", "scene_id", "project_id"):
        op.drop_index(f"ix_scene_asset_candidate_reviews_{name}", table_name="scene_asset_candidate_reviews")
    op.drop_table("scene_asset_candidate_reviews")

    for name in (
        "completed_at", "created_at", "media_batch_id", "expected_scene_asset_manifest_artifact_id",
        "visual_reference_approval_set_artifact_id", "prompt_bundle_artifact_id", "visual_plan_artifact_id",
        "scene_plan_artifact_id", "status", "operation_type", "project_id", "job_id",
    ):
        op.drop_index(f"ix_scene_asset_operations_{name}", table_name="scene_asset_operations")
    op.drop_table("scene_asset_operations")

    op.drop_index("ix_media_tasks_domain_revision_id", table_name="media_tasks")
    for name in ("metadata_json", "storage_namespace", "reference_images_json", "domain_revision_id"):
        op.drop_column("media_tasks", name)
    op.drop_index("ix_media_batches_domain_revision_id", table_name="media_batches")
    op.drop_column("media_batches", "domain_revision_id")

    op.drop_index("ix_projects_scene_asset_manifest_artifact_id", table_name="projects")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("ALTER TABLE projects DROP COLUMN scene_asset_manifest_artifact_id")
    else:
        op.drop_constraint("fk_projects_scene_asset_manifest_artifact_id_artifacts", "projects", type_="foreignkey")
        op.drop_column("projects", "scene_asset_manifest_artifact_id")
