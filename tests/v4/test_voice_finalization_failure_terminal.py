from __future__ import annotations

import json
from copy import deepcopy

import pytest
from sqlalchemy import text

from app.api.errors import AppError
from app.core.config import get_settings
from app.core.time import utc_now_iso
from app.db.enums import JobStatus
from app.db.models import Artifact, Project
from app.db.session import get_session_factory
from app.modules.v4.voice_audio import deterministic_fake_wav, validate_canonical_wav_bytes
from app.modules.v4.voice_service import VoiceService, canonical_hash
from app.modules.v4.voice_provider_adapters import FakeImmediateProvider
from app.voice_coordinator_runner import VoiceCoordinatorRunner
from app.db.enums import ArtifactStatus, ArtifactType
from tests.v4.test_voice_durable_provider_lifecycle import _artifact, _seed_submitting_task


class _FailingFinalizer:
    def __init__(self, error: AppError) -> None:
        self.error = error
        self.calls: list[str] = []
        self.renewals = 0

    def finalize_operation(
        self,
        operation_id: str,
        *,
        reconcile_lease=None,
        renew_reconcile_lease=None,
    ) -> None:
        self.calls.append(operation_id)
        assert reconcile_lease is not None
        if renew_reconcile_lease is not None:
            renew_reconcile_lease()
            self.renewals += 1
        raise self.error


def _seed_finalizing_operation() -> tuple[str, str, str]:
    task_id, _ = _seed_submitting_task(FakeImmediateProvider())
    now = utc_now_iso()
    artifact_id = f"artifact_{task_id}"
    with get_session_factory()() as session:
        operation_id = session.execute(
            text("SELECT operation_id FROM voice_segment_tasks WHERE voice_segment_task_id = :task_id"),
            {"task_id": task_id},
        ).scalar_one()
        job_id = session.execute(
            text("SELECT job_id FROM voice_generation_operations WHERE operation_id = :operation_id"),
            {"operation_id": operation_id},
        ).scalar_one()
        session.add(_artifact(artifact_id=artifact_id, now=now))
        session.flush()
        session.execute(
            text(
                """
                UPDATE voice_segment_tasks
                SET status = 'completed',
                    segment_audio_artifact_id = :artifact_id,
                    sample_count = 48000,
                    duration_seconds = 1.0,
                    sha256 = :sha256,
                    mime_type = 'audio/wav',
                    next_reconcile_at = NULL,
                    submit_attempt_token = NULL,
                    submit_started_at = NULL,
                    execution_attempt_token = NULL,
                    execution_started_at = NULL,
                    completed_at = :now_iso,
                    updated_at = :now_iso
                WHERE voice_segment_task_id = :task_id
                """
            ),
            {"task_id": task_id, "artifact_id": artifact_id, "sha256": "a" * 64, "now_iso": now},
        )
        session.execute(
            text(
                """
                UPDATE voice_generation_operations
                SET status = 'finalizing',
                    next_reconcile_at = :now_iso,
                    reconcile_owner = NULL,
                    reconcile_token = NULL,
                    reconcile_lease_expires_at = NULL,
                    updated_at = :now_iso
                WHERE operation_id = :operation_id
                """
            ),
            {"operation_id": operation_id, "now_iso": now},
        )
        session.execute(
            text(
                """
                UPDATE voice_generation_operations
                SET next_reconcile_at = '2999-01-01T00:00:00+00:00'
                WHERE operation_id != :operation_id
                  AND status IN ('queued', 'running', 'finalizing')
                """
            ),
            {"operation_id": operation_id},
        )
        session.execute(
            text(
                """
                UPDATE jobs
                SET status = :status,
                    progress = 95,
                    retryable = 0,
                    updated_at = :now_iso
                WHERE job_id = :job_id
                """
            ),
            {"job_id": job_id, "status": JobStatus.RUNNING.value, "now_iso": now},
        )
        session.commit()
    return str(operation_id), task_id, artifact_id


