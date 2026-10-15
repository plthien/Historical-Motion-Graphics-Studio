from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.errors import AppError
from app.db.enums import JobStatus
from app.modules.v4 import durable_render_service as durable_module
from app.modules.v4.durable_render_service import DurableRenderService
from app.modules.v4.render_service import RenderService


ROOT = Path(__file__).resolve().parents[2]


class _Session:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def flush(self) -> None:
        return None


class _Jobs:
    def __init__(self) -> None:
        self.created = []

    def create(self, job) -> None:
        self.created.append(job)


class _Operations:
    def __init__(self, operation=None) -> None:
        self.operation = operation
        self.created = []

    def get_active_for_project(self, _project_id: str):
        return None

    def create(self, operation) -> None:
        self.created.append(operation)

    def get_by_job_id(self, _job_id: str):
        return self.operation


class _Lifecycle:
    def __init__(self, job) -> None:
        self.job = job

    def get_required(self, _job_id: str):
        return self.job


class _RenderSettings:
    profile = {
        "profile_id": "youtube_1080p",
        "frame_rate": {"numerator": 30, "denominator": 1},
    }

    def get_profile(self, _profile_id: str) -> dict:
        return dict(self.profile)

    def identity_snapshot(self, _profile_id: str, *, required_input_codecs) -> dict:
        assert required_input_codecs == {"png"}
        return {
            "ffmpeg_path_identity": "C:/ffmpeg/bin/ffmpeg.exe",
            "ffmpeg_version": "ffmpeg test",
            "ffmpeg_capabilities_sha256": "a" * 64,
            "ffprobe_path_identity": "C:/ffmpeg/bin/ffprobe.exe",
            "ffprobe_version": "ffprobe test",
        }


def test_render_submit_persists_and_schedules_without_queue(monkeypatch) -> None:
    session = _Session()
    jobs = _Jobs()
    operations = _Operations()
    service = DurableRenderService.__new__(DurableRenderService)
    service.session = session
    service.jobs = jobs
    service.operations = operations
    service.runtime = SimpleNamespace(render_max_beat_count=10)
    service.render_settings = _RenderSettings()

    project = SimpleNamespace(
        subtitle_srt_artifact_id="artifact_srt",
        subtitle_vtt_artifact_id="artifact_vtt",
        final_video_artifact_id=None,
    )
    plan = SimpleNamespace(artifact_id="artifact_assembly")
    assembly = {"beats": [{"beat_id": "beat_1"}], "total_duration_time_units": 1_000_000}

    service._project_required = lambda _project_id: project
    service._validate_request_lineage = lambda _project, _request: None
    service._load_approved_assembly = lambda _project: (plan, None, None, assembly)
    service._validate_inputs = lambda _project, _assembly: ([{"artifact_id": "artifact_image"}], None, {"png"})
    service._operation_integrity = lambda _operation: None

    scheduled: list[str] = []
    service._schedule_operation = lambda operation_id, *, now_iso: scheduled.append(operation_id)

    monkeypatch.setattr(durable_module, "quantize_render_frames", lambda **_kwargs: {"frame_quantization_sha256": "b" * 64})
    monkeypatch.setattr(durable_module, "compile_ffmpeg_render_graph", lambda **_kwargs: {"command_graph_sha256": "c" * 64})

    response = service.submit(
        "project_1",
        {"render_profile_id": "youtube_1080p", "operation_type": "render"},
    )

    assert response["status"] == "queued"
    assert len(jobs.created) == 1
    assert len(operations.created) == 1
    assert operations.created[0].status == "queued"
    assert scheduled == [operations.created[0].render_operation_id]
    assert session.commit_count == 1
    assert "queue" not in service.__dict__


