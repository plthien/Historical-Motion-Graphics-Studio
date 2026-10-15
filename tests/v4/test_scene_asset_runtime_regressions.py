from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.v4.scene_asset_working_state import SceneAssetWorkingStateService
from app.repositories.scene_asset_working import SceneAssetReconcileLease
from app.repositories.visual_reference_working import VisualReferenceReconcileLease
from app.scene_asset_coordinator_runner import SceneAssetCoordinatorRunner
from app.visual_reference_coordinator_runner import VisualReferenceCoordinatorRunner


NOW = "2026-08-18T13:00:00+00:00"


class _FakeSession:
    def flush(self) -> None:
        pass

    def expire_all(self) -> None:
        pass


class _StateRepo:
    def __init__(self) -> None:
        self.operation = SimpleNamespace(
            operation_id="operation",
            status="running",
            total_beat_count=1,
            cancel_requested_at=None,
            cancelled_at=None,
            completed_at=None,
        )
        self.beat = SimpleNamespace(
            operation_beat_id="operation_beat",
            operation_id="operation",
            project_id="project",
            scene_id="scene",
            beat_id="beat",
            status="running",
            candidate_count=1,
            completed_at=None,
        )
        self.task = SimpleNamespace(
            operation_task_id="task",
            operation_beat_id="operation_beat",
            status="materializing",
        )
        self.state = SimpleNamespace(
            project_id="project",
            beat_id="beat",
            scene_id="scene",
            version=7,
            active_operation_beat_id="operation_beat",
            resolution_status="not_generated",
            review_status="unreviewed",
            current_draft_candidate_id=None,
            selected_candidate_id=None,
            last_progress_at=NOW,
            last_error_json=None,
        )
        self.candidate = SimpleNamespace(
            candidate_id="candidate",
            operation_beat_id="operation_beat",
        )
        self.cas_calls = 0

    def get_task(self, operation_task_id: str):
        return self.task if operation_task_id == self.task.operation_task_id else None

    def get_operation_beat(self, operation_beat_id: str):
        return self.beat if operation_beat_id == self.beat.operation_beat_id else None

    def get_operation(self, operation_id: str):
        return self.operation if operation_id == self.operation.operation_id else None

    def get_beat_state(self, project_id: str, beat_id: str):
        if project_id == self.state.project_id and beat_id == self.state.beat_id:
            return self.state
        return None

    def get_candidate_for_operation_task(self, operation_task_id: str):
        return self.candidate if operation_task_id == self.task.operation_task_id else None

    def compare_and_set_beat_state(
        self,
        *,
        project_id: str,
        beat_id: str,
        expected_version: int,
        values: dict[str, object],
        updated_at: str,
    ) -> bool:
        if (
            project_id != self.state.project_id
            or beat_id != self.state.beat_id
            or expected_version != self.state.version
        ):
            return False
        self.cas_calls += 1
        for key, value in values.items():
            setattr(self.state, key, value)
        self.state.version += 1
        self.state.updated_at = updated_at
        return True

    def transition_task(
        self,
        *,
        operation_task_id: str,
        expected_statuses: tuple[str, ...],
        target_status: str,
        values: dict[str, object],
    ) -> bool:
        if operation_task_id != self.task.operation_task_id or self.task.status not in expected_statuses:
            return False
        self.task.status = target_status
        for key, value in values.items():
            setattr(self.task, key, value)
        return True

    def list_tasks_for_operation_beat(self, operation_beat_id: str):
        return [self.task] if operation_beat_id == self.beat.operation_beat_id else []

    def transition_operation_beat(
        self,
        *,
        operation_beat_id: str,
        expected_statuses: tuple[str, ...],
        target_status: str,
        values: dict[str, object],
    ) -> bool:
        if operation_beat_id != self.beat.operation_beat_id or self.beat.status not in expected_statuses:
            return False
        self.beat.status = target_status
        for key, value in values.items():
            setattr(self.beat, key, value)
        return True

    def list_operation_beats(self, operation_id: str):
        return [self.beat] if operation_id == self.operation.operation_id else []

    def transition_operation(
        self,
        *,
        operation_id: str,
        expected_statuses: tuple[str, ...],
        target_status: str,
        values: dict[str, object],
    ) -> bool:
        if operation_id != self.operation.operation_id or self.operation.status not in expected_statuses:
            return False
        self.operation.status = target_status
        for key, value in values.items():
            setattr(self.operation, key, value)
        return True


