from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.api.errors import AppError
from app.api.render_schemas_v4 import RenderJobSubmissionResponse
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import JobStatus
from app.db.models import Job, RenderOperation
from app.modules.v4.render_frame_quantization import quantize_render_frames
from app.modules.v4.render_graph import compile_ffmpeg_render_graph
from app.modules.v4.render_service import JOB_TYPE, RenderService
from app.repositories.render_durable_working import RenderDurableWorkingRepository
from app.services.render_settings import canonical_hash


LATEST_RETRY_JOB_ID = "latest"


class DurableRenderService(RenderService):
    """Production Render mutations scheduled only through persisted SQLite state.

    The rendering engine, publication contract, status projection, cancellation,
    and artifact validation remain owned by ``RenderService``. This class owns
    only the hard-cutover mutation boundary: submit and retry make the exact
    RenderOperation due in the same SQLite transaction instead of dispatching
    an RQ job after commit.
    """

    def _schedule_operation(self, operation_id: str, *, now_iso: str) -> None:
        # Flush the newly-created/reset ORM state before the fenced raw SQL
        # scheduler update. Both writes remain in the caller's transaction.
        self.session.flush()
        if RenderDurableWorkingRepository(self.session).schedule_operation(
            operation_id=operation_id,
            now_iso=now_iso,
        ):
            return
        self.session.rollback()
        raise AppError(
            "RENDER_DURABLE_SCHEDULE_FAILED",
            "Render operation could not be scheduled for durable execution.",
            500,
            retryable=True,
            details={"render_operation_id": operation_id},
        )

    def submit(self, project_id: str, request: dict[str, Any]) -> dict[str, Any]:
        project = self._project_required(project_id)
        active = self.operations.get_active_for_project(project_id)
        if active is not None:
            raise AppError(
                "RENDER_OPERATION_ACTIVE",
                "A Render operation is already active for this project.",
                409,
                retryable=False,
                details={"render_operation_id": active.render_operation_id},
            )
        self._validate_request_lineage(project, request)
        plan, _, _, assembly = self._load_approved_assembly(project)
        if len(assembly["beats"]) > self.runtime.render_max_beat_count:
            raise AppError(
                "RENDER_BEAT_COUNT_EXCEEDED",
                "Assembly beat count exceeds the configured Render bound.",
                413,
                retryable=False,
            )
        profile = self.render_settings.get_profile(str(request["render_profile_id"]))
        descriptors, _, decoders = self._validate_inputs(project, assembly)
        identity = self.render_settings.identity_snapshot(
            profile["profile_id"],
            required_input_codecs=decoders,
        )
        quantization = quantize_render_frames(
            total_duration_time_units=int(assembly["total_duration_time_units"]),
            beats=assembly["beats"],
            frame_rate_numerator=int(profile["frame_rate"]["numerator"]),
            frame_rate_denominator=int(profile["frame_rate"]["denominator"]),
        )
        graph = compile_ffmpeg_render_graph(
            assembly_plan=assembly,
            render_profile=profile,
            frame_quantization=quantization,
            input_descriptors=descriptors,
        )
        now = utc_now_iso()
        job = Job(
            job_id=generate_id("job"),
            job_type=JOB_TYPE,
            status=JobStatus.QUEUED,
            progress=0,
            artifact_type="final_video",
            target_type="project",
            target_id=project_id,
            attempt=1,
            retryable=False,
            error_code=None,
            error_message=None,
            error_details_json=None,
            created_at=now,
            updated_at=now,
            started_at=None,
            finished_at=None,
            heartbeat_at=None,
            completed_at=None,
        )
        self.jobs.create(job)
        operation_id = generate_id("render_operation")
        operation = RenderOperation(
            render_operation_id=operation_id,
            job_id=job.job_id,
            project_id=project_id,
            operation_type=str(request.get("operation_type", "render")),
            status="queued",
            source_assembly_plan_artifact_id=plan.artifact_id,
            source_subtitle_srt_artifact_id=str(project.subtitle_srt_artifact_id),
            source_subtitle_vtt_artifact_id=str(project.subtitle_vtt_artifact_id),
            expected_final_video_artifact_id=project.final_video_artifact_id,
            render_profile_id=profile["profile_id"],
            render_profile_snapshot_json=deepcopy(profile),
            render_profile_snapshot_sha256=canonical_hash(profile),
            ffmpeg_path_identity=identity["ffmpeg_path_identity"],
            ffmpeg_version=identity["ffmpeg_version"],
            ffmpeg_capabilities_sha256=identity["ffmpeg_capabilities_sha256"],
            ffprobe_path_identity=identity["ffprobe_path_identity"],
            ffprobe_version=identity["ffprobe_version"],
            command_graph_json=graph,
            command_graph_sha256=graph["command_graph_sha256"],
            frame_quantization_json=quantization,
            frame_quantization_sha256=quantization["frame_quantization_sha256"],
            attempt_no=1,
            attempt_token=None,
            attempt_relative_output_path=None,
            process_pid=None,
            process_started_at=None,
            worker_instance_id=None,
            progress_time_units=0,
            progress_frame=0,
            progress_percent=0,
            recovery_enqueued=False,
            cancellation_requested=False,
            request_json={
                "schema_version": "2.0",
                **deepcopy(request),
                "validated_input_descriptors": descriptors,
            },
            result_json={},
            error_code=None,
            error_message=None,
            error_details_json=None,
            created_at=now,
            updated_at=now,
            started_at=None,
            completed_at=None,
        )
        self.operations.create(operation)
        self._operation_integrity(operation)
        self._schedule_operation(operation_id, now_iso=now)
        self.session.commit()
        return RenderJobSubmissionResponse(
            project_id=project_id,
            render_operation_id=operation_id,
            job_id=job.job_id,
            status="queued",
            render_profile_id=profile["profile_id"],
        ).model_dump(mode="json")

    def get_workspace(self, project_id: str) -> dict[str, Any]:
        """Project durable failure and final-file visibility into the Render workspace.

        Retry reuses the same Job row, so terminal errors must remain visible even
        after the active operation disappears from the workspace. A current Final
        Video pointer is also not considered browser-playable when its immutable
        file is missing from the configured artifact root.
        """

        response = super().get_workspace(project_id)
        errors = list(response.get("errors") or [])

        final_artifact_id = response.get("current_final_video_artifact_id")
        if isinstance(final_artifact_id, str) and final_artifact_id:
            artifact = self.artifact_rows.get(final_artifact_id)
            if artifact is not None:
                try:
                    self.artifacts.resolve_file_path(artifact)
                except AppError as exc:
                    response["video_url"] = None
                    response["final_video"] = None
                    response["validation_summary"] = None
                    errors.append(
                        {
                            "error_code": exc.error_code,
                            "message": "Current Final Video metadata exists but its immutable file is missing from the configured artifact root.",
                            "artifact_id": final_artifact_id,
                        }
                    )

        latest_failed = next(
            (item for item in self.operations.list_for_project(project_id) if item.status == "failed"),
            None,
        )
        if latest_failed is not None:
            job = self.jobs.get(latest_failed.job_id)
            error_code = latest_failed.error_code or (job.error_code if job is not None else None)
            error_message = latest_failed.error_message or (job.error_message if job is not None else None)
            if error_code or error_message:
                errors.append(
                    {
                        "error_code": error_code or "RENDER_EXECUTION_FAILED",
                        "message": error_message or "Render execution failed.",
                        "attempt_no": latest_failed.attempt_no,
                        "job_id": latest_failed.job_id,
                    }
                )

        response["errors"] = errors
        return response

    def _latest_retryable_failure(self, project_id: str):
        for candidate in self.operations.list_for_project(project_id):
            if candidate.status != "failed":
                continue
            job = self.jobs.get(candidate.job_id)
            if job is not None and job.retryable:
                return candidate, job
        return None

    def retry(self, project_id: str, job_id: str) -> dict[str, Any]:
        if job_id == LATEST_RETRY_JOB_ID:
            resolved = self._latest_retryable_failure(project_id)
            if resolved is None:
                raise AppError(
                    "RENDER_JOB_NOT_FOUND",
                    "Render Job was not found for this project.",
                    404,
                    retryable=False,
                )
            operation, job = resolved
        else:
            operation = self.operations.get_by_job_id(job_id)
            if operation is None or operation.project_id != project_id:
                raise AppError(
                    "RENDER_JOB_NOT_FOUND",
                    "Render Job was not found for this project.",
                    404,
                    retryable=False,
                )
            job = self.lifecycle.get_required(job_id)

        if operation.status != "failed" or not job.retryable:
            raise AppError(
                "RENDER_RETRY_NOT_ALLOWED",
                "Only a retryable failed Render operation may retry.",
                409,
                retryable=False,
            )
        project = self._project_required(project_id)
        current_lineage = (
            project.assembly_plan_artifact_id,
            project.subtitle_srt_artifact_id,
            project.subtitle_vtt_artifact_id,
            project.final_video_artifact_id,
        )
        frozen_lineage = (
            operation.source_assembly_plan_artifact_id,
            operation.source_subtitle_srt_artifact_id,
            operation.source_subtitle_vtt_artifact_id,
            operation.expected_final_video_artifact_id,
        )
        if current_lineage != frozen_lineage:
            raise AppError(
                "RENDER_RETRY_STALE",
                "Render retry inputs no longer match current approved lineage.",
                409,
                retryable=False,
            )

        now = utc_now_iso()
        operation.status = "queued"
        operation.attempt_no += 1
        operation.attempt_token = None
        operation.attempt_relative_output_path = None
        operation.process_pid = None
        operation.process_started_at = None
        operation.cancellation_requested = False
        operation.recovery_enqueued = False
        operation.progress_time_units = 0
        operation.progress_frame = 0
        operation.progress_percent = 0
        operation.error_code = None
        operation.error_message = None
        operation.error_details_json = None
        operation.completed_at = None
        operation.updated_at = now

        job.status = JobStatus.QUEUED
        job.progress = 0
        job.attempt += 1
        job.retryable = False
        job.error_code = None
        job.error_message = None
        job.error_details_json = None
        job.started_at = None
        job.finished_at = None
        job.completed_at = None
        job.heartbeat_at = None
        job.updated_at = now

        self._schedule_operation(operation.render_operation_id, now_iso=now)
        self.session.commit()
        return RenderJobSubmissionResponse(
            project_id=project_id,
            render_operation_id=operation.render_operation_id,
            job_id=job.job_id,
            status="queued",
            render_profile_id=operation.render_profile_id,
        ).model_dump(mode="json")
