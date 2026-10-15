from __future__ import annotations

import hashlib
import json
from typing import Any

from app.api.errors import AppError
from app.db.enums import ArtifactRole, ArtifactStatus, ArtifactType
from app.db.models import Project
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.style_bible_draft_builder import channel_dna_snapshot_sha256
from app.modules.v4.style_bible_validator import StyleBibleSemanticValidatorV4
from app.services.artifacts import ArtifactService


class StyleBibleArtifactLoaderV4:
    def __init__(self, artifacts: ArtifactService) -> None:
        self.artifacts = artifacts
        self.validator = V4ContractValidator()
        self.semantic = StyleBibleSemanticValidatorV4()

    def load(
        self,
        *,
        project: Project,
        artifact_id: str,
        script_analysis: dict[str, Any],
        require_current: bool = False,
    ) -> dict[str, Any]:
        try:
            row = self.artifacts.get_required(artifact_id)
            if row.logical_id != f"project/{project.project_id}/style_bible":
                raise AppError("STYLE_BIBLE_ARTIFACT_PROJECT_MISMATCH", "Stored Style Bible belongs to another project.", 409, retryable=False)
            if row.artifact_type != ArtifactType.JSON or row.artifact_role != ArtifactRole.PROJECT_STYLE_BIBLE or row.status != ArtifactStatus.READY or row.deleted_at is not None:
                raise AppError("STYLE_BIBLE_ARTIFACT_INVALID", "Stored Style Bible is unavailable or invalid.", 409, retryable=False)
            if require_current and (not row.is_current or row.superseded_by_artifact_id is not None or project.style_bible_artifact_id != artifact_id):
                raise AppError("STYLE_BIBLE_ARTIFACT_NOT_CURRENT", "Stored Style Bible is not current.", 409, retryable=False)
            content = self.artifacts.resolve_file_path(row).read_bytes()
            if row.size_bytes is not None and row.size_bytes != len(content):
                raise AppError("STYLE_BIBLE_ARTIFACT_CORRUPT", "Stored Style Bible size does not match metadata.", 409, retryable=False)
            if row.sha256 is not None and hashlib.sha256(content).hexdigest() != row.sha256:
                raise AppError("STYLE_BIBLE_ARTIFACT_CORRUPT", "Stored Style Bible hash does not match metadata.", 409, retryable=False)
            payload = json.loads(content.decode("utf-8"))
        except AppError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError("STYLE_BIBLE_ARTIFACT_CORRUPT", "Stored Style Bible is not valid JSON.", 409, retryable=False) from exc
        if not isinstance(payload, dict):
            raise AppError("STYLE_BIBLE_ARTIFACT_INVALID", "Stored Style Bible must be a JSON object.", 409, retryable=False)
        self.validator.validate("project_style_bible_v1.schema.json", payload, error_code="STYLE_BIBLE_ARTIFACT_INVALID")
        if payload["project_id"] != project.project_id or payload["style_bible_revision_id"] != artifact_id:
            raise AppError("STYLE_BIBLE_ARTIFACT_PROJECT_MISMATCH", "Stored Style Bible identity is invalid.", 409, retryable=False)
        lineage = payload["lineage"]
        if (
            lineage["script_analysis_artifact_id"] != project.script_analysis_artifact_id
            or lineage["source_script_artifact_id"] != project.script_artifact_id
            or lineage["channel_dna_snapshot_sha256"] != channel_dna_snapshot_sha256(project.channel_dna_json)
        ):
            raise AppError("STYLE_BIBLE_UPSTREAM_STALE", "The current Style Bible no longer matches Script Analysis or Channel DNA.", 409, retryable=False)
        self.semantic.validate(style_bible=payload, script_analysis=script_analysis, channel_dna=project.channel_dna_json)
        return payload
