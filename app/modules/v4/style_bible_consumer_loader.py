from __future__ import annotations

import hashlib
import json
from typing import Any

from app.api.errors import AppError
from app.db.enums import ArtifactRole, ArtifactStatus, ArtifactType
from app.db.models import Project
from app.modules.v4.contracts import V4ContractValidator
from app.services.artifacts import ArtifactService


class StyleBibleConsumerLoaderV4:
    """Read-only downstream boundary for the current text-approved Style Bible V4.

    It validates integrity, schema, identity and Project lineage without loading
    Script Analysis content. Producer-side semantic revalidation remains owned by
    StyleBibleArtifactLoaderV4.
    """

    def __init__(self, artifacts: ArtifactService) -> None:
        self.artifacts = artifacts
        self.validator = V4ContractValidator()

    def load_text_approved(self, project: Project, artifact_id: str | None = None) -> dict[str, Any]:
        selected = artifact_id or project.style_bible_artifact_id
        if selected is None:
            raise AppError("STYLE_BIBLE_NOT_FOUND", "A current text-approved Style Bible is required.", 409, retryable=False)
        row = self.artifacts.get_required(selected)
        if row.logical_id != f"project/{project.project_id}/style_bible":
            raise AppError("STYLE_BIBLE_ARTIFACT_PROJECT_MISMATCH", "Stored Style Bible belongs to another project.", 409, retryable=False)
        if (
            row.artifact_type != ArtifactType.JSON
            or row.artifact_role != ArtifactRole.PROJECT_STYLE_BIBLE
            or row.status != ArtifactStatus.READY
            or row.deleted_at is not None
        ):
            raise AppError("STYLE_BIBLE_ARTIFACT_INVALID", "Stored Style Bible is unavailable or invalid.", 409, retryable=False)
        if project.style_bible_artifact_id != selected or not row.is_current or row.superseded_by_artifact_id is not None:
            raise AppError("STYLE_BIBLE_ARTIFACT_NOT_CURRENT", "Stored Style Bible is not current.", 409, retryable=False)
        raw = self.artifacts.resolve_file_path(row).read_bytes()
        if row.size_bytes is not None and len(raw) != row.size_bytes:
            raise AppError("STYLE_BIBLE_ARTIFACT_CORRUPT", "Stored Style Bible size is invalid.", 409, retryable=False)
        if row.sha256 is not None and hashlib.sha256(raw).hexdigest() != row.sha256:
            raise AppError("STYLE_BIBLE_ARTIFACT_CORRUPT", "Stored Style Bible hash is invalid.", 409, retryable=False)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError("STYLE_BIBLE_ARTIFACT_CORRUPT", "Stored Style Bible is not valid JSON.", 409, retryable=False) from exc
        value = self.validator.validate(
            "project_style_bible_v1.schema.json", payload, error_code="STYLE_BIBLE_ARTIFACT_INVALID"
        )
        if value["project_id"] != project.project_id or value["style_bible_revision_id"] != selected:
            raise AppError("STYLE_BIBLE_ARTIFACT_PROJECT_MISMATCH", "Stored Style Bible identity is invalid.", 409, retryable=False)
        if value["status"] != "text_approved":
            raise AppError("STYLE_BIBLE_NOT_TEXT_APPROVED", "A text-approved Style Bible is required.", 409, retryable=False)
        lineage = value["lineage"]
        if (
            lineage["script_analysis_artifact_id"] != project.script_analysis_artifact_id
            or lineage["source_script_artifact_id"] != project.script_artifact_id
        ):
            raise AppError("STYLE_BIBLE_UPSTREAM_STALE", "Stored Style Bible lineage is stale.", 409, retryable=False)
        return value
