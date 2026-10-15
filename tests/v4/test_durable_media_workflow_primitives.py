from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules.media.durable_workflow.contracts import (
    OperationSnapshot,
    PreparedSubmission,
    ReconcileAction,
    ReconcileLease,
    TaskSnapshot,
    UnitSnapshot,
)
from app.modules.media.durable_workflow.states import (
    ACTIVE_OPERATION_STATUSES,
    ACTIVE_TASK_STATUSES,
    ACTIVE_UNIT_STATUSES,
    OPERATION_TRANSITIONS,
    TASK_TRANSITIONS,
    TERMINAL_OPERATION_STATUSES,
    TERMINAL_TASK_STATUSES,
    TERMINAL_UNIT_STATUSES,
    UNIT_TRANSITIONS,
)
from app.modules.media.durable_workflow.timing import parse_iso, plus_seconds
from app.modules.v4.scene_asset_coordinator import _parse_iso as scene_asset_parse_iso
from app.modules.v4.scene_asset_coordinator import _plus_seconds as scene_asset_plus_seconds
from app.modules.v4.scene_asset_working_state import (
    _OPERATION_BEAT_TRANSITIONS,
    _OPERATION_TRANSITIONS,
    _TASK_TRANSITIONS,
)
from app.repositories.durable_workflow_protocol import DurableWorkflowRepository
from app.repositories.scene_asset_workflow_adapter import SceneAssetWorkflowRepositoryAdapter
from app.repositories.scene_asset_working import (
    ACTIVE_OPERATION_BEAT_STATUSES,
    ACTIVE_OPERATION_STATUSES as SCENE_ACTIVE_OPERATION_STATUSES,
    ACTIVE_TASK_STATUSES as SCENE_ACTIVE_TASK_STATUSES,
    TERMINAL_OPERATION_BEAT_STATUSES,
    TERMINAL_OPERATION_STATUSES as SCENE_TERMINAL_OPERATION_STATUSES,
    TERMINAL_TASK_STATUSES as SCENE_TERMINAL_TASK_STATUSES,
)


def test_shared_execution_states_match_scene_assets_baseline() -> None:
    assert ACTIVE_OPERATION_STATUSES == SCENE_ACTIVE_OPERATION_STATUSES
    assert TERMINAL_OPERATION_STATUSES == SCENE_TERMINAL_OPERATION_STATUSES
    assert ACTIVE_UNIT_STATUSES == ACTIVE_OPERATION_BEAT_STATUSES
    assert TERMINAL_UNIT_STATUSES == TERMINAL_OPERATION_BEAT_STATUSES
    assert ACTIVE_TASK_STATUSES == SCENE_ACTIVE_TASK_STATUSES
    assert TERMINAL_TASK_STATUSES == SCENE_TERMINAL_TASK_STATUSES
    assert OPERATION_TRANSITIONS == _OPERATION_TRANSITIONS
    assert UNIT_TRANSITIONS == _OPERATION_BEAT_TRANSITIONS
    assert TASK_TRANSITIONS == _TASK_TRANSITIONS


def test_shared_timing_matches_scene_asset_coordinator_baseline() -> None:
    values = (
        "2026-08-17T10:00:00+00:00",
        "2026-08-17T17:00:00+07:00",
        "2026-08-17T10:00:00",
    )
    for value in values:
        assert parse_iso(value) == scene_asset_parse_iso(value)
        assert plus_seconds(value, 15) == scene_asset_plus_seconds(value, 15)
    with pytest.raises(ValueError, match="invalid ISO timestamp"):
        parse_iso("not-an-iso-date")
    with pytest.raises(ValueError, match="invalid ISO timestamp"):
        scene_asset_parse_iso("not-an-iso-date")


def test_neutral_contracts_do_not_expose_scene_or_anchor_identity() -> None:
    values = (
        ReconcileLease("operation", "owner", "token", "expires"),
        ReconcileAction("submit", "operation", "token", "unit", "task", "queued", None),
        PreparedSubmission("operation", "unit", "task", "attempt"),
        OperationSnapshot("operation", "queued", None, None),
        UnitSnapshot("unit", "operation", "queued"),
        TaskSnapshot("task", "unit", "queued", None, None),
    )
    for value in values:
        field_names = set(value.__dataclass_fields__)
        assert "beat_id" not in field_names
        assert "scene_id" not in field_names
        assert "anchor_id" not in field_names
        assert "anchor_type" not in field_names


