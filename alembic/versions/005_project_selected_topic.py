"""project selected topic

Revision ID: 005_project_selected_topic
Revises: 004_story_context_project_reference
Create Date: 2026-07-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "005_project_selected_topic"
down_revision = "004_story_context_project_reference"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.add_column(sa.Column("selected_topic_ideas_artifact_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("selected_topic_idea_id", sa.String(), nullable=True))
        batch_op.create_foreign_key(
            "fk_projects_selected_topic_ideas_artifact",
            "artifacts",
            ["selected_topic_ideas_artifact_id"],
            ["artifact_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_constraint("fk_projects_selected_topic_ideas_artifact", type_="foreignkey")
        batch_op.drop_column("selected_topic_idea_id")
        batch_op.drop_column("selected_topic_ideas_artifact_id")
