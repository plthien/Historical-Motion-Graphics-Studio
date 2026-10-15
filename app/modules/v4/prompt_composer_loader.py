from __future__ import annotations

import hashlib
import json
from typing import Any

from app.api.errors import AppError
from app.api.prompt_composer_schemas_v4 import PromptBundleModel
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Project
from app.services.artifacts import ArtifactService


class PromptBundleArtifactLoaderV4:
    def __init__(self, artifacts: ArtifactService) -> None:
        self.artifacts = artifacts

    def load(self, project: Project, artifact_id: str, *, require_current: bool = False) -> dict[str, Any]:
        row = self.artifacts.get_required(artifact_id)
        if row.logical_id != f"project/{project.project_id}/prompt_bundle":
            raise AppError("PROMPT_BUNDLE_ARTIFACT_PROJECT_MISMATCH", "Stored Prompt Bundle belongs to another project.", 409, retryable=False)
        if row.artifact_type != ArtifactType.JSON or row.status != ArtifactStatus.READY or row.deleted_at is not None:
            raise AppError("PROMPT_BUNDLE_ARTIFACT_INVALID", "Stored Prompt Bundle is unavailable or invalid.", 409, retryable=False)
        if require_current and (
            project.prompt_bundle_artifact_id != artifact_id
            or not row.is_current
            or row.superseded_by_artifact_id is not None
        ):
            raise AppError("PROMPT_BUNDLE_NOT_CURRENT", "Stored Prompt Bundle is not current.", 409, retryable=False)
        raw = self.artifacts.resolve_file_path(row).read_bytes()
        if row.size_bytes is not None and len(raw) != row.size_bytes:
            raise AppError("PROMPT_BUNDLE_ARTIFACT_CORRUPT", "Stored Prompt Bundle size is invalid.", 409, retryable=False)
        if row.sha256 is not None and hashlib.sha256(raw).hexdigest() != row.sha256:
            raise AppError("PROMPT_BUNDLE_ARTIFACT_CORRUPT", "Stored Prompt Bundle hash is invalid.", 409, retryable=False)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError("PROMPT_BUNDLE_ARTIFACT_CORRUPT", "Stored Prompt Bundle is not valid JSON.", 409, retryable=False) from exc
        try:
            value = PromptBundleModel.model_validate(payload).model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            raise AppError("PROMPT_BUNDLE_ARTIFACT_INVALID", "Stored Prompt Bundle 3.0 failed validation.", 409, retryable=False) from exc
        if value["project_id"] != project.project_id or value["prompt_bundle_revision_id"] != artifact_id:
            raise AppError("PROMPT_BUNDLE_ARTIFACT_INVALID", "Stored Prompt Bundle identity is invalid.", 409, retryable=False)
        if require_current and (
            value["source_script_revision_id"] != project.script_artifact_id
            or value["source_script_analysis_revision_id"] != project.script_analysis_artifact_id
            or value["source_style_bible_revision_id"] != project.style_bible_artifact_id
            or value["source_scene_plan_revision_id"] != project.scene_plan_artifact_id
            or value["source_visual_plan_revision_id"] != project.visual_plan_artifact_id
            or value["source_visual_reference_approval_set_artifact_id"]
            != project.style_bible_visual_reference_approval_set_artifact_id
        ):
            raise AppError("PROMPT_BUNDLE_UPSTREAM_STALE", "Stored Prompt Bundle lineage is stale.", 409, retryable=False)
        return value