def test_shared_core_source_has_no_scene_or_visual_reference_domain_imports() -> None:
    root = Path(__file__).resolve().parents[2] / "app" / "modules" / "media" / "durable_workflow"
    for path in root.glob("*.py"):
        source = path.read_text(encoding="utf-8").lower()
        assert "app.modules.v4.scene_asset" not in source
        assert "app.db.scene_asset" not in source
        assert "visual_reference" not in source


class _FakeSceneRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def list_due_operation_ids(self, *, now_iso: str, limit: int = 50) -> list[str]:
        self.calls.append(("list_due_operation_ids", {"now_iso": now_iso, "limit": limit}))
        return ["operation_1"]

    def get_operation(self, operation_id: str):
        return SimpleNamespace(
            operation_id=operation_id,
            status="running",
            next_reconcile_at="later",
            cancel_requested_at=None,
        )

    def list_operation_beats(self, operation_id: str):
        return [SimpleNamespace(operation_beat_id="beat_unit", operation_id=operation_id, status="running")]

    def get_task(self, operation_task_id: str):
        return SimpleNamespace(
            operation_task_id=operation_task_id,
            operation_beat_id="beat_unit",
            status="waiting_provider",
            next_reconcile_at="later",
            submit_started_at="before",
        )

    def list_tasks_for_operation_beat(self, operation_beat_id: str):
        return [self.get_task("task_1")]

    def list_stale_submitting_tasks(self, *, operation_id: str, stale_before_iso: str):
        return [SimpleNamespace(operation_task_id="task_stale")]

    def try_acquire_reconcile_lease(self, **kwargs) -> bool:
        self.calls.append(("try_acquire_reconcile_lease", kwargs))
        return True

    def renew_reconcile_lease(self, **kwargs) -> bool:
        self.calls.append(("renew_reconcile_lease", kwargs))
        return True

    def release_reconcile_lease(self, **kwargs) -> bool:
        self.calls.append(("release_reconcile_lease", kwargs))
        return True

    def fence_reconcile_write(self, **kwargs) -> bool:
        self.calls.append(("fence_reconcile_write", kwargs))
        return True


def test_scene_asset_repository_adapter_satisfies_neutral_contract() -> None:
    repository = _FakeSceneRepository()
    adapter = SceneAssetWorkflowRepositoryAdapter(repository)  # type: ignore[arg-type]

    assert isinstance(adapter, DurableWorkflowRepository)
    assert adapter.list_due_operation_ids(now_iso="now", limit=7) == ["operation_1"]
    assert adapter.get_operation_snapshot("operation_1") == OperationSnapshot(
        "operation_1", "running", "later", None
    )
    assert adapter.list_unit_snapshots("operation_1") == [
        UnitSnapshot("beat_unit", "operation_1", "running")
    ]
    assert adapter.get_task_snapshot("task_1") == TaskSnapshot(
        "task_1", "beat_unit", "waiting_provider", "later", "before"
    )
    assert adapter.list_task_snapshots_for_unit("beat_unit") == [
        TaskSnapshot("task_1", "beat_unit", "waiting_provider", "later", "before")
    ]
    assert adapter.list_stale_submitting_task_ids(
        operation_id="operation_1", stale_before_iso="before"
    ) == ["task_stale"]

    lease_args = {
        "operation_id": "operation_1",
        "owner": "owner",
        "token": "token",
        "now_iso": "now",
        "expires_at": "later",
    }
    assert adapter.try_acquire_reconcile_lease(**lease_args)
    assert adapter.renew_reconcile_lease(**lease_args)
    assert adapter.release_reconcile_lease(
        operation_id="operation_1", owner="owner", token="token", now_iso="now"
    )
    assert adapter.fence_reconcile_write(operation_id="operation_1", token="token", now_iso="now")
