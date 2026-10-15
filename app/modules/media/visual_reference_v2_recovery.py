from __future__ import annotations

from collections.abc import Callable
import hashlib
import shutil
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.time import utc_now_iso
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, MediaTask
from app.db.visual_reference_anchor_state import VisualReferenceAnchorState
from app.db.visual_reference_candidate import VisualReferenceCandidate
from app.db.visual_reference_operation_anchor import VisualReferenceOperationAnchor
from app.db.visual_reference_operation_task import VisualReferenceOperationTask
from app.db.visual_reference_working_operation import VisualReferenceWorkingOperation
from app.repositories.visual_reference_working import ACTIVE_OPERATION_STATUSES


_PROVIDER_CAPACITY_STATUSES = ("submitted", "provider_pending", "provider_running")
_ACTIVE_TASK_STATUSES = {"submitting", "waiting_provider", "downloading", "materializing"}
_ACTIVE_ANCHOR_STATUSES = {"queued", "running"}


def recover_visual_reference_v2_media_ownership(
    session_factory: Callable[[], Session],
    *,
    now_iso: str | None = None,
    settings: Settings | None = None,
) -> int:
    """Release local provider slots whose durable Visual Reference owner is gone.

    Provider cancellation is deliberately local. A remote provider may still
    finish after this recovery pass; candidate materialization remains fenced by
    exact OperationTask -> OperationAnchor -> AnchorState ownership checks.
    """

    now = now_iso or utc_now_iso()
    repaired = recover_visual_reference_v2_media_namespace(
        session_factory,
        settings=settings,
    )
    released = 0
    with session_factory() as session:
        tasks = list(
            session.scalars(
                select(MediaTask).where(
                    MediaTask.status.in_(_PROVIDER_CAPACITY_STATUSES)
                )
            )
        )
        for media_task in tasks:
            metadata = dict(media_task.metadata_json or {})
            if metadata.get("visual_reference_v2") is not True:
                continue
            operation_task_id = metadata.get("visual_reference_operation_task_id")
            task = (
                session.get(VisualReferenceOperationTask, str(operation_task_id))
                if isinstance(operation_task_id, str) and operation_task_id
                else None
            )
            unit = (
                session.get(VisualReferenceOperationAnchor, task.operation_anchor_id)
                if task is not None
                else None
            )
            operation = (
                session.get(VisualReferenceWorkingOperation, unit.operation_id)
                if unit is not None
                else None
            )
            state = (
                session.get(
                    VisualReferenceAnchorState,
                    (unit.project_id, unit.anchor_type, unit.anchor_id),
                )
                if unit is not None
                else None
            )
            owner_active = bool(
                task is not None
                and unit is not None
                and operation is not None
                and state is not None
                and task.media_task_id == media_task.media_task_id
                and task.status in _ACTIVE_TASK_STATUSES
                and unit.status in _ACTIVE_ANCHOR_STATUSES
                and operation.status in ACTIVE_OPERATION_STATUSES
                and operation.cancel_requested_at is None
                and state.active_operation_anchor_id == unit.operation_anchor_id
                and state.current_prompt_revision_id == unit.prompt_revision_id
                and metadata.get("visual_reference_operation_id") == operation.operation_id
                and metadata.get("visual_reference_operation_anchor_id")
                == unit.operation_anchor_id
                and metadata.get("visual_reference_operation_task_id")
                == task.operation_task_id
                and metadata.get("prompt_revision_id") == unit.prompt_revision_id
                and metadata.get("source_definition_hash")
                == unit.source_definition_hash
                and metadata.get("generation_options_hash")
                == unit.generation_options_hash
                and metadata.get("provider_settings_snapshot_hash")
                == operation.provider_settings_snapshot_hash
            )
            if owner_active:
                continue
            media_task.status = "canceled"
            media_task.provider_status = None
            media_task.error_code = "VISUAL_REFERENCE_V2_MEDIA_OWNER_TERMINAL"
            media_task.error = "Visual Reference media task owner is no longer active."
            media_task.error_detail = (
                "Startup recovery released this local Visual Reference V2 provider "
                "capacity slot. Any late remote result remains fenced from materialization."
            )
            media_task.poll_claim_token = None
            media_task.poll_claimed_at = None
            media_task.poll_claim_expires_at = None
            media_task.completed_at = now
            media_task.updated_at = now
            released += 1
        if released:
            session.commit()
        else:
            session.rollback()
    return repaired + released


