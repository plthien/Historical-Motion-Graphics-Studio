from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, MediaTask, Project
from app.modules.v4.scene_asset_manifest import canonical_hash
from app.modules.v4.prompt_composer_loader import PromptBundleArtifactLoaderV4
from app.repositories import ArtifactRepository
from app.services.artifacts import ArtifactService

_ALLOWED_IMAGE_MIME_PREFIX = "image/"


@dataclass(frozen=True)
class SceneAssetContinuitySource:
    source_beat_id: str
    source_asset_artifact_id: str
    source_asset_sha256: str
    mime_type: str
    source_kind: str

    def reference_descriptor(self) -> dict[str, Any]:
        return {
            "reference_tag": "prev_beat",
            "artifact_id": self.source_asset_artifact_id,
            "sha256": self.source_asset_sha256,
            "mime_type": self.mime_type,
        }

    def manifest_payload(self, relation: str) -> dict[str, Any]:
        return {
            "relation": relation,
            "source_beat_id": self.source_beat_id,
            "source_asset_artifact_id": self.source_asset_artifact_id,
            "source_asset_sha256": self.source_asset_sha256,
            "mime_type": self.mime_type,
            "source_kind": self.source_kind,
        }


@dataclass(frozen=True)
class SceneAssetContinuityResolution:
    source: SceneAssetContinuitySource | None
    block_reason: str | None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_image_artifact(
    *,
    artifacts: ArtifactService,
    project: Project,
    artifact_id: str,
    beat_id: str | None = None,
) -> Artifact | None:
    artifact = artifacts.repository.get(artifact_id)
    if artifact is None:
        return None
    if artifact.status != ArtifactStatus.READY or artifact.deleted_at is not None:
        return None
    if artifact.artifact_type != ArtifactType.IMAGE:
        return None
    if not artifact.logical_id.startswith(f"project/{project.project_id}/"):
        return None
    if beat_id is not None and f"/{beat_id}/" not in f"/{artifact.logical_id}/":
        return None
    if not artifact.mime_type or not artifact.mime_type.startswith(_ALLOWED_IMAGE_MIME_PREFIX):
        return None
    if not artifact.sha256 or len(artifact.sha256) != 64:
        return None
    try:
        path = artifacts.resolve_file_path(artifact)
    except Exception:
        return None
    try:
        if _file_sha256(path) != artifact.sha256:
            return None
    except OSError:
        return None
    return artifact


def _entry_metadata_matches(entry: dict[str, Any], artifact: Artifact) -> bool:
    return (
        entry.get("sha256") == artifact.sha256
        and entry.get("mime_type") == artifact.mime_type
        and entry.get("width") == artifact.width
        and entry.get("height") == artifact.height
    )


