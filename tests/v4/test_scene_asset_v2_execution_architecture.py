from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

from app.api.routes import scene_assets_v2
from app.modules.media import media_ownership_recovery
from app.modules.media.durable_workflow.provider_executor import DurableMediaProviderExecutor
from app.modules.v4.scene_asset_continuity import transitive_dependents
from app.modules.v4.scene_asset_provider_driver import SceneAssetDurableProviderExecutor
from app.modules.v4.scene_asset_provider_execution import SceneAssetProviderExecutionService
from app.scene_asset_coordinator_runner import SceneAssetCoordinatorRunner


ROOT = Path(__file__).resolve().parents[2]
WORKING_TABLES = {
    "scene_asset_working_operations",
    "scene_asset_prompt_revisions",
    "scene_asset_operation_beats",
    "scene_asset_operation_tasks",
    "scene_asset_candidates",
    "scene_asset_beat_states",
}


def test_continuity_invalidation_walks_full_chain_and_branches() -> None:
    graph = {
        "A": {"B", "D"},
        "B": {"C"},
        "C": {"A"},  # malformed cycle must not loop or invalidate the root
        "D": {"E"},
    }
    assert transitive_dependents(graph, "A") == ["B", "D", "C", "E"]


def test_continuity_invalidation_can_keep_unchanged_direct_branch() -> None:
    graph = {"A": {"B", "D"}, "B": {"C"}, "D": {"E"}}
    assert transitive_dependents(graph, "A", skip_direct={"B"}) == ["D", "E"]


def test_scene_asset_get_routes_are_read_only_projections() -> None:
    source = inspect.getsource(scene_assets_v2)
    assert "pump_project" not in source
    assert "SceneAssetExecutionProjection" in source
    assert "session.rollback()" not in source


def test_scene_asset_coordinator_runner_is_not_rq_owned() -> None:
    source = inspect.getsource(SceneAssetCoordinatorRunner).lower()
    assert "rq" not in source
    assert "redis" not in source
    assert "claim_due_operation" in source
    assert "submit_task" in source
    assert "resume_or_advance_task" in source


def test_live_scene_asset_provider_path_uses_shared_durable_executor() -> None:
    runner_source = inspect.getsource(SceneAssetCoordinatorRunner)
    facade_source = inspect.getsource(SceneAssetDurableProviderExecutor)

    assert "SceneAssetDurableProviderExecutor" in runner_source
    assert "SceneAssetProviderExecutionService" not in runner_source
    assert "DurableMediaProviderExecutor" in facade_source
    assert isinstance(DurableMediaProviderExecutor, type)


def test_scene_asset_provider_compatibility_entrypoints_delegate_to_shared_executor() -> None:
    submit_source = inspect.getsource(SceneAssetProviderExecutionService.submit_task)
    resume_source = inspect.getsource(SceneAssetProviderExecutionService.resume_or_advance_task)

    for source in (submit_source, resume_source):
        assert "SceneAssetDurableProviderExecutor" in source
        assert "advance_batch_once" not in source
        assert "prepare_submission" not in source
        assert "release_terminal_v2_provider_capacity" not in source


def test_scene_asset_v2_media_capacity_has_cancel_and_startup_cleanup_owners() -> None:
    route_source = inspect.getsource(scene_assets_v2)
    recovery_source = inspect.getsource(media_ownership_recovery)
    assert "release_terminal_v2_provider_capacity" in route_source
    assert "SceneAssetOperationTask" in recovery_source
    assert "SceneAssetOperationBeat" in recovery_source
    assert "SceneAssetWorkingOperation" in recovery_source
    assert "SceneAssetBeatState" in recovery_source
    assert "_scene_asset_v2_owner_active" in recovery_source
    assert 'metadata.get("scene_asset_v2") is True' in recovery_source


def test_runtime_scene_asset_v2_imports_register_complete_working_metadata() -> None:
    # The generic durable-media executor is intentionally domain-agnostic and
    # therefore must not import/register Scene Asset ORM tables. Only live
    # Scene Asset modules are required to register the complete working schema.
    modules = [
        "app.modules.v4.scene_asset_provider_execution",
        "app.modules.v4.scene_asset_provider_driver",
        "app.scene_asset_coordinator_runner",
        "app.main",
    ]
    for module in modules:
        code = (
            "import importlib; "
            f"importlib.import_module({module!r}); "
            "from app.db.base import Base; "
            f"missing = set({sorted(WORKING_TABLES)!r}) - set(Base.metadata.tables); "
            "raise SystemExit(f'missing {sorted(missing)}' if missing else 0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
