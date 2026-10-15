"""allow per-operation Voice generation providers

Revision ID: 031_voice_generation_providers
Revises: 030_render_durable_sqlite
Create Date: 2026-09-20
"""

from alembic import op
import sqlalchemy as sa


revision = "031_voice_generation_providers"
down_revision = "030_render_durable_sqlite"
branch_labels = None
depends_on = None


def _drop_leftover_sqlite_batch_table() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("DROP TABLE IF EXISTS _alembic_tmp_voice_generation_operations")


def _set_sqlite_foreign_keys(enabled: bool) -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")
    current = int(bind.exec_driver_sql("PRAGMA foreign_keys").scalar() or 0)
    expected = 1 if enabled else 0
    if current != expected:
        raise RuntimeError(
            f"031_voice_generation_providers could not set SQLite foreign_keys={expected}; got {current}."
        )


def _alter_voice_preset_nullable(*, nullable: bool) -> None:
    with op.batch_alter_table("voice_generation_operations") as batch:
        batch.alter_column(
            "voice_preset",
            existing_type=sa.String(),
            nullable=nullable,
        )


def _alter_voice_preset_nullable_with_sqlite_fk_guard(*, nullable: bool) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        _alter_voice_preset_nullable(nullable=nullable)
        return

    violations: list[object] = []
    with op.get_context().autocommit_block():
        _set_sqlite_foreign_keys(False)
        try:
            _drop_leftover_sqlite_batch_table()
            _alter_voice_preset_nullable(nullable=nullable)
            violations = list(bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall())
        finally:
            _set_sqlite_foreign_keys(True)

    if violations:
        preview = ", ".join(str(row) for row in violations[:10])
        direction = "upgrade" if nullable else "downgrade"
        raise RuntimeError(
            f"031_voice_generation_providers {direction} detected foreign-key violations after rebuilding "
            f"voice_generation_operations: {preview}"
        )


def upgrade() -> None:
    _alter_voice_preset_nullable_with_sqlite_fk_guard(nullable=True)


def downgrade() -> None:
    _alter_voice_preset_nullable_with_sqlite_fk_guard(nullable=False)
