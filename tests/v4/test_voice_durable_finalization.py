from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from app.api.errors import AppError
from app.db.enums import JobStatus
from app.modules.v4 import voice_durable_finalizer as finalizer_module
from app.modules.v4.voice_durable_finalizer import VoiceDurableFinalizer
from app.modules.v4.voice_first_policy import VOICE_ARCHITECTURE_VERSION


class _Session:
    def __init__(self) -> None:
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1


class _Repo:
    def __init__(self, value) -> None:
        self.value = value

    def get(self, key):
        return self.value


class _TaskRepo:
    def __init__(self, tasks) -> None:
        self.tasks = tasks

    def list_for_operation(self, operation_id: str):
        return list(self.tasks)


class _ArtifactStore:
    def __init__(self, shared: "_SharedState") -> None:
        self.shared = shared

    def stage_artifact_revision(self, **kwargs):
        staged = SimpleNamespace(
            artifact_id=f"final_section_artifact_{len(self.shared.staged) + 1}",
            artifact=SimpleNamespace(duration_sec=None),
            kwargs=kwargs,
        )
        self.shared.staged.append(staged)
        return staged


class _SharedState:
    def __init__(self) -> None:
        self.operation = SimpleNamespace(
            operation_id="voiceop_task2",
            job_id="job_task2",
            project_id="project_task2",
            operation_type="generate_all",
            status="finalizing",
            architecture_version=VOICE_ARCHITECTURE_VERSION,
            script_artifact_id="script_current",
            expected_narration_audio_artifact_id=None,
            expected_narration_word_timing_map_artifact_id=None,
            provider_identity_json={"provider_key": "elevenlabs"},
            provider_identity_sha256="a" * 64,
            voice_id="voice_task2",
            generation_settings_snapshot_sha256="b" * 64,
            request_json={
                "segmentation": {
                    "segments": [
                        {
                            "segment_id": "seg_001",
                            "segment_order": 1,
                            "section_id": "sec_001",
                            "source_start": 0,
                            "source_end": 17,
                            "exact_text": "Final narration.",
                            "text_sha256": "t" * 64,
                        }
                    ]
                },
                "script_section_pacing": {
                    "policy_version": "script_section_wpm_pacing_v2",
                    "pacing_identity_sha256": "p" * 64,
                    "sections": [],
                },
            },
            result_json={},
            error_code=None,
            error_message=None,
            error_details_json=None,
            completed_at=None,
            updated_at="2026-08-19T00:00:00Z",
        )
        self.job = SimpleNamespace(
            job_id="job_task2",
            status=JobStatus.RUNNING,
            retryable=False,
            progress=90,
            error_code=None,
            error_message=None,
            error_details_json=None,
            completed_at=None,
            finished_at=None,
            updated_at="2026-08-19T00:00:00Z",
            heartbeat_at="2026-08-19T00:00:00Z",
        )
        self.project = SimpleNamespace(
            project_id="project_task2",
            script_artifact_id="script_current",
            narration_audio_artifact_id=None,
            narration_word_timing_map_artifact_id=None,
        )
        self.tasks = [SimpleNamespace(voice_segment_task_id="task_1", status="completed")]
        self.staged: list[SimpleNamespace] = []
        self.integrity_checks = 0
        self.ordered_output_reads = 0


class _FakeDurableVoiceService:
    def __init__(self, session: _Session, shared: _SharedState) -> None:
        self.session = session
        self.shared = shared
        self.operations = _Repo(shared.operation)
        self.jobs = _Repo(shared.job)
        self.segment_tasks = _TaskRepo(shared.tasks)
        self.artifacts = _ArtifactStore(shared)
        self.tts_settings = SimpleNamespace(
            registry=SimpleNamespace(recommend_pacing=lambda provider_key, observation: None)
        )

    def _project_required(self, project_id: str):
        assert project_id == self.shared.project.project_id
        return self.shared.project

    def _cancellation_requested(self, job, operation) -> bool:
        return False

    def _terminalize_cancelled(self, job, operation) -> None:
        raise AssertionError("cancellation path was not expected")

    def _validate_operation_integrity(self, operation) -> None:
        self.shared.integrity_checks += 1

    def _ordered_segment_outputs(self, operation):
        self.shared.ordered_output_reads += 1
        return [b"raw-section"], [{"segment_audio_artifact_id": "raw_segment_artifact_1"}]


def _install_fakes(monkeypatch: pytest.MonkeyPatch, shared: _SharedState) -> None:
    monkeypatch.setattr(
        finalizer_module,
        "DurableVoiceService",
        lambda session: _FakeDurableVoiceService(session, shared),
    )
    analysis = SimpleNamespace(report={"status": "pass", "remediation_report_sha256": "r" * 64})
    monkeypatch.setattr(finalizer_module, "analyze_section_pacing", lambda **kwargs: analysis)
    monkeypatch.setattr(
        finalizer_module,
        "apply_section_pacing",
        lambda **kwargs: SimpleNamespace(
            segment_bytes=[b"paced-section"],
            segment_records=[{"segment_audio_artifact_id": "raw_segment_artifact_1"}],
            pacing_summary={"pacing_identity_sha256": "p" * 64, "sections": []},
        ),
    )
    monkeypatch.setattr(
        finalizer_module,
        "assemble_canonical_wav",
        lambda segment_bytes: (
            b"final-wav",
            SimpleNamespace(
                sample_rate_hz=48_000,
                sample_count=48_000,
                duration_seconds=1.0,
                sha256="c" * 64,
            ),
        ),
    )