def _historical_prompt_item(
    *,
    artifacts: ArtifactService,
    project: Project,
    prompt_bundle_artifact_id: str,
    beat_id: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    try:
        bundle = PromptBundleArtifactLoaderV4(artifacts).load(project, prompt_bundle_artifact_id, require_current=False)
    except Exception:
        return None
    if (
        bundle.get("status") != "approved"
        or bundle.get("source_script_revision_id") != project.script_artifact_id
        or bundle.get("source_script_analysis_revision_id") != project.script_analysis_artifact_id
        or bundle.get("source_style_bible_revision_id") != project.style_bible_artifact_id
        or bundle.get("source_scene_plan_revision_id") != project.scene_plan_artifact_id
        or bundle.get("source_visual_plan_revision_id") != project.visual_plan_artifact_id
        or bundle.get("source_visual_reference_approval_set_artifact_id")
        != project.style_bible_visual_reference_approval_set_artifact_id
    ):
        return None
    matches = [item for item in bundle.get("prompt_items", []) if item.get("beat_id") == beat_id]
    if len(matches) != 1:
        return None
    return bundle, matches[0]


def _entry_generation_lineage_matches(
    *,
    artifacts: ArtifactService,
    entry: dict[str, Any],
    project: Project,
) -> bool:
    lineage = entry.get("generation_lineage")
    if not isinstance(lineage, dict):
        return False
    expected = {
        "scene_plan_artifact_id": project.scene_plan_artifact_id,
        "visual_plan_artifact_id": project.visual_plan_artifact_id,
        "visual_reference_approval_set_artifact_id": project.style_bible_visual_reference_approval_set_artifact_id,
    }
    if any(lineage.get(key) != value for key, value in expected.items()):
        return False
    bundle_id = lineage.get("prompt_bundle_artifact_id")
    if not isinstance(bundle_id, str):
        return False
    resolved = _historical_prompt_item(
        artifacts=artifacts, project=project, prompt_bundle_artifact_id=bundle_id, beat_id=str(entry.get("beat_id", ""))
    )
    if resolved is None:
        return False
    _, item = resolved
    return (
        entry.get("source_prompt_item_id") == item.get("prompt_item_id")
        and entry.get("source_prompt_hash") == item.get("prompt_hash")
        and entry.get("reference_bindings") == item.get("reference_bindings", [])
        and lineage.get("prompt_item_id") == item.get("prompt_item_id")
        and lineage.get("prompt_hash") == item.get("prompt_hash")
        and lineage.get("reference_binding_snapshot_hash") == canonical_hash(item.get("reference_bindings", []))
        and lineage.get("generation_settings_snapshot_hash") == canonical_hash(item.get("generation_options", {}))
    )


def _valid_generated_output(
    *,
    session: Session,
    artifacts: ArtifactService,
    project: Project,
    entry: dict[str, Any],
    prompt_item: dict[str, Any] | None,
) -> bool:
    # Existing Scene Asset candidates remain valid historical outputs after a beat prompt edit.
    # Their authority is the immutable MediaTask + Prompt Bundle revision that actually generated them,
    # not whatever Prompt Bundle happens to be current today.
    if not _entry_generation_lineage_matches(artifacts=artifacts, entry=entry, project=project):
        return False
    candidate_ids = entry.get("candidate_asset_artifact_ids")
    task_ids = entry.get("media_task_ids")
    if not isinstance(candidate_ids, list) or not candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
        return False
    if not isinstance(task_ids, list) or len(task_ids) != len(set(task_ids)):
        return False

    candidate_artifacts: dict[str, Artifact] = {}
    for artifact_id in candidate_ids:
        if not isinstance(artifact_id, str):
            continue
        artifact = _valid_image_artifact(
            artifacts=artifacts,
            project=project,
            artifact_id=artifact_id,
            beat_id=str(entry.get("beat_id", "")),
        )
        if artifact is not None:
            candidate_artifacts[artifact_id] = artifact

    result_owners: set[str] = set()
    for task_id in task_ids:
        if not isinstance(task_id, str):
            continue
        task = session.get(MediaTask, task_id)
        if task is None:
            continue
        if (
            task.project_id != project.project_id
            or task.purpose != "scene_asset_generation"
            or task.entity_type != "beat"
            or task.entity_id != entry.get("beat_id")
        ):
            continue
        metadata = task.metadata_json or {}
        bundle_id = metadata.get("prompt_bundle_artifact_id")
        if not isinstance(bundle_id, str):
            continue
        resolved = _historical_prompt_item(
            artifacts=artifacts,
            project=project,
            prompt_bundle_artifact_id=bundle_id,
            beat_id=str(entry.get("beat_id", "")),
        )
        if resolved is None:
            continue
        bundle, task_prompt_item = resolved
        if (
            metadata.get("prompt_item_id") != task_prompt_item.get("prompt_item_id")
            or metadata.get("prompt_hash") != task_prompt_item.get("prompt_hash")
            or metadata.get("visual_reference_approval_set_artifact_id")
            != bundle.get("source_visual_reference_approval_set_artifact_id")
            or metadata.get("reference_binding_snapshot_hash")
            != canonical_hash(task_prompt_item.get("reference_bindings", []))
            or metadata.get("generation_settings_snapshot_hash")
            != canonical_hash(task_prompt_item.get("generation_options", {}))
            or task.prompt != task_prompt_item.get("image_prompt")
            or task.negative_prompt != task_prompt_item.get("negative_prompt")
            or task.aspect_ratio != task_prompt_item.get("generation_options", {}).get("aspect_ratio")
        ):
            continue
        if task.status == "completed":
            for result in task.result_json or []:
                artifact_id = result.get("artifact_id") if isinstance(result, dict) else None
                if isinstance(artifact_id, str):
                    result_owners.add(artifact_id)
    valid_candidates = {
        artifact_id: artifact
        for artifact_id, artifact in candidate_artifacts.items()
        if artifact_id in result_owners
    }
    if not valid_candidates:
        return False

    selected_id = entry.get("selected_asset_artifact_id")
    current_draft_id = entry.get("current_draft_asset_artifact_id")
    resolution_status = entry.get("resolution_status")
    review_status = entry.get("review_status")
    if resolution_status == "approved" or review_status == "approved":
        if not isinstance(selected_id, str) or selected_id not in valid_candidates:
            return False
        return _entry_metadata_matches(entry, valid_candidates[selected_id])
    if review_status == "current_draft" or current_draft_id is not None:
        return isinstance(current_draft_id, str) and current_draft_id in valid_candidates
    return resolution_status == "candidate_ready"


def _continuity_entry_lineage_matches_project(*, source_entry: dict[str, Any], project: Project) -> bool:
    lineage = source_entry.get("generation_lineage")
    if not isinstance(lineage, dict):
        return False
    expected = {
        "scene_plan_artifact_id": project.scene_plan_artifact_id,
        "visual_plan_artifact_id": project.visual_plan_artifact_id,
        "visual_reference_approval_set_artifact_id": project.style_bible_visual_reference_approval_set_artifact_id,
    }
    return all(lineage.get(key) == value for key, value in expected.items())


def resolve_scene_asset_continuity_readiness(
    *,
    session: Session,
    project: Project,
    source_entry: dict[str, Any] | None,
    relation: str,
) -> SceneAssetContinuityResolution:
    """Resolve approved-only continuity authority and a stable blocked reason."""

    if relation == "new_setup":
        return SceneAssetContinuityResolution(source=None, block_reason=None)
    if source_entry is None:
        return SceneAssetContinuityResolution(source=None, block_reason="source_not_generated")

    candidate_ids = source_entry.get("candidate_asset_artifact_ids")
    if not isinstance(candidate_ids, list) or len(candidate_ids) != len(set(candidate_ids)):
        return SceneAssetContinuityResolution(source=None, block_reason="source_integrity_failed")
    candidate_set = {item for item in candidate_ids if isinstance(item, str)}
    selected = source_entry.get("selected_asset_artifact_id")
    if not candidate_set and not isinstance(selected, str):
        return SceneAssetContinuityResolution(source=None, block_reason="source_not_generated")

    approved = (
        source_entry.get("resolution_status") == "approved"
        and source_entry.get("review_status") == "approved"
        and isinstance(selected, str)
    )
    if not approved:
        return SceneAssetContinuityResolution(source=None, block_reason="source_not_approved")
    if not _continuity_entry_lineage_matches_project(source_entry=source_entry, project=project):
        return SceneAssetContinuityResolution(source=None, block_reason="source_lineage_stale")

    artifact_id = str(selected)
    if artifact_id not in candidate_set:
        return SceneAssetContinuityResolution(source=None, block_reason="source_integrity_failed")
    artifacts = ArtifactService(session, ArtifactRepository(session))
    artifact_row = artifacts.repository.get(artifact_id)
    if artifact_row is None:
        return SceneAssetContinuityResolution(source=None, block_reason="source_artifact_missing")
    artifact = _valid_image_artifact(
        artifacts=artifacts,
        project=project,
        artifact_id=artifact_id,
        beat_id=str(source_entry.get("beat_id", "")),
    )
    if artifact is None or not _entry_metadata_matches(source_entry, artifact):
        return SceneAssetContinuityResolution(source=None, block_reason="source_integrity_failed")
    return SceneAssetContinuityResolution(
        source=SceneAssetContinuitySource(
            source_beat_id=str(source_entry.get("beat_id", "")),
            source_asset_artifact_id=artifact.artifact_id,
            source_asset_sha256=str(artifact.sha256),
            mime_type=str(artifact.mime_type),
            source_kind="approved",
        ),
        block_reason=None,
    )


def resolve_scene_asset_continuity_source(
    *,
    session: Session,
    project: Project,
    source_entry: dict[str, Any] | None,
    relation: str,
) -> SceneAssetContinuitySource | None:
    """Resolve the approved previous-beat image used as runtime continuity authority."""

    return resolve_scene_asset_continuity_readiness(
        session=session,
        project=project,
        source_entry=source_entry,
        relation=relation,
    ).source


def has_valid_scene_asset_output(
    *,
    session: Session,
    project: Project,
    entry: dict[str, Any],
    prompt_item: dict[str, Any] | None,
) -> bool:
    """Return whether a manifest entry has a usable current-lineage provider output."""

    artifacts = ArtifactService(session, ArtifactRepository(session))
    return _valid_generated_output(
        session=session,
        artifacts=artifacts,
        project=project,
        entry=entry,
        prompt_item=prompt_item,
    )
