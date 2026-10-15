from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Callable

from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import AppError
from app.core.config import Settings, get_settings
from app.core.time import utc_now_iso
from app.modules.v4.voice_durable_workflow import VoiceDurableWorkflowDriver
from app.modules.v4.voice_provider_contracts import (
    VoiceExecutionControl,
    VoiceProviderExecutionResult,
    VoiceProviderHandle,
    VoiceProviderPacingDirective,
    VoiceProviderProfile,
    VoiceProviderRegistry,
    VoiceProviderSubmission,
    VoiceSynthesisRequest,
)
from app.modules.v4.voice_provider_execution_pool import VoiceProviderExecutionPool, VoiceProviderReservation
from app.repositories.voice_provider_execution import VoiceProviderExecutionRepository, VoiceProviderTaskContext


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProviderActionProgress:
    action: str
    changed: bool
    state: str


class DatabaseVoiceExecutionControl(VoiceExecutionControl):
    """Cooperative provider control backed by fresh DB reads.

    The monotonic deadline is deliberately process-local: timeout is a live action
    concern. Restart ownership and correctness remain persisted in task state and
    the execution attempt token.
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        segment_task_id: str,
        execution_attempt_token: str,
        expected_execution_identity: dict[str, Any] | None = None,
        timeout_seconds: int,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._segment_task_id = segment_task_id
        self._execution_attempt_token = execution_attempt_token
        self._expected_execution_identity = (
            dict(expected_execution_identity)
            if isinstance(expected_execution_identity, dict)
            else None
        )
        self._monotonic_clock = monotonic_clock
        self._deadline = monotonic_clock() + max(1, timeout_seconds)

    @property
    def execution_attempt_token(self) -> str:
        return self._execution_attempt_token

    @property
    def expected_execution_identity(self) -> dict[str, Any] | None:
        return (
            dict(self._expected_execution_identity)
            if self._expected_execution_identity is not None
            else None
        )

    def cancellation_requested(self) -> bool:
        with self._session_factory() as session:
            repository = VoiceProviderExecutionRepository(session)
            return repository.cancellation_requested(
                task_id=self._segment_task_id,
                execution_attempt_token=self._execution_attempt_token,
            )

    def deadline_reached(self) -> bool:
        return self._monotonic_clock() >= self._deadline


class VoiceProviderActionController:
    """Capability-driven Voice provider lifecycle controller.

    This object owns no durable truth. It may dispatch provider I/O into a bounded
    in-process pool, but every transition/result is accepted only by a fresh,
    execution-token-fenced DB mutation.
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        registry: VoiceProviderRegistry,
        pool: VoiceProviderExecutionPool[Any],
        settings: Settings | None = None,
        poll_interval_seconds: int = 2,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        self.session_factory = session_factory
        self.registry = registry
        self.pool = pool
        self.settings = settings or get_settings()
        self.poll_interval_seconds = max(1, int(poll_interval_seconds))
        self.monotonic_clock = monotonic_clock
        self._submit_interrupt_requested: set[str] = set()
        self._retired_submit_keys: set[str] = set()
        self._background_cancel_keys: set[str] = set()
        self.cancel_pool = VoiceProviderExecutionPool[Any](max_workers=1, max_outstanding=8)

    def dispatch_submit(
        self,
        task_id: str,
        *,
        reservation: VoiceProviderReservation | None = None,
    ) -> ProviderActionProgress:
        context = self._context_required(task_id)
        request = self._request(context)
        adapter = self.registry.resolve(context.provider_key)
        key = self._pool_key("submit", context)
        action = lambda: self._run_provider_action(context, lambda control: adapter.submit(request, control))
        dispatched = (
            self.pool.dispatch_reserved(key, reservation, action)
            if reservation is not None
            else self.pool.dispatch(key, action)
        )
        if dispatched:
            logger.info(
                "Voice provider submit dispatched operation_id=%s task_id=%s segment_id=%s provider=%s retry_count=%s",
                context.operation_id,
                context.segment_task_id,
                context.segment_id,
                context.provider_key,
                context.retry_count,
            )
        return ProviderActionProgress(action="submit", changed=dispatched, state="running" if dispatched else "capacity_unavailable")

    def harvest_submit(self, task_id: str) -> ProviderActionProgress:
        context = self._context_required(task_id)
        key = self._pool_key("submit", context)
        if self.pool.is_active(key):
            if self._submission_hard_deadline_reached(context) and key not in self._submit_interrupt_requested:
                self._submit_interrupt_requested.add(key)
                interrupted = self._interrupt_execution_context(context, reason="timeout")
                logger.warning(
                    "Voice provider hard timeout operation_id=%s task_id=%s segment_id=%s provider=%s interrupted=%s timeout_seconds=%s",
                    context.operation_id,
                    context.segment_task_id,
                    context.segment_id,
                    context.provider_key,
                    interrupted,
                    self.settings.tts_provider_timeout_seconds,
                )
            return ProviderActionProgress(action="submit", changed=False, state="running")
        self._submit_interrupt_requested.discard(key)
        try:
            submission = self.pool.take_completed(key)
        except Exception as exc:
            return self._record_provider_failure(context, exc)
        if submission is None:
            return ProviderActionProgress(action="submit", changed=False, state="not_owned")
        if not isinstance(submission, VoiceProviderSubmission):
            return self._record_provider_failure(
                context,
                AppError("VOICE_PROVIDER_RESULT_INVALID", "Voice provider returned an invalid submission result.", 502, retryable=False),
            )
        return self._accept_submission(context, submission)

    def dispatch_poll(
        self,
        task_id: str,
        *,
        reservation: VoiceProviderReservation | None = None,
    ) -> ProviderActionProgress:
        context = self._context_required(task_id)
        handle = self._handle_required(context)
        adapter = self.registry.resolve(context.provider_key)
        key = self._pool_key("poll", context)
        action = lambda: self._run_provider_action(context, lambda control: adapter.poll(handle, control))
        dispatched = (
            self.pool.dispatch_reserved(key, reservation, action)
            if reservation is not None
            else self.pool.dispatch(key, action)
        )
        return ProviderActionProgress(action="poll", changed=dispatched, state="running" if dispatched else "capacity_unavailable")

    def harvest_poll(self, task_id: str) -> ProviderActionProgress:
        context = self._context_required(task_id)
        key = self._pool_key("poll", context)
        if self.pool.is_active(key):
            return ProviderActionProgress(action="poll", changed=False, state="running")
        try:
            result = self.pool.take_completed(key)
        except Exception as exc:
            return self._record_provider_failure(context, exc)
        if result is None:
            return ProviderActionProgress(action="poll", changed=False, state="not_owned")
        if result.status == "pending":
            return self._reschedule_waiting(context, handle=result.handle)
        assert result.result is not None
        return self._accept_execution_result(context, result.result)

    def dispatch_reconcile_submission(
        self,
        task_id: str,
        *,
        reservation: VoiceProviderReservation | None = None,
    ) -> ProviderActionProgress:
        context = self._context_required(task_id)
        request = self._request(context)
        handle = self._handle_optional(context)
        adapter = self.registry.resolve(context.provider_key)
        key = self._pool_key("reconcile", context)
        action = lambda: self._run_provider_action(
            context, lambda control: adapter.reconcile_submission(request, handle, control)
        )
        dispatched = (
            self.pool.dispatch_reserved(key, reservation, action)
            if reservation is not None
            else self.pool.dispatch(key, action)
        )
        return ProviderActionProgress(action="reconcile", changed=dispatched, state="running" if dispatched else "capacity_unavailable")

    def harvest_reconcile_submission(self, task_id: str) -> ProviderActionProgress:
        context = self._context_required(task_id)
        key = self._pool_key("reconcile", context)
        if self.pool.is_active(key):
            return ProviderActionProgress(action="reconcile", changed=False, state="running")
        try:
            result = self.pool.take_completed(key)
        except Exception as exc:
            if self._runtime_identity_error(exc):
                return self._record_provider_failure(context, exc)
            return self._mark_ambiguous(context, exc)
        if result is None:
            return ProviderActionProgress(action="reconcile", changed=False, state="not_owned")
        if result.status == "replay_safe":
            return self._replay_safe(context)
        if result.status == "ambiguous":
            return self._mark_ambiguous(context, None)
        if result.status == "pending":
            assert result.handle is not None
            return self._persist_pending(context, result.handle)
        assert result.result is not None
        return self._accept_execution_result(context, result.result)

    def best_effort_cancel(self, task_id: str) -> ProviderActionProgress:
        context = self._context_required(task_id)
        adapter = self.registry.resolve(context.provider_key)
        if not adapter.capabilities().supports_cancel:
            return ProviderActionProgress(action="cancel", changed=False, state="unsupported")
        handle = self._handle_optional(context)
        key = self._pool_key("cancel", context)
        # Cancellation uses an independent control lane. It must never compete
        # with synthesis/poll work for the bounded provider execution pool.
        dispatched = self.cancel_pool.dispatch(
            key, lambda: adapter.cancel(handle, self._control(context))
        )
        if dispatched:
            self._background_cancel_keys.add(key)
        return ProviderActionProgress(action="cancel", changed=dispatched, state="running" if dispatched else "capacity_unavailable")

    def interrupt_and_retire_submit(
        self,
        task_id: str,
        *,
        reason: str,
        settle_grace_seconds: float = 3.0,
    ) -> ProviderActionProgress:
        context = self._context_required(task_id)
        key = self._pool_key("submit", context)
        interrupted = self._interrupt_execution_context(context, reason=reason)
        if self.pool.is_active(key):
            # Durable cancellation is already persisted. Even if the adapter cannot
            # interrupt at this exact instant, give the action a bounded chance to
            # observe DB-backed cancellation and settle.
            self.pool.wait_until_settled(key, timeout_seconds=settle_grace_seconds)
        if self.pool.is_active(key):
            # Preserve ownership until settlement so a later host turn can reap
            # the old Future instead of leaking one provider-capacity slot.
            self._retired_submit_keys.add(key)
            logger.error(
                "Voice provider execution did not settle after interrupt operation_id=%s task_id=%s segment_id=%s provider=%s reason=%s",
                context.operation_id,
                context.segment_task_id,
                context.segment_id,
                context.provider_key,
                reason,
            )
            return ProviderActionProgress(action="interrupt", changed=interrupted, state="still_running")
        try:
            completed = self.pool.take_completed(key)
        except Exception:
            completed = None
        self._retired_submit_keys.discard(key)
        if isinstance(completed, VoiceProviderSubmission) and completed.result is not None:
            self._cleanup_result(completed.result)
        self._submit_interrupt_requested.discard(key)
        logger.info(
            "Voice provider execution retired operation_id=%s task_id=%s segment_id=%s provider=%s reason=%s interrupted=%s",
            context.operation_id,
            context.segment_task_id,
            context.segment_id,
            context.provider_key,
            reason,
            interrupted,
        )
        return ProviderActionProgress(action="interrupt", changed=interrupted, state="retired")

    def reap_background_actions(self) -> int:
        reaped = 0
        for key in tuple(self._retired_submit_keys):
            if self.pool.is_active(key):
                continue
            try:
                completed = self.pool.take_completed(key)
            except Exception:
                completed = None
            if isinstance(completed, VoiceProviderSubmission) and completed.result is not None:
                self._cleanup_result(completed.result)
            self._retired_submit_keys.discard(key)
            self._submit_interrupt_requested.discard(key)
            reaped += 1
        for key in tuple(self._background_cancel_keys):
            if self.cancel_pool.is_active(key):
                continue
            try:
                self.cancel_pool.take_completed(key)
            except Exception:
                pass
            self._background_cancel_keys.discard(key)
            reaped += 1
        return reaped

    def _submission_hard_deadline_reached(self, context: VoiceProviderTaskContext) -> bool:
        if not context.execution_started_at:
            return False
        try:
            started = datetime.fromisoformat(context.execution_started_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        deadline = started.astimezone(timezone.utc) + timedelta(seconds=self.settings.tts_provider_timeout_seconds)
        return datetime.now(timezone.utc) >= deadline

    def _interrupt_execution_context(self, context: VoiceProviderTaskContext, *, reason: str) -> bool:
        adapter = self.registry.resolve(context.provider_key)
        capabilities = adapter.capabilities()
        if not capabilities.supports_execution_interrupt:
            return False
        interrupt = getattr(adapter, "interrupt_execution", None)
        if not callable(interrupt):
            raise AppError(
                "VOICE_PROVIDER_INTERRUPT_CONTRACT_INVALID",
                "Voice provider declared execution interruption support without an interrupt implementation.",
                502,
                retryable=False,
                details={"provider_key": context.provider_key},
            )
        return bool(
            interrupt(
                segment_task_id=context.segment_task_id,
                execution_attempt_token=context.execution_attempt_token,
                reason=reason,
            )
        )

    def close(self) -> None:
        self.reap_background_actions()
        self.cancel_pool.close(wait=True)
        self.pool.close(wait=True)
        for value in self.pool.drain_completed():
            result = getattr(value, "result", None)
            if isinstance(result, VoiceProviderExecutionResult):
                self._cleanup_result(result)

    def _accept_submission(self, context: VoiceProviderTaskContext, submission: VoiceProviderSubmission) -> ProviderActionProgress:
        if submission.status == "ambiguous":
            return self._mark_ambiguous(context, None)
        if submission.status == "pending":
            assert submission.handle is not None
            return self._persist_pending(context, submission.handle)
        assert submission.result is not None
        return self._accept_execution_result(context, submission.result)

    def _persist_pending(self, context: VoiceProviderTaskContext, handle: VoiceProviderHandle) -> ProviderActionProgress:
        now = utc_now_iso()
        next_due = self._after(now, self.poll_interval_seconds)
        with self.session_factory() as session:
            repository = VoiceProviderExecutionRepository(session)
            changed = repository.persist_pending_submission(
                task_id=context.segment_task_id,
                execution_attempt_token=context.execution_attempt_token,
                provider_task_id=handle.provider_task_id,
                provider_handle_json={
                    "provider_key": handle.provider_key,
                    "provider_task_id": handle.provider_task_id,
                    "payload": handle.payload,
                },
                now_iso=now,
                next_reconcile_at=next_due,
            )
            if changed:
                VoiceDurableWorkflowDriver(repository).refresh_operation(operation_id=context.operation_id, now_iso=now)
                session.commit()
            else:
                session.rollback()
        return ProviderActionProgress(action="submit", changed=changed, state="waiting_provider" if changed else "stale")

    def _reschedule_waiting(self, context: VoiceProviderTaskContext, *, handle: VoiceProviderHandle) -> ProviderActionProgress:
        return self._persist_pending(context, handle)

    def _accept_execution_result(
        self,
        context: VoiceProviderTaskContext,
        result: VoiceProviderExecutionResult,
    ) -> ProviderActionProgress:
        if (
            result.operation_id != context.operation_id
            or result.segment_task_id != context.segment_task_id
            or result.execution_attempt_token != context.execution_attempt_token
            or result.provider_key != context.provider_key
        ):
            self._cleanup_result(result)
            return ProviderActionProgress(action="provider_result", changed=False, state="stale")
        metadata = dict(result.sanitized_metadata)
        metadata["durable_provider_result"] = {
            "audio_file_path": str(result.audio_file_path),
            "source_mime_type": result.source_mime_type,
            "sample_rate_hz": result.sample_rate_hz,
            "channels": result.channels,
            "sample_width_bytes": result.sample_width_bytes,
            "duration_seconds": result.duration_seconds,
            "provider_word_timings": result.provider_word_timings,
            "provider_character_timings": result.provider_character_timings,
            "execution_attempt_token": result.execution_attempt_token,
            "provider_key": result.provider_key,
        }
        now = utc_now_iso()
        with self.session_factory() as session:
            repository = VoiceProviderExecutionRepository(session)
            changed = repository.persist_completed_provider_result(
                task_id=context.segment_task_id,
                execution_attempt_token=context.execution_attempt_token,
                provider_task_id=result.provider_task_id,
                result_metadata=metadata,
                now_iso=now,
            )
            if changed:
                VoiceDurableWorkflowDriver(repository).refresh_operation(operation_id=context.operation_id, now_iso=now)
                session.commit()
            else:
                session.rollback()
        if not changed:
            self._cleanup_result(result)
        if changed:
            logger.info(
                "Voice provider result accepted operation_id=%s task_id=%s segment_id=%s provider=%s",
                context.operation_id, context.segment_task_id, context.segment_id, context.provider_key,
            )
        return ProviderActionProgress(action="provider_result", changed=changed, state="materializing" if changed else "stale")

    def _record_provider_failure(self, context: VoiceProviderTaskContext, exc: Exception) -> ProviderActionProgress:
        now = utc_now_iso()
        retryable = isinstance(exc, AppError) and bool(exc.retryable)
        error_code = exc.error_code if isinstance(exc, AppError) else "VOICE_PROVIDER_FAILED"
        error_message = exc.message if isinstance(exc, AppError) else str(exc)
        if error_code == "VOICE_OPERATION_CANCELLED":
            retryable = False
        retry_at = self._after(now, self._retry_delay_seconds(context.retry_count + 1)) if retryable else None
        with self.session_factory() as session:
            repository = VoiceProviderExecutionRepository(session)
            state = repository.retry_or_fail_provider_action(
                task_id=context.segment_task_id,
                execution_attempt_token=context.execution_attempt_token,
                error_code=error_code,
                error_message=error_message,
                retryable=retryable,
                now_iso=now,
                next_retry_at=retry_at,
            )
            if state != "stale":
                VoiceDurableWorkflowDriver(repository).refresh_operation(operation_id=context.operation_id, now_iso=now)
                session.commit()
            else:
                session.rollback()
        self._cleanup_workspace(context)
        logger.warning(
            "Voice provider action failed operation_id=%s task_id=%s segment_id=%s provider=%s error_code=%s retryable=%s state=%s retry_count=%s",
            context.operation_id, context.segment_task_id, context.segment_id, context.provider_key,
            error_code, retryable, state, context.retry_count,
        )
        return ProviderActionProgress(action="provider_failure", changed=state != "stale", state=state)

    def _replay_safe(self, context: VoiceProviderTaskContext) -> ProviderActionProgress:
        now = utc_now_iso()
        with self.session_factory() as session:
            repository = VoiceProviderExecutionRepository(session)
            state = repository.retry_or_fail_provider_action(
                task_id=context.segment_task_id,
                execution_attempt_token=context.execution_attempt_token,
                error_code="VOICE_PROVIDER_SUBMISSION_INTERRUPTED",
                error_message="The previous provider action was lost with its owning process and is safe to replay.",
                retryable=True,
                now_iso=now,
                next_retry_at=now,
            )
            if state != "stale":
                VoiceDurableWorkflowDriver(repository).refresh_operation(operation_id=context.operation_id, now_iso=now)
                session.commit()
            else:
                session.rollback()
        self._cleanup_workspace(context)
        return ProviderActionProgress(action="reconcile", changed=state != "stale", state=state)

    def _mark_ambiguous(self, context: VoiceProviderTaskContext, exc: Exception | None) -> ProviderActionProgress:
        now = utc_now_iso()
        with self.session_factory() as session:
            repository = VoiceProviderExecutionRepository(session)
            changed = repository.mark_submit_outcome_unknown(
                task_id=context.segment_task_id,
                expected_submit_token=context.execution_attempt_token,
                now_iso=now,
            )
            if changed:
                if exc is not None:
                    session.execute(
                        __import__("sqlalchemy").text(
                            """
                            UPDATE voice_segment_tasks
                            SET error_message = :message
                            WHERE voice_segment_task_id = :task_id
                            """
                        ),
                        {"task_id": context.segment_task_id, "message": str(exc)},
                    )
                VoiceDurableWorkflowDriver(repository).refresh_operation(operation_id=context.operation_id, now_iso=now)
                session.commit()
            else:
                session.rollback()
        return ProviderActionProgress(action="reconcile", changed=changed, state="submit_outcome_unknown" if changed else "stale")

    def _context_required(self, task_id: str) -> VoiceProviderTaskContext:
        with self.session_factory() as session:
            context = VoiceProviderExecutionRepository(session).get_provider_context(task_id)
        if context is None:
            raise AppError(
                "VOICE_PROVIDER_EXECUTION_CONTEXT_MISSING",
                "Voice provider execution context is unavailable or no longer current.",
                409,
                retryable=True,
                details={"voice_segment_task_id": task_id},
            )
        return context

    def _request(self, context: VoiceProviderTaskContext) -> VoiceSynthesisRequest:
        frozen_options = context.generation_settings_json.get("provider_options")
        provider_options = dict(frozen_options) if isinstance(frozen_options, dict) else {}
        payloads = context.operation_request_json.get("voice_first_segment_payloads")
        if isinstance(payloads, dict):
            payload = payloads.get(context.segment_id)
            if not isinstance(payload, dict):
                raise AppError(
                    "VOICE_SYNTHESIS_IDENTITY_INVALID",
                    "The frozen Voice-First provider payload is missing for this segment.",
                    409,
                    retryable=False,
                )
            provider_options["_voice_first_segment_payloads"] = {context.segment_task_id: dict(payload)}
            kokoro_pieces = payload.get("kokoro_pieces")
            if isinstance(kokoro_pieces, list):
                provider_options["_kokoro_segment_payloads"] = {
                    context.segment_task_id: {"pieces": kokoro_pieces}
                }
        workspace = (
            self.settings.resolve_configured_path(self.settings.artifact_root)
            / ".voice_attempts"
            / context.operation_id
            / context.segment_task_id
            / context.execution_attempt_token
        )
        return VoiceSynthesisRequest(
            operation_id=context.operation_id,
            segment_task_id=context.segment_task_id,
            execution_attempt_token=context.execution_attempt_token,
            idempotency_key=self._idempotency_key(context),
            exact_text=context.exact_text,
            voice_id=context.voice_id,
            language=context.language,
            speaking_rate=context.speaking_rate,
            requested_sample_rate_hz=context.sample_rate_hz,
            requested_output_format=context.output_format,
            provider_options=provider_options,
            attempt_workspace=workspace,
            pacing=self._pacing_directive(context),
            **self._continuity_context(context),
        )

    @staticmethod
    def _pacing_directive(context: VoiceProviderTaskContext) -> VoiceProviderPacingDirective:
        pacing_plan = context.operation_request_json.get("script_section_pacing")
        if not isinstance(pacing_plan, dict):
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice operation frozen pacing plan is unavailable.", 409, retryable=False)
        sections = pacing_plan.get("sections")
        if not isinstance(sections, list):
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice operation frozen pacing plan is malformed.", 409, retryable=False)
        matches = [section for section in sections if isinstance(section, dict) and section.get("section_id") == context.section_id]
        if len(matches) != 1:
            raise AppError("VOICE_PACING_PLAN_INVALID", "Voice segment pacing target is missing.", 409, retryable=False)
        section = matches[0]
        value = section.get("provider_pacing_value")
        return VoiceProviderPacingDirective(
            control_model=section["provider_pacing_control"],
            value=float(value) if isinstance(value, (int, float)) else None,
            target_narration_wpm=int(section["planned_narration_wpm"]),
            policy_version=str(section["provider_pacing_policy_version"]),
            clamped=bool(section.get("provider_pacing_clamped")),
            value_range=(
                (float(section["provider_pacing_value_range"][0]), float(section["provider_pacing_value_range"][1]))
                if isinstance(section.get("provider_pacing_value_range"), list)
                else None
            ),
        )

    @staticmethod
    def _continuity_context(context: VoiceProviderTaskContext) -> dict[str, str | None]:
        segmentation = context.operation_request_json.get("segmentation")
        if not isinstance(segmentation, dict):
            raise AppError(
                "VOICE_SEGMENTATION_CONTEXT_INVALID",
                "Voice operation frozen segmentation is unavailable.",
                409,
                retryable=False,
            )
        raw_segments = segmentation.get("segments")
        if not isinstance(raw_segments, list) or not raw_segments:
            raise AppError(
                "VOICE_SEGMENTATION_CONTEXT_INVALID",
                "Voice operation frozen segmentation is malformed.",
                409,
                retryable=False,
            )
        segments: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in raw_segments:
            if not isinstance(item, dict):
                raise AppError("VOICE_SEGMENTATION_CONTEXT_INVALID", "Voice frozen segment entry is malformed.", 409, retryable=False)
            segment_id = item.get("segment_id")
            exact_text = item.get("exact_text")
            order = item.get("segment_order")
            if (
                not isinstance(segment_id, str)
                or not segment_id
                or segment_id in seen_ids
                or not isinstance(exact_text, str)
                or not isinstance(order, int)
            ):
                raise AppError("VOICE_SEGMENTATION_CONTEXT_INVALID", "Voice frozen segment identity is malformed.", 409, retryable=False)
            seen_ids.add(segment_id)
            segments.append(item)
        ordered = sorted(segments, key=lambda item: (int(item["segment_order"]), str(item["segment_id"])))
        if [int(item["segment_order"]) for item in ordered] != list(range(1, len(ordered) + 1)):
            raise AppError("VOICE_SEGMENTATION_CONTEXT_INVALID", "Voice frozen segment order is not deterministic.", 409, retryable=False)
        matches = [index for index, item in enumerate(ordered) if item["segment_id"] == context.segment_id]
        if len(matches) != 1:
            raise AppError("VOICE_SEGMENTATION_CONTEXT_INVALID", "Voice task is not represented exactly once in frozen segmentation.", 409, retryable=False)
        index = matches[0]
        current = ordered[index]
        if current.get("exact_text") != context.exact_text or current.get("segment_order") != context.segment_order:
            raise AppError("VOICE_SEGMENTATION_CONTEXT_INVALID", "Voice frozen segment context does not match the current task.", 409, retryable=False)
        previous_text = ordered[index - 1]["exact_text"] if index > 0 else None
        next_text = ordered[index + 1]["exact_text"] if index + 1 < len(ordered) else None
        return {"previous_text": previous_text, "next_text": next_text}

    def _run_provider_action(self, context: VoiceProviderTaskContext, action: Callable[[VoiceExecutionControl], Any]) -> Any:
        now = utc_now_iso()
        with self.session_factory() as session:
            repository = VoiceProviderExecutionRepository(session)
            if not repository.mark_execution_started(
                task_id=context.segment_task_id,
                execution_attempt_token=context.execution_attempt_token,
                now_iso=now,
            ):
                session.rollback()
                raise AppError(
                    "VOICE_OPERATION_CANCELLED",
                    "Voice provider action is no longer current.",
                    409,
                    retryable=False,
                )
            session.commit()
        logger.info(
            "Voice provider execution started operation_id=%s task_id=%s segment_id=%s provider=%s",
            context.operation_id, context.segment_task_id, context.segment_id, context.provider_key,
        )
        self._ensure_provider_runtime_unchanged(context)
        return action(self._control(context))

    def _ensure_provider_runtime_unchanged(self, context: VoiceProviderTaskContext) -> None:
        frozen_identity = context.provider_identity_json.get("execution_identity")
        if not isinstance(frozen_identity, dict) or not frozen_identity:
            raise AppError(
                "VOICE_PROVIDER_RUNTIME_IDENTITY_MISSING",
                "Voice operation is missing its provider runtime identity.",
                409,
                retryable=False,
                details={"provider_key": context.provider_key},
            )
        try:
            current_identity = self.registry.execution_identity(self._profile(context))
        except AppError as exc:
            raise AppError(
                "VOICE_PROVIDER_RUNTIME_UNAVAILABLE",
                "Voice provider runtime identity could not be verified before provider execution.",
                exc.status_code,
                retryable=exc.retryable,
                details={
                    "provider_key": context.provider_key,
                    "cause_error_code": exc.error_code,
                },
            ) from exc
        if current_identity != frozen_identity:
            raise AppError(
                "VOICE_PROVIDER_RUNTIME_CHANGED",
                "The Voice provider runtime changed after this operation was created. Submit a new Voice generation operation.",
                409,
                retryable=False,
                details={"provider_key": context.provider_key},
            )

    @staticmethod
    def _runtime_identity_error(exc: Exception) -> bool:
        return isinstance(exc, AppError) and exc.error_code in {
            "VOICE_PROVIDER_RUNTIME_CHANGED",
            "VOICE_PROVIDER_RUNTIME_IDENTITY_MISSING",
            "VOICE_PROVIDER_RUNTIME_UNAVAILABLE",
        }

    @staticmethod
    def _profile(context: VoiceProviderTaskContext) -> VoiceProviderProfile:
        provider_options = context.generation_settings_json.get("provider_options")
        if not isinstance(provider_options, dict):
            provider_options = {}
        return VoiceProviderProfile(
            profile_id=str(context.provider_identity_json.get("profile_id") or context.generation_settings_json.get("tts_profile_id") or ""),
            provider_key=context.provider_key,
            enabled=True,
            display_name=str(context.provider_identity_json.get("profile_id") or context.provider_key),
            voice_id=context.voice_id,
            language=context.language,
            speaking_rate=context.speaking_rate,
            requested_sample_rate_hz=context.sample_rate_hz,
            requested_output_format=context.output_format,
            provider_options=dict(provider_options),
            maximum_segment_characters=int(context.generation_settings_json.get("maximum_segment_characters") or 0) or None,
        )

    def _control(self, context: VoiceProviderTaskContext) -> DatabaseVoiceExecutionControl:
        frozen_identity = context.provider_identity_json.get("execution_identity")
        return DatabaseVoiceExecutionControl(
            session_factory=self.session_factory,
            segment_task_id=context.segment_task_id,
            execution_attempt_token=context.execution_attempt_token,
            expected_execution_identity=(
                dict(frozen_identity) if isinstance(frozen_identity, dict) else None
            ),
            timeout_seconds=self.settings.tts_provider_timeout_seconds,
            monotonic_clock=self.monotonic_clock,
        )

    @staticmethod
    def _idempotency_key(context: VoiceProviderTaskContext) -> str:
        material = json.dumps(
            {
                "operation_id": context.operation_id,
                "segment_task_id": context.segment_task_id,
                "attempt_no": context.attempt_no,
                "provider_key": context.provider_key,
                "provider_identity": context.provider_identity_json,
                "generation_settings": context.generation_settings_json,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    @staticmethod
    def _pool_key(action: str, context: VoiceProviderTaskContext) -> str:
        return f"{action}:{context.segment_task_id}:{context.execution_attempt_token}"

    @staticmethod
    def _handle_optional(context: VoiceProviderTaskContext) -> VoiceProviderHandle | None:
        payload = context.provider_handle_json
        if payload is None and context.provider_task_id is None:
            return None
        provider_task_id = context.provider_task_id or str((payload or {}).get("provider_task_id") or "")
        if not provider_task_id:
            return None
        return VoiceProviderHandle(
            provider_key=str((payload or {}).get("provider_key") or context.provider_key),
            provider_task_id=provider_task_id,
            payload=dict((payload or {}).get("payload") or {}),
        )

    def _handle_required(self, context: VoiceProviderTaskContext) -> VoiceProviderHandle:
        handle = self._handle_optional(context)
        if handle is None:
            raise AppError(
                "VOICE_PROVIDER_HANDLE_MISSING",
                "Async Voice provider task has no durable provider handle.",
                409,
                retryable=False,
            )
        return handle

    @staticmethod
    def _after(now_iso: str, seconds: int) -> str:
        parsed = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        return (parsed + timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _retry_delay_seconds(attempt_no: int) -> int:
        return (2, 5, 15)[min(max(attempt_no - 1, 0), 2)]

    @staticmethod
    def _cleanup_result(result: VoiceProviderExecutionResult) -> None:
        try:
            result.audio_file_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _cleanup_workspace(self, context: VoiceProviderTaskContext) -> None:
        workspace = self._request(context).attempt_workspace
        try:
            shutil.rmtree(workspace, ignore_errors=True)
        except OSError:
            pass
