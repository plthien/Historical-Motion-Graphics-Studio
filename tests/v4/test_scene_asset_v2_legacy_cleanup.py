from __future__ import annotations

import inspect
from pathlib import Path

import app.api.routes.production_v4 as production_v4
import app.api.routes.scene_assets_v2 as scene_assets_runtime_v2
import app.api.routes.scene_assets_workspace_v2 as scene_assets_workspace_v2
import app.durable_media_workflow_runner as durable_media_workflow_runner
import app.modules.media.media_ownership_recovery as media_ownership_recovery
import app.modules.v4.scene_asset_runtime_v2 as runtime_v2_module


APP_ROOT = Path(__file__).resolve().parents[2] / "app"
PLATFORM_WORKER_PATH = APP_ROOT / "worker.py"
LEGACY_OWNER_PATH = APP_ROOT / "modules" / "v4" / "scene_asset_service.py"
LEGACY_OWNER_TOKENS = ("scene_asset_service", "SceneAssetService")


def _route_paths(router) -> set[str]:
    return {route.path for route in router.routes}


def test_production_router_no_longer_owns_scene_assets() -> None:
    source = inspect.getsource(production_v4)
    assert "SceneAssetService" not in source
    assert "scene_asset_schemas_v4" not in source
    assert not any("/scene-assets" in path for path in _route_paths(production_v4.router))


def test_v2_routers_are_the_only_scene_asset_http_owners() -> None:
    runtime_paths = _route_paths(scene_assets_runtime_v2.router)
    workspace_paths = _route_paths(scene_assets_workspace_v2.router)
    assert "/api/v4/projects/{project_id}/scene-assets/jobs" in runtime_paths
    assert "/api/v4/projects/{project_id}/scene-assets/execution-status" in runtime_paths
    assert "/api/v4/projects/{project_id}/scene-assets" in workspace_paths
    assert "/api/v4/projects/{project_id}/scene-assets/beats/{beat_id}/prompt" in workspace_paths
    assert "/api/v4/projects/{project_id}/scene-assets/beats/{beat_id}/prompt/restore-compiled" in workspace_paths
    assert "/api/v4/projects/{project_id}/scene-assets/approve" in workspace_paths


def test_platform_worker_is_removed_and_shared_durable_host_owns_scene_assets() -> None:
    assert not PLATFORM_WORKER_PATH.exists()
    source = inspect.getsource(durable_media_workflow_runner.build_host)
    assert 'RegisteredDurableWorkflow("scene-assets-v2"' in source
    assert "SceneAssetCoordinatorRunner" in source
    assert "scene_asset_service" not in source
    assert "scene_asset_worker" not in source
    assert "SCENE_ASSET_JOB" not in source


def test_shared_durable_host_does_not_invoke_legacy_scene_asset_recovery() -> None:
    source = inspect.getsource(durable_media_workflow_runner)
    assert "recover_stale_scene_asset_operations" not in source
    assert "scene_asset_recovery" not in source


def test_v2_scene_asset_routes_do_not_use_delayed_rq_or_legacy_service() -> None:
    runtime_source = inspect.getsource(scene_assets_runtime_v2)
    workspace_source = inspect.getsource(scene_assets_workspace_v2)
    combined = runtime_source + workspace_source
    assert "SceneAssetService" not in combined
    assert "enqueue_in" not in combined
    assert "expected_scene_asset_manifest_artifact_id" not in combined


def test_request_runtime_cannot_advance_scene_asset_provider_work() -> None:
    source = inspect.getsource(runtime_v2_module.SceneAssetRuntimeV2)
    assert "pump_project" not in source
    assert "_claim_due_for_project" not in source
    assert "SceneAssetProviderExecutionService" not in source
    assert "scene_assets_v2_request" not in source


def test_media_startup_recovery_does_not_own_scene_assets_via_legacy_operation() -> None:
    source = inspect.getsource(media_ownership_recovery)
    # Generic MediaTask startup cleanup may inspect the durable V2 owner graph
    # only to decide whether a provider-active transport still owns capacity.
    # It must not resurrect the removed legacy SceneAssetOperation/RQ owner.
    assert "select(SceneAssetOperation)" not in source
    assert "SceneAssetOperation.media_batch_id" not in source
    assert "_ACTIVE_SCENE_ASSET_OPERATION_STATUSES" not in source
    assert "scene_asset_generation_v4" not in source
    assert "SceneAssetOperationTask" in source
    assert "SceneAssetOperationBeat" in source
    assert "SceneAssetWorkingOperation" in source
    assert "SceneAssetBeatState" in source


def test_active_app_source_has_no_consumer_of_legacy_scene_asset_owner() -> None:
    offenders: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        if path == LEGACY_OWNER_PATH:
            continue
        source = path.read_text(encoding="utf-8")
        if any(token in source for token in LEGACY_OWNER_TOKENS):
            offenders.append(path.relative_to(APP_ROOT.parent).as_posix())
    assert offenders == []