def _service(repo: _StateRepo) -> SceneAssetWorkingStateService:
    service = SceneAssetWorkingStateService(_FakeSession())
    service.repo = repo
    return service


def test_materialized_regenerate_candidate_seeds_empty_beat_state_before_completion() -> None:
    repo = _StateRepo()
    service = _service(repo)

    service.mark_task_completed(operation_task_id="task", now_iso=NOW)

    assert repo.task.status == "completed"
    assert repo.state.current_draft_candidate_id == "candidate"
    assert repo.state.resolution_status == "candidate_ready"
    assert repo.state.review_status == "current_draft"
    assert repo.cas_calls == 1


def test_materialized_candidate_does_not_overwrite_existing_selected_candidate() -> None:
    repo = _StateRepo()
    repo.state.selected_candidate_id = "selected_candidate"
    service = _service(repo)

    service.mark_task_completed(operation_task_id="task", now_iso=NOW)

    assert repo.task.status == "completed"
    assert repo.state.selected_candidate_id == "selected_candidate"
    assert repo.state.current_draft_candidate_id is None
    assert repo.cas_calls == 0


def test_capacity_deferred_task_projects_back_to_queued_not_running() -> None:
    repo = _StateRepo()
    repo.task.status = "queued"
    repo.beat.status = "running"
    service = _service(repo)

    assert service.refresh_operation_beat(operation_beat_id="operation_beat", now_iso=NOW) == "queued"
    assert repo.beat.status == "queued"
    assert service.refresh_operation(operation_id="operation", now_iso=NOW) == "queued"
    assert repo.operation.status == "queued"

    repo.task.status = "waiting_provider"
    assert service.refresh_operation_beat(operation_beat_id="operation_beat", now_iso=NOW) == "running"
    assert repo.beat.status == "running"
    assert service.refresh_operation(operation_id="operation", now_iso=NOW) == "running"
    assert repo.operation.status == "running"


class _FailingCoordinator:
    def __init__(self, lease) -> None:
        self.lease = lease

    def claim_due_operation(self, **_kwargs):
        return self.lease

    def plan_next_action(self, _lease):
        return SimpleNamespace(action="materialize", operation_task_id="task")

    def release_lease(self, _lease, **_kwargs) -> None:
        pass


class _FailingProvider:
    def resume_or_advance_task(self, **_kwargs):
        raise RuntimeError("materialize failed")


@pytest.mark.parametrize(
    ("runner_class", "lease"),
    (
        (
            SceneAssetCoordinatorRunner,
            SceneAssetReconcileLease(
                operation_id="scene_operation",
                owner="owner",
                token="token",
                expires_at=NOW,
            ),
        ),
        (
            VisualReferenceCoordinatorRunner,
            VisualReferenceReconcileLease(
                operation_id="visual_operation",
                owner="owner",
                token="token",
                expires_at=NOW,
            ),
        ),
    ),
)
def test_failed_domain_iteration_defers_poison_operation(runner_class, lease) -> None:
    runner = object.__new__(runner_class)
    runner.owner = "owner"
    runner.lease_seconds = 15
    runner.coordinator = _FailingCoordinator(lease)
    runner.provider = _FailingProvider()
    runner._repair_unscheduled_active_operations = lambda: 0
    deferred: list[str] = []
    runner._defer_operation_after_failure = deferred.append

    with pytest.raises(RuntimeError, match="materialize failed"):
        runner.run_once()

    assert deferred == [lease.operation_id]