def recover_visual_reference_v2_media_namespace(
    session_factory: Callable[[], Session],
    *,
    settings: Settings | None = None,
) -> int:
    """Repair only the historical V2 generated-media namespace bug.

    Affected rows are fully identified by durable V2 ownership metadata:
    generated Visual Reference V2 candidates point at completed MediaTasks whose
    explicit storage_namespace is already the authoritative /visual_references/
    root, while the downloaded Artifact was persisted under the older
    /style_bible/ path because MediaTaskService used style_bible_revision_id
    before storage_namespace. Recovery copies intact bytes to the authoritative
    location before changing DB metadata, so a crash can only leave an extra
    recoverable file, never DB metadata pointing at missing media.
    """

    active_settings = settings or get_settings()
    artifact_root = active_settings.resolve_configured_path(active_settings.artifact_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    repaired = 0
    with session_factory() as session:
        rows = list(
            session.execute(
                select(MediaTask, VisualReferenceCandidate, Artifact)
                .join(
                    VisualReferenceCandidate,
                    VisualReferenceCandidate.media_task_id
                    == MediaTask.media_task_id,
                )
                .join(
                    Artifact,
                    Artifact.artifact_id
                    == VisualReferenceCandidate.media_artifact_id,
                )
                .where(
                    MediaTask.purpose == "style_bible_visual_reference",
                    MediaTask.status == "completed",
                    MediaTask.storage_namespace.is_not(None),
                    VisualReferenceCandidate.source_operation_task_id.is_not(None),
                    Artifact.artifact_type == ArtifactType.IMAGE,
                    Artifact.status == ArtifactStatus.READY,
                    Artifact.deleted_at.is_(None),
                )
            )
        )
        for media_task, candidate, artifact in rows:
            if _repair_completed_generated_media(
                session,
                artifact_root=artifact_root,
                media_task=media_task,
                candidate=candidate,
                artifact=artifact,
            ):
                repaired += 1
        if repaired:
            session.commit()
        else:
            session.rollback()
    return repaired


def _repair_completed_generated_media(
    session: Session,
    *,
    artifact_root: Path,
    media_task: MediaTask,
    candidate: VisualReferenceCandidate,
    artifact: Artifact,
) -> bool:
    metadata = dict(media_task.metadata_json or {})
    provenance = dict(candidate.provenance_json or {})
    project_id = str(media_task.project_id or "")
    namespace = str(media_task.storage_namespace or "")
    task_id = str(media_task.media_task_id)
    if (
        metadata.get("visual_reference_v2") is not True
        or provenance.get("source_kind") != "generated"
        or candidate.project_id != project_id
        or candidate.media_task_id != task_id
        or candidate.media_artifact_id != artifact.artifact_id
        or media_task.project_id != candidate.project_id
        or media_task.entity_type != candidate.anchor_type
        or media_task.entity_id != candidate.anchor_id
        or int(media_task.candidate_index) != int(candidate.candidate_index)
        or media_task.style_bible_revision_id is None
        or not namespace.startswith(f"project/{project_id}/visual_references/")
        or not artifact.logical_id.startswith(
            f"project/{project_id}/style_bible/{media_task.style_bible_revision_id}/"
        )
        or f"/{task_id}/" not in artifact.logical_id
        or artifact.sha256 != candidate.media_sha256
        or artifact.mime_type != candidate.mime_type
        or not artifact.sha256
        or not artifact.mime_type
    ):
        return False
    result_items = [item for item in (media_task.result_json or []) if isinstance(item, dict)]
    if len(result_items) != 1 or result_items[0].get("artifact_id") != artifact.artifact_id:
        return False
    operation_task = session.get(VisualReferenceOperationTask, candidate.source_operation_task_id)
    unit = (
        session.get(VisualReferenceOperationAnchor, operation_task.operation_anchor_id)
        if operation_task is not None
        else None
    )
    operation = (
        session.get(VisualReferenceWorkingOperation, unit.operation_id)
        if unit is not None
        else None
    )
    if (
        operation_task is None
        or unit is None
        or operation is None
        or operation_task.media_task_id != task_id
        or unit.project_id != project_id
        or unit.anchor_type != candidate.anchor_type
        or unit.anchor_id != candidate.anchor_id
        or operation.project_id != project_id
        or operation.source_style_bible_artifact_id != media_task.style_bible_revision_id
        or metadata.get("visual_reference_operation_id") != operation.operation_id
        or metadata.get("visual_reference_operation_anchor_id") != unit.operation_anchor_id
        or metadata.get("visual_reference_operation_task_id") != operation_task.operation_task_id
        or metadata.get("prompt_revision_id") != unit.prompt_revision_id
        or metadata.get("source_definition_hash") != unit.source_definition_hash
        or metadata.get("generation_options_hash") != unit.generation_options_hash
        or metadata.get("provider_settings_snapshot_hash")
        != operation.provider_settings_snapshot_hash
        or metadata.get("source_style_bible_artifact_id")
        != operation.source_style_bible_artifact_id
        or provenance.get("operation_id") != operation.operation_id
        or provenance.get("operation_anchor_id") != unit.operation_anchor_id
        or provenance.get("operation_task_id") != operation_task.operation_task_id
        or provenance.get("provider_settings_snapshot_hash")
        != operation.provider_settings_snapshot_hash
        or provenance.get("source_definition_hash") != unit.source_definition_hash
        or provenance.get("source_style_bible_artifact_id")
        != operation.source_style_bible_artifact_id
    ):
        return False
    expected_logical_id = (Path(namespace) / task_id / "result_001").as_posix()
    if artifact.logical_id == expected_logical_id:
        return False
    conflict = session.scalar(
        select(Artifact).where(
            Artifact.logical_id == expected_logical_id,
            Artifact.version_no == artifact.version_no,
            Artifact.artifact_id != artifact.artifact_id,
        )
    )
    if conflict is not None:
        return False
    expected_path = (
        Path(expected_logical_id).parent
        / f"{Path(expected_logical_id).name}_v{int(artifact.version_no):03d}{Path(artifact.path).suffix}"
    )
    _ensure_recovered_file(
        artifact_root=artifact_root,
        old_relative_path=Path(artifact.path),
        new_relative_path=expected_path,
        expected_sha256=str(artifact.sha256),
    )
    updated_results = [dict(result_items[0])]
    updated_results[0]["storage_uri"] = expected_path.as_posix()
    artifact.logical_id = expected_logical_id
    artifact.path = expected_path.as_posix()
    media_task.storage_uri = expected_path.as_posix()
    media_task.result_json = updated_results
    return True


def _ensure_recovered_file(
    *,
    artifact_root: Path,
    old_relative_path: Path,
    new_relative_path: Path,
    expected_sha256: str,
) -> None:
    old_path = (artifact_root / old_relative_path).resolve()
    new_path = (artifact_root / new_relative_path).resolve()
    old_path.relative_to(artifact_root.resolve())
    new_path.relative_to(artifact_root.resolve())
    if new_path.is_file():
        if _sha256_for_path(new_path) != expected_sha256:
            raise RuntimeError("Recovered Visual Reference media target hash mismatch.")
        return
    if not old_path.is_file():
        raise RuntimeError("Visual Reference media recovery source file is missing.")
    if _sha256_for_path(old_path) != expected_sha256:
        raise RuntimeError("Visual Reference media recovery source hash mismatch.")
    new_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = new_path.with_name(f".tmp-{uuid4().hex[:12]}-{new_path.name}")
    shutil.copy2(old_path, temp_path)
    try:
        if _sha256_for_path(temp_path) != expected_sha256:
            raise RuntimeError("Visual Reference media recovery copied hash mismatch.")
        temp_path.replace(new_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def _sha256_for_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
