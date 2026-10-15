from __future__ import annotations

from pathlib import Path

from app.db.models import MediaTask
from app.db.scene_asset_operation_task import SceneAssetOperationTask
from app.modules.v4.scene_asset_coordinator import SceneAssetCoordinator
from app.modules.v4.scene_asset_provider_driver import SceneAssetDurableProviderExecutor
from tests.v4.test_scene_asset_v2_capacity_regression import (
    FUTURE,
    NOW,
    PendingClient,
    _add_capacity_blocker,
    _factory,
    _seed,
)


def test_shared_live_facade_capacity_wait_reuses_exact_transport_on_retry(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory)
    with factory() as session:
        blocker = _add_capacity_blocker(session)
        blocker_id = blocker.media_task_id
        session.commit()

    coordinator = SceneAssetCoordinator(factory)
    first_lease = coordinator.claim_due_operation(owner="shared-capacity-first", now_iso=NOW, lease_seconds=30)
    assert first_lease is not None
    client = PendingClient()
    provider = SceneAssetDurableProviderExecutor(factory)

    first = provider.submit_task(
        first_lease,
        operation_task_id="operation_task_capacity",
        client=client,
        now_iso=NOW,
    )

    assert first.v2_task_status == "queued"
    assert first.media_status == "queued"
    assert first.media_task_id is not None
    assert client.submit_payloads == []
    first_media_task_id = first.media_task_id

    with factory() as session:
        task = session.get(SceneAssetOperationTask, "operation_task_capacity")
        blocker = session.get(MediaTask, blocker_id)
        assert task is not None and blocker is not None
        assert task.media_task_id == first_media_task_id
        assert task.submit_attempt_token is None
        assert task.submit_started_at is None
        blocker.status = "completed"
        blocker.provider_status = "completed"
        blocker.completed_at = NOW
        blocker.updated_at = NOW
        session.commit()

    retry_lease = coordinator.claim_due_operation(owner="shared-capacity-retry", now_iso=FUTURE, lease_seconds=30)
    assert retry_lease is not None
    second = provider.submit_task(
        retry_lease,
        operation_task_id="operation_task_capacity",
        client=client,
        now_iso=FUTURE,
    )

    assert second.media_task_id == first_media_task_id
    assert second.media_status == "submitted"
    assert second.v2_task_status == "waiting_provider"
    assert len(client.submit_payloads) == 1
