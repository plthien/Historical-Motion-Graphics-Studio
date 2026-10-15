from __future__ import annotations

from types import SimpleNamespace

from app.db.scene_asset_working_operation import SceneAssetWorkingOperation
from app.modules.v4.scene_asset_execution_projection import SceneAssetExecutionProjection


class _Projects:
    def get(self, project_id: str):
        assert project_id == "project_001"
        return SimpleNamespace(project_id=project_id)


class _Repo:
    def __init__(self, beat, task) -> None:
        self.beat = beat
        self.task = task

    def list_operations_for_project(self, _project_id: str):
        raise AssertionError("execution projection must filter active operations in SQL")

    def list_operation_beats(self, operation_id: str):
        assert operation_id == "operation_001"
        return [self.beat]

    def list_tasks_for_operation(self, operation_id: str):
        assert operation_id == "operation_001"
        return [self.task]

    def list_tasks_for_operation_beat(self, _operation_beat_id: str):
        raise AssertionError("execution projection must batch operation tasks")


class _Session:
    def __init__(self, operation, state) -> None:
        self.operation = operation
        self.state = state
        self.scalars_calls = 0

    def scalars(self, _statement):
        self.scalars_calls += 1
        if self.scalars_calls == 1:
            return [self.operation]
        if self.scalars_calls == 2:
            return [self.state]
        raise AssertionError("unexpected extra batch query")

    def get(self, model, key):
        assert model is SceneAssetWorkingOperation
        assert key == "operation_001"
        return self.operation


def test_execution_projection_batches_active_operation_reads() -> None:
    operation = SimpleNamespace(
        operation_id="operation_001",
        project_id="project_001",
        operation_type="generate_all",
        status="running",
        reconcile_token=None,
        reconcile_lease_expires_at=None,
        next_reconcile_at="2099-08-17T08:30:00+00:00",
        last_progress_at="2026-08-17T08:30:00+00:00",
        total_beat_count=1,
        completed_count=0,
        failed_count=0,
        cancelled_count=0,
        cancel_requested_at=None,
        completed_at=None,
        error_code=None,
        error_message=None,
        created_at="2026-08-17T08:30:00+00:00",
    )
    beat = SimpleNamespace(
        operation_beat_id="operation_beat_001",
        beat_id="beat_001",
        status="running",
        candidate_count=1,
        error_code=None,
        error_message=None,
        updated_at="2026-08-17T08:30:00+00:00",
    )
    task = SimpleNamespace(
        operation_task_id="operation_task_001",
        operation_beat_id="operation_beat_001",
        candidate_index=1,
        status="waiting_provider",
        media_task_id="media_task_001",
        next_reconcile_at="2099-08-17T08:30:00+00:00",
        submit_started_at=None,
        error_code=None,
        error_message=None,
    )
    state = SimpleNamespace(
        beat_id="beat_001",
        last_progress_at="2026-08-17T08:30:00+00:00",
    )
    session = _Session(operation, state)
    projection = SceneAssetExecutionProjection(session)
    projection.projects = _Projects()
    projection.repo = _Repo(beat, task)

    payload = projection.get_execution_status("project_001")

    assert payload["active"] is True
    assert payload["active_operation_count"] == 1
    assert payload["operations"][0]["beats"][0]["tasks"][0]["operation_task_id"] == "operation_task_001"
    assert session.scalars_calls == 2
