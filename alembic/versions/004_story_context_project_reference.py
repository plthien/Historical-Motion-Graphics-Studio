"""Historical revision marker; Story Context storage was removed before release.

Revision ID: 004_story_context_project_reference
Revises: 003_media_gateway_v1
"""
from __future__ import annotations

revision = '004_story_context_project_reference'
down_revision = '003_media_gateway_v1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: development databases are rebuilt from the current V4 schema."""


def downgrade() -> None:
    """No-op: obsolete Story Context storage is not restored."""
