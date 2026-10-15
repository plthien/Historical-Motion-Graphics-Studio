from __future__ import annotations

import hashlib
import json
from typing import Any

from app.api.errors import AppError
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Project
from app.modules.platform.services import build_script_section_ranges, validate_script_full_script_integrity
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.script_analysis_input_builder import build_script_analysis_input
from app.modules.v4.script_analysis_validator import ScriptAnalysisSemanticValidator
from app.services.artifacts import ArtifactService


class ScriptAnalysisArtifactLoader:
    def __init__(
        self,
        artifacts: ArtifactService,
        validator: V4ContractValidator,
        semantic_validator: ScriptAnalysisSemanticValidator,
    ) -> None:
        self.artifacts = artifacts
        self.validator = validator
        self.semantic_validator = semantic_validator

    def load(
        self,
        *,
        project: Project,
        artifact_id: str,
        script_artifact: dict[str, Any],
        expected_script_revision_id: str,
        require_current: bool = False,
    ) -> dict[str, Any]:
        try:
            row = self.artifacts.get_required(artifact_id)
            if row.logical_id != f"project/{project.project_id}/script_analysis":
                raise AppError(
                    "SCRIPT_ANALYSIS_ARTIFACT_PROJECT_MISMATCH",
                    "Stored Script Analysis belongs to another project.",
                    409,
                    retryable=False,
                    details={"project_id": project.project_id, "artifact_id": artifact_id},
                )
            if row.artifact_type != ArtifactType.JSON or row.status != ArtifactStatus.READY or row.deleted_at is not None:
                raise AppError(
                    "SCRIPT_ANALYSIS_ARTIFACT_INVALID",
                    "Stored Script Analysis artifact is unavailable or has an invalid type.",
                    409,
                    retryable=False,
                    details={"project_id": project.project_id, "artifact_id": artifact_id},
                )
            if require_current and (not row.is_current or row.superseded_by_artifact_id is not None):
                raise AppError(
                    "SCRIPT_ANALYSIS_ARTIFACT_NOT_CURRENT",
                    "Stored Script Analysis artifact is not current.",
                    409,
                    retryable=False,
                    details={"project_id": project.project_id, "artifact_id": artifact_id},
                )
            artifact_path = self.artifacts.resolve_file_path(row)
            content = artifact_path.read_bytes()
            if row.size_bytes is not None and len(content) != row.size_bytes:
                raise AppError(
                    "SCRIPT_ANALYSIS_ARTIFACT_CORRUPT",
                    "Stored Script Analysis artifact size does not match its metadata.",
                    409,
                    retryable=False,
                    details={"project_id": project.project_id, "artifact_id": artifact_id},
                )
            if row.sha256 is not None and hashlib.sha256(content).hexdigest() != row.sha256:
                raise AppError(
                    "SCRIPT_ANALYSIS_ARTIFACT_CORRUPT",
                    "Stored Script Analysis artifact hash does not match its metadata.",
                    409,
                    retryable=False,
                    details={"project_id": project.project_id, "artifact_id": artifact_id},
                )
            payload = json.loads(content.decode("utf-8"))
        except AppError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError(
                "SCRIPT_ANALYSIS_ARTIFACT_CORRUPT",
                "Stored Script Analysis artifact is not valid JSON.",
                409,
                retryable=False,
                details={"project_id": project.project_id, "artifact_id": artifact_id},
            ) from exc

        if not isinstance(payload, dict):
            raise AppError(
                "SCRIPT_ANALYSIS_ARTIFACT_INVALID",
                "Stored Script Analysis artifact must be a JSON object.",
                409,
                retryable=False,
            )
        try:
            self.validator.validate(
                "script_analysis_v1.schema.json",
                payload,
                error_code="SCRIPT_ANALYSIS_ARTIFACT_INVALID",
            )
            if payload["project_id"] != project.project_id:
                raise AppError(
                    "SCRIPT_ANALYSIS_ARTIFACT_PROJECT_MISMATCH",
                    "Stored Script Analysis identity does not match the project.",
                    409,
                    retryable=False,
                )
            source_script_revision_id = str(payload["source_script_revision_id"])
            if source_script_revision_id != expected_script_revision_id:
                raise AppError(
                    "SCRIPT_ANALYSIS_ARTIFACT_STALE",
                    "Stored Script Analysis does not match the expected Script revision.",
                    409,
                    retryable=False,
                    details={
                        "expected_script_revision_id": expected_script_revision_id,
                        "actual_script_revision_id": source_script_revision_id,
                    },
                )
            validate_script_full_script_integrity(script_artifact, artifact_id=source_script_revision_id)
            llm_input = build_script_analysis_input(
                project_id=project.project_id,
                script_revision_id=source_script_revision_id,
                script_artifact=script_artifact,
            )
            self.semantic_validator.validate_canonical(
                llm_input=llm_input,
                canonical=payload,
                section_ranges=build_script_section_ranges(script_artifact["script_sections"]),
            )
        except AppError as exc:
            if exc.error_code.startswith("SCRIPT_ANALYSIS_ARTIFACT_"):
                raise
            raise AppError(
                "SCRIPT_ANALYSIS_ARTIFACT_INVALID",
                "Stored Script Analysis failed deterministic validation.",
                409,
                retryable=False,
                details={"project_id": project.project_id, "artifact_id": artifact_id, "cause": exc.error_code},
            ) from exc
        return payload
