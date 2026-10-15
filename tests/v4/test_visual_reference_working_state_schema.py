from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import CheckConstraint, UniqueConstraint, inspect

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import create_sqlalchemy_engine
from app.db import models as _models  # noqa: F401
from app.db import scene_asset_working_models as _scene_asset_working_models  # noqa: F401
from app.db import visual_reference_working_models as working_models  # noqa: F401


WORKING_TABLES = {
    "visual_reference_working_operations",
    "visual_reference_prompt_revisions",
    "visual_reference_operation_anchors",
    "visual_reference_operation_tasks",
    "visual_reference_candidates",
    "visual_reference_anchor_states",
}


def _migration_config(database_path: Path) -> Config:
    settings = get_settings()
    config = Config(str((settings.root_dir / "alembic.ini").resolve()))
    config.set_main_option("script_location", str((settings.root_dir / "alembic").resolve()))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.resolve()}")
    return config


def _check_sql(table_name: str, constraint_name: str) -> str:
    table = Base.metadata.tables[table_name]
    return next(
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == constraint_name
    )


def test_visual_reference_working_models_register_anchor_owned_durable_state() -> None:
    assert WORKING_TABLES <= set(Base.metadata.tables)

    state = Base.metadata.tables["visual_reference_anchor_states"]
    assert tuple(state.primary_key.columns.keys()) == ("project_id", "anchor_type", "anchor_id")
    assert state.c.version.nullable is False
    assert state.c.current_prompt_revision_id.nullable is False
    assert "continuity_source_beat_id" not in state.c
    assert {
        "fk_visual_reference_anchor_states_current_prompt",
        "fk_visual_reference_anchor_states_selected_candidate",
        "fk_visual_reference_anchor_states_approved_candidate",
        "fk_visual_reference_anchor_states_active_operation_anchor",
    } <= {constraint.name for constraint in state.foreign_key_constraints}

    prompt_revision = Base.metadata.tables["visual_reference_prompt_revisions"]
    current_index = next(index for index in prompt_revision.indexes if index.name == "ux_visual_reference_prompt_revisions_current")
    assert current_index.unique is True
    assert tuple(column.name for column in current_index.columns) == ("project_id", "anchor_type", "anchor_id")
    assert str(current_index.dialect_options["sqlite"]["where"]) == "is_current = 1"

    operation = Base.metadata.tables["visual_reference_working_operations"]
    assert "job_id" not in operation.c
    assert "media_batch_id" not in operation.c
    operation_status = _check_sql(
        "visual_reference_working_operations",
        "ck_visual_reference_working_operations_status",
    )
    assert "waiting_provider" not in operation_status
    assert "downloading" not in operation_status

    operation_anchor = Base.metadata.tables["visual_reference_operation_anchors"]
    assert "continuity_snapshot_json" not in operation_anchor.c
    assert {
        "fk_visual_reference_operation_anchors_operation_project",
        "fk_visual_reference_operation_anchors_prompt_revision",
    } <= {constraint.name for constraint in operation_anchor.foreign_key_constraints}

    task = Base.metadata.tables["visual_reference_operation_tasks"]
    assert "submit_outcome_unknown" in _check_sql(
        "visual_reference_operation_tasks",
        "ck_visual_reference_operation_tasks_status",
    )
    assert "candidate_index <= candidate_count" in _check_sql(
        "visual_reference_operation_tasks",
        "ck_visual_reference_operation_tasks_candidate_index",
    )

    candidate = Base.metadata.tables["visual_reference_candidates"]
    task_index = next(index for index in candidate.indexes if index.name == "ux_visual_reference_candidates_operation_task")
    assert task_index.unique is True
    assert tuple(column.name for column in task_index.columns) == ("source_operation_task_id",)


def test_visual_reference_composite_foreign_keys_reference_unique_parent_keys() -> None:
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


def test_migration_026_027_upgrade_downgrade_upgrade_matches_visual_reference_models(tmp_path: Path) -> None:
    database_path = tmp_path / "visual_reference_working_state.sqlite3"
    config = _migration_config(database_path)

    command.upgrade(config, "025_scene_asset_prompt_revisions")
    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.resolve()}")
    try:
        assert WORKING_TABLES.isdisjoint(set(inspect(engine).get_table_names()))
    finally:
        engine.dispose()

    command.upgrade(config, "026_visual_reference_working_state_v2")
    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.resolve()}")
    try:
        inspector = inspect(engine)
        assert WORKING_TABLES <= set(inspector.get_table_names())
        for table_name in WORKING_TABLES:
            migrated_columns = {column["name"] for column in inspector.get_columns(table_name)}
            assert migrated_columns == set(Base.metadata.tables[table_name].c.keys())
        candidate_indexes = {item["name"] for item in inspector.get_indexes("visual_reference_candidates")}
        assert "ux_visual_reference_candidates_operation_task" not in candidate_indexes
    finally:
        engine.dispose()

    command.upgrade(config, "027_visual_reference_candidate_task_fence")
    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.resolve()}")
    try:
        inspector = inspect(engine)
        prompt_indexes = {
            item["name"]: item
            for item in inspector.get_indexes("visual_reference_prompt_revisions")
        }
        assert prompt_indexes["ux_visual_reference_prompt_revisions_current"]["unique"] == 1
        candidate_indexes = {
            item["name"]: item
            for item in inspector.get_indexes("visual_reference_candidates")
        }
        assert candidate_indexes["ux_visual_reference_candidates_operation_task"]["unique"] == 1
        assert tuple(candidate_indexes["ux_visual_reference_candidates_operation_task"]["column_names"]) == (
            "source_operation_task_id",
        )
        state_checks = {
            item["name"]: item["sqltext"]
            for item in inspector.get_check_constraints("visual_reference_anchor_states")
        }
        assert "approved_candidate_id" in state_checks["ck_visual_reference_anchor_states_approved_consistency"]
    finally:
        engine.dispose()

    command.downgrade(config, "026_visual_reference_working_state_v2")
    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.resolve()}")
    try:
        candidate_indexes = {item["name"] for item in inspect(engine).get_indexes("visual_reference_candidates")}
        assert "ux_visual_reference_candidates_operation_task" not in candidate_indexes
    finally:
        engine.dispose()

    command.downgrade(config, "025_scene_asset_prompt_revisions")
    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.resolve()}")
    try:
        assert WORKING_TABLES.isdisjoint(set(inspect(engine).get_table_names()))
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.resolve()}")
    try:
        inspector = inspect(engine)
        assert WORKING_TABLES <= set(inspector.get_table_names())
        candidate_indexes = {item["name"] for item in inspector.get_indexes("visual_reference_candidates")}
        assert "ux_visual_reference_candidates_operation_task" in candidate_indexes
    finally:
        engine.dispose()
