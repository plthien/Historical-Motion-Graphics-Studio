from __future__ import annotations

import inspect
import json
import uuid

import pytest
from sqlalchemy import text

from app.core.time import utc_now_iso
from app.db.session import get_session_factory
from app.modules.v4.durable_voice_service import DurableVoiceService
from app.modules.v4.voice_durable_workflow import VoiceDurableWorkflowDriver
from app.modules.v4.voice_provider_adapters import FakeImmediateProvider
from app.repositories.voice_durable_working import VoiceDurableWorkingRepository
from tests.v4.test_voice_durable_provider_lifecycle import _seed_submitting_task


def _identity(task_id: str) -> dict[str, str]:
    with get_session_factory()() as session:
        return dict(
            session.execute(
                text(
                    """
                    SELECT t.voice_segment_task_id AS task_id, t.operation_id, t.job_id,
                           t.project_id, t.segment_id, t.provider
                    FROM voice_segment_tasks t
                    WHERE t.voice_segment_task_id = :task_id
                    """
                ),
                {"task_id": task_id},
            ).mappings().one()
        )


def _set_result_snapshot(operation_id: str, segment_id: str) -> None:
    payload = {
        "complete_segments": [
            {
                "segment_id": segment_id,
                "segment_audio_artifact_id": None,
            }
        ]
    }
    with get_session_factory()() as session:
        session.execute(
            text("UPDATE voice_generation_operations SET result_json = :payload WHERE operation_id = :operation_id"),
            {"payload": json.dumps(payload), "operation_id": operation_id},
        )
        session.commit()


def test_begin_submission_projects_generic_job_to_running() -> None:
    task_id, _ = _seed_submitting_task(FakeImmediateProvider())
    ids = _identity(task_id)
    now = utc_now_iso()
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                UPDATE voice_segment_tasks
                SET status = 'queued', submit_attempt_token = NULL, submit_started_at = NULL,
                    execution_attempt_token = NULL, execution_started_at = NULL
                WHERE voice_segment_task_id = :task_id
                """
            ),
            {"task_id": task_id},
        )
        session.execute(
            text("UPDATE voice_generation_operations SET status = 'queued' WHERE operation_id = :operation_id"),
            {"operation_id": ids["operation_id"]},
        )
        session.execute(
            text(
                "UPDATE jobs SET status = 'queued', started_at = NULL, heartbeat_at = NULL, progress = 0 WHERE job_id = :job_id"
            ),
            {"job_id": ids["job_id"]},
        )
        repository = VoiceDurableWorkingRepository(session)
        VoiceDurableWorkflowDriver(repository).begin_task_submission(
            operation_task_id=task_id,
            submit_attempt_token=f"projection_{uuid.uuid4().hex}",
            now_iso=now,
        )
        row = session.execute(
            text("SELECT status, started_at, heartbeat_at FROM jobs WHERE job_id = :job_id"),
            {"job_id": ids["job_id"]},
        ).mappings().one()
        assert row["status"] == "running"
        assert row["started_at"] == now
        assert row["heartbeat_at"] == now
        session.rollback()


def test_retry_wait_status_is_rich_and_get_does_not_mutate_job_projection() -> None:
    task_id, _ = _seed_submitting_task(FakeImmediateProvider())
    ids = _identity(task_id)
    retry_at = "2099-01-01T00:00:00+00:00"
    _set_result_snapshot(ids["operation_id"], ids["segment_id"])
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                UPDATE voice_segment_tasks
                SET status = 'queued', retry_count = 2, max_attempts = 5,
                    next_reconcile_at = :retry_at,
                    submit_attempt_token = NULL, submit_started_at = NULL,
                    execution_attempt_token = NULL, execution_started_at = NULL,
                    error_code = 'VOICE_PROVIDER_TRANSIENT',
                    error_message = 'Provider asks us to retry.',
                    error_details_json = :details
                WHERE voice_segment_task_id = :task_id
                """
            ),
            {
                "task_id": task_id,
                "retry_at": retry_at,
                "details": json.dumps({"provider_status": 503}),
            },
        )
        session.execute(
            text("UPDATE voice_generation_operations SET status = 'running' WHERE operation_id = :operation_id"),
            {"operation_id": ids["operation_id"]},
        )
        session.execute(
            text("UPDATE jobs SET status = 'queued', progress = 7 WHERE job_id = :job_id"),
            {"job_id": ids["job_id"]},
        )
        session.commit()

    with get_session_factory()() as session:
        before = session.execute(
            text("SELECT status, progress, updated_at FROM jobs WHERE job_id = :job_id"),
            {"job_id": ids["job_id"]},
        ).mappings().one()
        result = DurableVoiceService(session).job_status(ids["project_id"], ids["job_id"])
        after = session.execute(
            text("SELECT status, progress, updated_at FROM jobs WHERE job_id = :job_id"),
            {"job_id": ids["job_id"]},
        ).mappings().one()

        assert dict(after) == dict(before)
        assert result["status"] == "running"
        assert result["current_segment_id"] == ids["segment_id"]
        assert result["provider"] == ids["provider"]
        assert result["action"] == "retry_wait"
        assert result["retry_count"] == 2
        assert result["max_attempts"] == 5
        assert result["next_retry_at"] == retry_at
        assert result["cancel_requested"] is False
        assert result["problem"] == {
            "error_code": "VOICE_PROVIDER_TRANSIENT",
            "message": "Provider asks us to retry.",
            "details": {"provider_status": 503},
        }


