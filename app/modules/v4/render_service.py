from __future__ import annotations

import logging
import os
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.api.render_schemas_v4 import FinalVideoMetadataModel, RenderJobStatusResponse, RenderWorkspaceResponse
from app.core.config import Settings, get_settings
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactRole, ArtifactStatus, ArtifactType, JobStatus
from app.db.models import Artifact, Job, Project, RenderOperation
from app.modules.v4.assembly_service import AssemblyService
from app.modules.v4.render_ffmpeg import (
    WINDOWS_INPUT_ALIAS_DIRECTORY,
    analyze_audio_clipping,
    build_ffmpeg_args,
    run_ffmpeg_process,
)
from app.modules.v4.render_frame_quantization import canonical_json_sha256, quantize_render_frames
from app.modules.v4.render_graph import compile_ffmpeg_render_graph
from app.modules.v4.render_validation import probe_visual_input, sha256_path, validate_final_video
from app.modules.v4.voice_audio import validate_audio_artifact_bytes
from app.repositories import (
    ArtifactRepository,
    FinalVideoCommitGuard,
    JobRepository,
    ProjectRepository,
    RenderOperationRepository,
    SettingsRepository,
)
from app.services.artifacts import ArtifactService, StagedArtifact, cleanup_staged_artifact_safely, rollback_safely
from app.services.jobs import JobLifecycleService
from app.services.render_settings import RenderSettingsService, canonical_hash

logger = logging.getLogger(__name__)
JOB_TYPE = "ffmpeg_render_v4"
ACTIVE_STATUSES = set(RenderOperationRepository.ACTIVE_STATUSES)
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "superseded"}


