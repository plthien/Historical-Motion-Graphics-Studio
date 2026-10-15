from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.repositories.voice_durable_working import VoiceDurableWorkingRepository


@dataclass(frozen=True, slots=True)
class VoiceProviderTaskContext:
    operation_id: str
    segment_task_id: str
    segment_id: str
    segment_order: int
    section_id: str
    provider_key: str
    execution_attempt_token: str
    exact_text: str
    voice_id: str
    language: str
    speaking_rate: float
    sample_rate_hz: int
    output_format: str
    attempt_no: int
    retry_count: int
    max_attempts: int
    provider_identity_json: dict[str, Any]
    generation_settings_json: dict[str, Any]
    operation_request_json: dict[str, Any]
    cancel_requested_at: str | None
    provider_task_id: str | None
    provider_handle_json: dict[str, Any] | None
    execution_started_at: str | None = None


class VoiceProviderExecutionRepository(VoiceDurableWorkingRepository):
    def __init__(self, session: Session) -> None:
        super().__init__(session)

    def get_provider_context(self, task_id: str) -> VoiceProviderTaskContext | None:
        row = self.session.execute(
            text(
                """
                SELECT o.operation_id,
                       o.provider_identity_json,
                       o.generation_settings_snapshot_json,
                       o.request_json,
                       o.cancel_requested_at,
                       t.voice_segment_task_id,
                       t.segment_id,
                       t.segment_order,
                       t.section_id,
                       t.provider,
                       t.execution_attempt_token,
                       t.execution_started_at,
                       t.exact_text,
                       t.voice_id,
                       t.language,
                       t.speaking_rate,
                       t.sample_rate_hz,
                       t.output_format,
                       t.attempt_no,
                       t.retry_count,
                       t.max_attempts,
                       t.provider_task_id,
                       t.provider_handle_json
                FROM voice_segment_tasks t
                JOIN voice_generation_operations o ON o.operation_id = t.operation_id
                WHERE t.voice_segment_task_id = :task_id
                """
            ),
            {"task_id": task_id},
        ).mappings().one_or_none()
        if row is None or row["execution_attempt_token"] is None:
            return None
        provider_identity = self._json_object(row["provider_identity_json"])
        settings = self._json_object(row["generation_settings_snapshot_json"])
        provider_key = str(provider_identity.get("provider_key") or provider_identity.get("provider") or row["provider"])
        return VoiceProviderTaskContext(
            operation_id=str(row["operation_id"]),
            segment_task_id=str(row["voice_segment_task_id"]),
            segment_id=str(row["segment_id"]),
            segment_order=int(row["segment_order"]),
            section_id=str(row["section_id"]),
            provider_key=provider_key,
            execution_attempt_token=str(row["execution_attempt_token"]),
            execution_started_at=(str(row["execution_started_at"]) if row["execution_started_at"] else None),
            exact_text=str(row["exact_text"]),
            voice_id=str(row["voice_id"]),
            language=str(row["language"]),
            speaking_rate=float(row["speaking_rate"]),
            sample_rate_hz=int(row["sample_rate_hz"]),
            output_format=str(row["output_format"]),
            attempt_no=int(row["attempt_no"]),
            retry_count=int(row["retry_count"]),
            max_attempts=int(row["max_attempts"]),
            provider_identity_json=provider_identity,
            generation_settings_json=settings,
            operation_request_json=self._json_object(row["request_json"]),
            cancel_requested_at=row["cancel_requested_at"],
            provider_task_id=row["provider_task_id"],
            provider_handle_json=self._json_optional(row["provider_handle_json"]),
        )

    def cancellation_requested(self, *, task_id: str, execution_attempt_token: str) -> bool:
        row = self.session.execute(
            text(
                """
                SELECT o.cancel_requested_at, t.execution_attempt_token, t.status
                FROM voice_segment_tasks t
                JOIN voice_generation_operations o ON o.operation_id = t.operation_id
                WHERE t.voice_segment_task_id = :task_id
                """
            ),
            {"task_id": task_id},
        ).mappings().one_or_none()
        if row is None:
            return True
        return (
            row["cancel_requested_at"] is not None
            or row["execution_attempt_token"] != execution_attempt_token
            or row["status"] == "cancelled"
        )

    def persist_pending_submission(
        self,
        *,
        task_id: str,
        execution_attempt_token: str,
        provider_task_id: str,
        provider_handle_json: dict[str, Any],
        now_iso: str,
        next_reconcile_at: str,
    ) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE voice_segment_tasks
                SET status = 'waiting_provider',
                    provider_task_id = :provider_task_id,
                    provider_status = 'pending',
                    provider_submitted_at = COALESCE(provider_submitted_at, :now_iso),
                    provider_handle_json = :provider_handle_json,
                    next_reconcile_at = :next_reconcile_at,
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE voice_segment_task_id = :task_id
                  AND status IN ('submitting', 'waiting_provider')
                  AND execution_attempt_token = :execution_attempt_token
                  AND EXISTS (
                      SELECT 1 FROM voice_generation_operations o
                      WHERE o.operation_id = voice_segment_tasks.operation_id
                        AND o.cancel_requested_at IS NULL
                        AND o.status IN ('queued', 'running', 'finalizing')
                  )
                """
            ),
            {
                "task_id": task_id,
                "execution_attempt_token": execution_attempt_token,
                "provider_task_id": provider_task_id,
                "provider_handle_json": json.dumps(provider_handle_json, sort_keys=True, separators=(",", ":")),
                "now_iso": now_iso,
                "next_reconcile_at": next_reconcile_at,
            },
        )
        return int(result.rowcount or 0) == 1

    def persist_completed_provider_result(
        self,
        *,
        task_id: str,
        execution_attempt_token: str,
        provider_task_id: str | None,
        result_metadata: dict[str, Any],
        now_iso: str,
    ) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE voice_segment_tasks
                SET status = 'materializing',
                    provider_task_id = COALESCE(:provider_task_id, provider_task_id),
                    provider_status = 'completed',
                    provider_result_metadata_json = :result_metadata,
                    next_reconcile_at = :now_iso,
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE voice_segment_task_id = :task_id
                  AND status IN ('submitting', 'waiting_provider')
                  AND execution_attempt_token = :execution_attempt_token
                  AND EXISTS (
                      SELECT 1 FROM voice_generation_operations o
                      WHERE o.operation_id = voice_segment_tasks.operation_id
                        AND o.cancel_requested_at IS NULL
                        AND o.status IN ('queued', 'running', 'finalizing')
                  )
                """
            ),
            {
                "task_id": task_id,
                "execution_attempt_token": execution_attempt_token,
                "provider_task_id": provider_task_id,
                "result_metadata": json.dumps(result_metadata, sort_keys=True, separators=(",", ":")),
                "now_iso": now_iso,
            },
        )
        return int(result.rowcount or 0) == 1

    def retry_or_fail_provider_action(
        self,
        *,
        task_id: str,
        execution_attempt_token: str,
        error_code: str,
        error_message: str,
        retryable: bool,
        now_iso: str,
        next_retry_at: str | None,
    ) -> str:
        context = self.get_provider_context(task_id)
        if context is None or context.execution_attempt_token != execution_attempt_token:
            return "stale"
        total_started_attempts = context.retry_count + 1
        if retryable and total_started_attempts < context.max_attempts and next_retry_at is not None:
            result = self.session.execute(
                text(
                    """
                    UPDATE voice_segment_tasks
                    SET status = 'queued',
                        retry_count = retry_count + 1,
                        submit_attempt_token = NULL,
                        submit_started_at = NULL,
                        execution_attempt_token = NULL,
                        execution_started_at = NULL,
                        provider_task_id = NULL,
                        provider_status = NULL,
                        provider_submitted_at = NULL,
                        provider_handle_json = NULL,
                        provider_result_metadata_json = NULL,
                        next_reconcile_at = :next_retry_at,
                        last_retry_at = :now_iso,
                        error_code = :error_code,
                        error_message = :error_message,
                        updated_at = :now_iso,
                        state_revision = state_revision + 1
                    WHERE voice_segment_task_id = :task_id
                      AND execution_attempt_token = :execution_attempt_token
                      AND status IN ('submitting', 'waiting_provider')
                    """
                ),
                {
                    "task_id": task_id,
                    "execution_attempt_token": execution_attempt_token,
                    "next_retry_at": next_retry_at,
                    "now_iso": now_iso,
                    "error_code": error_code,
                    "error_message": error_message,
                },
            )
            return "queued" if int(result.rowcount or 0) == 1 else "stale"
        result = self.session.execute(
            text(
                """
                UPDATE voice_segment_tasks
                SET status = 'failed',
                    provider_status = 'failed',
                    next_reconcile_at = NULL,
                    error_code = :error_code,
                    error_message = :error_message,
                    completed_at = COALESCE(completed_at, :now_iso),
                    updated_at = :now_iso,
                    state_revision = state_revision + 1
                WHERE voice_segment_task_id = :task_id
                  AND execution_attempt_token = :execution_attempt_token
                  AND status IN ('submitting', 'waiting_provider')
                """
            ),
            {
                "task_id": task_id,
                "execution_attempt_token": execution_attempt_token,
                "now_iso": now_iso,
                "error_code": error_code,
                "error_message": error_message,
            },
        )
        return "failed" if int(result.rowcount or 0) == 1 else "stale"

    @staticmethod
    def _json_object(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str):
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, dict) else {}
        return {}

    @classmethod
    def _json_optional(cls, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        parsed = cls._json_object(value)
        return parsed or None