def test_cancel_request_is_durable_and_visible_without_get_side_effects() -> None:
    task_id, _ = _seed_submitting_task(FakeImmediateProvider())
    ids = _identity(task_id)
    _set_result_snapshot(ids["operation_id"], ids["segment_id"])

    with get_session_factory()() as session:
        result = DurableVoiceService(session).cancel_job(ids["project_id"], ids["job_id"])
        assert result["cancel_requested"] is True
        assert result["action"] == "cancel"
        persisted = session.execute(
            text(
                "SELECT cancel_requested_at FROM voice_generation_operations WHERE operation_id = :operation_id"
            ),
            {"operation_id": ids["operation_id"]},
        ).scalar_one()
        assert persisted is not None


def test_terminal_failed_transition_projects_job_without_making_job_authoritative() -> None:
    task_id, _ = _seed_submitting_task(FakeImmediateProvider())
    ids = _identity(task_id)
    now = utc_now_iso()
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                UPDATE voice_segment_tasks
                SET status = 'failed', error_code = 'VOICE_PROVIDER_FATAL',
                    error_message = 'Provider rejected the request.'
                WHERE voice_segment_task_id = :task_id
                """
            ),
            {"task_id": task_id},
        )
        repository = VoiceDurableWorkingRepository(session)
        status = VoiceDurableWorkflowDriver(repository).refresh_operation(
            operation_id=ids["operation_id"],
            now_iso=now,
        )
        row = session.execute(
            text("SELECT status, retryable, completed_at FROM jobs WHERE job_id = :job_id"),
            {"job_id": ids["job_id"]},
        ).mappings().one()
        assert status == "failed"
        assert row["status"] == "failed"
        assert bool(row["retryable"]) is False
        assert row["completed_at"] == now
        session.rollback()


def test_job_status_source_is_read_only() -> None:
    source = inspect.getsource(DurableVoiceService.job_status).lower()
    for forbidden in ("commit(", "flush(", "update ", "insert ", "delete "):
        assert forbidden not in source
    assert "active_status" in source


def test_finalizing_job_status_projects_alignment_phase_instead_of_stale_synthesis_90() -> None:
    task_id, _ = _seed_submitting_task(FakeImmediateProvider())
    ids = _identity(task_id)
    payload = {
        "complete_segments": [
            {
                "segment_id": ids["segment_id"],
                "segment_audio_artifact_id": "artifact_final_segment",
            }
        ],
        "final_sections": [{"section_id": "sec_001"}],
        "task_3_status": "alignment",
        "alignment_status": "queued",
    }
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                UPDATE voice_generation_operations
                SET status = 'finalizing', result_json = :payload
                WHERE operation_id = :operation_id
                """
            ),
            {"payload": json.dumps(payload), "operation_id": ids["operation_id"]},
        )
        session.execute(
            text(
                """
                UPDATE voice_segment_tasks
                SET status = 'completed', provider_status = 'completed',
                    completed_at = :now, next_reconcile_at = NULL
                WHERE voice_segment_task_id = :task_id
                """
            ),
            {"task_id": task_id, "now": utc_now_iso()},
        )
        session.execute(
            text("UPDATE jobs SET status = 'running', progress = 90 WHERE job_id = :job_id"),
            {"job_id": ids["job_id"]},
        )
        session.commit()

    with get_session_factory()() as session:
        result = DurableVoiceService(session).job_status(ids["project_id"], ids["job_id"])

    assert result["status"] == "finalizing"
    assert result["progress"] == 91
    assert result["provider"] == "elevenlabs"
    assert result["action"] == "alignment_queue"
    assert result["current_segment_id"] is None
    assert result["problem"] is None