class RenderService:
    """Render execution, validation, status, and Final Video publication authority.

    Submission and retry scheduling are owned by DurableRenderService. This
    base service intentionally has no queue transport or worker ownership.
    """

    def __init__(self, session: Session, *, runtime_settings: Settings | None = None) -> None:
        self.session = session
        self.runtime = runtime_settings or get_settings()
        self.projects = ProjectRepository(session)
        self.jobs = JobRepository(session)
        self.operations = RenderOperationRepository(session)
        self.artifact_rows = ArtifactRepository(session)
        self.artifacts = ArtifactService(session, self.artifact_rows, settings=self.runtime)
        self.render_settings = RenderSettingsService(session, SettingsRepository(session), runtime_settings=self.runtime)
        self.lifecycle = JobLifecycleService(session, self.jobs)
        self.assembly = AssemblyService(session)

    def _project_required(self, project_id: str) -> Project:
        project = self.projects.get(project_id)
        if project is None:
            raise AppError("PROJECT_NOT_FOUND", f"Project '{project_id}' was not found.", 404, retryable=False)
        return project

    def _load_approved_assembly(self, project: Project) -> tuple[Artifact, Artifact, Artifact, dict[str, Any]]:
        plan, srt, vtt, payload = self.assembly._load_current_plan(project)
        if payload.get("status") != "approved" or not plan.is_current or plan.status != ArtifactStatus.READY or plan.deleted_at is not None:
            raise AppError("RENDER_ASSEMBLY_NOT_APPROVED", "Only the current approved Assembly Plan may render.", 409, retryable=False)
        if payload.get("source_subtitle_srt_artifact_id") != project.subtitle_srt_artifact_id or payload.get("source_subtitle_vtt_artifact_id") != project.subtitle_vtt_artifact_id:
            raise AppError("RENDER_INPUT_LINEAGE_STALE", "Assembly subtitle lineage is stale.", 409, retryable=False)
        return plan, srt, vtt, payload

    def _artifact_project_owned(self, project: Project, artifact: Artifact) -> None:
        if not artifact.logical_id.startswith(f"project/{project.project_id}/"):
            raise AppError("RENDER_INPUT_PROJECT_MISMATCH", "Render input Artifact belongs to another project.", 409, retryable=False, details={"artifact_id": artifact.artifact_id})
        if artifact.status != ArtifactStatus.READY or artifact.deleted_at is not None:
            raise AppError("RENDER_INPUT_INVALID", "Render input Artifact is unavailable.", 409, retryable=False, details={"artifact_id": artifact.artifact_id})

    def _validate_artifact_path(self, project: Project, artifact: Artifact, *, expected_sha256: str | None = None, expected_mime: str | None = None) -> Path:
        self._artifact_project_owned(project, artifact)
        path = self.artifacts.resolve_file_path(artifact)
        if path.is_symlink() or not path.is_file():
            raise AppError("RENDER_INPUT_PATH_INVALID", "Render input must be an ordinary regular file.", 409, retryable=False, details={"artifact_id": artifact.artifact_id})
        actual_size = path.stat().st_size
        if artifact.size_bytes != actual_size:
            raise AppError("RENDER_INPUT_SIZE_MISMATCH", "Render input byte count does not match persisted metadata.", 409, retryable=False, details={"artifact_id": artifact.artifact_id})
        actual_sha = sha256_path(path)
        if not artifact.sha256 or artifact.sha256 != actual_sha or (expected_sha256 is not None and expected_sha256 != actual_sha):
            raise AppError("RENDER_INPUT_HASH_MISMATCH", "Render input SHA-256 does not match immutable authority.", 409, retryable=False, details={"artifact_id": artifact.artifact_id})
        if expected_mime is not None and artifact.mime_type != expected_mime:
            raise AppError("RENDER_INPUT_MIME_MISMATCH", "Render input MIME type does not match Assembly authority.", 409, retryable=False, details={"artifact_id": artifact.artifact_id})
        return path

    def _validate_raw_artifact(self, project: Project, artifact: Artifact, *, expected_sha256: str | None = None, expected_mime: str | None = None) -> tuple[Path, bytes]:
        path = self._validate_artifact_path(project, artifact, expected_sha256=expected_sha256, expected_mime=expected_mime)
        return path, path.read_bytes()

    def _validate_inputs(
        self,
        project: Project,
        assembly: dict[str, Any],
        *,
        operation_deadline_monotonic: float | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
        started_callback_factory: Callable[[str, Path], Callable[[int], None]] | None = None,
        finished_callback: Callable[[], None] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Path], set[str]]:
        descriptors: list[dict[str, Any]] = []
        paths: dict[str, Path] = {}
        decoder_requirements: set[str] = set()
        beats_by_artifact: dict[str, list[dict[str, Any]]] = {}
        for beat in assembly["beats"]:
            artifact_id = beat.get("selected_visual_artifact_id")
            if isinstance(artifact_id, str):
                beats_by_artifact.setdefault(artifact_id, []).append(beat)
        for artifact_id, beats in beats_by_artifact.items():
            artifact = self.artifacts.get_required(artifact_id)
            if artifact.artifact_type != ArtifactType.IMAGE:
                raise AppError("RENDER_VISUAL_INPUT_INVALID", "Assembly visual input is not an image Artifact.", 409, retryable=False)
            authority = beats[0]
            path = self._validate_artifact_path(
                project,
                artifact,
                expected_sha256=authority.get("selected_visual_sha256"),
                expected_mime=authority.get("selected_visual_mime_type"),
            )
            technical = probe_visual_input(
                path,
                declared_mime_type=artifact.mime_type,
                expected_width=artifact.width,
                expected_height=artifact.height,
                settings=self.runtime,
                operation_deadline_monotonic=operation_deadline_monotonic,
                cancellation_requested=cancellation_requested,
                started_callback=started_callback_factory("input_probe", path) if started_callback_factory else None,
                finished_callback=finished_callback,
                cancel_grace_seconds=self.runtime.render_cancel_grace_seconds,
            )
            if artifact.width != authority.get("selected_visual_width") or artifact.height != authority.get("selected_visual_height"):
                raise AppError("RENDER_VISUAL_DIMENSIONS_INVALID", "Visual dimensions differ from Assembly authority.", 409, retryable=False)
            if artifact.mime_type == "image/svg+xml":
                decoder_requirements.add("svg")

            def endpoint_state(beat: dict[str, Any], *, final: bool) -> dict[str, Any]:
                visual = beat["visual_instruction"]
                keyframes = visual.get("motion_keyframes") or []
                if not keyframes:
                    crop = visual.get("end_crop" if final else "start_crop")
                else:
                    selected = keyframes[-1] if final else keyframes[0]
                    crop = selected.get("crop") if isinstance(selected, dict) else None
                return {
                    "fit_mode": str(visual.get("fit_mode", "cover")),
                    "anchor_x": float(visual.get("anchor_x", 0.5)),
                    "anchor_y": float(visual.get("anchor_y", 0.5)),
                    "crop": deepcopy(crop),
                }

            descriptors.append({
                "artifact_id": artifact_id,
                "input_index": len(descriptors),
                "kind": "visual",
                "mime_type": artifact.mime_type,
                "sha256": artifact.sha256,
                "byte_count": artifact.size_bytes,
                "width": technical["width"],
                "height": technical["height"],
                "decoder": technical["codec"],
                "initial_visual_state": endpoint_state(beats[0], final=False),
                "final_visual_state": endpoint_state(beats[-1], final=True),
            })
            paths[artifact_id] = path

        def add_audio(binding: dict[str, Any], kind: str) -> None:
            artifact_id = str(binding["artifact_id"])
            if artifact_id in paths:
                return
            artifact = self.artifacts.get_required(artifact_id)
            if artifact.artifact_type != ArtifactType.AUDIO:
                raise AppError("RENDER_AUDIO_INPUT_INVALID", "Assembly audio input is not an audio Artifact.", 409, retryable=False)
            path, raw = self._validate_raw_artifact(project, artifact, expected_sha256=binding.get("sha256"), expected_mime=binding.get("mime_type"))
            info = validate_audio_artifact_bytes(raw, declared_mime_type=artifact.mime_type, expected_size_bytes=artifact.size_bytes, expected_sha256=artifact.sha256)
            for key in ("sample_rate_hz", "channels", "sample_count"):
                if key in binding and int(binding[key]) != int(getattr(info, key)):
                    raise AppError("RENDER_AUDIO_INPUT_INVALID", "Audio technical metadata differs from Assembly authority.", 409, retryable=False, details={"artifact_id": artifact_id, "field": key})
            if "byte_count" in binding and int(binding["byte_count"]) != info.byte_count:
                raise AppError("RENDER_AUDIO_INPUT_INVALID", "Audio byte count differs from Assembly authority.", 409, retryable=False)
            descriptors.append({
                "artifact_id": artifact_id,
                "input_index": len(descriptors),
                "kind": kind,
                "mime_type": info.mime_type,
                "sha256": info.sha256,
                "byte_count": info.byte_count,
                "sample_rate_hz": info.sample_rate_hz,
                "channels": info.channels,
                "duration_seconds": info.duration_seconds,
                **({"loop_mode": binding.get("loop_mode", "none")} if kind == "background_music" else {}),
            })
            paths[artifact_id] = path

        add_audio(assembly["narration_track"], "narration")
        if assembly.get("background_audio_track"):
            add_audio(assembly["background_audio_track"], "background_music")
        for sfx in assembly.get("sfx_track") or []:
            add_audio(sfx, "sound_effect")

        for field, kind, expected_type in (
            ("source_subtitle_srt_artifact_id", "subtitle_srt", {ArtifactType.SUBTITLE_SRT, ArtifactType.SRT}),
            ("source_subtitle_vtt_artifact_id", "subtitle_vtt", {ArtifactType.SUBTITLE_VTT, ArtifactType.VTT}),
        ):
            artifact_id = str(assembly[field])
            artifact = self.artifacts.get_required(artifact_id)
            if artifact.artifact_type not in expected_type:
                raise AppError("RENDER_SUBTITLE_INPUT_INVALID", "Subtitle Artifact type is invalid.", 409, retryable=False)
            path, _ = self._validate_raw_artifact(project, artifact)
            descriptors.append({
                "artifact_id": artifact_id,
                "input_index": len(descriptors),
                "kind": kind,
                "mime_type": artifact.mime_type,
                "sha256": artifact.sha256,
                "byte_count": artifact.size_bytes,
            })
            paths[artifact_id] = path
        if len(descriptors) > self.runtime.render_max_input_count:
            raise AppError("RENDER_INPUT_COUNT_EXCEEDED", "Render input count exceeds the configured bound.", 413, retryable=False)
        return descriptors, paths, decoder_requirements

    def _validate_request_lineage(self, project: Project, request: dict[str, Any]) -> None:
        expected = (
            request.get("expected_assembly_plan_artifact_id"),
            request.get("expected_subtitle_srt_artifact_id"),
            request.get("expected_subtitle_vtt_artifact_id"),
            request.get("expected_final_video_artifact_id"),
        )
        actual = (
            project.assembly_plan_artifact_id,
            project.subtitle_srt_artifact_id,
            project.subtitle_vtt_artifact_id,
            project.final_video_artifact_id,
        )
        if expected != actual:
            raise AppError("RENDER_PUBLICATION_CONFLICT", "Render source or final-video pointer changed before submission.", 409, retryable=False)

    def _operation_integrity(self, operation: RenderOperation) -> None:
        job = self.jobs.get(operation.job_id)
        if job is None or job.job_type != JOB_TYPE or job.target_type != "project" or job.target_id != operation.project_id:
            raise AppError("RENDER_OPERATION_INTEGRITY_INVALID", "Render operation Job ownership is invalid.", 409, retryable=False)
        if canonical_hash(operation.render_profile_snapshot_json) != operation.render_profile_snapshot_sha256:
            raise AppError("RENDER_OPERATION_INTEGRITY_INVALID", "Render profile snapshot hash is invalid.", 409, retryable=False)
        if operation.command_graph_json is not None and canonical_json_sha256({key: value for key, value in operation.command_graph_json.items() if key != "command_graph_sha256"}) != operation.command_graph_sha256:
            if operation.command_graph_json.get("command_graph_sha256") != operation.command_graph_sha256:
                raise AppError("RENDER_OPERATION_INTEGRITY_INVALID", "Render command graph hash is invalid.", 409, retryable=False)
        if operation.frame_quantization_json is not None:
            quantized_without_hash = {key: value for key, value in operation.frame_quantization_json.items() if key != "frame_quantization_sha256"}
            if operation.frame_quantization_json.get("frame_quantization_sha256") != operation.frame_quantization_sha256 or canonical_json_sha256(quantized_without_hash) != operation.frame_quantization_sha256:
                raise AppError("RENDER_OPERATION_INTEGRITY_INVALID", "Render frame quantization hash is invalid.", 409, retryable=False)
        if operation.attempt_no < 1 or not (0 <= operation.progress_percent <= 100) or operation.progress_frame < 0 or operation.progress_time_units < 0:
            raise AppError("RENDER_OPERATION_INTEGRITY_INVALID", "Render attempt or progress state is invalid.", 409, retryable=False)
        status_map = {
            "queued": {JobStatus.QUEUED},
            "running": {JobStatus.RUNNING},
            "rendering": {JobStatus.RUNNING},
            "probing": {JobStatus.RUNNING},
            "validating": {JobStatus.RUNNING},
            "finalizing": {JobStatus.RUNNING},
            "completed": {JobStatus.COMPLETED},
            "failed": {JobStatus.FAILED, JobStatus.STALE_FAILED},
            "cancelled": {JobStatus.CANCELLED},
            "superseded": {JobStatus.CANCELLED, JobStatus.FAILED},
        }
        if operation.status not in status_map or job.status not in status_map[operation.status]:
            raise AppError("RENDER_OPERATION_INTEGRITY_INVALID", "Render operation and Job statuses are inconsistent.", 409, retryable=False)
        if operation.status == "completed":
            artifact_id = operation.result_json.get("final_video_artifact_id") if isinstance(operation.result_json, dict) else None
            artifact = self.artifact_rows.get(str(artifact_id)) if artifact_id else None
            if artifact is None or artifact.source_job_id != job.job_id or artifact.artifact_role != ArtifactRole.FINAL_VIDEO or artifact.status != ArtifactStatus.READY:
                raise AppError("RENDER_OPERATION_INTEGRITY_INVALID", "Completed Render operation output ownership is invalid.", 409, retryable=False)

    def get_status(self, project_id: str, job_id: str) -> dict[str, Any]:
        operation = self.operations.get_by_job_id(job_id)
        if operation is None or operation.project_id != project_id:
            raise AppError("RENDER_JOB_NOT_FOUND", "Render Job was not found for this project.", 404, retryable=False)
        self._operation_integrity(operation)
        job = self.lifecycle.get_required(job_id)
        return RenderJobStatusResponse(
            project_id=project_id,
            render_operation_id=operation.render_operation_id,
            job_id=job_id,
            operation_type=operation.operation_type,
            status=operation.status,
            progress=operation.progress_percent,
            progress_frame=operation.progress_frame,
            progress_time_units=operation.progress_time_units,
            attempt_no=operation.attempt_no,
            retryable=bool(job.retryable),
            error_code=operation.error_code,
            error_message=operation.error_message,
        ).model_dump(mode="json")

    def cancel(self, project_id: str, job_id: str) -> dict[str, Any]:
        operation = self.operations.get_by_job_id(job_id)
        if operation is None or operation.project_id != project_id:
            raise AppError("RENDER_JOB_NOT_FOUND", "Render Job was not found for this project.", 404, retryable=False)
        job = self.lifecycle.get_required(job_id)
        if operation.status not in ACTIVE_STATUSES:
            raise AppError("RENDER_CANCEL_NOT_ALLOWED", "Only an active Render operation may be cancelled.", 409, retryable=False)
        operation.cancellation_requested = True
        operation.updated_at = utc_now_iso()
        if operation.status == "queued":
            operation.status = "cancelled"
            operation.completed_at = operation.updated_at
            self.lifecycle.mark_cancelled(job, "Render operation cancelled before durable execution.")
        self.session.commit()
        return self.get_status(project_id, job_id)

    def _attempt_dir(self, operation: RenderOperation) -> Path:
        root = self.artifacts._artifact_root()
        attempt = root / "project" / operation.project_id / "render" / "operations" / operation.render_operation_id / f"attempt_{operation.attempt_no}"
        resolved = attempt.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise AppError("RENDER_ATTEMPT_PATH_INVALID", "Render attempt path is outside controlled storage.", 500, retryable=False) from exc
        resolved.mkdir(parents=True, exist_ok=False)
        return resolved

    @staticmethod
    def _cleanup_attempt(attempt_dir: Path | None) -> None:
        if attempt_dir is None or not attempt_dir.exists():
            return
        for name in ("final_video.mp4.part", "filter_complex.txt", "audio_analysis.txt", "stderr_tail.txt"):
            path = attempt_dir / name
            if path.exists() and path.is_file():
                path.unlink()
        alias_directory = attempt_dir / WINDOWS_INPUT_ALIAS_DIRECTORY
        if alias_directory.exists() and alias_directory.is_dir() and not alias_directory.is_symlink():
            for alias in alias_directory.iterdir():
                if alias.is_file() and not alias.is_symlink():
                    alias.unlink()
            try:
                alias_directory.rmdir()
            except OSError:
                pass
        try:
            attempt_dir.rmdir()
        except OSError:
            pass

    def _terminalize_failure(self, operation: RenderOperation, job: Job, exc: Exception, *, attempt_dir: Path | None) -> None:
        self._cleanup_attempt(attempt_dir)
        now = utc_now_iso()
        operation.process_pid = None
        operation.process_started_at = None
        operation.updated_at = now
        operation.completed_at = now
        request_json = deepcopy(operation.request_json) if isinstance(operation.request_json, dict) else {}
        request_json.pop("active_process", None)
        operation.request_json = request_json
        if isinstance(exc, AppError) and exc.error_code == "RENDER_CANCELLED":
            operation.status = "cancelled"
            operation.error_code = None
            operation.error_message = None
            operation.error_details_json = None
            self.lifecycle.mark_cancelled(job, "Render operation cancelled.")
        else:
            app_error = exc if isinstance(exc, AppError) else AppError("RENDER_EXECUTION_FAILED", "Render execution failed.", 500, retryable=True)
            operation.status = "failed"
            operation.error_code = app_error.error_code
            operation.error_message = app_error.message
            operation.error_details_json = app_error.details
            self.lifecycle.mark_failed(job, app_error)
        self.session.commit()

    def publish_final_video(
        self,
        *,
        project: Project,
        operation: RenderOperation,
        job: Job,
        output_path: Path,
        validation: dict[str, Any],
        quantization: dict[str, Any],
        assembly: dict[str, Any],
        process_result: dict[str, Any],
        audio_analysis: dict[str, Any],
    ) -> str:
        staged: StagedArtifact | None = None
        try:
            current_project = self._project_required(project.project_id)
            current_plan, _, _, current_assembly = self._load_approved_assembly(current_project)
            if (
                current_plan.artifact_id,
                current_project.subtitle_srt_artifact_id,
                current_project.subtitle_vtt_artifact_id,
                current_project.final_video_artifact_id,
            ) != (
                operation.source_assembly_plan_artifact_id,
                operation.source_subtitle_srt_artifact_id,
                operation.source_subtitle_vtt_artifact_id,
                operation.expected_final_video_artifact_id,
            ):
                raise AppError("RENDER_PUBLICATION_CONFLICT", "Render source lineage changed before publication.", 409, retryable=False)
            self._validate_inputs(current_project, current_assembly)
            operation.status = "finalizing"
            operation.updated_at = utc_now_iso()
            self.session.flush()

            final_artifact_id = generate_id("artifact")
            staged = self.artifacts.stage_artifact_file_revision(
                logical_id=f"project/{current_project.project_id}/final_video",
                artifact_type=ArtifactType.VIDEO,
                artifact_role=ArtifactRole.FINAL_VIDEO,
                filename="final_video.mp4",
                source_path=output_path,
                artifact_id=final_artifact_id,
                source_job_id=job.job_id,
                provider_source="ffmpeg_local",
                content_hash=operation.command_graph_sha256,
            )
            if staged.artifact.sha256 != validation["sha256"] or staged.artifact.size_bytes != validation["byte_count"]:
                raise AppError("RENDER_OUTPUT_BYTES_CHANGED", "Validated Render output bytes changed before immutable publication.", 409, retryable=True)
            metadata = validation["stream_metadata"]
            staged.artifact.mime_type = "video/mp4"
            staged.artifact.width = metadata["width"]
            staged.artifact.height = metadata["height"]
            staged.artifact.duration_sec = validation["durations"]["actual_container_seconds"]

            guard = FinalVideoCommitGuard(
                assembly_plan_artifact_id=operation.source_assembly_plan_artifact_id,
                subtitle_srt_artifact_id=operation.source_subtitle_srt_artifact_id,
                subtitle_vtt_artifact_id=operation.source_subtitle_vtt_artifact_id,
                previous_final_video_artifact_id=operation.expected_final_video_artifact_id,
            )
            if not self.projects.compare_and_set_final_video(
                project_id=current_project.project_id,
                new_final_video_artifact_id=final_artifact_id,
                guard=guard,
                updated_at=utc_now_iso(),
            ):
                raise AppError("RENDER_PUBLICATION_CONFLICT", "Another Render publication won the final-video pointer race.", 409, retryable=False)
            previous = self.artifact_rows.get(operation.expected_final_video_artifact_id) if operation.expected_final_video_artifact_id else None
            if previous is not None:
                previous.is_current = False
                previous.superseded_by_artifact_id = final_artifact_id
            if not self.artifact_rows.promote_exact_current_artifact(
                logical_id=staged.artifact.logical_id,
                new_artifact_id=final_artifact_id,
            ):
                raise AppError("RENDER_PUBLICATION_INVARIANT_FAILED", "Final Video revision could not be promoted.", 500, retryable=False)
            operation.result_json = {
                "schema_version": "2.0",
                "final_video_artifact_id": final_artifact_id,
                "source_assembly_plan_artifact_id": operation.source_assembly_plan_artifact_id,
                "source_subtitle_srt_artifact_id": operation.source_subtitle_srt_artifact_id,
                "source_subtitle_vtt_artifact_id": operation.source_subtitle_vtt_artifact_id,
                "render_profile_id": operation.render_profile_id,
                "render_profile_snapshot_sha256": operation.render_profile_snapshot_sha256,
                "ffmpeg_version": operation.ffmpeg_version,
                "ffprobe_version": operation.ffprobe_version,
                "ffmpeg_capabilities_sha256": operation.ffmpeg_capabilities_sha256,
                "command_graph_sha256": operation.command_graph_sha256,
                "frame_quantization_sha256": operation.frame_quantization_sha256,
                "attempt_no": operation.attempt_no,
                "validation": validation,
                "process": {"elapsed_seconds": process_result["elapsed_seconds"]},
                "audio_clipping_analysis": deepcopy(audio_analysis),
                "output_sha256": validation["sha256"],
                "output_byte_count": validation["byte_count"],
            }
            operation.status = "completed"
            operation.progress_percent = 100
            operation.progress_frame = int(quantization["total_frame_count"])
            operation.progress_time_units = int(assembly["total_duration_time_units"])
            operation.completed_at = utc_now_iso()
            operation.updated_at = operation.completed_at
            operation.error_code = None
            operation.error_message = None
            operation.error_details_json = None
            self.lifecycle.mark_completed(job, progress=100)
            self.session.flush()
            self._operation_integrity(operation)
            self.session.commit()
            staged.owns_file = False
            return final_artifact_id
        except Exception:
            rollback_safely(self.session, logger=logger)
            cleanup_staged_artifact_safely(staged, logger=logger)
            raise

    def run_operation(self, render_operation_id: str) -> None:
        operation = self.operations.get(render_operation_id)
        if operation is None:
            raise AppError("RENDER_OPERATION_NOT_FOUND", "Render operation was not found.", 404, retryable=False)
        job = self.lifecycle.get_required(operation.job_id)
        if job.status != JobStatus.RUNNING or operation.status != "queued":
            raise AppError("RENDER_OPERATION_NOT_EXECUTABLE", "Render operation is not in an executable state.", 409, retryable=False)
        attempt_dir: Path | None = None
        staged: StagedArtifact | None = None
        try:
            operation.status = "running"
            operation.started_at = operation.started_at or utc_now_iso()
            operation.updated_at = utc_now_iso()
            operation.recovery_enqueued = False
            self.session.commit()
            operation_deadline_monotonic = time.monotonic() + float(self.runtime.render_timeout_seconds)

            def cancelled() -> bool:
                self.session.expire(operation, ["cancellation_requested"])
                return bool(operation.cancellation_requested)

            def started_for(process_kind: str, marker_path: Path | None):
                relative_marker = marker_path.relative_to(self.artifacts._artifact_root()).as_posix() if marker_path is not None else None

                def started(pid: int) -> None:
                    operation.process_pid = pid
                    operation.process_started_at = utc_now_iso()
                    request_json = deepcopy(operation.request_json) if isinstance(operation.request_json, dict) else {}
                    request_json["active_process"] = {
                        "process_kind": process_kind,
                        "attempt_no": operation.attempt_no,
                        "attempt_token": operation.attempt_token,
                        "relative_marker_path": relative_marker,
                    }
                    operation.request_json = request_json
                    operation.updated_at = operation.process_started_at
                    self.session.commit()

                return started

            def process_finished() -> None:
                operation.process_pid = None
                operation.process_started_at = None
                request_json = deepcopy(operation.request_json) if isinstance(operation.request_json, dict) else {}
                request_json.pop("active_process", None)
                operation.request_json = request_json
                operation.updated_at = utc_now_iso()
                self.session.commit()

            project = self._project_required(operation.project_id)
            plan, _, _, assembly = self._load_approved_assembly(project)
            if (
                plan.artifact_id,
                project.subtitle_srt_artifact_id,
                project.subtitle_vtt_artifact_id,
                project.final_video_artifact_id,
            ) != (
                operation.source_assembly_plan_artifact_id,
                operation.source_subtitle_srt_artifact_id,
                operation.source_subtitle_vtt_artifact_id,
                operation.expected_final_video_artifact_id,
            ):
                operation.status = "superseded"
                operation.completed_at = utc_now_iso()
                operation.updated_at = operation.completed_at
                self.lifecycle.mark_cancelled(job, "Render source lineage changed before execution.")
                self.session.commit()
                return
            descriptors, paths, decoders = self._validate_inputs(
                project,
                assembly,
                operation_deadline_monotonic=operation_deadline_monotonic,
                cancellation_requested=cancelled,
                started_callback_factory=started_for,
                finished_callback=process_finished,
            )
            attempt_dir = self._attempt_dir(operation)
            identity = self.render_settings.identity_snapshot_owned(
                operation.render_profile_id,
                required_input_codecs=decoders,
                operation_deadline_monotonic=operation_deadline_monotonic,
                cancellation_requested=cancelled,
                cancel_grace_seconds=self.runtime.render_cancel_grace_seconds,
                started_callback_factory=lambda process_kind: started_for(process_kind, None),
                finished_callback=process_finished,
            )
            if any(identity[key] != getattr(operation, key) for key in ("ffmpeg_path_identity", "ffmpeg_version", "ffmpeg_capabilities_sha256", "ffprobe_path_identity", "ffprobe_version")):
                raise AppError("RENDER_RUNTIME_IDENTITY_CHANGED", "FFmpeg or FFprobe runtime identity changed after submission.", 409, retryable=True)
            quantization = quantize_render_frames(
                total_duration_time_units=int(assembly["total_duration_time_units"]),
                beats=assembly["beats"],
                frame_rate_numerator=int(operation.render_profile_snapshot_json["frame_rate"]["numerator"]),
                frame_rate_denominator=int(operation.render_profile_snapshot_json["frame_rate"]["denominator"]),
            )
            graph = compile_ffmpeg_render_graph(
                assembly_plan=assembly,
                render_profile=operation.render_profile_snapshot_json,
                frame_quantization=quantization,
                input_descriptors=descriptors,
            )
            if quantization["frame_quantization_sha256"] != operation.frame_quantization_sha256 or graph["command_graph_sha256"] != operation.command_graph_sha256:
                raise AppError("RENDER_FROZEN_COMPILATION_MISMATCH", "Frozen Render compiler output no longer matches current exact inputs.", 409, retryable=False)
            output_path = attempt_dir / "final_video.mp4.part"
            filter_path = attempt_dir / "filter_complex.txt"
            audio_analysis_path = attempt_dir / "audio_analysis.txt"
            operation.attempt_token = generate_id("attempt")
            operation.attempt_relative_output_path = output_path.relative_to(self.artifacts._artifact_root()).as_posix()
            operation.worker_instance_id = f"pid-{os.getpid()}"
            operation.status = "rendering"
            operation.updated_at = utc_now_iso()
            self.session.commit()
            args, _ = build_ffmpeg_args(graph=graph, artifact_paths=paths, filter_script_path=filter_path, output_path=output_path, settings=self.runtime)
            last_commit = 0.0
            last_percent = 0

            if cancelled():
                raise AppError("RENDER_CANCELLED", "Render operation was cancelled.", 409, retryable=False)
            audio_analysis = analyze_audio_clipping(
                graph=graph,
                artifact_paths=paths,
                filter_script_path=audio_analysis_path,
                operation_deadline_monotonic=operation_deadline_monotonic,
                cancel_grace_seconds=self.runtime.render_cancel_grace_seconds,
                cancellation_requested=cancelled,
                started_callback=started_for("clipping_analysis", audio_analysis_path),
                finished_callback=process_finished,
                settings=self.runtime,
            )
            if cancelled():
                raise AppError("RENDER_CANCELLED", "Render operation was cancelled.", 409, retryable=False)
            if time.monotonic() >= operation_deadline_monotonic:
                raise AppError("RENDER_TIMEOUT", "Local Render processing exceeded the configured overall timeout.", 504, retryable=True)

            def progress(update: dict[str, Any]) -> None:
                nonlocal last_commit, last_percent
                percent = max(last_percent, min(98, int(update.get("percent", 0))))
                now_mono = time.monotonic()
                if percent == last_percent and (now_mono - last_commit) * 1000 < self.runtime.render_progress_commit_interval_ms:
                    return
                last_percent = percent
                last_commit = now_mono
                operation.progress_percent = percent
                operation.progress_frame = max(operation.progress_frame, int(update.get("frame", 0)))
                operation.progress_time_units = max(operation.progress_time_units, int(update.get("out_time_us", 0)))
                operation.updated_at = utc_now_iso()
                job.progress = percent
                job.heartbeat_at = operation.updated_at
                job.updated_at = operation.updated_at
                self.session.commit()

            process_result = run_ffmpeg_process(
                args,
                total_duration_time_units=int(assembly["total_duration_time_units"]),
                total_frames=int(quantization["total_frame_count"]),
                operation_deadline_monotonic=operation_deadline_monotonic,
                cancel_grace_seconds=self.runtime.render_cancel_grace_seconds,
                progress_callback=progress,
                cancellation_requested=cancelled,
                started_callback=started_for("final_encode", output_path),
                finished_callback=process_finished,
            )
            operation.status = "probing"
            operation.progress_percent = 99
            job.progress = 99
            operation.process_pid = None
            operation.updated_at = utc_now_iso()
            job.updated_at = operation.updated_at
            job.heartbeat_at = operation.updated_at
            self.session.commit()
            validation = validate_final_video(
                output_path,
                profile=operation.render_profile_snapshot_json,
                expected_total_frames=int(quantization["total_frame_count"]),
                assembly_duration_time_units=int(assembly["total_duration_time_units"]),
                maximum_bytes=self.runtime.render_max_output_bytes,
                settings=self.runtime,
                operation_deadline_monotonic=operation_deadline_monotonic,
                cancellation_requested=cancelled,
                started_callback=started_for("output_validation", output_path),
                finished_callback=process_finished,
                cancel_grace_seconds=self.runtime.render_cancel_grace_seconds,
            )
            operation.status = "validating"
            operation.updated_at = utc_now_iso()
            self.session.commit()
            if cancelled():
                raise AppError("RENDER_CANCELLED", "Render operation was cancelled.", 409, retryable=False)
            if time.monotonic() >= operation_deadline_monotonic:
                raise AppError("RENDER_TIMEOUT", "Local Render processing exceeded the configured overall timeout.", 504, retryable=True)
            self.publish_final_video(
                project=project,
                operation=operation,
                job=job,
                output_path=output_path,
                validation=validation,
                quantization=quantization,
                assembly=assembly,
                process_result=process_result,
                audio_analysis=audio_analysis,
            )
            self._cleanup_attempt(attempt_dir)
        except Exception as exc:
            rollback_safely(self.session, logger=logger)
            cleanup_staged_artifact_safely(staged, logger=logger)
            operation = self.operations.get(render_operation_id)
            if operation is None:
                raise
            job = self.lifecycle.get_required(operation.job_id)
            self._terminalize_failure(operation, job, exc, attempt_dir=attempt_dir)

    def get_workspace(self, project_id: str) -> dict[str, Any]:
        project = self._project_required(project_id)
        settings = self.render_settings.get_settings()
        profile_id = settings["active_profile_id"]
        readiness = self.render_settings.readiness(profile_id)
        active = self.operations.get_active_for_project(project_id)
        errors: list[dict[str, Any]] = []
        final_video: dict[str, Any] | None = None
        validation_summary: dict[str, Any] | None = None
        if project.final_video_artifact_id:
            artifact = self.artifact_rows.get(project.final_video_artifact_id)
            if artifact is None or artifact.artifact_role != ArtifactRole.FINAL_VIDEO or artifact.status != ArtifactStatus.READY or not artifact.is_current:
                errors.append({"error_code": "RENDER_CURRENT_VIDEO_INVALID", "message": "Current final-video pointer is invalid."})
            else:
                operation = next((item for item in self.operations.list_for_project(project_id) if item.result_json.get("final_video_artifact_id") == artifact.artifact_id), None)
                if operation and isinstance(operation.result_json.get("validation"), dict):
                    validation = operation.result_json["validation"]
                    stream = validation["stream_metadata"]
                    final_video = FinalVideoMetadataModel(
                        artifact_id=artifact.artifact_id,
                        sha256=str(artifact.sha256),
                        size_bytes=int(artifact.size_bytes or 0),
                        width=int(artifact.width or 0),
                        height=int(artifact.height or 0),
                        duration_seconds=float(artifact.duration_sec or 0),
                        video_codec="h264",
                        pixel_format="yuv420p",
                        frame_rate=stream["frame_rate"],
                        frame_count=int(stream["frame_count"]),
                        audio_codec="aac",
                        audio_sample_rate_hz=48000,
                        audio_channels=2,
                        subtitle_mode=operation.render_profile_snapshot_json["subtitle_mode"],
                        subtitle_stream_count=int(stream["subtitle_stream_count"]),
                        faststart=bool(validation["faststart"]),
                        decode_valid=bool(validation["decode_valid"]),
                    ).model_dump(mode="json")
                    validation_summary = validation
        actions: list[str] = []
        approved = False
        try:
            _, _, _, assembly = self._load_approved_assembly(project)
            approved = assembly.get("status") == "approved"
        except AppError as exc:
            errors.append({"error_code": exc.error_code, "message": exc.message})
        if active:
            actions.append("cancel")
        elif approved and readiness["status"] == "ready":
            actions.append("rerender" if project.final_video_artifact_id else "render")
        latest_failed = next((item for item in self.operations.list_for_project(project_id) if item.status == "failed"), None)
        if latest_failed:
            job = self.jobs.get(latest_failed.job_id)
            if job and job.retryable:
                actions.append("retry")
        response = {
            "project_id": project_id,
            "readiness": readiness,
            "current_assembly_plan_artifact_id": project.assembly_plan_artifact_id,
            "current_subtitle_srt_artifact_id": project.subtitle_srt_artifact_id,
            "current_subtitle_vtt_artifact_id": project.subtitle_vtt_artifact_id,
            "selected_render_profile_id": profile_id,
            "active_operation": ({
                "render_operation_id": active.render_operation_id,
                "job_id": active.job_id,
                "operation_type": active.operation_type,
                "status": active.status,
                "progress": active.progress_percent,
                "attempt_no": active.attempt_no,
            } if active else None),
            "current_final_video_artifact_id": project.final_video_artifact_id,
            "video_url": f"/api/v4/projects/{project_id}/render/video" if project.final_video_artifact_id else None,
            "vtt_url": f"/api/v4/projects/{project_id}/assembly/subtitles.vtt" if project.subtitle_vtt_artifact_id else None,
            "final_video": final_video,
            "validation_summary": validation_summary,
            "available_actions": actions,
            "errors": errors,
        }
        return RenderWorkspaceResponse.model_validate(response).model_dump(mode="json")

    def current_video(self, project_id: str) -> tuple[Artifact, Path]:
        project = self._project_required(project_id)
        if not project.final_video_artifact_id:
            raise AppError("RENDER_VIDEO_NOT_FOUND", "Current final video does not exist.", 404, retryable=False)
        artifact = self.artifacts.get_required(project.final_video_artifact_id)
        self._artifact_project_owned(project, artifact)
        if artifact.artifact_type != ArtifactType.VIDEO or artifact.artifact_role != ArtifactRole.FINAL_VIDEO or not artifact.is_current or artifact.mime_type != "video/mp4":
            raise AppError("RENDER_VIDEO_NOT_FOUND", "Current final-video pointer is invalid.", 404, retryable=False)
        path = self._validate_artifact_path(project, artifact, expected_mime="video/mp4")
        return artifact, path