def test_non_retryable_finalization_error_terminalizes_operation_without_deferring_retry() -> None:
    operation_id, task_id, artifact_id = _seed_finalizing_operation()
    finalizer = _FailingFinalizer(
        AppError(
            "VOICE_PACING_CORRECTION_OUT_OF_RANGE",
            "Pacing correction is outside the quality fence.",
            422,
            retryable=False,
            details={
                "section_id": "sec_001",
                "planned_narration_wpm": 120,
                "provider_pacing_policy_version": "test_policy_v1",
            },
        )
    )
    runner = VoiceCoordinatorRunner(get_session_factory(), error_backoff_seconds=60)
    runner.finalizer = finalizer
    try:
        assert runner.run_once() is True
        assert finalizer.calls == [operation_id]
        assert finalizer.renewals == 1

        with get_session_factory()() as session:
            row = session.execute(
                text(
                    """
                    SELECT status, error_code, error_message, error_details_json,
                           next_reconcile_at, reconcile_owner, reconcile_token,
                           reconcile_lease_expires_at, completed_at
                    FROM voice_generation_operations
                    WHERE operation_id = :operation_id
                    """
                ),
                {"operation_id": operation_id},
            ).mappings().one()
            assert row["status"] == "failed"
            assert row["error_code"] == "VOICE_PACING_CORRECTION_OUT_OF_RANGE"
            assert row["next_reconcile_at"] is None
            assert row["reconcile_owner"] is None
            assert row["reconcile_token"] is None
            assert row["reconcile_lease_expires_at"] is None
            assert row["completed_at"] is not None
            details = json.loads(row["error_details_json"])
            assert details["section_id"] == "sec_001"
            assert details["provider_pacing_policy_version"] == "test_policy_v1"

            task = session.execute(
                text(
                    """
                    SELECT status, segment_audio_artifact_id
                    FROM voice_segment_tasks
                    WHERE voice_segment_task_id = :task_id
                    """
                ),
                {"task_id": task_id},
            ).mappings().one()
            assert task["status"] == "completed"
            assert task["segment_audio_artifact_id"] == artifact_id

            job = session.execute(
                text(
                    """
                    SELECT status, error_code, error_details_json, completed_at
                    FROM jobs
                    WHERE job_id = (
                        SELECT job_id FROM voice_generation_operations WHERE operation_id = :operation_id
                    )
                    """
                ),
                {"operation_id": operation_id},
            ).mappings().one()
            assert job["status"] == JobStatus.FAILED.value
            assert job["error_code"] == "VOICE_PACING_CORRECTION_OUT_OF_RANGE"
            assert json.loads(job["error_details_json"])["section_id"] == "sec_001"
            assert job["completed_at"] is not None

        assert runner.run_once() is False
        assert finalizer.calls == [operation_id]
    finally:
        runner.close()


def test_retryable_finalization_error_is_deferred_for_retry() -> None:
    operation_id, _, _ = _seed_finalizing_operation()
    finalizer = _FailingFinalizer(
        AppError(
            "VOICE_PACING_NORMALIZER_UNAVAILABLE",
            "Transient pacing normalizer failure.",
            503,
            retryable=True,
            details={"section_id": "sec_001"},
        )
    )
    runner = VoiceCoordinatorRunner(get_session_factory(), error_backoff_seconds=60)
    runner.finalizer = finalizer
    try:
        with pytest.raises(AppError, match="Transient pacing normalizer failure"):
            runner.run_once()
        assert finalizer.calls == [operation_id]

        with get_session_factory()() as session:
            row = session.execute(
                text(
                    """
                    SELECT status, error_code, next_reconcile_at,
                           reconcile_owner, reconcile_token, reconcile_lease_expires_at
                    FROM voice_generation_operations
                    WHERE operation_id = :operation_id
                    """
                ),
                {"operation_id": operation_id},
            ).mappings().one()
            assert row["status"] == "finalizing"
            assert row["error_code"] is None
            assert row["next_reconcile_at"] is not None
            assert row["reconcile_owner"] is None
            assert row["reconcile_token"] is None
            assert row["reconcile_lease_expires_at"] is None

        assert runner.run_once() is False
        assert finalizer.calls == [operation_id]
    finally:
        runner.close()


