from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import CheckConstraint, UniqueConstraint, inspect

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import create_sqlalchemy_engine
from app.db import scene_asset_working_models as working_models  # noqa: F401


BASE_WORKING_TABLES = {
    "scene_asset_working_operations",
    "scene_asset_operation_beats",
    "scene_asset_operation_tasks",
    "scene_asset_candidates",
    "scene_asset_beat_states",
}
PROMPT_REVISION_TABLE = "scene_asset_prompt_revisions"
WORKING_TABLES = BASE_WORKING_TABLES | {PROMPT_REVISION_TABLE}

LEGACY_SCENE_ASSET_TABLES = {
    "scene_asset_operations",
    "scene_asset_beat_claims",
    "scene_asset_beat_operation_leases",
}


def _migration_config(database_path: Path) -> Config:
    settings = get_settings()
    config = Config(str((settings.root_dir / "alembic.ini").resolve()))
    config.set_main_option("script_location", str((settings.root_dir / "alembic").resolve()))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.resolve()}")
    return config


def _table_names(database_path: Path) -> set[str]:
    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.resolve()}")
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _check_sql(table_name: str, constraint_name: str) -> str:
    table = Base.metadata.tables[table_name]
    return next(
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == constraint_name
    )


def test_scene_asset_working_models_are_registered_with_owner_constraints() -> None:
    assert WORKING_TABLES <= set(Base.metadata.tables)

    prompt_revision = Base.metadata.tables[PROMPT_REVISION_TABLE]
    assert {
        "prompt_revision_id",
        "project_id",
        "scene_id",
        "beat_id",
        "revision_no",
        "revision_kind",
        "is_current",
        "source_prompt_bundle_artifact_id",
        "source_prompt_item_id",
        "source_prompt_hash",
        "image_prompt",
        "negative_prompt",
        "created_at",
    } <= set(prompt_revision.c.keys())

    operation = Base.metadata.tables["scene_asset_working_operations"]
    assert "job_id" not in operation.c
    assert "media_batch_id" not in operation.c
    assert {
        "source_prompt_bundle_artifact_id",
        "provider_settings_snapshot_json",
        "provider_settings_snapshot_hash",
        "next_reconcile_at",
        "reconcile_token",
        "cancel_requested_at",
    } <= set(operation.c.keys())
    aggregate_status_sql = _check_sql(
        "scene_asset_working_operations",
        "ck_scene_asset_working_operations_status",
    )
    assert "waiting_provider" not in aggregate_status_sql
    assert "downloading" not in aggregate_status_sql

    operation_beat = Base.metadata.tables["scene_asset_operation_beats"]
    assert operation_beat.c.candidate_count.nullable is False
    assert {
        "prompt_revision_id",
        "prompt_bundle_artifact_id",
        "prompt_item_id",
        "prompt_hash",
        "image_prompt",
        "negative_prompt",
        "generation_options_json",
        "generation_options_hash",
        "reference_bindings_json",
        "reference_bindings_hash",
    } <= set(operation_beat.c.keys())
    assert {
        constraint.name for constraint in operation_beat.foreign_key_constraints
    } >= {
        "fk_scene_asset_operation_beats_operation_project",
        "fk_scene_asset_operation_beats_prompt_revision",
    }
    assert "fk_scene_asset_operation_beats_operation_prompt_bundle" not in {
        constraint.name for constraint in operation_beat.foreign_key_constraints
    }

    operation_task = Base.metadata.tables["scene_asset_operation_tasks"]
    assert "candidate_count" in operation_task.c
    assert "candidate_index <= candidate_count" in _check_sql(
        "scene_asset_operation_tasks",
        "ck_scene_asset_operation_tasks_candidate_index",
    )
    assert {constraint.name for constraint in operation_task.foreign_key_constraints} >= {
        "fk_scene_asset_operation_tasks_candidate_count_owner",
    }
    assert "submit_outcome_unknown" in _check_sql(
        "scene_asset_operation_tasks",
        "ck_scene_asset_operation_tasks_status",
    )
    assert {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in operation_task.constraints
        if isinstance(constraint, UniqueConstraint)
    }["ux_scene_asset_operation_tasks_beat_candidate"] == (
        "operation_beat_id", "candidate_index"
    )

    candidate = Base.metadata.tables["scene_asset_candidates"]
    assert {
        "source_prompt_revision_id",
        "source_prompt_bundle_artifact_id",
        "source_prompt_item_id",
        "source_prompt_hash",
    } <= set(candidate.c.keys())
    assert {
        constraint.name for constraint in candidate.foreign_key_constraints
    } >= {
        "fk_scene_asset_candidates_operation_beat_owner",
        "fk_scene_asset_candidates_operation_task_owner",
        "fk_scene_asset_candidates_prompt_revision",
        "fk_scene_asset_candidates_generated_prompt_owner",
    }

    beat_state = Base.metadata.tables["scene_asset_beat_states"]
    assert set(beat_state.primary_key.columns.keys()) == {"project_id", "beat_id"}
    assert beat_state.c.version.nullable is False
    assert "current_prompt_revision_id" not in beat_state.c
    assert "active_operation_beat_id" in beat_state.c
    assert "active_operation_id" not in beat_state.c
    assert {
        constraint.name for constraint in beat_state.foreign_key_constraints
    } >= {
        "fk_scene_asset_beat_states_current_draft_owner",
        "fk_scene_asset_beat_states_selected_owner",
        "fk_scene_asset_beat_states_active_operation_beat_owner",
        "fk_scene_asset_beat_states_continuity_source",
        "fk_scene_asset_beat_states_continuity_candidate_owner",
    }
    resolution_sql = _check_sql(
        "scene_asset_beat_states",
        "ck_scene_asset_beat_states_resolution_status",
    )
    assert "not_generated" in resolution_sql
    assert "generating" not in resolution_sql
    assert "queued" not in resolution_sql