def test_failed_job_status_exposes_terminal_problem() -> None:
    task_id, _ = _seed_submitting_task(FakeImmediateProvider())
    ids = _identity(task_id)
    _set_operation_status(ids, "failed")

    with get_session_factory()() as session:
        result = DurableVoiceService(session).job_status(ids["project_id"], ids["job_id"])

    assert result["status"] == "failed"
    assert result["problem"] is not None
    assert result["problem"]["error_code"] == "VOICE_TEST_TERMINAL"
    assert result["problem"]["message"] == "Voice test terminal state."


ACTIVE_OPERATION_STATUSES = ("queued", "running", "waiting_for_provider", "assembling", "finalizing")
TERMINAL_OPERATION_STATUSES = ("completed", "failed", "cancelled", "superseded")


def _status_projection_for(operation_status: str) -> tuple[str, int, str, str | None]:
    if operation_status == "queued":
        return "queued", 0, "queued", None
    if operation_status == "completed":
        return "completed", 100, "completed", "completed"
    if operation_status == "failed":
        return "failed", 0, "failed", "failed"
    if operation_status == "cancelled":
        return "cancelled", 0, "cancelled", "cancelled"
    if operation_status == "superseded":
        return "failed", 0, "failed", "superseded"
    if operation_status == "waiting_for_provider":
        return "running", 30, "waiting_provider", "submitted"
    if operation_status == "assembling":
        return "running", 90, "completed", "completed"
    if operation_status == "finalizing":
        return "running", 95, "completed", "completed"
    return "running", 40, "submitting", "submitting"


def _set_operation_status(ids: dict[str, str], operation_status: str) -> None:
    now = utc_now_iso()
    job_status, progress, task_status, provider_status = _status_projection_for(operation_status)
    terminal = operation_status in TERMINAL_OPERATION_STATUSES
    failed = operation_status in {"failed", "superseded"}
    cancelled = operation_status == "cancelled"
    task_terminal = task_status in {"completed", "failed", "cancelled"}
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                UPDATE voice_generation_operations
                SET status = :operation_status,
                    error_code = :error_code,
                    error_message = :error_message,
                    completed_at = :completed_at,
                    next_reconcile_at = NULL,
                    reconcile_owner = NULL,
                    reconcile_token = NULL,
                    reconcile_lease_expires_at = NULL,
                    cancel_requested_at = :cancel_requested_at,
                    cancelled_at = :cancelled_at,
                    updated_at = :now
                WHERE operation_id = :operation_id
                """
            ),
            {
                "operation_id": ids["operation_id"],
                "operation_status": operation_status,
                "error_code": "VOICE_TEST_TERMINAL" if failed else None,
                "error_message": "Voice test terminal state." if failed else None,
                "completed_at": now if terminal else None,
                "cancel_requested_at": now if cancelled else None,
                "cancelled_at": now if cancelled else None,
                "now": now,
            },
        )
        session.execute(
            text(
                """
                UPDATE jobs
                SET status = :job_status,
                    progress = :progress,
                    retryable = 0,
                    error_code = :error_code,
                    error_message = :error_message,
                    error_details_json = NULL,
                    completed_at = :completed_at,
                    finished_at = :completed_at,
                    heartbeat_at = :heartbeat_at,
                    updated_at = :now
                WHERE job_id = :job_id
                """
            ),
            {
                "job_id": ids["job_id"],
                "job_status": job_status,
                "progress": progress,
                "error_code": "VOICE_TEST_TERMINAL" if failed else None,
                "error_message": "Voice test terminal state." if failed else None,
                "completed_at": now if terminal else None,
                "heartbeat_at": now if not terminal else None,
                "now": now,
            },
        )
        session.execute(
            text(
                """
                UPDATE voice_segment_tasks
                SET status = :task_status,
                    provider_status = :provider_status,
                    error_code = :error_code,
                    error_message = :error_message,
                    error_details_json = NULL,
                    completed_at = :completed_at,
                    next_reconcile_at = NULL,
                    updated_at = :now
                WHERE voice_segment_task_id = :task_id
                """
            ),
            {
                "task_id": ids["task_id"],
                "task_status": task_status,
                "provider_status": provider_status,
                "error_code": "VOICE_TEST_TERMINAL" if failed else None,
                "error_message": "Voice test terminal state." if failed else None,
                "completed_at": now if task_terminal else None,
                "now": now,
            },
        )
        session.commit()


def _durable_voice_snapshot(ids: dict[str, str]) -> dict[str, dict[str, object]]:
    with get_session_factory()() as session:
        operation = dict(
            session.execute(
                text(
                    """
                    SELECT status, error_code, error_message, completed_at, next_reconcile_at,
                           reconcile_owner, reconcile_token, reconcile_lease_expires_at,
                           cancel_requested_at, cancelled_at, updated_at, state_revision
                    FROM voice_generation_operations
                    WHERE operation_id = :operation_id
                    """
                ),
                {"operation_id": ids["operation_id"]},
            ).mappings().one()
        )
        job = dict(
            session.execute(
                text(
                    """
                    SELECT status, progress, retryable, error_code, error_message,
                           completed_at, finished_at, heartbeat_at, updated_at
                    FROM jobs
                    WHERE job_id = :job_id
                    """
                ),
                {"job_id": ids["job_id"]},
            ).mappings().one()
        )
        task = dict(
            session.execute(
                text(
                    """
                    SELECT status, provider_status, error_code, error_message,
                           completed_at, next_reconcile_at, updated_at, state_revision
                    FROM voice_segment_tasks
                    WHERE voice_segment_task_id = :task_id
                    """
                ),
                {"task_id": ids["task_id"]},
            ).mappings().one()
        )
        counts = dict(
            session.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM voice_generation_operations WHERE project_id = :project_id) AS operations,
                        (SELECT COUNT(*) FROM jobs WHERE job_id = :job_id) AS jobs,
                        (SELECT COUNT(*) FROM voice_segment_tasks WHERE operation_id = :operation_id) AS tasks
                    """
                ),
                {
                    "project_id": ids["project_id"],
                    "job_id": ids["job_id"],
                    "operation_id": ids["operation_id"],
                },
            ).mappings().one()
        )
        return {"operation": operation, "job": job, "task": task, "counts": counts}


