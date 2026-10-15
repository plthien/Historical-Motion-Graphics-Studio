from __future__ import annotations

import hashlib
import json
from typing import Any

from app.api.errors import AppError
from app.api.visual_plan_schemas_v4 import VisualPlanModel
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Project
from app.services.artifacts import ArtifactService


class VisualPlanArtifactLoaderV4:
    """Read-only boundary for current immutable Visual Plan 3.0 artifacts."""

    def __init__(self, artifacts: ArtifactService) -> None:
        self.artifacts = artifacts

    def load_visual_plan(
        self,
        project: Project,
        artifact_id: str,
        *,
        require_current: bool = False,
    ) -> dict[str, Any]:
        row = self.artifacts.get_required(artifact_id)
        if (
            row.logical_id != f"project/{project.project_id}/visual_plan"
            or row.artifact_type != ArtifactType.JSON
            or row.status != ArtifactStatus.READY
            or row.deleted_at is not None
        ):
            raise AppError(
                "VISUAL_PLAN_INVALID",
                "Stored Visual Plan artifact is unavailable or has invalid ownership.",
                409,
                retryable=False,
            )
        raw = self.artifacts.resolve_file_path(row).read_bytes()
        if row.size_bytes is not None and len(raw) != row.size_bytes:
            raise AppError(
                "VISUAL_PLAN_CORRUPT",
                "Stored Visual Plan size is invalid.",
                409,
                retryable=False,
            )
        if row.sha256 is not None and hashlib.sha256(raw).hexdigest() != row.sha256:
            raise AppError(
                "VISUAL_PLAN_CORRUPT",
                "Stored Visual Plan hash is invalid.",
                409,
                retryable=False,
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
            value = VisualPlanModel.model_validate(payload).model_dump(mode="json")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise AppError(
                "VISUAL_PLAN_INVALID",
                "Stored Visual Plan 3.0 failed validation.",
                409,
                retryable=False,
            ) from exc
        if value["project_id"] != project.project_id or value["visual_plan_revision_id"] != artifact_id:
            raise AppError(
                "VISUAL_PLAN_INVALID",
                "Stored Visual Plan identity is invalid.",
                409,
                retryable=False,
            )
        if require_current:
            if (
                project.visual_plan_artifact_id != artifact_id
                or not row.is_current
                or row.superseded_by_artifact_id is not None
            ):
                raise AppError(
                    "VISUAL_PLAN_NOT_CURRENT",
                    "The requested Visual Plan is not current.",
                    409,
                    retryable=False,
                )
            if (
                value["source_script_revision_id"] != project.script_artifact_id
                or value["source_script_analysis_revision_id"] != project.script_analysis_artifact_id
                or value["source_style_bible_revision_id"] != project.style_bible_artifact_id
                or value["source_scene_plan_revision_id"] != project.scene_plan_artifact_id
                or value["source_visual_reference_approval_set_artifact_id"]
                != project.style_bible_visual_reference_approval_set_artifact_id
            ):
                raise AppError(
                    "VISUAL_PLAN_UPSTREAM_STALE",
                    "The current Visual Plan lineage no longer matches the Project.",
                    409,
                    retryable=False,
                )
        return value

    def load_approved_visual_plan_for_consumer(
        self,
        project: Project,
        artifact_id: str | None = None,
    ) -> dict[str, Any]:
        selected = artifact_id or project.visual_plan_artifact_id
        if selected is None:
            raise AppError(
                "VISUAL_PLAN_NOT_FOUND",
                "An approved current Visual Plan is required.",
                409,
                retryable=False,
            )
        plan = self.load_visual_plan(project, selected, require_current=True)
        if plan["schema_version"] != "3.0":
            raise AppError(
                "VISUAL_PLAN_VERSION_UNSUPPORTED",
                "Downstream V4 consumers require Visual Plan 3.0.",
                409,
                retryable=False,
            )
        if plan["status"] != "approved":
            raise AppError(
                "VISUAL_PLAN_NOT_APPROVED",
                "Downstream consumers require an approved Visual Plan.",
                409,
                retryable=False,
            )
        return plan