def test_scene_asset_working_composite_foreign_keys_reference_unique_parent_keys() -> None:
    """SQLite requires every non-PK composite parent key to be exactly UNIQUE."""
    for table_name in WORKING_TABLES:
        table = Base.metadata.tables[table_name]
        for foreign_key in table.foreign_key_constraints:
            if len(foreign_key.elements) <= 1:
                continue
            parent_table = foreign_key.elements[0].column.table
            parent_columns = tuple(element.column.name for element in foreign_key.elements)
            parent_primary_key = tuple(column.name for column in parent_table.primary_key.columns)
            parent_unique_keys = {
                tuple(column.name for column in constraint.columns)
                for constraint in parent_table.constraints
                if isinstance(constraint, UniqueConstraint)
            }
            assert parent_columns == parent_primary_key or parent_columns in parent_unique_keys, (
                table_name, foreign_key.name, parent_table.name, parent_columns,
            )


def test_migration_024_025_upgrade_downgrade_upgrade_is_additive_and_matches_models(tmp_path: Path) -> None:
    database_path = tmp_path / "scene_asset_working_state.sqlite3"
    config = _migration_config(database_path)

    command.upgrade(config, "023_scene_asset_beat_operation_leases")
    before = _table_names(database_path)
    assert BASE_WORKING_TABLES.isdisjoint(before)
    assert PROMPT_REVISION_TABLE not in before
    assert LEGACY_SCENE_ASSET_TABLES <= before

    # 024 is intentionally the pre-PromptRevision intermediate schema.
    command.upgrade(config, "024_scene_asset_working_state_v2")
    after_024 = _table_names(database_path)
    assert BASE_WORKING_TABLES <= after_024
    assert PROMPT_REVISION_TABLE not in after_024
    assert LEGACY_SCENE_ASSET_TABLES <= after_024

    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.resolve()}")
    try:
        inspector = inspect(engine)
        beat_fks = {item["name"] for item in inspector.get_foreign_keys("scene_asset_operation_beats")}
        assert "fk_scene_asset_operation_beats_operation_prompt_bundle" in beat_fks
        assert "prompt_revision_id" not in {
            column["name"] for column in inspector.get_columns("scene_asset_operation_beats")
        }
        assert "source_prompt_revision_id" not in {
            column["name"] for column in inspector.get_columns("scene_asset_candidates")
        }
    finally:
        engine.dispose()

    # 025 is the current no-backfill PromptRevision schema and must match models.
    command.upgrade(config, "025_scene_asset_prompt_revisions")
    after_025 = _table_names(database_path)
    assert WORKING_TABLES <= after_025
    assert LEGACY_SCENE_ASSET_TABLES <= after_025

    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.resolve()}")
    try:
        inspector = inspect(engine)
        for table_name in WORKING_TABLES:
            migrated_columns = {column["name"] for column in inspector.get_columns(table_name)}
            model_columns = set(Base.metadata.tables[table_name].c.keys())
            assert migrated_columns == model_columns

        operation_columns = {column["name"] for column in inspector.get_columns("scene_asset_working_operations")}
        assert "job_id" not in operation_columns
        assert "media_batch_id" not in operation_columns
        assert {
            "source_prompt_bundle_artifact_id",
            "provider_settings_snapshot_json",
            "provider_settings_snapshot_hash",
            "next_reconcile_at",
            "reconcile_token",
            "cancel_requested_at",
        } <= operation_columns

        operation_checks = {
            item["name"]: item["sqltext"]
            for item in inspector.get_check_constraints("scene_asset_working_operations")
        }
        assert "waiting_provider" not in operation_checks["ck_scene_asset_working_operations_status"]
        assert "downloading" not in operation_checks["ck_scene_asset_working_operations_status"]

        beat_fks = {item["name"] for item in inspector.get_foreign_keys("scene_asset_operation_beats")}
        assert "fk_scene_asset_operation_beats_prompt_revision" in beat_fks
        assert "fk_scene_asset_operation_beats_operation_prompt_bundle" not in beat_fks

        task_checks = {
            item["name"]: item["sqltext"]
            for item in inspector.get_check_constraints("scene_asset_operation_tasks")
        }
        assert "submit_outcome_unknown" in task_checks["ck_scene_asset_operation_tasks_status"]

        candidate_uniques = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_unique_constraints("scene_asset_candidates")
        }
        assert candidate_uniques["ux_scene_asset_candidates_artifact_id"] == ("artifact_id",)
        assert candidate_uniques["ux_scene_asset_candidates_operation_task_id"] == ("operation_task_id",)
        candidate_fks = {item["name"] for item in inspector.get_foreign_keys("scene_asset_candidates")}
        assert {
            "fk_scene_asset_candidates_prompt_revision",
            "fk_scene_asset_candidates_generated_prompt_owner",
        } <= candidate_fks

        beat_state_fks = {item["name"] for item in inspector.get_foreign_keys("scene_asset_beat_states")}
        assert {
            "fk_scene_asset_beat_states_active_operation_beat_owner",
            "fk_scene_asset_beat_states_continuity_source",
            "fk_scene_asset_beat_states_continuity_candidate_owner",
        } <= beat_state_fks
    finally:
        engine.dispose()

    command.downgrade(config, "024_scene_asset_working_state_v2")
    after_025_downgrade = _table_names(database_path)
    assert BASE_WORKING_TABLES <= after_025_downgrade
    assert PROMPT_REVISION_TABLE not in after_025_downgrade

    command.downgrade(config, "023_scene_asset_beat_operation_leases")
    after_024_downgrade = _table_names(database_path)
    assert BASE_WORKING_TABLES.isdisjoint(after_024_downgrade)
    assert PROMPT_REVISION_TABLE not in after_024_downgrade
    assert LEGACY_SCENE_ASSET_TABLES <= after_024_downgrade

    command.upgrade(config, "head")
    after_reupgrade = _table_names(database_path)
    assert WORKING_TABLES <= after_reupgrade
    assert LEGACY_SCENE_ASSET_TABLES <= after_reupgrade
