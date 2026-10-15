"""Phase 8C Style Bible visual reference generation.

Revision ID: 014_style_bible_visual_references_v3
Revises: 013_prompt_composer_v3
Create Date: 2026-07-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "014_style_bible_visual_references_v3"
down_revision = "013_prompt_composer_v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            "ALTER TABLE projects ADD COLUMN style_bible_visual_reference_set_artifact_id "
            "VARCHAR REFERENCES artifacts (artifact_id)"
        )
    else:
        op.add_column(
            "projects",
            sa.Column("style_bible_visual_reference_set_artifact_id", sa.String(), nullable=True),
        )
        op.create_foreign_key(
            "fk_projects_style_bible_visual_reference_set_artifact_id_artifacts",
            "projects",
            "artifacts",
            ["style_bible_visual_reference_set_artifact_id"],
            ["artifact_id"],
        )

    op.create_table(
        "style_bible_visual_operations",
        sa.Column("operation_id", sa.String(), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.job_id"), nullable=False),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("operation_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("script_artifact_id", sa.String(), nullable=False),
        sa.Column("script_analysis_artifact_id", sa.String(), nullable=False),
        sa.Column("style_bible_artifact_id", sa.String(), nullable=False),
        sa.Column("expected_visual_reference_set_artifact_id", sa.String(), nullable=True),
        sa.Column("media_batch_id", sa.String(), sa.ForeignKey("media_batches.batch_id"), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.UniqueConstraint("job_id", name="ux_style_bible_visual_operations_job_id"),
    )
    op.create_index("ix_style_bible_visual_operations_job_id", "style_bible_visual_operations", ["job_id"], unique=True)
    op.create_index("ix_style_bible_visual_operations_project_id", "style_bible_visual_operations", ["project_id"], unique=False)
    op.create_index("ix_style_bible_visual_operations_operation_type", "style_bible_visual_operations", ["operation_type"], unique=False)
    op.create_index("ix_style_bible_visual_operations_status", "style_bible_visual_operations", ["status"], unique=False)
    op.create_index("ix_style_bible_visual_operations_script_artifact_id", "style_bible_visual_operations", ["script_artifact_id"], unique=False)
    op.create_index("ix_style_bible_visual_operations_script_analysis_artifact_id", "style_bible_visual_operations", ["script_analysis_artifact_id"], unique=False)
    op.create_index("ix_style_bible_visual_operations_style_bible_artifact_id", "style_bible_visual_operations", ["style_bible_artifact_id"], unique=False)
    op.create_index("ix_style_bible_visual_operations_expected_visual_reference_set_artifact_id", "style_bible_visual_operations", ["expected_visual_reference_set_artifact_id"], unique=False)
    op.create_index("ix_style_bible_visual_operations_media_batch_id", "style_bible_visual_operations", ["media_batch_id"], unique=False)
    op.create_index("ix_style_bible_visual_operations_created_at", "style_bible_visual_operations", ["created_at"], unique=False)
    op.create_index("ix_style_bible_visual_operations_completed_at", "style_bible_visual_operations", ["completed_at"], unique=False)


def downgrade() -> None:
    for name in (
        "ix_style_bible_visual_operations_completed_at",
        "ix_style_bible_visual_operations_created_at",
        "ix_style_bible_visual_operations_media_batch_id",
        "ix_style_bible_visual_operations_expected_visual_reference_set_artifact_id",
        "ix_style_bible_visual_operations_style_bible_artifact_id",
        "ix_style_bible_visual_operations_script_analysis_artifact_id",
        "ix_style_bible_visual_operations_script_artifact_id",
        "ix_style_bible_visual_operations_status",
        "ix_style_bible_visual_operations_operation_type",
        "ix_style_bible_visual_operations_project_id",
        "ix_style_bible_visual_operations_job_id",
    ):
        op.drop_index(name, table_name="style_bible_visual_operations")
    op.drop_table("style_bible_visual_operations")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("ALTER TABLE projects DROP COLUMN style_bible_visual_reference_set_artifact_id")
    else:
        op.drop_constraint(
            "fk_projects_style_bible_visual_reference_set_artifact_id_artifacts",
            "projects",
            type_="foreignkey",
        )
        op.drop_column("projects", "style_bible_visual_reference_set_artifact_id")
