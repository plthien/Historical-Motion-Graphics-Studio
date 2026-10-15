from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.time import utc_now_iso
from app.db.enums import JobStatus
from app.db.models import Artifact, Job, MediaBatch, MediaTask, StyleBibleVisualOperation
from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.db.scene_asset_operation_beat import SceneAssetOperationBeat
from app.db.scene_asset_operation_task import SceneAssetOperationTask
from app.db.scene_asset_working_operation import SceneAssetWorkingOperation

_PROVIDER_ACTIVE = ("submitted", "provider_pending", "provider_running")
_ACTIVE_LOOKING = ("submitted", "provider_pending", "provider_running", "downloading")
_ACTIVE_VISUAL_REFERENCE_OPERATION_STATUSES = ("queued", "generating")
_ACTIVE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)
_ACTIVE_SCENE_ASSET_V2_OPERATION_STATUSES = ("queued", "running", "finalizing")
_ACTIVE_SCENE_ASSET_V2_BEAT_STATUSES = ("queued", "running")
_ACTIVE_SCENE_ASSET_V2_PROVIDER_TASK_STATUSES = (
    "submitting",
    "waiting_provider",
    "downloading",
    "materializing",
)


def _is_visual_reference_v2_transport(task: MediaTask) -> bool:
    return dict(task.metadata_json or {}).get("visual_reference_v2") is True


