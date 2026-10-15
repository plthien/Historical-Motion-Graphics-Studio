from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.api.errors import AppError
from app.db.enums import JobStatus
from app.durable_render_runner import production_lease_seconds
from app.modules.v4.durable_render_service import DurableRenderService
from app.modules.v4.render_service import RenderService


class _Session:
    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


class _Operations:
    def __init__(self, operation) -> None:
        self.operation = operation

    def list_for_project(self, project_id: str):
        return [self.operation] if self.operation.project_id == project_id else []

    def get_by_job_id(self, job_id: str):
        return self.operation if self.operation.job_id == job_id else None


class _Jobs:
    def __init__(self, job) -> None:
        self.job = job

    def get(self, job_id: str):
        return self.job if self.job.job_id == job_id else None


def _failed_state():
    operation = SimpleNamespace(
        render_operation_id="render_operation_1",
        job_id="job_1",
        project_id="project_1",
        source_assembly_plan_artifact_id="artifact_assembly",
        source_subtitle_srt_artifact_id="artifact_srt",
        source_subtitle_vtt_artifact_id="artifact_vtt",
        expected_final_video_artifact_id=None,
        render_profile_id="youtube_1080p",
        status="failed",
        attempt_no=1,
        attempt_token="old-token",
        attempt_relative_output_path="old/output.mp4",
        process_pid=123,
        process_started_at="old",
        cancellation_requested=False,
        recovery_enqueued=False,
        progress_time_units=100,
        progress_frame=20,
        progress_percent=50,
        error_code="RENDER_FFMPEG_FAILED",
        error_message="Local FFmpeg rendering failed.",
        error_details_json={"return_code": 1},
        completed_at="old",
        updated_at="old",
    )
    job = SimpleNamespace(
        job_id="job_1",
        status=JobStatus.FAILED,
        progress=50,
        attempt=1,
        retryable=True,
        error_code="RENDER_FFMPEG_FAILED",
        error_message="Local FFmpeg rendering failed.",
        error_details_json={"return_code": 1},
        started_at="old",
        finished_at="old",
        completed_at="old",
        heartbeat_at="old",
        updated_at="old",
    )
    project = SimpleNamespace(
        assembly_plan_artifact_id="artifact_assembly",
        subtitle_srt_artifact_id="artifact_srt",
        subtitle_vtt_artifact_id="artifact_vtt",
        final_video_artifact_id=None,
    )
    return operation, job, project


def test_retry_latest_recovers_retryable_failure_after_workspace_refresh() -> None:
    operation, job, project = _failed_state()
    service = DurableRenderService.__new__(DurableRenderService)
    service.session = _Session()
    service.operations = _Operations(operation)
    service.jobs = _Jobs(job)
    service._project_required = lambda _project_id: project
    scheduled: list[str] = []
    service._schedule_operation = lambda operation_id, *, now_iso: scheduled.append(operation_id)

    response = service.retry("project_1", "latest")

    assert response["job_id"] == "job_1"
    assert response["status"] == "queued"
    assert operation.status == "queued"
    assert operation.attempt_no == 2
    assert job.status == JobStatus.QUEUED
    assert job.attempt == 2
    assert scheduled == ["render_operation_1"]
    assert service.session.commit_count == 1


def test_render_workspace_surfaces_latest_failed_retry_attempt(monkeypatch) -> None:
    operation, job, _project = _failed_state()
    operation.attempt_no = 3
    operation.error_code = "RENDER_OUTPUT_VALIDATION_FAILED"
    operation.error_message = "Final video validation failed after encoding."
    job.error_code = operation.error_code
    job.error_message = operation.error_message

    monkeypatch.setattr(
        RenderService,
        "get_workspace",
        lambda _self, project_id: {
            "project_id": project_id,
            "current_final_video_artifact_id": None,
            "video_url": None,
            "final_video": None,
            "validation_summary": None,
            "errors": [],
        },
    )
    service = DurableRenderService.__new__(DurableRenderService)
    service.operations = _Operations(operation)
    service.jobs = _Jobs(job)

    response = service.get_workspace("project_1")

    assert response["errors"] == [
        {
            "error_code": "RENDER_OUTPUT_VALIDATION_FAILED",
            "message": "Final video validation failed after encoding.",
            "attempt_no": 3,
            "job_id": "job_1",
        }
    ]


def test_render_workspace_rejects_missing_current_final_video_file(monkeypatch) -> None:
    monkeypatch.setattr(
        RenderService,
        "get_workspace",
        lambda _self, project_id: {
            "project_id": project_id,
            "current_final_video_artifact_id": "artifact_final",
            "video_url": f"/api/v4/projects/{project_id}/render/video",
            "final_video": {"artifact_id": "artifact_final"},
            "validation_summary": {"valid": True},
            "errors": [],
        },
    )
    service = DurableRenderService.__new__(DurableRenderService)
    service.artifact_rows = SimpleNamespace(get=lambda _artifact_id: SimpleNamespace(artifact_id="artifact_final"))

    def missing_file(_artifact):
        raise AppError(
            "ARTIFACT_FILE_MISSING",
            "Artifact file does not exist on disk.",
            404,
            retryable=False,
        )

    service.artifacts = SimpleNamespace(resolve_file_path=missing_file)
    service.operations = SimpleNamespace(list_for_project=lambda _project_id: [])
    service.jobs = SimpleNamespace(get=lambda _job_id: None)

    response = service.get_workspace("project_1")

    assert response["video_url"] is None
    assert response["final_video"] is None
    assert response["validation_summary"] is None
    assert response["errors"][0]["error_code"] == "ARTIFACT_FILE_MISSING"


def test_render_host_failure_logging_is_attempt_aware_and_reports_storage_root() -> None:
    source = Path("app/durable_render_runner.py").read_text(encoding="utf-8")
    assert "ro.attempt_no" in source
    assert "last_logged_failure_signature" in source
    assert "attempt_no=%s" in source
    assert "artifact_root=%s" in source
    assert "database=%s" in source


def test_production_render_lease_exceeds_sqlite_writer_wait_window() -> None:
    assert production_lease_seconds(15_000) == 60
    assert production_lease_seconds(30_000) == 120
    assert production_lease_seconds(1_000) == 60