def test_legacy_raw_segment_reuse_is_disabled_after_voice_first_cutover() -> None:
    operation_id, task_id, _ = _seed_finalizing_operation()
    with get_session_factory()() as session:
        row = session.execute(
            text(
                """
                SELECT project_id, segment_id, segment_order, section_id,
                       source_start, source_end, text_sha256, exact_text
                FROM voice_segment_tasks
                WHERE voice_segment_task_id = :task_id
                """
            ),
            {"task_id": task_id},
        ).mappings().one()
        project = session.get(Project, str(row["project_id"]))
        assert project is not None
        segmentation = {
            "segmentation_input_sha256": session.execute(
                text("SELECT segmentation_input_sha256 FROM voice_generation_operations WHERE operation_id = :operation_id"),
                {"operation_id": operation_id},
            ).scalar_one(),
            "segments": [
                {
                    "segment_id": row["segment_id"],
                    "segment_order": row["segment_order"],
                    "section_id": row["section_id"],
                    "source_start": row["source_start"],
                    "source_end": row["source_end"],
                    "exact_text": row["exact_text"],
                    "text_sha256": row["text_sha256"],
                }
            ],
        }
        service = VoiceService(session)

        assert service._reusable_segments(
            project,
            segmentation,
            "b" * 64,
            "c" * 64,
            {row["segment_id"]: None},
            {row["segment_id"]: "d" * 64},
        ) == {}

        columns = {
            item["name"]
            for item in session.execute(text("PRAGMA table_info(voice_generation_operations)")).mappings()
        }
        assert "scene_plan_artifact_id" not in columns


def test_legacy_pacing_section_remediation_is_fenced_after_voice_first_cutover() -> None:
    operation_id, task_id, artifact_id = _seed_finalizing_operation()
    with get_session_factory()() as session:
        service = VoiceService(session)
        operation = service.operations.get(operation_id)
        assert operation is not None
        project = service.projects.get(operation.project_id)
        assert project is not None
        task = service.segment_tasks.get(task_id)
        assert task is not None

        project.script_artifact_id = operation.script_artifact_id
        project.narration_audio_artifact_id = None

        segmentation = deepcopy(operation.request_json["segmentation"])
        segmentation["segmentation_input_sha256"] = operation.segmentation_input_sha256
        session.flush()

        workspace = service.get_workspace(project.project_id)
        with pytest.raises(AppError) as exc_info:
            service._section_remediation_plan(
                project=project,
                request={},
                target_section_id=task.section_id,
                provider_hash=operation.provider_identity_sha256,
                segmentation=segmentation,
                tts_profile_id=operation.tts_profile_id,
                voice_id=operation.voice_id,
                language=operation.language,
            )

    assert workspace["current_narration_audio_artifact_id"] is None
    assert workspace["current_narration_word_timing_map_artifact_id"] is None
    assert workspace["available_actions"] == []
    assert workspace["segments"] == []
    assert exc_info.value.error_code == "VOICE_TASK2_DURABLE_SERVICE_REQUIRED"

class _LeaseLosingFinalizer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def finalize_operation(
        self,
        operation_id: str,
        *,
        reconcile_lease=None,
        renew_reconcile_lease=None,
    ) -> None:
        self.calls.append(operation_id)
        assert reconcile_lease is not None
        assert renew_reconcile_lease is not None
        with get_session_factory()() as session:
            session.execute(
                text(
                    """
                    UPDATE voice_generation_operations
                    SET reconcile_lease_expires_at = '2000-01-01T00:00:00+00:00'
                    WHERE operation_id = :operation_id
                      AND reconcile_owner = :owner
                      AND reconcile_token = :token
                    """
                ),
                {
                    "operation_id": operation_id,
                    "owner": reconcile_lease.owner,
                    "token": reconcile_lease.token,
                },
            )
            session.commit()
        renew_reconcile_lease()


class _CancelledFinalizer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def finalize_operation(
        self,
        operation_id: str,
        *,
        reconcile_lease=None,
        renew_reconcile_lease=None,
    ) -> None:
        self.calls.append(operation_id)
        assert reconcile_lease is not None
        with get_session_factory()() as session:
            service = VoiceService(session)
            operation = service.operations.get(operation_id)
            assert operation is not None
            job = service.jobs.get(operation.job_id)
            assert job is not None
            service._terminalize_cancelled(job, operation)
        raise AppError(
            "VOICE_OPERATION_CANCELLED",
            "Voice generation was cancelled.",
            409,
            retryable=False,
        )


