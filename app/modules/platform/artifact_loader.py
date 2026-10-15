from __future__ import annotations

import json
from typing import Any

from app.api.errors import AppError
from app.db.enums import ArtifactRole, ArtifactStatus, ArtifactType
from app.db.models import Artifact, Project
from app.modules.platform.contracts import PlatformContractValidator
from app.modules.v4.script_word_budget import ScriptTimingError, validate_persisted_script_integrity
from app.services.artifacts import ArtifactService


class ProjectArtifactLoader:
    """Loads the active immutable Topic Ideas and Script artifacts."""

    def __init__(self, artifacts: ArtifactService, validator: PlatformContractValidator):
        self.artifacts = artifacts
        self.validator = validator

    def _read_json_artifact(
        self,
        project_id: str,
        artifact_id: str,
        expected_role: ArtifactRole,
        expected_logical_id_suffix: str,
        schema_name: str,
        error_prefix: str,
        *,
        require_current: bool = False,
    ) -> tuple[Artifact, dict[str, Any]]:
        try:
            artifact_row = self.artifacts.get_required(artifact_id)
            expected_logical_id = f"project/{project_id}/{expected_logical_id_suffix}"
            if artifact_row.status != ArtifactStatus.READY or artifact_row.deleted_at is not None:
                raise AppError(
                    f"{error_prefix}_UNAVAILABLE",
                    f"Stored {expected_logical_id_suffix} artifact for project '{project_id}' is unavailable.",
                    409, retryable=False,
                    details={"project_id": project_id, "artifact_id": artifact_id},
                )
            if require_current and (not artifact_row.is_current or artifact_row.superseded_by_artifact_id is not None):
                raise AppError(
                    f"{error_prefix}_NOT_CURRENT",
                    f"Stored {expected_logical_id_suffix} artifact for project '{project_id}' is not current.",
                    409, retryable=False,
                    details={"project_id": project_id, "artifact_id": artifact_id},
                )
            if artifact_row.logical_id != expected_logical_id:
                raise AppError(
                    f"{error_prefix}_PROJECT_MISMATCH",
                    f"Stored {expected_logical_id_suffix} artifact belongs to another project.",
                    409, retryable=False,
                    details={"project_id": project_id, "artifact_id": artifact_id, "logical_id": artifact_row.logical_id},
                )
            if artifact_row.artifact_type != ArtifactType.JSON or artifact_row.artifact_role != expected_role:
                raise AppError(
                    f"{error_prefix}_INVALID",
                    f"Stored {expected_logical_id_suffix} artifact has an invalid type or role.",
                    409, retryable=False,
                    details={"project_id": project_id, "artifact_id": artifact_id},
                )
            raw_bytes = self.artifacts.resolve_file_path(artifact_row).read_bytes()
        except AppError as exc:
            if exc.error_code in {"ARTIFACT_NOT_FOUND", "ARTIFACT_FILE_MISSING", "ARTIFACT_PATH_INVALID"}:
                raise AppError(
                    f"{error_prefix}_UNAVAILABLE",
                    f"Stored {expected_logical_id_suffix} artifact for project '{project_id}' is unavailable.",
                    409, retryable=False,
                    details={"project_id": project_id, "artifact_id": artifact_id, "cause": exc.error_code},
                ) from exc
            raise
        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError(
                f"{error_prefix}_CORRUPT",
                f"Stored {expected_logical_id_suffix} artifact is not valid JSON.",
                409, retryable=False,
                details={"project_id": project_id, "artifact_id": artifact_id},
            ) from exc
        if not isinstance(payload, dict):
            raise AppError(
                f"{error_prefix}_INVALID",
                f"Stored {expected_logical_id_suffix} artifact must be a JSON object.",
                409, retryable=False,
                details={"project_id": project_id, "artifact_id": artifact_id},
            )
        try:
            validated = self.validator.validate(schema_name, payload, error_code=f"{error_prefix}_INVALID")
        except AppError as exc:
            if exc.error_code == f"{error_prefix}_INVALID":
                raise AppError(
                    f"{error_prefix}_INVALID",
                    f"Stored {expected_logical_id_suffix} artifact failed contract validation.",
                    409, retryable=False,
                    details={"project_id": project_id, "artifact_id": artifact_id, "errors": exc.details.get("errors", [])},
                ) from exc
            raise
        return artifact_row, validated

    def load_topic_ideas(self, project: Project, artifact_id: str, *, require_current: bool = False) -> dict[str, Any]:
        return self._read_json_artifact(
            project.project_id, artifact_id, ArtifactRole.TOPIC_IDEAS, "topic_ideas",
            "topic_ideas.schema.json", "TOPIC_IDEAS_ARTIFACT", require_current=require_current,
        )[1]

    def load_script(
        self, project: Project, artifact_id: str, *, require_current: bool = False, require_downstream_ready: bool = False
    ) -> dict[str, Any]:
        payload = self._read_json_artifact(
            project.project_id, artifact_id, ArtifactRole.SCRIPT, "script",
            "script.schema.json", "SCRIPT_ARTIFACT", require_current=require_current,
        )[1]
        channel_dna = project.channel_dna_json
        pacing_profiles = channel_dna.get("pacing_profiles") if isinstance(channel_dna, dict) else None
        try:
            validate_persisted_script_integrity(payload, pacing_profiles=pacing_profiles)
        except ScriptTimingError as exc:
            raise AppError(
                "SCRIPT_ARTIFACT_INVALID",
                f"Stored script artifact for project '{project.project_id}' has invalid derived data.",
                409, retryable=False,
                details={"project_id": project.project_id, "artifact_id": artifact_id, "cause": "script_integrity_invalid"},
            ) from exc
        return payload