@pytest.mark.parametrize("operation_status", ACTIVE_OPERATION_STATUSES)
def test_workspace_active_operation_exposes_only_active_voice_statuses(operation_status: str) -> None:
    task_id, _ = _seed_submitting_task(FakeImmediateProvider())
    ids = _identity(task_id)
    _set_operation_status(ids, operation_status)

    with get_session_factory()() as session:
        workspace = DurableVoiceService(session).get_workspace(ids["project_id"])

    assert workspace["active_operation"] == {
        "operation_id": ids["operation_id"],
        "job_id": ids["job_id"],
        "status": operation_status,
        "operation_type": "generate_all",
        "target_section_id": None,
    }


@pytest.mark.parametrize("operation_status", TERMINAL_OPERATION_STATUSES)
def test_workspace_active_operation_omits_terminal_voice_statuses(operation_status: str) -> None:
    task_id, _ = _seed_submitting_task(FakeImmediateProvider())
    ids = _identity(task_id)
    _set_operation_status(ids, operation_status)
    before = _durable_voice_snapshot(ids)

    with get_session_factory()() as session:
        service = DurableVoiceService(session)
        first = service.get_workspace(ids["project_id"])
        second = service.get_workspace(ids["project_id"])

    after = _durable_voice_snapshot(ids)
    assert first["active_operation"] is None
    assert second["active_operation"] is None
    assert after == before


def test_completed_job_status_reads_are_side_effect_free() -> None:
    task_id, _ = _seed_submitting_task(FakeImmediateProvider())
    ids = _identity(task_id)
    _set_result_snapshot(ids["operation_id"], ids["segment_id"])
    _set_operation_status(ids, "completed")
    before = _durable_voice_snapshot(ids)

    with get_session_factory()() as session:
        service = DurableVoiceService(session)
        first = service.job_status(ids["project_id"], ids["job_id"])
        second = service.job_status(ids["project_id"], ids["job_id"])

    after = _durable_voice_snapshot(ids)
    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert first["progress"] == 100
    assert second["progress"] == 100
    assert after == before