def recover_media_ownership(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    """Recover generic MediaTask transport ownership left by a dead process.

    Scene Assets V2 keeps workflow authority in its durable DB graph and is
    checked explicitly below. Visual References V2 has its own durable recovery
    pass which runs before this function; generic recovery must never rewrite a
    V2 Visual Reference transport task after that domain-specific decision.
    Historical RQ-backed Visual Reference rows are still recognized so stale
    legacy transport can be released safely.
    """

    artifact_root = settings.resolve_configured_path(settings.artifact_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    now = utc_now_iso()

    with session_factory() as session:
        try:
            active_with_claim = list(
                session.scalars(
                    select(MediaTask).where(
                        MediaTask.status.in_(_PROVIDER_ACTIVE),
                        or_(
                            MediaTask.poll_claim_token.is_not(None),
                            MediaTask.poll_claimed_at.is_not(None),
                            MediaTask.poll_claim_expires_at.is_not(None),
                        ),
                    )
                )
            )
            interrupted_submits = list(
                session.scalars(
                    select(MediaTask).where(
                        MediaTask.status == "submitted",
                        MediaTask.external_task_id.is_(None),
                    )
                )
            )
            interrupted_downloads = list(
                session.scalars(select(MediaTask).where(MediaTask.status == "downloading"))
            )
            failed_downloads_with_residue = list(
                session.scalars(select(MediaTask).where(MediaTask.status == "download_failed"))
            )

            touched_batches: set[str] = set()
            for task in active_with_claim:
                # Poll claims are process-local transport leases. Clearing a
                # stale claim at startup is safe even for durable V2 owners and
                # lets their coordinator resume the exact MediaTask identity.
                task.poll_claim_token = None
                task.poll_claimed_at = None
                task.poll_claim_expires_at = None
                task.updated_at = now
                touched_batches.add(task.batch_id)

            for task in interrupted_submits:
                if _is_visual_reference_v2_transport(task):
                    # V2 submission ambiguity is owned by the durable workflow
                    # task state machine. Never translate it into a generic
                    # MediaTask failure here.
                    continue
                task.status = "failed"
                task.provider_status = None
                task.provider_submitted_at = None
                task.error_code = "MEDIA_PROVIDER_SUBMISSION_INTERRUPTED"
                task.error = "Media provider submission was interrupted."
                task.error_detail = (
                    "The previous local process ended after acquiring submission ownership "
                    "but before a provider task ID was durably stored. The task was not resubmitted."
                )
                task.provider_error_code = None
                task.poll_claim_token = None
                task.poll_claimed_at = None
                task.poll_claim_expires_at = None
                task.updated_at = now
                touched_batches.add(task.batch_id)

            for task in interrupted_downloads:
                if _is_visual_reference_v2_transport(task):
                    # V2 owns the exact download/materialization continuation.
                    # Rewinding this transport would desynchronize the durable
                    # OperationTask from its MediaTask.
                    continue
                _cleanup_interrupted_download(session, artifact_root, task)
                task.status = "provider_running"
                task.provider_status = "completed"
                task.result_json = []
                task.storage_uri = None
                task.mime_type = None
                task.source_result_url = None
                task.completed_at = None
                task.error_code = None
                task.error = None
                task.error_detail = None
                task.provider_error_code = None
                task.poll_claim_token = None
                task.poll_claimed_at = None
                task.poll_claim_expires_at = None
                task.updated_at = now
                touched_batches.add(task.batch_id)

            for task in failed_downloads_with_residue:
                if _is_visual_reference_v2_transport(task):
                    continue
                _cleanup_interrupted_download(session, artifact_root, task)
                if task.error_detail and "Local cleanup deferred." in task.error_detail:
                    task.error_detail = task.error_detail.replace(" Local cleanup deferred.", "").replace(
                        "Local cleanup deferred.", ""
                    ).strip()
                task.updated_at = now
                touched_batches.add(task.batch_id)

            for task in _orphaned_operation_owned_tasks(session):
                if task.status == "downloading":
                    _cleanup_interrupted_download(session, artifact_root, task)
                task.status = "failed"
                task.provider_status = None
                task.error_code = "MEDIA_TASK_ORPHANED"
                task.error = "Media task owner is no longer active."
                task.error_detail = (
                    "Startup recovery found an active media task whose owning operation "
                    "or job is terminal/missing, so the task was terminalized to release execution capacity."
                )
                task.result_json = []
                task.storage_uri = None
                task.mime_type = None
                task.source_result_url = None
                task.provider_error_code = None
                task.poll_claim_token = None
                task.poll_claimed_at = None
                task.poll_claim_expires_at = None
                task.updated_at = now
                task.completed_at = now
                touched_batches.add(task.batch_id)

            if touched_batches:
                batches = list(session.scalars(select(MediaBatch).where(MediaBatch.batch_id.in_(touched_batches))))
                for batch in batches:
                    batch.updated_at = now
            session.commit()
        except Exception:
            session.rollback()
            raise


def _cleanup_interrupted_download(session: Session, artifact_root: Path, task: MediaTask) -> None:
    temp_root = artifact_root / ".tmp"
    if temp_root.exists():
        for path in temp_root.glob(f"{task.media_task_id}_*.part"):
            path.unlink()

    logical_prefix = _logical_prefix(task)
    task_root = (artifact_root / logical_prefix).resolve()
    resolved_root = artifact_root.resolve()
    task_root.relative_to(resolved_root)
    if task_root.exists():
        if task_root.is_dir():
            shutil.rmtree(task_root)
        else:
            task_root.unlink()

    session.execute(
        delete(Artifact).where(
            Artifact.provider_source == "glabs_webhook",
            Artifact.logical_id.like(f"{logical_prefix.as_posix()}/%"),
        )
    )


def _logical_prefix(task: MediaTask) -> Path:
    if task.storage_namespace:
        return Path(task.storage_namespace) / task.media_task_id
    if task.style_bible_revision_id:
        entity_plural = {
            "character": "characters",
            "location": "locations",
            "prop": "recurring_props",
            "recurring_visual_subject": "recurring_visual_subjects",
        }.get(task.entity_type)
        if entity_plural is None:
            raise RuntimeError("Unsupported media entity type during startup recovery.")
        return (
            Path("project")
            / task.project_id
            / "style_bible"
            / task.style_bible_revision_id
            / entity_plural
            / task.entity_id
            / "candidates"
            / task.media_task_id
        )
    return Path("media") / task.batch_id / task.media_task_id


def _scene_asset_v2_owner_active(session: Session, media_task: MediaTask) -> bool:
    metadata = dict(media_task.metadata_json or {})
    operation_task_id = metadata.get("scene_asset_operation_task_id")
    if metadata.get("scene_asset_v2") is not True or not isinstance(operation_task_id, str) or not operation_task_id:
        return False
    task = session.get(SceneAssetOperationTask, operation_task_id)
    beat = session.get(SceneAssetOperationBeat, task.operation_beat_id) if task is not None else None
    operation = session.get(SceneAssetWorkingOperation, beat.operation_id) if beat is not None else None
    state = (
        session.get(SceneAssetBeatState, (operation.project_id, beat.beat_id))
        if operation is not None and beat is not None
        else None
    )
    return bool(
        task is not None
        and beat is not None
        and operation is not None
        and state is not None
        and task.media_task_id == media_task.media_task_id
        and task.status in _ACTIVE_SCENE_ASSET_V2_PROVIDER_TASK_STATUSES
        and operation.status in _ACTIVE_SCENE_ASSET_V2_OPERATION_STATUSES
        and operation.cancel_requested_at is None
        and beat.status in _ACTIVE_SCENE_ASSET_V2_BEAT_STATUSES
        and state.active_operation_beat_id == beat.operation_beat_id
    )


def _orphaned_operation_owned_tasks(session: Session) -> list[MediaTask]:
    tasks = list(session.scalars(select(MediaTask).where(MediaTask.status.in_(_ACTIVE_LOOKING))))
    if not tasks:
        return []
    batch_ids = {
        task.batch_id for task in tasks if not _is_visual_reference_v2_transport(task)
    }
    visual_operations = {
        operation.media_batch_id: operation
        for operation in session.scalars(
            select(StyleBibleVisualOperation).where(StyleBibleVisualOperation.media_batch_id.in_(batch_ids))
        )
    } if batch_ids else {}
    job_ids = {operation.job_id for operation in visual_operations.values()}
    jobs = {
        job.job_id: job
        for job in session.scalars(select(Job).where(Job.job_id.in_(job_ids)))
    } if job_ids else {}

    orphaned: list[MediaTask] = []
    for task in tasks:
        metadata = dict(task.metadata_json or {})
        if metadata.get("visual_reference_v2") is True:
            # Domain-specific recovery ran before this generic pass and owns the
            # decision to preserve or release this transport task.
            continue
        if metadata.get("scene_asset_v2") is True:
            if not _scene_asset_v2_owner_active(session, task):
                orphaned.append(task)
            continue
        visual_operation = visual_operations.get(task.batch_id)
        if visual_operation is None:
            continue
        job = jobs.get(visual_operation.job_id)
        if (
            visual_operation.status not in _ACTIVE_VISUAL_REFERENCE_OPERATION_STATUSES
            or job is None
            or job.status not in _ACTIVE_JOB_STATUSES
        ):
            orphaned.append(task)
    return orphaned