def test_durable_finalizer_has_no_legacy_timing_publication_entrypoint() -> None:
    source = inspect.getsource(VoiceDurableFinalizer.finalize_operation)
    assert "run_operation" not in source
    assert "build_narration_timing_map" not in source
    assert "publish_voice_output_pair" not in source
    assert "narration_timing_map_artifact_id" not in source


def test_restart_safe_finalization_materializes_final_section_audio_only(monkeypatch: pytest.MonkeyPatch) -> None:
    shared = _SharedState()
    _install_fakes(monkeypatch, shared)
    session = _Session()

    first_process = VoiceDurableFinalizer(session_factory=lambda: session)
    assert first_process.finalize_operation(shared.operation.operation_id) is True
    assert shared.operation.status == "finalizing"
    assert shared.job.status == JobStatus.RUNNING
    assert shared.job.progress == 80
    assert shared.integrity_checks == 1
    assert shared.ordered_output_reads == 1
    assert shared.project.narration_audio_artifact_id is None
    assert shared.project.narration_word_timing_map_artifact_id is None
    assert len(shared.staged) == 1
    final_section = shared.operation.result_json["final_sections"][0]
    assert final_section["section_audio_artifact_id"] == "final_section_artifact_1"
    assert final_section["spoken_transcript"] == "Final narration."
    assert final_section["sample_rate_hz"] == 48_000
    assert final_section["sample_count"] == 48_000
    assert final_section["audio_sha256"] == "c" * 64
    assert shared.operation.result_json["task_3_status"] == "alignment"
    assert shared.operation.result_json["alignment_status"] == "queued"

    second_process = VoiceDurableFinalizer(session_factory=lambda: session)
    assert second_process.finalize_operation(shared.operation.operation_id) is False
    assert len(shared.staged) == 1
    assert shared.integrity_checks == 1
    assert shared.ordered_output_reads == 1


def test_stale_finalizer_ignores_scene_and_blocks_only_script_or_voice_pointer_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    shared = _SharedState()
    shared.project.script_artifact_id = "script_newer"
    _install_fakes(monkeypatch, shared)

    finalizer = VoiceDurableFinalizer(session_factory=lambda: _Session())
    assert finalizer.finalize_operation(shared.operation.operation_id) is False
    assert shared.operation.status == "superseded"
    assert shared.operation.error_code == "VOICE_UPSTREAM_STALE"
    assert shared.job.status == JobStatus.FAILED
    assert shared.staged == []


def test_finalizer_refuses_incomplete_segment_set(monkeypatch: pytest.MonkeyPatch) -> None:
    shared = _SharedState()
    shared.tasks[0].status = "materializing"
    _install_fakes(monkeypatch, shared)

    finalizer = VoiceDurableFinalizer(session_factory=lambda: _Session())
    with pytest.raises(AppError) as exc_info:
        finalizer.finalize_operation(shared.operation.operation_id)
    assert exc_info.value.error_code == "VOICE_SEGMENTS_INCOMPLETE"
    assert shared.staged == []

def test_kokoro_finalizer_never_renews_external_lease_after_local_write_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    shared = _SharedState()
    shared.operation.provider_identity_json = {
        "provider_key": "kokoro_local",
        "execution_identity": {"runtime": "exact-kokoro-test"},
    }
    shared.operation.voice_id = "af_heart"
    shared.local_write_started = False
    renewals: list[int] = []

    _install_fakes(monkeypatch, shared)

    class _CalibrationSettings:
        def __init__(self, session, repository) -> None:
            pass

        def kokoro_empirical_pacing_recommendation(self, **kwargs):
            return None

        def record_kokoro_pacing_observations(self, **kwargs) -> None:
            # SettingsRepository.upsert() flushes in production and starts the
            # SQLite write transaction that caused the observed self-lock.
            shared.local_write_started = True

    monkeypatch.setattr(finalizer_module, "VoicePacingCalibrationSettingsService", _CalibrationSettings)

    def apply_with_heartbeat(**kwargs):
        kwargs["progress_callback"]()
        return SimpleNamespace(
            segment_bytes=[b"paced-section"],
            segment_records=[{"segment_audio_artifact_id": "raw_segment_artifact_1"}],
            pacing_summary={"pacing_identity_sha256": "p" * 64, "sections": []},
        )

    monkeypatch.setattr(finalizer_module, "apply_section_pacing", apply_with_heartbeat)

    def renew_external_lease() -> None:
        assert shared.local_write_started is False
        renewals.append(len(renewals) + 1)

    finalizer = VoiceDurableFinalizer(session_factory=lambda: _Session())
    assert finalizer.finalize_operation(
        shared.operation.operation_id,
        renew_reconcile_lease=renew_external_lease,
    ) is True
    assert shared.local_write_started is True
    assert len(renewals) >= 4

