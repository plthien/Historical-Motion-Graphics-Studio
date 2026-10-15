from __future__ import annotations

import hashlib
from typing import Any, Callable

from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import AppError
from app.core.time import utc_now_iso
from app.db.enums import ArtifactType, JobStatus
from app.modules.media.durable_workflow.contracts import ReconcileLease
from app.modules.v4.durable_voice_service import DurableVoiceService
from app.modules.v4.script_word_budget import SCRIPT_SECTION_SEPARATOR
from app.modules.v4.voice_audio import (
    CANONICAL_CHANNELS,
    CANONICAL_CODEC,
    CANONICAL_SAMPLE_RATE,
    CANONICAL_SAMPLE_WIDTH,
    assemble_canonical_wav,
)
from app.modules.v4.voice_durable_workflow import VoiceDurableWorkflowErrors
from app.modules.v4.voice_pacing import analyze_section_pacing, apply_section_pacing
from app.modules.v4.voice_pacing_manual_acceptance import (
    VOICE_PACING_MANUAL_ACCEPTANCES_RESULT_KEY,
    apply_manual_pacing_acceptances,
)
from app.modules.v4.voice_pacing_calibration_settings import (
    KOKORO_PROVIDER_KEY,
    VoicePacingCalibrationSettingsService,
)
from app.modules.v4.voice_service import canonical_hash
from app.repositories import SettingsRepository
from app.repositories.voice_durable_working import VoiceDurableWorkingRepository
from app.services.artifacts import StagedArtifact, cleanup_staged_artifact_safely, rollback_safely


