from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.api.schemas import ChannelVoiceProfileModel
from app.api.voice_schemas_v4 import (
    VoiceJobStatusResponse, VoiceJobSubmissionResponse, VoiceOperationIntegrityModel,
    VoiceWorkspaceResponse,
)
from app.core.config import get_settings
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactStatus, ArtifactType, JobStatus
from app.db.models import Artifact, Channel, Job, Project, VoiceGenerationOperation, VoiceSegmentTask
from app.modules.v4.invalidation import ProjectPipelineInvalidationService
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.production_artifact_loader import ProductionArtifactLoaderV4
from app.modules.v4.voice_alignment import ALIGNMENT_VERSION
from app.modules.v4.voice_audio import (
    CANONICAL_CHANNELS, CANONICAL_CODEC, CANONICAL_MIME, CANONICAL_SAMPLE_RATE, CANONICAL_SAMPLE_WIDTH,
    assemble_canonical_wav, validate_canonical_wav_bytes,
)
from app.modules.v4.voice_provider import TTSProvider, TTSRequest
from app.modules.v4.voice_pacing import (
    analyze_section_pacing, apply_section_pacing, apply_section_pacing_recommendation,
    build_voice_pacing_plan, pace_section_outputs, pacing_section_for_segment,
    provider_control_value_for_segment,
    raw_segment_synthesis_identity_sha256, raw_segment_synthesis_material_identity_sha256,
    raw_synthesis_identity_sha256, validate_voice_pacing_plan,
)
from app.modules.v4.voice_provider_contracts import VoiceProviderProfile
from app.modules.v4.voice_segmentation import SEGMENTATION_VERSION, segment_narration
from app.modules.v4.voice_first_policy import ACTIVE_VOICE_PRESETS, VOICE_ARCHITECTURE_VERSION
from app.repositories import (
    ArtifactRepository, JobRepository, ProjectRepository, SettingsRepository,
    VoiceGenerationOperationRepository, VoiceOutputPairCommitGuard, VoiceSegmentTaskRepository,
)
from app.services.artifacts import ArtifactService, StagedArtifact, cleanup_staged_artifact_safely, rollback_safely
from app.services.jobs import JobLifecycleService
from app.services.tts_settings import TTSSettingsService

