from __future__ import annotations

import hashlib
import json
from typing import Any

from app.api.errors import AppError
from app.db.models import Project
from app.modules.v4.contracts import V4ContractValidator
from app.repositories import ArtifactRepository
from app.services.artifacts import ArtifactService


class SceneBreakdownArtifactLoaderV4:
    def __init__(
        self,
        artifacts: ArtifactService,
        repository: ArtifactRepository,
        validator: V4ContractValidator | None = None,
    ) -> None:
        self.artifacts = artifacts
        self.repository = repository
        self.validator = validator or V4ContractValidator()

    def _load(
        self,
        *,
        artifact_id: str,
        logical_id: str | None,
        logical_prefix: str | None,
        schema_name: str,
        error_prefix: str,
    ) -> tuple[Any, dict[str, Any]]:
        row = self.repository.get(artifact_id)
        if row is None or row.deleted_at is not None:
            raise AppError(
                f"{error_prefix}_NOT_FOUND",
                "The requested Scene Breakdown artifact was not found.",
                404,
                retryable=False,
                details={"artifact_id": artifact_id},
            )
        if logical_id is not None and row.logical_id != logical_id:
            raise AppError(
                f"{error_prefix}_NOT_FOUND",
                "The requested Scene Breakdown artifact was not found.",
                404,
                retryable=False,
                details={"artifact_id": artifact_id},
            )
        if logical_prefix is not None and not row.logical_id.startswith(logical_prefix):
            raise AppError(
                f"{error_prefix}_NOT_FOUND",
                "The requested Scene Breakdown artifact was not found.",
                404,
                retryable=False,
                details={"artifact_id": artifact_id},
            )
        content = self.artifacts.read_artifact_bytes(artifact_id)
        if row.size_bytes is not None and row.size_bytes != len(content):
            raise AppError(
                f"{error_prefix}_INTEGRITY_FAILED",
                "Stored artifact byte size does not match metadata.",
                409,
                retryable=False,
                details={"artifact_id": artifact_id},
            )
        digest = hashlib.sha256(content).hexdigest()
        if row.sha256 is not None and row.sha256 != digest:
            raise AppError(
                f"{error_prefix}_INTEGRITY_FAILED",
                "Stored artifact hash does not match metadata.",
                409,
                retryable=False,
                details={"artifact_id": artifact_id},
            )
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError(
                f"{error_prefix}_INVALID",
                "Stored Scene Breakdown artifact is not valid JSON.",
                409,
                retryable=False,
                details={"artifact_id": artifact_id},
            ) from exc
        return row, self.validator.validate(
            schema_name,
            payload,
            error_code=f"{error_prefix}_INVALID",
        )

    def load_scene_plan(
        self, project: Project, artifact_id: str, *, require_current: bool = False
    ) -> dict[str, Any]:
        row, payload = self._load(
            artifact_id=artifact_id,
            logical_id=f"project/{project.project_id}/scene_plan",
            logical_prefix=None,
            schema_name="scene_plan_v1.schema.json",
            error_prefix="SCENE_PLAN",
        )
        if (
            payload.get("project_id") != project.project_id
            or payload.get("scene_plan_revision_id") != artifact_id
        ):
            raise AppError(
                "SCENE_PLAN_INVALID",
                "Stored Scene Plan identity is invalid.",
                409,
                retryable=False,
            )
        if require_current:
            if project.scene_plan_artifact_id != artifact_id or not row.is_current:
                raise AppError(
                    "SCENE_PLAN_NOT_CURRENT",
                    "The requested Scene Plan is not current.",
                    409,
                    retryable=False,
                )
            if (
                payload["source_script_revision_id"] != project.script_artifact_id
                or payload["source_script_analysis_revision_id"]
                != project.script_analysis_artifact_id
                or payload["source_style_bible_revision_id"]
                != project.style_bible_artifact_id
            ):
                raise AppError(
                    "SCENE_BREAKDOWN_UPSTREAM_STALE",
                    "The current Scene Plan lineage no longer matches the Project.",
                    409,
                    retryable=False,
                )
        return payload

    def load_approved_scene_plan_for_consumer(
        self, project: Project, artifact_id: str | None = None
    ) -> dict[str, Any]:
        """Load the current approved Scene Plan 2.0 consumer authority.

        Downstream V4 consumers must enter through this boundary instead of
        reconstructing hidden Script Analysis groupings or accepting draft/stale plans.
        """
        selected = artifact_id or project.scene_plan_artifact_id
        if selected is None:
            raise AppError(
                "SCENE_PLAN_NOT_FOUND",
                "An approved current Scene Plan is required.",
                409,
                retryable=False,
            )
        payload = self.load_scene_plan(project, selected, require_current=True)
        if payload.get("schema_version") != "2.0":
            raise AppError(
                "SCENE_PLAN_VERSION_UNSUPPORTED",
                "Downstream V4 consumers require Scene Plan 2.0.",
                409,
                retryable=False,
                details={"artifact_id": selected},
            )
        if payload.get("status") != "approved":
            raise AppError(
                "SCENE_PLAN_NOT_APPROVED",
                "Downstream consumers require an approved Scene Plan.",
                409,
                retryable=False,
                details={"artifact_id": selected},
            )
        return payload


    def load_section_edit_validation(
        self, project: Project, artifact_id: str
    ) -> dict[str, Any]:
        prefix = (
            f"project/{project.project_id}/scene_breakdown_v4/"
            "review_validations/"
        )
        _row, payload = self._load(
            artifact_id=artifact_id,
            logical_id=None,
            logical_prefix=prefix,
            schema_name="scene_breakdown_section_edit_validation_v1.schema.json",
            error_prefix="SCENE_BREAKDOWN_EDIT_VALIDATION",
        )
        if (
            payload.get("project_id") != project.project_id
            or payload.get("validation_artifact_id") != artifact_id
        ):
            raise AppError(
                "SCENE_BREAKDOWN_EDIT_VALIDATION_INVALID",
                "Stored Scene Breakdown edit validation identity is invalid.",
                409,
                retryable=False,
            )
        return payload

    def load_run(self, project: Project, artifact_id: str) -> dict[str, Any]:
        prefix = f"project/{project.project_id}/scene_breakdown_v4/runs/"
        row, payload = self._load(
            artifact_id=artifact_id,
            logical_id=None,
            logical_prefix=prefix,
            schema_name="scene_breakdown_run_v1.schema.json",
            error_prefix="SCENE_BREAKDOWN_RUN",
        )
        if "/failures/" in row.logical_id:
            raise AppError(
                "SCENE_BREAKDOWN_RUN_NOT_FOUND",
                "Scene Breakdown run was not found.",
                404,
                retryable=False,
            )
        expected = f"{prefix}{payload['run_id']}"
        if row.logical_id != expected or payload["project_id"] != project.project_id:
            raise AppError(
                "SCENE_BREAKDOWN_RUN_INVALID",
                "Scene Breakdown run ownership is invalid.",
                409,
                retryable=False,
            )
        return payload

    def load_failure(self, project: Project, artifact_id: str) -> dict[str, Any]:
        prefix = f"project/{project.project_id}/scene_breakdown_v4/runs/"
        row, payload = self._load(
            artifact_id=artifact_id,
            logical_id=None,
            logical_prefix=prefix,
            schema_name="scene_breakdown_validation_failed_v1.schema.json",
            error_prefix="SCENE_BREAKDOWN_FAILURE",
        )
        if "/failures/" not in row.logical_id:
            raise AppError(
                "SCENE_BREAKDOWN_FAILURE_NOT_FOUND",
                "Scene Breakdown failure was not found.",
                404,
                retryable=False,
            )
        run = self.load_run(project, payload["source_run_artifact_id"])
        expected = f"{prefix}{run['run_id']}/failures/{payload['section_id']}"
        if row.logical_id != expected:
            raise AppError(
                "SCENE_BREAKDOWN_FAILURE_OWNERSHIP_INVALID",
                "Scene Breakdown failure ownership is invalid.",
                409,
                retryable=False,
            )
        for field in (
            "source_script_revision_id",
            "source_script_analysis_revision_id",
            "source_style_bible_revision_id",
        ):
            if payload[field] != run[field]:
                raise AppError(
                    "SCENE_BREAKDOWN_FAILURE_LINEAGE_INVALID",
                    "Scene Breakdown failure lineage is inconsistent with its run.",
                    409,
                    retryable=False,
                    details={"field": field},
                )
        return payload
