from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import create_sqlalchemy_engine


def _migration_config(database_path: Path) -> Config:
    settings = get_settings()
    config = Config(str((settings.root_dir / "alembic.ini").resolve()))
    config.set_main_option("script_location", str((settings.root_dir / "alembic").resolve()))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.resolve()}")
    return config


def test_029_rebuilds_populated_projects_without_breaking_child_foreign_keys(tmp_path: Path) -> None:
    database_path = tmp_path / "populated.sqlite3"
    config = _migration_config(database_path)
    command.upgrade(config, "028_durable_voice_workflow")

    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.resolve()}")
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO channels "
                "(channel_id, name, language, niche, audience, channel_dna_json, channel_context_json, created_at, updated_at) "
                "VALUES ('channel_populated', 'Test', 'English', 'niche', 'audience', '{}', '{}', 'now', 'now')"
            ))
            conn.execute(text(
                "INSERT INTO projects "
                "(project_id, channel_id, name, channel_context_json, channel_dna_json, created_at, updated_at) "
                "VALUES ('project_populated', 'channel_populated', 'Test', '{}', '{}', 'now', 'now')"
            ))
            # Reproduce the real SQLite condition that broke the migration: a
            # populated child table owns a valid FK to projects while Alembic's
            # batch recreate needs to replace the projects parent table.
            conn.execute(text(
                "CREATE TABLE migration_project_child ("
                "child_id TEXT PRIMARY KEY, "
                "project_id TEXT NOT NULL REFERENCES projects(project_id)"
                ")"
            ))
            conn.execute(text(
                "INSERT INTO migration_project_child (child_id, project_id) "
                "VALUES ('child_1', 'project_populated')"
            ))
            assert int(conn.exec_driver_sql("PRAGMA foreign_keys").scalar() or 0) == 1
            assert conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    finally:
        engine.dispose()

    command.upgrade(config, "029_voice_first_elevenlabs")

    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.resolve()}")
    try:
        with engine.connect() as conn:
            assert conn.execute(text(
                "SELECT project_id FROM projects WHERE project_id = 'project_populated'"
            )).scalar_one() == "project_populated"
            assert conn.execute(text(
                "SELECT project_id FROM migration_project_child WHERE child_id = 'child_1'"
            )).scalar_one() == "project_populated"
            assert conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    finally:
        engine.dispose()
