from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import AppError
from app.core.config import get_settings
from app.core.time import utc_now_iso
from app.modules.v4.voice_durable_workflow import VoiceDurableWorkflowDriver
from app.modules.v4.voice_normalization import VoiceAudioNormalizer
from app.modules.v4.voice_service import VoiceService
from app.repositories.voice_durable_working import VoiceDurableWorkingRepository
from app.services.artifacts import StagedArtifact, cleanup_staged_artifact_safely


logger = logging.getLogger(__name__)


class VoiceDurableMaterializer:
    """Normalize and durably materialize one fenced provider result.

    Provider I/O has already finished before this class runs. The provider result
    is normalized outside a long-lived DB transaction, then a fresh transaction
    stages the immutable segment artifact and conditionally completes the task.
    The final SQL fence proves the same execution attempt is still materializing
    and its operation has not been cancelled or terminalized.
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        audio_normalizer: VoiceAudioNormalizer | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.audio_normalizer = audio_normalizer or VoiceAudioNormalizer(
            maximum_bytes=get_settings().tts_provider_max_output_bytes
        )

    def materialize_task(self, task_id: str) -> bool:
        logger.info("Voice segment materializer entered task_id=%s", task_id)
        staged: StagedArtifact | None = None
        source_path: Path | None = None
        normalized_path: Path | None = None
        committed = False

        # Phase one snapshots only immutable attempt identity/provider-result
        # metadata. Do not keep a read transaction open while file I/O occurs.
        with self.session_factory() as session:
            service = VoiceService(session)
            task = service.segment_tasks.get(task_id)
            if task is None:
                raise AppError("VOICE_SEGMENT_TASK_NOT_FOUND", "Voice segment task was not found.", 404, retryable=False)
            operation = service.operations.get(task.operation_id)
            if operation is None:
                raise AppError("VOICE_OPERATION_NOT_FOUND", "Voice operation was not found.", 404, retryable=False)
            if task.status != "materializing":
                return False
            expected_token = task.execution_attempt_token
            metadata = task.provider_result_metadata_json if isinstance(task.provider_result_metadata_json, dict) else {}
            durable_result = metadata.get("durable_provider_result") if isinstance(metadata, dict) else None
            if not isinstance(durable_result, dict):
                raise AppError(
                    "VOICE_PROVIDER_RESULT_INVALID",
                    "Durable Voice provider result metadata is missing.",
                    409,
                    retryable=False,
                )
            result_token = durable_result.get("execution_attempt_token")
            if not isinstance(expected_token, str) or result_token != expected_token:
                self._cleanup_source(durable_result.get("audio_file_path"))
                return False
            raw_path = durable_result.get("audio_file_path")
            if not isinstance(raw_path, str) or not raw_path:
                raise AppError("VOICE_PROVIDER_RESULT_INVALID", "Voice provider audio path is missing.", 409, retryable=False)
            source_path = Path(raw_path)
            word_timings = durable_result.get("provider_word_timings")
            character_timings = durable_result.get("provider_character_timings")
            provider_timing = (
                {
                    "word_timings": word_timings if isinstance(word_timings, list) else None,
                    "character_timings": character_timings if isinstance(character_timings, list) else None,
                }
                if isinstance(word_timings, list) or isinstance(character_timings, list)
                else None
            )

        try:
            normalized_path, _normalized_info = self.audio_normalizer.normalize_file(source_path)
            data = normalized_path.read_bytes()
        except OSError as exc:
            raise AppError(
                "VOICE_PROVIDER_RESULT_MISSING",
                "Voice provider output is no longer available for materialization.",
                409,
                retryable=True,
            ) from exc

        # Deterministic test hook and explicit concurrency boundary: cancellation
        # or token supersession committed before the final transaction must win.
        self._before_materialization_commit(
            task_id=task_id,
            execution_attempt_token=expected_token,
        )

        try:
            with self.session_factory() as session:
                service = VoiceService(session)
                task = service.segment_tasks.get(task_id)
                if task is None:
                    self._cleanup_normalized_files(source_path, normalized_path)
                    return False
                operation = service.operations.get(task.operation_id)
                if operation is None:
                    self._cleanup_normalized_files(source_path, normalized_path)
                    return False

                staged, audio_meta = service._stage_segment_artifact(operation, task, data)
                completed_at = utc_now_iso()
                result = session.execute(
                    text(
                        """
                        UPDATE voice_segment_tasks
                        SET segment_audio_artifact_id = :artifact_id,
                            sample_count = :sample_count,
                            duration_seconds = :duration_seconds,
                            sha256 = :sha256,
                            mime_type = 'audio/wav',
                            provider_timing_json = :provider_timing_json,
                            status = 'completed',
                            provider_status = 'completed',
                            error_code = NULL,
                            error_message = NULL,
                            error_details_json = NULL,
                            completed_at = :completed_at,
                            updated_at = :completed_at,
                            next_reconcile_at = NULL,
                            state_revision = state_revision + 1
                        WHERE voice_segment_task_id = :task_id
                          AND status = 'materializing'
                          AND execution_attempt_token = :execution_attempt_token
                          AND EXISTS (
                              SELECT 1
                              FROM voice_generation_operations o
                              WHERE o.operation_id = voice_segment_tasks.operation_id
                                AND o.cancel_requested_at IS NULL
                                AND o.status IN ('queued', 'running', 'finalizing')
                          )
                        """
                    ),
                    {
                        "task_id": task_id,
                        "execution_attempt_token": expected_token,
                        "artifact_id": staged.artifact_id,
                        "sample_count": int(audio_meta["sample_count"]),
                        "duration_seconds": float(audio_meta["duration_seconds"]),
                        "sha256": str(audio_meta["sha256"]),
                        "provider_timing_json": (
                            json.dumps(provider_timing, sort_keys=True, separators=(",", ":"))
                            if provider_timing is not None
                            else None
                        ),
                        "completed_at": completed_at,
                    },
                )
                if int(result.rowcount or 0) != 1:
                    session.rollback()
                    cleanup_staged_artifact_safely(staged, logger=logger)
                    self._cleanup_normalized_files(source_path, normalized_path)
                    return False

                session.expire(task)
                session.refresh(task)
                operation = service.operations.get(task.operation_id)
                if operation is None:
                    session.rollback()
                    cleanup_staged_artifact_safely(staged, logger=logger)
                    self._cleanup_normalized_files(source_path, normalized_path)
                    return False
                service._update_result_snapshot_from_task(operation, task)
                repository = VoiceDurableWorkingRepository(session)
                VoiceDurableWorkflowDriver(repository).refresh_operation(
                    operation_id=operation.operation_id,
                    now_iso=completed_at,
                )
                session.commit()
                committed = True
        except Exception:
            if staged is not None and not committed:
                cleanup_staged_artifact_safely(staged, logger=logger)
            raise

        if committed:
            self._cleanup_normalized_files(source_path, normalized_path)
            if source_path is not None:
                self._cleanup_empty_parents(source_path.parent)
        logger.info("Voice segment materializer exited task_id=%s committed=%s", task_id, committed)
        return committed

    def _before_materialization_commit(self, *, task_id: str, execution_attempt_token: str) -> None:
        """No-op hook used by deterministic race tests immediately before final fencing."""
        _ = (task_id, execution_attempt_token)

    @classmethod
    def _cleanup_normalized_files(cls, source_path: Path | None, normalized_path: Path | None) -> None:
        if normalized_path is not None and normalized_path != source_path:
            cls._cleanup_source(str(normalized_path))
        if source_path is not None:
            cls._cleanup_source(str(source_path))

    @staticmethod
    def _cleanup_source(value: object) -> None:
        if not isinstance(value, str) or not value:
            return
        try:
            Path(value).unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _cleanup_empty_parents(directory: Path) -> None:
        current = directory
        for _ in range(4):
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
