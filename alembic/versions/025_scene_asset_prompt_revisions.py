"""add first-class Scene Assets V2 per-beat PromptRevision

Revision ID: 025_scene_asset_prompt_revisions
Revises: 024_scene_asset_working_state_v2
Create Date: 2026-08-17 02:20:00

This migration intentionally provides no backfill. Scene Assets V2 development
state is disposable; upgrade requires the V2 working tables to contain no rows.
"""

from alembic import op
import sqlalchemy as sa


revision = "025_scene_asset_prompt_revisions"
down_revision = "024_scene_asset_working_state_v2"
branch_labels = None
depends_on = None


def _assert_working_state_empty() -> None:
    connection = op.get_bind()
    for table_name in (
        "scene_asset_candidates",
        "scene_asset_operation_tasks",
        "scene_asset_operation_beats",
        "scene_asset_working_operations",
    ):
        count = connection.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()
        if int(count) != 0:
            raise RuntimeError(
                "025_scene_asset_prompt_revisions requires disposable Scene Assets V2 working state; "
                f"table {table_name} is not empty. Recreate the development database instead of backfilling."
            )


def upgrade() -> None:
    _assert_working_state_empty()

    op.create_table(
        "scene_asset_prompt_revisions",
        sa.Column("prompt_revision_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("scene_id", sa.String(), nullable=False),
        sa.Column("beat_id", sa.String(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("revision_kind", sa.String(), nullable=False),
        sa.Column("is_current", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("source_prompt_bundle_artifact_id", sa.String(), nullable=False),
        sa.Column("source_prompt_item_id", sa.String(), nullable=False),
        sa.Column("source_prompt_hash", sa.String(), nullable=False),
        sa.Column("image_prompt", sa.Text(), nullable=False),
        sa.Column("negative_prompt", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.CheckConstraint("revision_no >= 1", name="ck_scene_asset_prompt_revisions_revision_positive"),
        sa.CheckConstraint(
            "revision_kind IN ('compiled', 'manual_edit', 'restore_compiled')",
            name="ck_scene_asset_prompt_revisions_kind",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"]),
        sa.ForeignKeyConstraint(["source_prompt_bundle_artifact_id"], ["artifacts.artifact_id"]),
        sa.PrimaryKeyConstraint("prompt_revision_id"),
        sa.UniqueConstraint(
            "project_id", "beat_id", "revision_no",
            name="ux_scene_asset_prompt_revisions_beat_revision",
        ),
        sa.UniqueConstraint(
            "prompt_revision_id", "project_id", "scene_id", "beat_id",
            name="ux_scene_asset_prompt_revisions_identity_owner",
        ),
        sa.UniqueConstraint(
            "prompt_revision_id", "project_id", "scene_id", "beat_id",
            "source_prompt_bundle_artifact_id", "source_prompt_item_id", "source_prompt_hash",
            name="ux_scene_asset_prompt_revisions_source_identity",
        ),
    )
    op.create_index(
        "ux_scene_asset_prompt_revisions_current",
        "scene_asset_prompt_revisions",
        ["project_id", "beat_id"],
        unique=True,
        sqlite_where=sa.text("is_current = 1"),
    )
    op.create_index(
        "ix_scene_asset_prompt_revisions_bundle",
        "scene_asset_prompt_revisions",
        ["source_prompt_bundle_artifact_id"],
        unique=False,
    )

    with op.batch_alter_table("scene_asset_operation_beats", recreate="always") as batch_op:
        # Operation-level Prompt Bundle is the aggregate submission snapshot.
        # Beat-level PromptRevision may point to an older aggregate bundle after
        # unrelated beat edits, so the old equality FK is intentionally removed.
        batch_op.drop_constraint("fk_scene_asset_operation_beats_operation_prompt_bundle", type_="foreignkey")
        batch_op.drop_constraint("ux_scene_asset_operation_beats_prompt_identity", type_="unique")
        batch_op.add_column(sa.Column("prompt_revision_id", sa.String(), nullable=False))
        batch_op.create_unique_constraint(
            "ux_scene_asset_operation_beats_prompt_identity",
            [
                "operation_beat_id", "project_id", "scene_id", "beat_id",
                "prompt_revision_id", "prompt_bundle_artifact_id", "prompt_item_id", "prompt_hash",
            ],
        )
        batch_op.create_foreign_key(
            "fk_scene_asset_operation_beats_prompt_revision",
            "scene_asset_prompt_revisions",
            [
                "prompt_revision_id", "project_id", "scene_id", "beat_id",
                "prompt_bundle_artifact_id", "prompt_item_id", "prompt_hash",
            ],
            [
                "prompt_revision_id", "project_id", "scene_id", "beat_id",
                "source_prompt_bundle_artifact_id", "source_prompt_item_id", "source_prompt_hash",
            ],
        )

    with op.batch_alter_table("scene_asset_candidates", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_scene_asset_candidates_generated_prompt_owner", type_="foreignkey")
        batch_op.add_column(sa.Column("source_prompt_revision_id", sa.String(), nullable=False))
        batch_op.create_foreign_key(
            "fk_scene_asset_candidates_prompt_revision",
            "scene_asset_prompt_revisions",
            [
                "source_prompt_revision_id", "project_id", "scene_id", "beat_id",
                "source_prompt_bundle_artifact_id", "source_prompt_item_id", "source_prompt_hash",
            ],
            [
                "prompt_revision_id", "project_id", "scene_id", "beat_id",
                "source_prompt_bundle_artifact_id", "source_prompt_item_id", "source_prompt_hash",
            ],
        )
        batch_op.create_foreign_key(
            "fk_scene_asset_candidates_generated_prompt_owner",
            "scene_asset_operation_beats",
            [
                "operation_beat_id", "project_id", "scene_id", "beat_id",
                "source_prompt_revision_id", "source_prompt_bundle_artifact_id", "source_prompt_item_id", "source_prompt_hash",
            ],
            [
                "operation_beat_id", "project_id", "scene_id", "beat_id",
                "prompt_revision_id", "prompt_bundle_artifact_id", "prompt_item_id", "prompt_hash",
            ],
        )


def downgrade() -> None:
    _assert_working_state_empty()

    # During the candidate-table downgrade SQLite validates its new 024-shaped
    # generated-prompt FK immediately. Keep a temporary 024-shaped parent key
    # alongside the current PromptRevision-aware key until the child table has
    # been recreated. This avoids ever weakening FK enforcement or disabling it.
    with op.batch_alter_table("scene_asset_operation_beats", recreate="always") as batch_op:
        batch_op.create_unique_constraint(
            "ux_scene_asset_operation_beats_prompt_identity_024_bridge",
            [
                "operation_beat_id", "project_id", "scene_id", "beat_id",
                "prompt_bundle_artifact_id", "prompt_item_id", "prompt_hash",
            ],
        )

    with op.batch_alter_table("scene_asset_candidates", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_scene_asset_candidates_generated_prompt_owner", type_="foreignkey")
        batch_op.drop_constraint("fk_scene_asset_candidates_prompt_revision", type_="foreignkey")
        batch_op.drop_column("source_prompt_revision_id")
        batch_op.create_foreign_key(
            "fk_scene_asset_candidates_generated_prompt_owner",
            "scene_asset_operation_beats",
            [
                "operation_beat_id", "project_id", "scene_id", "beat_id",
                "source_prompt_bundle_artifact_id", "source_prompt_item_id", "source_prompt_hash",
            ],
            [
                "operation_beat_id", "project_id", "scene_id", "beat_id",
                "prompt_bundle_artifact_id", "prompt_item_id", "prompt_hash",
            ],
        )

    with op.batch_alter_table("scene_asset_operation_beats", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_scene_asset_operation_beats_prompt_revision", type_="foreignkey")
        batch_op.drop_constraint("ux_scene_asset_operation_beats_prompt_identity", type_="unique")
        batch_op.drop_constraint("ux_scene_asset_operation_beats_prompt_identity_024_bridge", type_="unique")
        batch_op.drop_column("prompt_revision_id")
        batch_op.create_unique_constraint(
            "ux_scene_asset_operation_beats_prompt_identity",
            [
                "operation_beat_id", "project_id", "scene_id", "beat_id",
                "prompt_bundle_artifact_id", "prompt_item_id", "prompt_hash",
            ],
        )
        batch_op.create_foreign_key(
            "fk_scene_asset_operation_beats_operation_prompt_bundle",
            "scene_asset_working_operations",
            ["operation_id", "project_id", "prompt_bundle_artifact_id"],
            ["operation_id", "project_id", "source_prompt_bundle_artifact_id"],
        )

    op.drop_index("ix_scene_asset_prompt_revisions_bundle", table_name="scene_asset_prompt_revisions")
    op.drop_index("ux_scene_asset_prompt_revisions_current", table_name="scene_asset_prompt_revisions")
    op.drop_table("scene_asset_prompt_revisions")