class VoiceDurableFinalizer:
    """Finalize Task-2 Voice work at immutable final-section audio.

    Provider execution and raw-segment materialization are complete before this
    class runs. Task 2 deliberately stops after provider-neutral section pacing
    and immutable 48 kHz mono PCM S16LE section WAV publication. It does not
    create word timing, approve Voice, move Project Voice pointers, or invalidate
    Scene/downstream state. Those are Task-3+ responsibilities.
    """

    def __init__(self, *, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def finalize_operation(
        self,
        operation_id: str,
        *,
        reconcile_lease: ReconcileLease | None = None,
        renew_reconcile_lease: Callable[[], None] | None = None,
    ) -> bool:
        staged: list[StagedArtifact] = []

        def renew() -> None:
            if renew_reconcile_lease is not None:
                renew_reconcile_lease()

        try:
            renew()
            with self.session_factory() as session:
                service = DurableVoiceService(session)
                operation = service.operations.get(operation_id)
                if operation is None:
                    raise AppError("VOICE_OPERATION_NOT_FOUND", "Voice operation was not found.", 404, retryable=False)
                if operation.status == "completed":
                    return False
                if operation.status != "finalizing":
                    raise AppError(
                        "VOICE_OPERATION_NOT_FINALIZABLE",
                        "Voice operation is not in durable finalization state.",
                        409,
                        retryable=True,
                        details={"operation_id": operation_id, "status": operation.status},
                    )
                if (
                    isinstance(operation.result_json, dict)
                    and isinstance(operation.result_json.get("final_sections"), list)
                    and operation.result_json.get("final_sections")
                    and operation.result_json.get("task_3_status") == "alignment"
                ):
                    return False
                job = service.jobs.get(operation.job_id)
                if job is None:
                    raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice operation Job is missing.", 409, retryable=False)
                project = service._project_required(operation.project_id)

                # Task-2 lineage is Script + approved-Voice pointer CAS only.
                # Scene changes are intentionally irrelevant.
                if (
                    project.script_artifact_id != operation.script_artifact_id
                    or project.narration_audio_artifact_id != operation.expected_narration_audio_artifact_id
                    or project.narration_word_timing_map_artifact_id
                    != operation.expected_narration_word_timing_map_artifact_id
                ):
                    self._terminalize_superseded(operation, job)
                    session.commit()
                    return False

                if service._cancellation_requested(job, operation):
                    service._terminalize_cancelled(job, operation)
                    raise AppError("VOICE_OPERATION_CANCELLED", "Voice generation was cancelled.", 409, retryable=False)

                tasks = service.segment_tasks.list_for_operation(operation_id)
                incomplete = [task.voice_segment_task_id for task in tasks if task.status != "completed"]
                if incomplete:
                    raise AppError(
                        "VOICE_SEGMENTS_INCOMPLETE",
                        "Voice finalization requires all scheduled segment tasks to be completed.",
                        409,
                        retryable=True,
                        details={"operation_id": operation_id, "incomplete_task_ids": incomplete[:20]},
                    )

                service._validate_operation_integrity(operation)
                raw_segment_bytes, raw_records = service._ordered_segment_outputs(operation)
                renew()
                provider_identity = operation.provider_identity_json if isinstance(operation.provider_identity_json, dict) else {}
                provider_key = str(provider_identity.get("provider_key") or provider_identity.get("provider") or "")
                execution_identity = provider_identity.get("execution_identity")
                execution_identity_sha256 = canonical_hash(execution_identity) if isinstance(execution_identity, dict) else None
                calibration_settings = VoicePacingCalibrationSettingsService(
                    session,
                    SettingsRepository(session),
                )

                def recommend_pacing(observation):
                    if provider_key == KOKORO_PROVIDER_KEY:
                        if execution_identity_sha256 is None:
                            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Kokoro execution identity is missing.", 409, retryable=False)
                        empirical = calibration_settings.kokoro_empirical_pacing_recommendation(
                            voice_id=operation.voice_id,
                            execution_identity_sha256=execution_identity_sha256,
                            observation=observation,
                        )
                        if empirical is not None:
                            return empirical
                    return service.tts_settings.registry.recommend_pacing(provider_key, observation)

                analysis = analyze_section_pacing(
                    segment_bytes=raw_segment_bytes,
                    segment_records=raw_records,
                    segmentation=operation.request_json["segmentation"],
                    pacing_plan=operation.request_json["script_section_pacing"],
                    source_operation_id=operation.operation_id,
                    source_script_revision_id=str(operation.script_artifact_id),
                    provider_identity_sha256=operation.provider_identity_sha256,
                    generation_settings_snapshot_sha256=operation.generation_settings_snapshot_sha256,
                    recommendation_resolver=recommend_pacing,
                    progress_callback=renew,
                )
                accepted_records = (
                    operation.result_json.get(VOICE_PACING_MANUAL_ACCEPTANCES_RESULT_KEY)
                    if isinstance(operation.result_json, dict)
                    else None
                )
                analysis = apply_manual_pacing_acceptances(analysis, accepted_records)

                def record_kokoro_observations() -> None:
                    if provider_key != KOKORO_PROVIDER_KEY:
                        return
                    calibration_settings.record_kokoro_pacing_observations(
                        voice_id=operation.voice_id,
                        operation_id=operation.operation_id,
                        execution_identity_sha256=str(execution_identity_sha256),
                        analysis_report=analysis.report,
                        commit=False,
                    )

                if analysis.report["status"] == "regeneration_required":
                    # Renew before the first local write. Kokoro history upsert flushes
                    # immediately and acquires SQLite's writer lock. No external lease
                    # heartbeat may run after this point.
                    renew()
                    record_kokoro_observations()
                    self._terminalize_remediation_required(
                        session=session,
                        operation=operation,
                        job=job,
                        report=analysis.report,
                        reconcile_lease=reconcile_lease,
                    )
                    session.commit()
                    return False

                paced = apply_section_pacing(
                    analysis=analysis,
                    segment_records=raw_records,
                    segmentation=operation.request_json["segmentation"],
                    pacing_plan=operation.request_json["script_section_pacing"],
                    progress_callback=renew,
                )
                if service._cancellation_requested(job, operation):
                    service._terminalize_cancelled(job, operation)
                    raise AppError("VOICE_OPERATION_CANCELLED", "Voice generation was cancelled.", 409, retryable=False)

                segmentation = operation.request_json["segmentation"]
                segments = segmentation.get("segments")
                if not isinstance(segments, list) or len(segments) != len(paced.segment_bytes):
                    raise AppError(
                        "VOICE_OPERATION_INTEGRITY_INVALID",
                        "Final Voice section coverage does not match deterministic segmentation.",
                        409,
                        retryable=False,
                    )

                section_order: list[str] = []
                section_segment_bytes: dict[str, list[bytes]] = {}
                section_transcript_parts: dict[str, list[str]] = {}
                section_segment_ids: dict[str, list[str]] = {}
                for segment, data in zip(segments, paced.segment_bytes, strict=True):
                    if not isinstance(segment, dict):
                        raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice segment is malformed.", 409, retryable=False)
                    section_id = str(segment["section_id"])
                    if section_id not in section_segment_bytes:
                        section_order.append(section_id)
                        section_segment_bytes[section_id] = []
                        section_transcript_parts[section_id] = []
                        section_segment_ids[section_id] = []
                    section_segment_bytes[section_id].append(data)
                    section_transcript_parts[section_id].append(str(segment["exact_text"]))
                    section_segment_ids[section_id].append(str(segment["segment_id"]))

                # Keep CPU/audio assembly in the read-only phase so lease heartbeats
                # can still commit on their separate SQLite connection.
                prepared_sections: list[dict[str, Any]] = []
                for index, section_id in enumerate(section_order, 1):
                    section_bytes, info = assemble_canonical_wav(section_segment_bytes[section_id])
                    transcript = "".join(section_transcript_parts[section_id])
                    if transcript.endswith(SCRIPT_SECTION_SEPARATOR):
                        transcript = transcript[: -len(SCRIPT_SECTION_SEPARATOR)]
                    transcript_sha = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
                    identity_material = {
                        "schema_version": 1,
                        "architecture_version": operation.architecture_version,
                        "operation_id": operation.operation_id,
                        "section_id": section_id,
                        "section_order": index,
                        "segment_ids": section_segment_ids[section_id],
                        "spoken_transcript_sha256": transcript_sha,
                        "pacing_identity_sha256": paced.pacing_summary["pacing_identity_sha256"],
                        "audio_sha256": info.sha256,
                        "sample_rate_hz": info.sample_rate_hz,
                        "sample_count": info.sample_count,
                        "channels": CANONICAL_CHANNELS,
                        "sample_width_bytes": CANONICAL_SAMPLE_WIDTH,
                    }
                    prepared_sections.append({
                        "section_id": section_id,
                        "section_bytes": section_bytes,
                        "info": info,
                        "transcript": transcript,
                        "identity_material": identity_material,
                        "final_identity": canonical_hash(identity_material),
                    })
                    renew()

                if not prepared_sections:
                    raise AppError("VOICE_FINAL_SECTIONS_EMPTY", "Voice finalization produced no section audio.", 409, retryable=False)
                if service._cancellation_requested(job, operation):
                    service._terminalize_cancelled(job, operation)
                    raise AppError("VOICE_OPERATION_CANCELLED", "Voice generation was cancelled.", 409, retryable=False)

                # Final cross-connection heartbeat. Everything below may flush writes
                # into this session, so renew() must never run again in this turn.
                renew()
                record_kokoro_observations()

                final_sections: list[dict[str, Any]] = []
                for prepared in prepared_sections:
                    section_id = str(prepared["section_id"])
                    section_bytes = prepared["section_bytes"]
                    info = prepared["info"]
                    transcript = str(prepared["transcript"])
                    identity_material = prepared["identity_material"]
                    final_identity = str(prepared["final_identity"])
                    staged_artifact = service.artifacts.stage_artifact_revision(
                        logical_id=(
                            f"project/{operation.project_id}/voice/operations/"
                            f"{operation.operation_id}/final-sections/{section_id}"
                        ),
                        artifact_type=ArtifactType.AUDIO,
                        filename=f"{section_id}.wav",
                        content=section_bytes,
                        source_job_id=operation.job_id,
                        provider_source="voice_task2_final_section",
                        content_hash=info.sha256,
                    )
                    staged_artifact.artifact.duration_sec = info.duration_seconds
                    staged.append(staged_artifact)
                    final_sections.append({
                        **identity_material,
                        "final_section_identity_sha256": final_identity,
                        "section_audio_artifact_id": staged_artifact.artifact_id,
                        "spoken_transcript": transcript,
                        "duration_seconds": info.duration_seconds,
                        "codec": CANONICAL_CODEC,
                    })

                completed_at = utc_now_iso()
                self._fence_reconcile_write(
                    session=session,
                    operation_id=operation.operation_id,
                    reconcile_lease=reconcile_lease,
                    now_iso=completed_at,
                )
                operation.status = "finalizing"
                operation.result_json = {
                    **operation.result_json,
                    "final_sections": final_sections,
                    "pacing_summary": paced.pacing_summary,
                    "task_3_status": "alignment",
                    "alignment_status": "queued",
                    "alignment_submit_outcome_unknown_section_ids": [],
                }
                operation.error_code = None
                operation.error_message = None
                operation.error_details_json = None
                operation.completed_at = None
                operation.next_reconcile_at = completed_at
                operation.reconcile_owner = None
                operation.reconcile_token = None
                operation.reconcile_lease_expires_at = None
                operation.updated_at = completed_at
                job.status = JobStatus.RUNNING
                job.retryable = False
                job.progress = 80
                job.error_code = None
                job.error_message = None
                job.error_details_json = None
                job.completed_at = None
                job.finished_at = None
                job.updated_at = completed_at
                job.heartbeat_at = completed_at
                session.commit()
                staged.clear()
                return True
        except Exception:
            # Files created by this attempt are not shared and must not survive a
            # failed transaction/finalization turn.
            for item in reversed(staged):
                cleanup_staged_artifact_safely(item, logger=__import__("logging").getLogger(__name__))
            raise

    @staticmethod
    def _fence_reconcile_write(
        *,
        session: Session,
        operation_id: str,
        reconcile_lease: ReconcileLease | None,
        now_iso: str,
    ) -> None:
        if reconcile_lease is None:
            return
        if not VoiceDurableWorkingRepository(session).fence_reconcile_write(
            operation_id=operation_id,
            token=reconcile_lease.token,
            now_iso=now_iso,
        ):
            raise VoiceDurableWorkflowErrors.lease_lost(operation_id)

    @classmethod
    def _terminalize_remediation_required(
        cls,
        *,
        session: Session,
        operation,
        job,
        report: dict[str, Any],
        reconcile_lease: ReconcileLease | None,
    ) -> None:
        now = utc_now_iso()
        cls._fence_reconcile_write(
            session=session,
            operation_id=operation.operation_id,
            reconcile_lease=reconcile_lease,
            now_iso=now,
        )
        operation.status = "failed"
        operation.error_code = "VOICE_PACING_REGENERATION_REQUIRED"
        operation.error_message = "One or more Voice sections require explicit provider resynthesis before final pacing can be applied."
        operation.error_details_json = report
        operation.result_json = {**operation.result_json, "pacing_remediation_report": report, "task_3_status": "blocked"}
        operation.completed_at = now
        operation.next_reconcile_at = None
        operation.reconcile_owner = None
        operation.reconcile_token = None
        operation.reconcile_lease_expires_at = None
        operation.updated_at = now
        job.status = JobStatus.FAILED
        job.retryable = False
        job.progress = 90
        job.error_code = operation.error_code
        job.error_message = operation.error_message
        job.error_details_json = report
        job.completed_at = now
        job.finished_at = now
        job.updated_at = now
        job.heartbeat_at = now

    @staticmethod
    def _terminalize_superseded(operation, job) -> None:
        now = utc_now_iso()
        operation.status = "superseded"
        operation.error_code = "VOICE_UPSTREAM_STALE"
        operation.error_message = "Voice operation Script or approved Voice pointer lineage is stale."
        operation.completed_at = now
        operation.updated_at = now
        operation.next_reconcile_at = None
        operation.reconcile_owner = None
        operation.reconcile_token = None
        operation.reconcile_lease_expires_at = None
        job.status = JobStatus.FAILED
        job.retryable = False
        job.error_code = operation.error_code
        job.error_message = operation.error_message
        job.completed_at = now
        job.finished_at = now
        job.updated_at = now