logger = logging.getLogger(__name__)
JOB_TYPE = "voice_generation_v4"
ACTIVE_STATUSES = {"queued", "running", "waiting_for_provider", "assembling", "finalizing"}
TERMINAL_STATUSES = {"completed", "failed", "superseded", "cancelled"}
SYNTHESIS_SOURCE_KINDS = {"synthesis_required", "regenerated_target"}


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class VoiceService:
    def __init__(self, session: Session, *, provider: TTSProvider | None = None) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.artifact_rows = ArtifactRepository(session)
        self.artifacts = ArtifactService(session, self.artifact_rows)
        self.jobs = JobRepository(session)
        self.operations = VoiceGenerationOperationRepository(session)
        self.segment_tasks = VoiceSegmentTaskRepository(session)
        self.validator = V4ContractValidator()
        self.loader = ProductionArtifactLoaderV4(self.artifacts, self.validator)
        self.tts_settings = TTSSettingsService(session, SettingsRepository(session))
        self.provider = provider

    def _project_required(self, project_id: str) -> Project:
        project = self.projects.get(project_id)
        if project is None:
            raise AppError("PROJECT_NOT_FOUND", f"Project '{project_id}' was not found.", 404, retryable=False)
        return project

    def _load_sources(self, project: Project) -> tuple[dict[str, Any], dict[str, Any]]:
        if not project.script_artifact_id:
            raise AppError("VOICE_SCRIPT_REQUIRED", "Current Script is required.", 409, retryable=False)
        script = self.loader.load_script(project, project.script_artifact_id, require_current=True, require_downstream_ready=True)
        return script, {}

    def _provider_for_profile(self, profile: dict[str, Any]) -> TTSProvider:
        _ = profile
        if self.provider is None:
            raise AppError(
                "VOICE_LEGACY_PROVIDER_NOT_INJECTED",
                "Legacy in-process Voice execution requires an explicitly injected test provider; production execution is owned by the durable media workflow host.",
                409,
                retryable=False,
            )
        return self.provider

    def readiness(self, project_id: str, *, profile_id: str | None = None, voice_id: str | None = None) -> dict[str, Any]:
        _ = (profile_id, voice_id)
        project = self._project_required(project_id)
        issues: list[dict[str, str]] = []
        try:
            self._load_sources(project)
        except AppError as exc:
            issues.append({"error_code": exc.error_code, "message": exc.message})
        selected_voice_id = None
        channel = self.session.get(Channel, project.channel_id)
        profile_payload = channel.voice_profile_json if channel is not None else None
        if not isinstance(profile_payload, dict):
            issues.append({"error_code": "VOICE_CHANNEL_PROFILE_REQUIRED", "message": "Channel Voice Profile is required."})
        else:
            try:
                selected_voice_id = ChannelVoiceProfileModel.model_validate(profile_payload).voice_id
            except Exception:
                issues.append({"error_code": "VOICE_CHANNEL_PROFILE_INVALID", "message": "Channel Voice Profile is invalid."})
        active = self.operations.list_active_for_project(project_id)
        if active:
            issues.append({"error_code": "VOICE_OPERATION_ACTIVE", "message": "A Voice operation is already active."})
        return {
            "status": "ready" if not issues else "not_ready",
            "issues": issues,
            "profile_id": None,
            "provider": "elevenlabs" if selected_voice_id else None,
            "voice_id": selected_voice_id,
            "requires_ffmpeg": False,
        }
    def _operation_integrity_payload(self, operation: VoiceGenerationOperation) -> dict[str, Any]:
        return {
            "schema_version": "4.0", "operation_id": operation.operation_id, "job_id": operation.job_id,
            "project_id": operation.project_id, "operation_type": operation.operation_type, "status": operation.status,
            "script_artifact_id": operation.script_artifact_id,
            "architecture_version": getattr(operation, "architecture_version", VOICE_ARCHITECTURE_VERSION),
            "expected_narration_audio_artifact_id": operation.expected_narration_audio_artifact_id,
            "expected_narration_word_timing_map_artifact_id": operation.expected_narration_word_timing_map_artifact_id,
            "voice_preset": operation.voice_preset,
            "tts_profile_id": operation.tts_profile_id, "voice_id": operation.voice_id,
            "provider_identity_sha256": operation.provider_identity_sha256,
            "generation_settings_snapshot_sha256": operation.generation_settings_snapshot_sha256,
            "pacing_identity_sha256": str(operation.request_json.get("script_section_pacing", {}).get("pacing_identity_sha256", "")),
            "segmentation_version": operation.segmentation_version,
            "segmentation_input_sha256": operation.segmentation_input_sha256,
            "segment_count": operation.segment_count,
        }

    @staticmethod
    def _snapshot_entries(operation: VoiceGenerationOperation, *, result: bool) -> list[dict[str, Any]]:
        payload = operation.result_json if result else operation.request_json
        entries = payload.get("complete_segments") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            raise AppError(
                "VOICE_OPERATION_INTEGRITY_INVALID",
                "Voice complete-segment snapshot is missing.",
                409,
                retryable=False,
            )
        return entries

    @staticmethod
    def _synthesis_context_hashes(
        segmentation: dict[str, Any],
        provider_identity: dict[str, Any],
    ) -> dict[str, str | None]:
        segments = segmentation.get("segments")
        if not isinstance(segments, list):
            return {}
        execution_identity = provider_identity.get("execution_identity") if isinstance(provider_identity, dict) else None
        continuity_mode = execution_identity.get("continuity_mode") if isinstance(execution_identity, dict) else None
        if continuity_mode != "previous_next_text_v1":
            return {str(item.get("segment_id")): None for item in segments if isinstance(item, dict)}
        hashes: dict[str, str | None] = {}
        for index, item in enumerate(segments):
            if not isinstance(item, dict):
                continue
            previous_item = segments[index - 1] if index > 0 else None
            next_item = segments[index + 1] if index + 1 < len(segments) else None
            previous_text = previous_item.get("exact_text") if isinstance(previous_item, dict) else None
            next_text = next_item.get("exact_text") if isinstance(next_item, dict) else None
            hashes[str(item.get("segment_id"))] = canonical_hash(
                {
                    "schema_version": 1,
                    "continuity_mode": continuity_mode,
                    "previous_text": previous_text if isinstance(previous_text, str) else None,
                    "next_text": next_text if isinstance(next_text, str) else None,
                }
            )
        return hashes

    @staticmethod
    def _with_synthesis_context_hash(entry: dict[str, Any], context_hash: str | None) -> dict[str, Any]:
        if context_hash is not None:
            entry["synthesis_context_sha256"] = context_hash
        return entry

    @staticmethod
    def _expected_segment_speaking_rate(operation: VoiceGenerationOperation, segment: dict[str, Any]) -> float:
        pacing_plan = operation.request_json.get("script_section_pacing") if isinstance(operation.request_json, dict) else None
        if isinstance(pacing_plan, dict):
            return provider_control_value_for_segment(pacing_plan, segment)
        return float(operation.speaking_rate)

    @staticmethod
    def _expected_raw_segment_synthesis_identity(
        operation: VoiceGenerationOperation,
        segment: dict[str, Any],
        synthesis_context_sha256: str | None,
    ) -> str:
        pacing_plan = operation.request_json.get("script_section_pacing") if isinstance(operation.request_json, dict) else None
        if not isinstance(pacing_plan, dict):
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice pacing plan is missing.", 409, retryable=False)
        return raw_segment_synthesis_identity_sha256(
            provider_identity=operation.provider_identity_json,
            settings_snapshot=operation.generation_settings_snapshot_json,
            pacing_plan=pacing_plan,
            segment=segment,
            synthesis_context_sha256=synthesis_context_sha256,
        )

    @staticmethod
    def _source_segment_identity_matches_current(
        *,
        source_operation: VoiceGenerationOperation,
        source_segment: dict[str, Any],
        source_context_sha256: str | None,
        stored_identity: Any,
        current_expected_identity: str | None,
    ) -> bool:
        if not isinstance(stored_identity, str) or not isinstance(current_expected_identity, str):
            return False
        source_pacing_plan = (
            source_operation.request_json.get("script_section_pacing")
            if isinstance(source_operation.request_json, dict)
            else None
        )
        if not isinstance(source_pacing_plan, dict):
            return False
        source_current_v2 = raw_segment_synthesis_identity_sha256(
            provider_identity=source_operation.provider_identity_json,
            settings_snapshot=source_operation.generation_settings_snapshot_json,
            pacing_plan=source_pacing_plan,
            segment=source_segment,
            synthesis_context_sha256=source_context_sha256,
        )
        return stored_identity == source_current_v2 == current_expected_identity

    def _section_remediation_plan(
        self,
        *,
        project: Project,
        request: dict[str, Any],
        target_section_id: str,
        provider_hash: str,
        segmentation: dict[str, Any],
        tts_profile_id: str,
        voice_id: str,
        language: str,
    ) -> dict[str, Any]:
        _ = (project, request, target_section_id, provider_hash, segmentation, tts_profile_id, voice_id, language)
        raise AppError(
            "VOICE_TASK2_DURABLE_SERVICE_REQUIRED",
            "Voice section regeneration is owned by DurableVoiceService in the Voice-first architecture.",
            409,
            retryable=False,
        )

    def _latest_pacing_remediation_report(self, project: Project) -> dict[str, Any] | None:
        _ = project
        return None
    def _validate_reused_snapshot_entry(
        self,
        operation: VoiceGenerationOperation,
        segment: dict[str, Any],
        entry: dict[str, Any],
    ) -> None:
        artifact_id = entry.get("segment_audio_artifact_id")
        task_id = entry.get("source_voice_segment_task_id")
        if not isinstance(artifact_id, str) or not isinstance(task_id, str):
            raise AppError(
                "VOICE_OPERATION_INTEGRITY_INVALID",
                "Reusable Voice segment identity is incomplete.",
                409,
                retryable=False,
            )
        source_task = self.segment_tasks.get(task_id)
        if (
            source_task is None
            or source_task.project_id != operation.project_id
            or source_task.segment_id != segment["segment_id"]
            or source_task.segment_order != segment["segment_order"]
            or source_task.section_id != segment["section_id"]
            or source_task.source_start != segment["source_start"]
            or source_task.source_end != segment["source_end"]
            or source_task.text_sha256 != segment["text_sha256"]
            or source_task.exact_text != segment["exact_text"]
            or source_task.voice_id != operation.voice_id
            or source_task.language != operation.language
            or source_task.speaking_rate != float(entry.get("provider_pacing_value") if entry.get("provider_pacing_value") is not None else operation.speaking_rate)
            or source_task.status != "completed"
            or source_task.segment_audio_artifact_id != artifact_id
            or entry.get("raw_segment_synthesis_identity_sha256")
            != self._expected_raw_segment_synthesis_identity(
                operation,
                segment,
                entry.get("synthesis_context_sha256") if isinstance(entry.get("synthesis_context_sha256"), str) else None,
            )
        ):
            raise AppError(
                "VOICE_OPERATION_INTEGRITY_INVALID",
                "Reusable Voice segment task lineage is invalid.",
                409,
                retryable=False,
            )
        if not self._completed_task_output_valid(operation, source_task, segment):
            raise AppError(
                "VOICE_OPERATION_INTEGRITY_INVALID",
                "Reusable Voice segment Artifact is invalid.",
                409,
                retryable=False,
            )
        if (
            entry.get("sample_count") != source_task.sample_count
            or entry.get("duration_seconds") != source_task.duration_seconds
            or entry.get("sha256") != source_task.sha256
        ):
            raise AppError(
                "VOICE_OPERATION_INTEGRITY_INVALID",
                "Reusable Voice segment snapshot metadata is invalid.",
                409,
                retryable=False,
            )

    def _validate_operation_integrity(self, operation: VoiceGenerationOperation) -> None:
        payload = self._operation_integrity_payload(operation)
        VoiceOperationIntegrityModel.model_validate(payload)
        self.validator.validate("voice_operation_integrity_v1.schema.json", payload, error_code="VOICE_OPERATION_INTEGRITY_INVALID")
        if canonical_hash(operation.provider_identity_json) != operation.provider_identity_sha256:
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice provider identity hash does not match.", 409, retryable=False)
        if canonical_hash(operation.generation_settings_snapshot_json) != operation.generation_settings_snapshot_sha256:
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice settings snapshot hash does not match.", 409, retryable=False)
        if not isinstance(operation.request_json, dict) or operation.request_json.get("schema_version") != "4.0":
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice operation request schema version is not current.", 409, retryable=False)
        segmentation = operation.request_json.get("segmentation") if isinstance(operation.request_json, dict) else None
        if not isinstance(segmentation, dict) or segmentation.get("segmentation_input_sha256") != operation.segmentation_input_sha256:
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice segmentation identity does not match.", 409, retryable=False)
        pacing_plan = operation.request_json.get("script_section_pacing") if isinstance(operation.request_json, dict) else None
        try:
            pacing_identity = validate_voice_pacing_plan(
                pacing_plan, expected_script_revision_id=operation.script_artifact_id, segmentation=segmentation
            )
        except AppError as exc:
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice frozen pacing plan is invalid.", 409, retryable=False, details={"cause_error_code": exc.error_code}) from exc
        if operation.generation_settings_snapshot_json.get("pacing_identity_sha256") != pacing_identity:
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice pacing identity anchor does not match the frozen plan.", 409, retryable=False)
        raw_synthesis_identity = operation.generation_settings_snapshot_json.get("raw_synthesis_identity_sha256")
        if not isinstance(raw_synthesis_identity, str):
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice raw synthesis identity is missing.", 409, retryable=False)
        raw_settings = {
            key: value
            for key, value in operation.generation_settings_snapshot_json.items()
            if key not in {"raw_synthesis_identity_sha256", "pacing_identity_sha256"}
        }
        expected_raw_synthesis_identity = raw_synthesis_identity_sha256(
            provider_identity=operation.provider_identity_json,
            settings_snapshot=raw_settings,
            pacing_plan=pacing_plan,
        )
        if raw_synthesis_identity != expected_raw_synthesis_identity:
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice raw synthesis identity hash does not match provider pacing inputs.", 409, retryable=False)
        job = self.jobs.get(operation.job_id)
        if job is None or job.job_type != JOB_TYPE or job.target_type != "project" or job.target_id != operation.project_id:
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice operation Job ownership is invalid.", 409, retryable=False)
        if operation.status == "completed" and job.status != JobStatus.COMPLETED:
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Completed Voice operation has inconsistent Job state.", 409, retryable=False)
        if operation.status in {"failed", "superseded"} and job.status not in {JobStatus.FAILED, JobStatus.CANCELLED}:
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Terminal Voice operation has inconsistent Job state.", 409, retryable=False)
        segments = segmentation.get("segments")
        if not isinstance(segments, list) or len(segments) != operation.segment_count:
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice segmentation coverage is incomplete.", 409, retryable=False)
        synthesis_context_hashes = self._synthesis_context_hashes(segmentation, operation.provider_identity_json)
        scheduled_ids = operation.request_json.get("scheduled_segment_ids")
        if (
            not isinstance(scheduled_ids, list)
            or len(scheduled_ids) != len(set(scheduled_ids))
            or any(not isinstance(item, str) for item in scheduled_ids)
        ):
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice scheduled segment identity is invalid.", 409, retryable=False)
        target_section_id = operation.request_json.get("target_section_id")
        all_ids = [str(item.get("segment_id")) for item in segments]
        if any(segment_id not in all_ids for segment_id in scheduled_ids):
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice scheduled segment is not in deterministic segmentation.", 409, retryable=False)
        if operation.operation_type == "generate_all" and scheduled_ids != all_ids:
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Generate-all task coverage is invalid.", 409, retryable=False)
        if operation.operation_type == "regenerate_section" and (
            not isinstance(target_section_id, str)
            or scheduled_ids != [str(item.get("segment_id")) for item in segments if item.get("section_id") == target_section_id]
        ):
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Regenerate-section target ownership is invalid.", 409, retryable=False)

        request_entries = self._snapshot_entries(operation, result=False)
        result_entries = self._snapshot_entries(operation, result=True)
        if len(request_entries) != operation.segment_count or len(result_entries) != operation.segment_count:
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice complete-segment snapshot coverage is invalid.", 409, retryable=False)

        tasks = self.segment_tasks.list_for_operation(operation.operation_id)
        if len(tasks) != len(scheduled_ids):
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice segment task ownership is not target-scoped.", 409, retryable=False)
        task_by_segment: dict[str, VoiceSegmentTask] = {}
        for task in tasks:
            if task.segment_id in task_by_segment or task.segment_id not in scheduled_ids:
                raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice operation contains an extra or duplicate segment task.", 409, retryable=False)
            task_by_segment[task.segment_id] = task

        for index, segment in enumerate(segments):
            expected_order = index + 1
            segment_id = str(segment.get("segment_id"))
            if segment.get("segment_order") != expected_order:
                raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice deterministic segment order is invalid.", 409, retryable=False)
            request_entry = request_entries[index]
            result_entry = result_entries[index]
            expected_context_hash = synthesis_context_hashes.get(segment_id)
            for entry in (request_entry, result_entry):
                if (
                    entry.get("segment_id") != segment_id
                    or entry.get("segment_order") != expected_order
                    or entry.get("section_id") != segment.get("section_id")
                    or entry.get("source_start") != segment.get("source_start")
                    or entry.get("source_end") != segment.get("source_end")
                    or entry.get("exact_text_sha256") != segment.get("text_sha256")
                    or (
                        expected_context_hash is not None
                        and entry.get("synthesis_context_sha256") != expected_context_hash
                    )
                ):
                    raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice complete-segment snapshot identity or ordering is invalid.", 409, retryable=False)
            pacing_section = pacing_section_for_segment(pacing_plan, segment)
            expected_material_identity = raw_segment_synthesis_material_identity_sha256(
                provider_identity=operation.provider_identity_json,
                settings_snapshot=operation.generation_settings_snapshot_json,
                pacing_plan=pacing_plan,
                segment=segment,
                synthesis_context_sha256=expected_context_hash,
            )
            for entry in (request_entry, result_entry):
                if (
                    entry.get("target_narration_wpm") != pacing_section.get("planned_narration_wpm")
                    or entry.get("provider_pacing_control") != pacing_section.get("provider_pacing_control")
                    or entry.get("provider_pacing_value") != pacing_section.get("provider_pacing_value")
                    or entry.get("provider_pacing_policy_version") != pacing_section.get("provider_pacing_policy_version")
                    or entry.get("provider_pacing_clamped") != pacing_section.get("provider_pacing_clamped")
                    or entry.get("raw_segment_synthesis_identity_sha256")
                    != self._expected_raw_segment_synthesis_identity(
                        operation,
                        segment,
                        expected_context_hash,
                    )
                    or entry.get("raw_segment_material_identity_sha256") != expected_material_identity
                ):
                    raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice segment pacing directive does not match the frozen plan.", 409, retryable=False)
            source_kind = request_entry.get("source_kind")
            if result_entry.get("source_kind") != source_kind:
                raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice complete-segment source kind changed after submission.", 409, retryable=False)
            if segment_id in scheduled_ids:
                task = task_by_segment.get(segment_id)
                if task is None:
                    raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Scheduled Voice segment task is missing.", 409, retryable=False)
                expected_kind = "regenerated_target" if operation.operation_type == "regenerate_section" else "synthesis_required"
                if source_kind != expected_kind or request_entry.get("source_voice_segment_task_id") != task.voice_segment_task_id or request_entry.get("segment_audio_artifact_id") is not None:
                    raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Scheduled Voice segment snapshot ownership is invalid.", 409, retryable=False)
                if (
                    task.job_id != operation.job_id
                    or task.project_id != operation.project_id
                    or task.segment_order != expected_order
                    or task.section_id != segment.get("section_id")
                    or task.source_start != segment.get("source_start")
                    or task.source_end != segment.get("source_end")
                    or task.exact_text != segment.get("exact_text")
                    or task.text_sha256 != segment.get("text_sha256")
                    or task.voice_id != operation.voice_id
                    or task.language != operation.language
                    or task.speaking_rate != self._expected_segment_speaking_rate(operation, segment)
                    or task.sample_rate_hz != int(operation.generation_settings_snapshot_json.get("sample_rate_hz", 0))
                    or task.output_format != str(operation.generation_settings_snapshot_json.get("output_format", ""))
                    or task.provider != operation.provider_identity_json.get("provider")
                ):
                    raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice segment task text or settings lineage is invalid.", 409, retryable=False)
                if result_entry.get("source_voice_segment_task_id") != task.voice_segment_task_id:
                    raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice result snapshot task identity is invalid.", 409, retryable=False)
                if task.status == "completed":
                    output_valid = (
                        self._completed_task_output_identity_valid(operation, task, segment)
                        if operation.status == "partial"
                        else self._completed_task_output_valid(operation, task, segment)
                    )
                    if not output_valid:
                        raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Completed Voice segment output is invalid.", 409, retryable=False)
                    if (
                        result_entry.get("segment_audio_artifact_id") != task.segment_audio_artifact_id
                        or result_entry.get("sample_count") != task.sample_count
                        or result_entry.get("duration_seconds") != task.duration_seconds
                        or result_entry.get("sha256") != task.sha256
                    ):
                        raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Completed Voice result snapshot is stale.", 409, retryable=False)
                elif result_entry.get("segment_audio_artifact_id") is not None or task.segment_audio_artifact_id is not None:
                    raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Incomplete Voice task cannot own segment output.", 409, retryable=False)
            else:
                if source_kind != "reused_segment_artifact" or segment_id in task_by_segment:
                    raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Reusable Voice segment unexpectedly owns a new task.", 409, retryable=False)
                if result_entry != request_entry:
                    raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Reusable Voice segment snapshot changed after submission.", 409, retryable=False)
                self._validate_reused_snapshot_entry(operation, segment, request_entry)

        if operation.status == "partial" and job.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Partial Voice operation must remain recoverable on its existing Job.", 409, retryable=False)
        if operation.status == "cancelled" and job.status != JobStatus.CANCELLED:
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Cancelled Voice operation has inconsistent Job state.", 409, retryable=False)

    def _operation_required(self, operation_id: str) -> VoiceGenerationOperation:
        operation = self.operations.get(operation_id)
        if operation is None:
            raise AppError("VOICE_OPERATION_NOT_FOUND", "Voice operation was not found.", 404, retryable=False)
        self._validate_operation_integrity(operation)
        return operation

    def _read_timing_map(self, artifact: Artifact) -> dict[str, Any]:
        _ = artifact
        raise AppError(
            "VOICE_ALIGNMENT_NOT_READY",
            "NarrationWordTimingMap loading is not implemented until Task 3; legacy NarrationTimingMap output is disabled.",
            409,
            retryable=False,
        )

    def _load_current_pair(self, project: Project, *, require_approved: bool = False, validate_segment_outputs: bool = True) -> tuple[Artifact, Artifact, dict[str, Any]]:
        _ = (require_approved, validate_segment_outputs)
        audio_id = project.narration_audio_artifact_id
        timing_id = project.narration_word_timing_map_artifact_id
        if not audio_id or not timing_id:
            raise AppError("VOICE_OUTPUT_NOT_AVAILABLE", "No approved Voice output pair is currently available.", 404, retryable=False)
        raise AppError(
            "VOICE_ALIGNMENT_NOT_READY",
            "Approved Voice output requires Task 3 NarrationWordTimingMap publication; legacy NarrationTimingMap fallback is disabled.",
            409,
            retryable=False,
        )
    def _reusable_segments(
        self,
        project: Project,
        segmentation: dict[str, Any],
        raw_synthesis_identity: str,
        provider_identity_sha256: str,
        synthesis_context_hashes: dict[str, str | None],
        raw_segment_synthesis_hashes: dict[str, str],
    ) -> dict[str, dict[str, Any]]:
        _ = (project, segmentation, raw_synthesis_identity, provider_identity_sha256, synthesis_context_hashes, raw_segment_synthesis_hashes)
        return {}
    def submit_generation(
        self,
        project_id: str,
        request: dict[str, Any],
        *,
        target_section_id: str | None = None,
    ) -> dict[str, Any]:
        _ = (project_id, request, target_section_id)
        raise AppError(
            "VOICE_TASK2_DURABLE_SERVICE_REQUIRED",
            "Voice generation is owned by DurableVoiceService in the Voice-first architecture.",
            409,
            retryable=False,
        )
    def _stage_segment_artifact(self, operation: VoiceGenerationOperation, task: VoiceSegmentTask, data: bytes) -> tuple[StagedArtifact, dict[str, Any]]:
        info = validate_canonical_wav_bytes(data, maximum_bytes=get_settings().tts_provider_max_output_bytes)
        staged = self.artifacts.write_artifact(
            logical_id=f"project/{operation.project_id}/voice/operations/{operation.operation_id}/segments/{task.segment_id}/attempt_{task.attempt_no}",
            artifact_type=ArtifactType.AUDIO, filename="segment.wav", content=data, source_job_id=operation.job_id,
            provider_source=task.provider, content_hash=info.sha256,
        )
        artifact = staged.artifact
        artifact.mime_type = CANONICAL_MIME
        artifact.sha256 = info.sha256
        artifact.size_bytes = info.size_bytes
        artifact.duration_sec = info.duration_seconds
        return staged, {"sample_count": info.sample_count, "duration_seconds": info.duration_seconds, "sha256": info.sha256}

    def _cancellation_requested(self, job: Job, operation: VoiceGenerationOperation) -> bool:
        bind = self.session.get_bind()
        with Session(bind=bind, autoflush=False, expire_on_commit=False) as probe:
            persisted_job = probe.get(Job, job.job_id)
            persisted_operation = probe.get(VoiceGenerationOperation, operation.operation_id)
            return bool(
                persisted_job is not None
                and persisted_operation is not None
                and (
                    persisted_job.status == JobStatus.CANCELLED
                    or persisted_operation.status == "cancelled"
                )
            )

    def _terminalize_cancelled(self, job: Job, operation: VoiceGenerationOperation, task: VoiceSegmentTask | None = None) -> None:
        now = utc_now_iso()
        if task is not None:
            task.status = "cancelled"
            task.error_code = "VOICE_OPERATION_CANCELLED"
            task.error_message = "Voice generation was cancelled."
            task.completed_at = now
            task.updated_at = now
        operation.status = "cancelled"
        operation.error_code = "VOICE_OPERATION_CANCELLED"
        operation.error_message = "Voice generation was cancelled."
        operation.completed_at = now
        operation.next_reconcile_at = None
        operation.reconcile_owner = None
        operation.reconcile_token = None
        operation.reconcile_lease_expires_at = None
        operation.updated_at = now
        if job.status != JobStatus.CANCELLED:
            JobLifecycleService(self.session, self.jobs).mark_cancelled(job, "Voice generation was cancelled.")
        self.session.commit()

    def cancel_job(self, project_id: str, job_id: str) -> dict[str, Any]:
        operation = self.operations.get_by_job_id(job_id)
        job = self.jobs.get(job_id)
        if operation is None or job is None or operation.project_id != project_id or job.target_id != project_id:
            raise AppError("VOICE_JOB_NOT_FOUND", "Voice Job was not found for this project.", 404, retryable=False)
        if operation.status in TERMINAL_STATUSES:
            return self.job_status(project_id, job_id)
        self._terminalize_cancelled(job, operation)
        return self.job_status(project_id, job_id)

    def _update_result_snapshot_from_task(
        self,
        operation: VoiceGenerationOperation,
        task: VoiceSegmentTask,
        *, clear_output: bool = False,
    ) -> None:
        entries = deepcopy(self._snapshot_entries(operation, result=True))
        matches = [entry for entry in entries if entry.get("segment_id") == task.segment_id]
        if len(matches) != 1:
            raise AppError(
                "VOICE_OPERATION_INTEGRITY_INVALID",
                "Voice result snapshot target is missing or duplicated.",
                409,
                retryable=False,
            )
        entry = matches[0]
        entry["source_voice_segment_task_id"] = task.voice_segment_task_id
        if clear_output:
            entry["segment_audio_artifact_id"] = None
            entry["sample_count"] = None
            entry["duration_seconds"] = None
            entry["sha256"] = None
        else:
            entry["segment_audio_artifact_id"] = task.segment_audio_artifact_id
            entry["sample_count"] = task.sample_count
            entry["duration_seconds"] = task.duration_seconds
            entry["sha256"] = task.sha256
        operation.result_json = {**operation.result_json, "complete_segments": entries}

    def _ordered_segment_outputs(
        self,
        operation: VoiceGenerationOperation,
    ) -> tuple[list[bytes], list[dict[str, Any]]]:
        segmentation = operation.request_json["segmentation"]
        entries = self._snapshot_entries(operation, result=True)
        if len(entries) != len(segmentation["segments"]):
            raise AppError("VOICE_SEGMENTS_INCOMPLETE", "Voice complete-segment output coverage is incomplete.", 409, retryable=False)
        segment_bytes: list[bytes] = []
        records: list[dict[str, Any]] = []
        for segment, entry in zip(segmentation["segments"], entries, strict=True):
            task_id = entry.get("source_voice_segment_task_id")
            artifact_id = entry.get("segment_audio_artifact_id")
            task = self.segment_tasks.get(str(task_id)) if isinstance(task_id, str) else None
            if task is None or not isinstance(artifact_id, str) or task.segment_audio_artifact_id != artifact_id:
                raise AppError("VOICE_SEGMENTS_INCOMPLETE", "Voice complete-segment output identity is incomplete.", 409, retryable=False)
            if not self._completed_task_output_valid(operation, task, segment):
                raise AppError("VOICE_SEGMENT_AUDIO_INVALID", "A segment Artifact failed persisted validation.", 409, retryable=False)
            artifact = self.artifacts.get_required(artifact_id)
            data = self.artifacts.resolve_file_path(artifact).read_bytes()
            info = validate_canonical_wav_bytes(data, maximum_bytes=get_settings().tts_provider_max_output_bytes)
            segment_bytes.append(data)
            records.append({
                "segment_task_id": task.voice_segment_task_id,
                "segment_audio_artifact_id": artifact.artifact_id,
                "sample_count": info.sample_count,
                "provider_timing": task.provider_timing_json,
                "synthesis_context_sha256": entry.get("synthesis_context_sha256"),
                "raw_segment_synthesis_identity_sha256": entry.get("raw_segment_synthesis_identity_sha256"),
                "raw_segment_material_identity_sha256": entry.get("raw_segment_material_identity_sha256"),
            })
        return segment_bytes, records

    def run_operation(self, operation_id: str) -> None:
        operation = self._operation_required(operation_id)
        job = self.jobs.get(operation.job_id)
        if job is None:
            raise AppError("VOICE_OPERATION_INTEGRITY_INVALID", "Voice operation Job is missing.", 409, retryable=False)
        if operation.status in TERMINAL_STATUSES:
            return
        now = utc_now_iso()
        operation.status = "failed"
        operation.error_code = "VOICE_TASK2_DURABLE_SERVICE_REQUIRED"
        operation.error_message = "Voice execution is owned by the durable Voice-first workflow."
        operation.error_details_json = {"architecture_version": VOICE_ARCHITECTURE_VERSION}
        operation.completed_at = now
        operation.updated_at = now
        job.status = JobStatus.FAILED
        job.retryable = False
        job.error_code = operation.error_code
        job.error_message = operation.error_message
        job.error_details_json = operation.error_details_json
        job.completed_at = now
        job.finished_at = now
        job.updated_at = now
        self.session.commit()
    def publish_voice_output_pair(
        self,
        *,
        project: Project,
        audio_bytes: bytes | None,
        timing_payload: dict[str, Any],
        expected_audio_artifact_id: str | None,
        expected_timing_artifact_id: str | None,
        source_job_id: str | None,
        audio_artifact_id: str | None = None,
        reuse_audio_artifact: Artifact | None = None,
        before_commit: Callable[[], None] | None = None,
        staged_segment_artifacts: list[StagedArtifact] | None = None,
    ) -> tuple[Artifact, Artifact]:
        _ = (project, audio_bytes, timing_payload, expected_audio_artifact_id, expected_timing_artifact_id, source_job_id, audio_artifact_id, reuse_audio_artifact, before_commit, staged_segment_artifacts)
        raise AppError(
            "VOICE_ALIGNMENT_NOT_READY",
            "Voice output pair publication requires Task 3 Forced Alignment and NarrationWordTimingMap runtime.",
            409,
            retryable=False,
        )

    def approve(self, project_id: str, request: dict[str, Any]) -> dict[str, Any]:
        project = self._project_required(project_id)
        if self.operations.list_active_for_project(project_id):
            raise AppError("VOICE_OPERATION_ACTIVE", "Voice approval is blocked while generation is active.", 409, retryable=False)
        if project.narration_audio_artifact_id != request.get("expected_narration_audio_artifact_id") or project.narration_word_timing_map_artifact_id != request.get("expected_narration_word_timing_map_artifact_id"):
            raise AppError("VOICE_PUBLICATION_CONFLICT", "Voice output pair changed before approval.", 409, retryable=False)
        raise AppError(
            "VOICE_ALIGNMENT_NOT_READY",
            "Voice approval requires a Task 3 draft NarrationWordTimingMap candidate.",
            409,
            retryable=False,
        )
    def job_status(self, project_id: str, job_id: str) -> dict[str, Any]:
        operation = self.operations.get_by_job_id(job_id)
        job = self.jobs.get(job_id)
        if operation is None or job is None or operation.project_id != project_id:
            raise AppError("VOICE_JOB_NOT_FOUND", "Voice generation Job was not found.", 404, retryable=False)
        tasks = self.segment_tasks.list_for_operation(operation.operation_id)
        complete_entries = self._snapshot_entries(operation, result=True)
        return VoiceJobStatusResponse(
            job_id=job.job_id, operation_id=operation.operation_id, operation_type=operation.operation_type,
            status=operation.status, progress=job.progress, segment_count=operation.segment_count,
            completed_segment_count=sum(isinstance(item.get("segment_audio_artifact_id"), str) for item in complete_entries),
            failed_segment_count=sum(item.status == "failed" for item in tasks),
            error_code=operation.error_code, error_message=operation.error_message,
        ).model_dump(mode="json")

    def get_workspace(self, project_id: str) -> dict[str, Any]:
        project = self._project_required(project_id)
        readiness = self.readiness(project_id)
        active_operations = self.operations.list_active_for_project(project_id)
        active = active_operations[0] if active_operations else None
        sections: list[dict[str, Any]] = []
        segments: list[dict[str, Any]] = []
        try:
            script, _ = self._load_sources(project)
            segmentation = segment_narration(script, maximum_characters=10000)
            section_ids: dict[str, list[str]] = {}
            for item in segmentation["segments"]:
                section_ids.setdefault(item["section_id"], []).append(item["segment_id"])
            for index, item in enumerate(script.get("script_sections", []) if isinstance(script.get("script_sections"), list) else [], 1):
                section_id = str(item.get("section_id"))
                sections.append({
                    "section_id": section_id,
                    "section_order": index,
                    "segment_ids": section_ids.get(section_id, []),
                    "target_wpm": item.get("planned_narration_wpm"),
                    "raw_wpm": None,
                    "pacing_status": "pending",
                    "correction_relative": None,
                    "current_provider_pacing_value": None,
                    "recommended_provider_pacing_value": None,
                    "recommendation_policy_version": None,
                    "remediation_identity": None,
                    "remediation_report_sha256": None,
                    "source_remediation_operation_id": None,
                    "can_regenerate": False,
                    "issue": None,
                })
            for item in segmentation["segments"]:
                segments.append({**item, "status": "pending", "segment_task_id": None, "segment_audio_artifact_id": None, "duration_seconds": None, "sha256": None, "audio_url": None, "timing_source": None, "error": None, "can_regenerate": False})
        except AppError:
            pass
        return VoiceWorkspaceResponse(
            project_id=project_id,
            readiness=readiness,
            script_artifact_id=project.script_artifact_id,
            selected_tts_profile_id=None,
            selected_voice_id=readiness.get("voice_id"),
            speaking_rate=None,
            active_operation={"operation_id": active.operation_id, "job_id": active.job_id, "status": active.status, "operation_type": active.operation_type} if active else None,
            current_narration_audio_artifact_id=project.narration_audio_artifact_id,
            audio_duration_seconds=None,
            audio_mime_type=CANONICAL_MIME if project.narration_audio_artifact_id else None,
            audio_sha256=None,
            audio_url=None,
            current_narration_word_timing_map_artifact_id=project.narration_word_timing_map_artifact_id,
            timing_map_status=None,
            timing_source=None,
            current_output_problem=None,
            sections=sections,
            segments=segments,
            approval_eligible=False,
            available_actions=[],
        ).model_dump(mode="json")
