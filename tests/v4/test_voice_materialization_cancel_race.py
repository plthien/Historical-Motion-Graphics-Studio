from __future__ import annotations

from pathlib import Path
from threading import Event, Thread

from sqlalchemy import text

from app.core.config import get_settings
from app.core.time import utc_now_iso
from app.db.session import get_session_factory
from app.modules.v4.voice_audio import deterministic_fake_wav
from app.modules.v4.voice_durable_materializer import VoiceDurableMaterializer
from app.modules.v4.voice_durable_workflow import VoiceDurableWorkflowDriver
from app.modules.v4.voice_provider_adapters import FakeImmediateProvider
from app.repositories.voice_durable_working import VoiceDurableWorkingRepository
from tests.v4.test_voice_durable_provider_lifecycle import _seed_submitting_task


class PausingMaterializer(VoiceDurableMaterializer):
    def __init__(self, *, checked: Event, resume: Event) -> None:
        super().__init__(session_factory=get_session_factory())
        self.checked = checked
        self.resume = resume

    def _before_materialization_commit(self, *, task_id: str, execution_attempt_token: str) -> None:
        assert task_id
        assert execution_attempt_token
        self.checked.set()
        assert self.resume.wait(timeout=10)


def _prepare_materializing_task() -> tuple[str, str, str, Path]:
    provider = FakeImmediateProvider()
    task_id, execution_token = _seed_submitting_task(provider)
    root = get_settings().resolve_configured_path(get_settings().artifact_root)
    source = root / ".voice_attempts" / "materialization-race" / task_id / execution_token / "voice.wav"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(deterministic_fake_wav("materialization race"))

    with get_session_factory()() as session:
        row = session.execute(
            text(
                """
                SELECT operation_id, segment_id, segment_order, section_id,
                       source_start, source_end, text_sha256
                FROM voice_segment_tasks
                WHERE voice_segment_task_id=:task_id
                """
            ),
            {"task_id": task_id},
        ).mappings().one()
        operation_id = str(row["operation_id"])
        entry = {
            "segment_id": str(row["segment_id"]),
            "segment_order": int(row["segment_order"]),
            "section_id": str(row["section_id"]),
            "source_start": int(row["source_start"]),
            "source_end": int(row["source_end"]),
            "exact_text_sha256": str(row["text_sha256"]),
            "source_kind": "synthesis_required",
            "segment_audio_artifact_id": None,
            "source_voice_segment_task_id": task_id,
            "sample_count": None,
            "duration_seconds": None,
            "sha256": None,
        }
        session.execute(
            text(
                """
                UPDATE voice_segment_tasks
                SET status='materializing', provider_status='completed',
                    provider_result_metadata_json=:metadata,
                    next_reconcile_at=:now
                WHERE voice_segment_task_id=:task_id
                """
            ),
            {
                "task_id": task_id,
                "now": utc_now_iso(),
                "metadata": __import__("json").dumps(
                    {
                        "durable_provider_result": {
                            "audio_file_path": str(source),
                            "source_mime_type": "audio/wav",
                            "sample_rate_hz": 48_000,
                            "channels": 1,
                            "sample_width_bytes": 2,
                            "duration_seconds": 1.0,
                            "provider_word_timings": None,
                            "provider_character_timings": None,
                            "execution_attempt_token": execution_token,
                            "provider_key": provider.provider_key,
                        }
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        )
        session.execute(
            text(
                """
                UPDATE voice_generation_operations
                SET status='running', next_reconcile_at=:now,
                    result_json=:result_json
                WHERE operation_id=:operation_id
                """
            ),
            {
                "operation_id": operation_id,
                "now": utc_now_iso(),
                "result_json": __import__("json").dumps(
                    {"complete_segments": [entry]}, sort_keys=True, separators=(",", ":")
                ),
            },
        )
        session.commit()
    return task_id, execution_token, operation_id, source


def _run_paused_materializer(materializer: PausingMaterializer, task_id: str) -> tuple[Thread, list[bool], list[BaseException]]:
    results: list[bool] = []
    errors: list[BaseException] = []

    def target() -> None:
        try:
            results.append(materializer.materialize_task(task_id))
        except BaseException as exc:  # test thread must surface every failure
            errors.append(exc)

    thread = Thread(target=target, daemon=True)
    thread.start()
    return thread, results, errors


def _artifact_count(operation_id: str) -> int:
    with get_session_factory()() as session:
        return int(
            session.execute(
                text("SELECT count(*) FROM artifacts WHERE logical_id LIKE :prefix"),
                {"prefix": f"%/voice/operations/{operation_id}/segments/%"},
            ).scalar_one()
        )


def _persisted_segment_artifact_id(task_id: str) -> str | None:
    with get_session_factory()() as session:
        return session.execute(
            text("SELECT segment_audio_artifact_id FROM voice_segment_tasks WHERE voice_segment_task_id=:task_id"),
            {"task_id": task_id},
        ).scalar_one()


def test_cancellation_committed_during_materialization_wins_final_fence_and_cleans_stage() -> None:
    task_id, _token, operation_id, source = _prepare_materializing_task()
    checked = Event()
    resume = Event()
    materializer = PausingMaterializer(checked=checked, resume=resume)
    thread, results, errors = _run_paused_materializer(materializer, task_id)
    assert checked.wait(timeout=5)

    with get_session_factory()() as session:
        repository = VoiceDurableWorkingRepository(session)
        VoiceDurableWorkflowDriver(repository).cancel_operation(
            operation_id=operation_id,
            now_iso=utc_now_iso(),
        )
        session.commit()

    resume.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert errors == []
    assert results == [False]
    assert not source.exists()
    assert _artifact_count(operation_id) == 0
    assert _persisted_segment_artifact_id(task_id) is None

    with get_session_factory()() as session:
        repository = VoiceDurableWorkingRepository(session)
        task = repository.get_task(task_id)
        operation = repository.get_operation(operation_id)
        assert task is not None and task.status == "cancelled"
        assert operation is not None and operation.status == "cancelled"
        assert operation.cancel_requested_at is not None


def test_execution_token_replaced_during_materialization_cannot_complete_old_attempt() -> None:
    task_id, token, operation_id, source = _prepare_materializing_task()
    checked = Event()
    resume = Event()
    materializer = PausingMaterializer(checked=checked, resume=resume)
    thread, results, errors = _run_paused_materializer(materializer, task_id)
    assert checked.wait(timeout=5)

    replacement = f"replacement-{token}"
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                UPDATE voice_segment_tasks
                SET execution_attempt_token=:replacement,
                    state_revision=state_revision+1,
                    updated_at=:now
                WHERE voice_segment_task_id=:task_id
                  AND status='materializing'
                """
            ),
            {"task_id": task_id, "replacement": replacement, "now": utc_now_iso()},
        )
        session.commit()

    resume.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert errors == []
    assert results == [False]
    assert not source.exists()
    assert _artifact_count(operation_id) == 0
    assert _persisted_segment_artifact_id(task_id) is None

    with get_session_factory()() as session:
        repository = VoiceDurableWorkingRepository(session)
        task = repository.get_task(task_id)
        operation = repository.get_operation(operation_id)
        assert task is not None
        assert task.status == "materializing"
        assert task.execution_attempt_token == replacement
        assert operation is not None and operation.status == "running"