def test_render_retry_resets_attempt_and_reschedules_sqlite() -> None:
    session = _Session()
    operation = SimpleNamespace(
        render_operation_id="render_operation_1",
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
        cancellation_requested=True,
        recovery_enqueued=True,
        progress_time_units=100,
        progress_frame=20,
        progress_percent=50,
        error_code="RENDER_EXECUTION_FAILED",
        error_message="failed",
        error_details_json={"reason": "test"},
        completed_at="old",
        updated_at="old",
    )
    job = SimpleNamespace(
        job_id="job_1",
        status=JobStatus.FAILED,
        progress=50,
        attempt=1,
        retryable=True,
        error_code="RENDER_EXECUTION_FAILED",
        error_message="failed",
        error_details_json={"reason": "test"},
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

    service = DurableRenderService.__new__(DurableRenderService)
    service.session = session
    service.operations = _Operations(operation)
    service.lifecycle = _Lifecycle(job)
    service._project_required = lambda _project_id: project

    scheduled: list[str] = []
    service._schedule_operation = lambda operation_id, *, now_iso: scheduled.append(operation_id)

    response = service.retry("project_1", "job_1")

    assert response["status"] == "queued"
    assert operation.status == "queued"
    assert operation.attempt_no == 2
    assert operation.attempt_token is None
    assert operation.process_pid is None
    assert operation.progress_percent == 0
    assert job.status == JobStatus.QUEUED
    assert job.attempt == 2
    assert job.retryable is False
    assert scheduled == ["render_operation_1"]
    assert session.commit_count == 1


def test_production_render_has_no_redis_rq_runtime() -> None:
    route_source = Path("app/api/routes/production_v4.py").read_text(encoding="utf-8")
    assert "durable_render_service import DurableRenderService as RenderService" in route_source
    for relative in (
        "app/worker.py",
        "app/infrastructure/redis.py",
        "app/modules/v4/render_worker.py",
        "app/modules/v4/render_recovery.py",
        "scripts/windows/start_worker.bat",
    ):
        assert not (ROOT / relative).exists(), relative

    from app.main import create_app

    app = create_app()
    assert app.title == "YouTube AI Studio V3 Platform API"


def test_windows_launcher_starts_durable_render_without_worker_or_redis_gate() -> None:
    launcher = Path("start.bat").read_text(encoding="utf-8").lower()
    render_launcher = Path("scripts/windows/start_durable_render.bat").read_text(encoding="utf-8").lower()

    assert "start_durable_render.bat" in launcher
    assert "start_worker.bat" not in launcher
    assert "backend worker" not in launcher
    assert "redis must" not in launcher
    assert "-m app.durable_render_runner" in render_launcher
    assert "ffmpeg" in render_launcher
    assert "ffprobe" in render_launcher


def test_final_video_publication_rejects_stale_lineage_before_staging() -> None:
    session = _Session()
    service = RenderService.__new__(RenderService)
    service.session = session

    current_project = SimpleNamespace(
        project_id="project_1",
        subtitle_srt_artifact_id="artifact_srt",
        subtitle_vtt_artifact_id="artifact_vtt",
        final_video_artifact_id="artifact_final_old",
    )
    current_plan = SimpleNamespace(artifact_id="artifact_assembly_new")
    current_assembly = {"total_duration_time_units": 1_000_000}
    operation = SimpleNamespace(
        source_assembly_plan_artifact_id="artifact_assembly_old",
        source_subtitle_srt_artifact_id="artifact_srt",
        source_subtitle_vtt_artifact_id="artifact_vtt",
        expected_final_video_artifact_id="artifact_final_old",
    )
    job = SimpleNamespace(job_id="job_1")

    service._project_required = lambda _project_id: current_project
    service._load_approved_assembly = lambda _project: (current_plan, None, None, current_assembly)
    service.artifacts = SimpleNamespace(
        stage_artifact_file_revision=lambda **_kwargs: pytest.fail("stale lineage must fail before Final Video staging")
    )

    with pytest.raises(AppError) as exc_info:
        service.publish_final_video(
            project=current_project,
            operation=operation,
            job=job,
            output_path=Path("unused.mp4"),
            validation={},
            quantization={},
            assembly=current_assembly,
            process_result={},
            audio_analysis={},
        )

    assert exc_info.value.error_code == "RENDER_PUBLICATION_CONFLICT"
    assert session.commit_count == 0
    assert session.rollback_count == 1


def test_final_video_publication_cas_leaves_exactly_one_current_artifact() -> None:
    session = _Session()
    service = RenderService.__new__(RenderService)
    service.session = session

    current_project = SimpleNamespace(
        project_id="project_1",
        assembly_plan_artifact_id="artifact_assembly",
        subtitle_srt_artifact_id="artifact_srt",
        subtitle_vtt_artifact_id="artifact_vtt",
        final_video_artifact_id="artifact_final_old",
    )
    current_plan = SimpleNamespace(artifact_id="artifact_assembly")
    current_assembly = {"total_duration_time_units": 1_000_000}
    previous_artifact = SimpleNamespace(
        artifact_id="artifact_final_old",
        is_current=True,
        superseded_by_artifact_id=None,
    )
    staged_artifact = SimpleNamespace(
        artifact_id=None,
        logical_id="project/project_1/final_video",
        sha256="d" * 64,
        size_bytes=123,
        mime_type=None,
        width=None,
        height=None,
        duration_sec=None,
        is_current=False,
    )
    staged = SimpleNamespace(artifact=staged_artifact, owns_file=True)

    def stage_artifact_file_revision(**kwargs):
        staged_artifact.artifact_id = kwargs["artifact_id"]
        return staged

    class _PublicationProjects:
        def compare_and_set_final_video(self, *, project_id, new_final_video_artifact_id, guard, updated_at):
            assert project_id == current_project.project_id
            assert updated_at
            assert guard.assembly_plan_artifact_id == current_project.assembly_plan_artifact_id
            assert guard.subtitle_srt_artifact_id == current_project.subtitle_srt_artifact_id
            assert guard.subtitle_vtt_artifact_id == current_project.subtitle_vtt_artifact_id
            assert guard.previous_final_video_artifact_id == current_project.final_video_artifact_id
            current_project.final_video_artifact_id = new_final_video_artifact_id
            return True

    operation = SimpleNamespace(
        source_assembly_plan_artifact_id="artifact_assembly",
        source_subtitle_srt_artifact_id="artifact_srt",
        source_subtitle_vtt_artifact_id="artifact_vtt",
        expected_final_video_artifact_id="artifact_final_old",
        command_graph_sha256="c" * 64,
        render_profile_id="youtube_1080p",
        render_profile_snapshot_sha256="p" * 64,
        ffmpeg_version="ffmpeg test",
        ffprobe_version="ffprobe test",
        ffmpeg_capabilities_sha256="a" * 64,
        frame_quantization_sha256="q" * 64,
        attempt_no=2,
        status="validating",
        progress_percent=99,
        progress_frame=29,
        progress_time_units=999_000,
        result_json={},
        completed_at=None,
        updated_at="before",
        error_code=None,
        error_message=None,
        error_details_json=None,
    )
    job = SimpleNamespace(job_id="job_1", progress=99)

    service._project_required = lambda _project_id: current_project
    service._load_approved_assembly = lambda _project: (current_plan, None, None, current_assembly)
    service._validate_inputs = lambda *_args, **_kwargs: None
    service.projects = _PublicationProjects()
    service.artifacts = SimpleNamespace(stage_artifact_file_revision=stage_artifact_file_revision)

    def promote_exact_current_artifact(*, logical_id, new_artifact_id):
        assert logical_id == staged_artifact.logical_id
        assert new_artifact_id == staged_artifact.artifact_id
        staged_artifact.is_current = True
        return True

    service.artifact_rows = SimpleNamespace(
        get=lambda artifact_id: previous_artifact if artifact_id == previous_artifact.artifact_id else None,
        promote_exact_current_artifact=promote_exact_current_artifact,
    )
    service.lifecycle = SimpleNamespace(mark_completed=lambda _job, *, progress: setattr(_job, "progress", progress))
    service._operation_integrity = lambda _operation: None

    final_artifact_id = service.publish_final_video(
        project=current_project,
        operation=operation,
        job=job,
        output_path=Path("unused.mp4"),
        validation={
            "sha256": "d" * 64,
            "byte_count": 123,
            "stream_metadata": {"width": 1920, "height": 1080},
            "durations": {"actual_container_seconds": 1.0},
        },
        quantization={"total_frame_count": 30},
        assembly=current_assembly,
        process_result={"elapsed_seconds": 0.5},
        audio_analysis={},
    )

    assert final_artifact_id == current_project.final_video_artifact_id
    assert previous_artifact.is_current is False
    assert previous_artifact.superseded_by_artifact_id == final_artifact_id
    assert staged_artifact.is_current is True
    assert sum(1 for artifact in (previous_artifact, staged_artifact) if artifact.is_current) == 1
    assert operation.status == "completed"
    assert operation.result_json["final_video_artifact_id"] == final_artifact_id
    assert job.progress == 100
    assert staged.owns_file is False
    assert session.commit_count == 1
    assert session.rollback_count == 0
