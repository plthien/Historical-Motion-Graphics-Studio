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


def test_031_clears_leftover_sqlite_batch_table_before_nullable_voice_preset(tmp_path: Path) -> None:
    database_path = tmp_path / "voice_generation_providers.sqlite3"
    config = _migration_config(database_path)
    command.upgrade(config, "030_render_durable_sqlite")

    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.resolve()}")
    try:
        with engine.begin() as conn:
            assert int(conn.exec_driver_sql("PRAGMA foreign_keys").scalar() or 0) == 1
            conn.execute(
                text(
                    "INSERT INTO channels "
                    "(channel_id, name, language, niche, audience, channel_dna_json, channel_context_json, created_at, updated_at) "
                    "VALUES ('channel_031', 'Test', 'English', 'niche', 'audience', '{}', '{}', 'now', 'now')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO projects "
                    "(project_id, channel_id, name, channel_context_json, channel_dna_json, created_at, updated_at) "
                    "VALUES ('project_031', 'channel_031', 'Test', '{}', '{}', 'now', 'now')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO jobs "
                    "(job_id, job_type, status, created_at, updated_at) "
                    "VALUES ('job_031', 'voice_generation', 'completed', 'now', 'now')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO voice_generation_operations ("
                    "operation_id, job_id, project_id, operation_type, status, script_artifact_id, "
                    "tts_profile_id, voice_id, language, speaking_rate, provider_identity_json, "
                    "provider_identity_sha256, generation_settings_snapshot_json, "
                    "generation_settings_snapshot_sha256, segmentation_version, segmentation_input_sha256, "
                    "segment_count, request_json, result_json, created_at, updated_at"
                    ") VALUES ("
                    "'operation_031', 'job_031', 'project_031', 'full_generation', 'completed', 'script_031', "
                    "'tts_profile_031', 'voice_031', 'en', 1.0, '{}', "
                    "'provider_sha', '{}', "
                    "'settings_sha', 'segmentation_v1', 'segmentation_sha', "
                    "0, '{}', '{}', 'now', 'now'"
                    ")"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE migration_voice_operation_child ("
                    "child_id TEXT PRIMARY KEY, "
                    "operation_id TEXT NOT NULL REFERENCES voice_generation_operations(operation_id)"
                    ")"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO migration_voice_operation_child (child_id, operation_id) "
                    "VALUES ('child_031', 'operation_031')"
                )
            )
            # Reproduce a local startup retry after an interrupted SQLite batch
            # migration left Alembic's scratch table behind.
            conn.execute(text("CREATE TABLE _alembic_tmp_voice_generation_operations (operation_id TEXT)"))
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.resolve()}")
    try:
        with engine.connect() as conn:
            temp_table_count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type = 'table' AND name = '_alembic_tmp_voice_generation_operations'"
                )
            ).scalar_one()
            voice_preset_info = [
                row._mapping
                for row in conn.execute(text("PRAGMA table_info(voice_generation_operations)"))
                if row._mapping["name"] == "voice_preset"
            ]
            child_operation_id = conn.execute(
                text("SELECT operation_id FROM migration_voice_operation_child WHERE child_id = 'child_031'")
            ).scalar_one()
            foreign_key_violations = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    finally:
        engine.dispose()

    assert temp_table_count == 0
    assert voice_preset_info
    assert voice_preset_info[0]["notnull"] == 0
    assert child_operation_id == "operation_031"
    assert foreign_key_violations == []