def test_expired_finalization_lease_cannot_terminalize_until_fresh_runner_reclaims() -> None:
    operation_id, _, _ = _seed_finalizing_operation()
    stale_finalizer = _LeaseLosingFinalizer()
    stale_runner = VoiceCoordinatorRunner(get_session_factory(), error_backoff_seconds=0)
    stale_runner.finalizer = stale_finalizer
    try:
        with pytest.raises(AppError) as exc_info:
            stale_runner.run_once()
        assert exc_info.value.error_code == "VOICE_RECONCILE_LEASE_LOST"
        assert stale_finalizer.calls == [operation_id]
        with get_session_factory()() as session:
            row = session.execute(
                text(
                    """
                    SELECT status, error_code, next_reconcile_at
                    FROM voice_generation_operations
                    WHERE operation_id = :operation_id
                    """
                ),
                {"operation_id": operation_id},
            ).mappings().one()
            assert row["status"] == "finalizing"
            assert row["error_code"] is None
            assert row["next_reconcile_at"] is not None
    finally:
        stale_runner.close()

    terminal_finalizer = _FailingFinalizer(
        AppError(
            "VOICE_PACING_CORRECTION_OUT_OF_RANGE",
            "Pacing correction is outside the quality fence.",
            422,
            retryable=False,
            details={"section_id": "sec_001"},
        )
    )
    fresh_runner = VoiceCoordinatorRunner(get_session_factory(), error_backoff_seconds=0)
    fresh_runner.finalizer = terminal_finalizer
    try:
        assert fresh_runner.run_once() is True
        assert terminal_finalizer.calls == [operation_id]
        with get_session_factory()() as session:
            row = session.execute(
                text(
                    """
                    SELECT status, error_code, next_reconcile_at
                    FROM voice_generation_operations
                    WHERE operation_id = :operation_id
                    """
                ),
                {"operation_id": operation_id},
            ).mappings().one()
            assert row["status"] == "failed"
            assert row["error_code"] == "VOICE_PACING_CORRECTION_OUT_OF_RANGE"
            assert row["next_reconcile_at"] is None
        assert fresh_runner.run_once() is False
        assert terminal_finalizer.calls == [operation_id]
    finally:
        fresh_runner.close()


def test_finalizer_cancellation_remains_cancelled_and_is_not_reclassified_as_failure() -> None:
    operation_id, _, _ = _seed_finalizing_operation()
    finalizer = _CancelledFinalizer()
    runner = VoiceCoordinatorRunner(get_session_factory(), error_backoff_seconds=0)
    runner.finalizer = finalizer
    try:
        assert runner.run_once() is True
        assert finalizer.calls == [operation_id]
        with get_session_factory()() as session:
            row = session.execute(
                text(
                    """
                    SELECT status, error_code, next_reconcile_at,
                           reconcile_owner, reconcile_token, reconcile_lease_expires_at
                    FROM voice_generation_operations
                    WHERE operation_id = :operation_id
                    """
                ),
                {"operation_id": operation_id},
            ).mappings().one()
            assert row["status"] == "cancelled"
            assert row["error_code"] == "VOICE_OPERATION_CANCELLED"
            assert row["next_reconcile_at"] is None
            assert row["reconcile_owner"] is None
            assert row["reconcile_token"] is None
            assert row["reconcile_lease_expires_at"] is None

            job = session.execute(
                text(
                    """
                    SELECT status, error_code
                    FROM jobs
                    WHERE job_id = (
                        SELECT job_id FROM voice_generation_operations WHERE operation_id = :operation_id
                    )
                    """
                ),
                {"operation_id": operation_id},
            ).mappings().one()
            assert job["status"] == JobStatus.CANCELLED.value
            assert job["error_code"] != "VOICE_PACING_CORRECTION_OUT_OF_RANGE"
        assert runner.run_once() is False
        assert finalizer.calls == [operation_id]
    finally:
        runner.close()
