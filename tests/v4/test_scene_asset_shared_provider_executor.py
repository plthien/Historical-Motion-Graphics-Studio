from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.db.models import MediaTask
from app.db.scene_asset_operation_task import SceneAssetOperationTask
from app.modules.v4.scene_asset_coordinator import SceneAssetCoordinator
from app.modules.v4.scene_asset_provider_driver import SceneAssetDurableProviderExecutor
from app.scene_asset_coordinator_runner import SceneAssetCoordinatorRunner
from tests.v4.test_scene_asset_provider_execution import (
    NOW,
    AmbiguousSubmitClient,
    PendingClient,
    _factory,
    _seed,
)


def test_live_runner_uses_shared_scene_asset_provider_facade(tmp_path: Path) -> None:
    factory = _factory(tmp_path)

    runner = SceneAssetCoordinatorRunner(factory, owner="phase_d")

    assert isinstance(runner.provider, SceneAssetDurableProviderExecutor)


def test_shared_facade_submits_through_existing_scene_asset_domain_bridge(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory)
    lease = SceneAssetCoordinator(factory).claim_due_operation(owner="phase_d", now_iso=NOW, lease_seconds=30)
    assert lease is not None
    client = PendingClient()

    result = SceneAssetDurableProviderExecutor(factory).submit_task(
        lease,
        operation_task_id="operation_task_v2",
        client=client,
        now_iso=NOW,
    )

    assert result.v2_task_status == "waiting_provider"
    assert result.media_status == "submitted"
    assert len(client.submit_payloads) == 1
    with factory() as session:
        task = session.get(SceneAssetOperationTask, "operation_task_v2")
        assert task is not None and task.media_task_id is not None
        media = session.get(MediaTask, task.media_task_id)
        assert media is not None
        assert media.metadata_json["scene_asset_operation_task_id"] == "operation_task_v2"
        assert media.metadata_json["prompt_revision_id"]


def test_shared_facade_preserves_ambiguous_submit_fence(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory)
    lease = SceneAssetCoordinator(factory).claim_due_operation(owner="phase_d", now_iso=NOW, lease_seconds=30)
    assert lease is not None

    result = SceneAssetDurableProviderExecutor(factory).submit_task(
        lease,
        operation_task_id="operation_task_v2",
        client=AmbiguousSubmitClient(),
        now_iso=NOW,
    )

    assert result.v2_task_status == "submit_outcome_unknown"
    with factory() as session:
        task = session.get(SceneAssetOperationTask, "operation_task_v2")
        assert task is not None and task.status == "submit_outcome_unknown"
        media = session.scalar(select(MediaTask).where(MediaTask.media_task_id == task.media_task_id))
        assert media is not None and media.external_task_id is None
