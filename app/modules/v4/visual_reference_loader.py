from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from app.api.errors import AppError
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Project
from app.modules.v4.contracts import V4ContractValidator
from app.services.artifacts import ArtifactService


class VisualReferenceArtifactLoaderV4:
    def __init__(self, artifacts: ArtifactService) -> None:
        self.artifacts = artifacts
        self.validator = V4ContractValidator()

    def _json_payload(self, artifact_id: str, *, logical_id: str) -> tuple[Any, dict[str, Any]]:
        row = self.artifacts.get_required(artifact_id)
        if (
            row.logical_id != logical_id
            or row.artifact_type != ArtifactType.JSON
            or row.status != ArtifactStatus.READY
            or row.deleted_at is not None
        ):
            raise AppError("VISUAL_REFERENCE_ARTIFACT_INVALID", "Stored visual-reference artifact is unavailable or has invalid ownership.", 409, retryable=False)
        raw = self.artifacts.resolve_file_path(row).read_bytes()
        if row.size_bytes is not None and len(raw) != row.size_bytes:
            raise AppError("VISUAL_REFERENCE_ARTIFACT_CORRUPT", "Stored visual-reference artifact size is invalid.", 409, retryable=False)
        if row.sha256 is not None and hashlib.sha256(raw).hexdigest() != row.sha256:
            raise AppError("VISUAL_REFERENCE_ARTIFACT_CORRUPT", "Stored visual-reference artifact hash is invalid.", 409, retryable=False)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError("VISUAL_REFERENCE_ARTIFACT_CORRUPT", "Stored visual-reference artifact is not valid JSON.", 409, retryable=False) from exc
        if not isinstance(payload, dict):
            raise AppError("VISUAL_REFERENCE_ARTIFACT_INVALID", "Stored visual-reference artifact must be an object.", 409, retryable=False)
        return row, payload

    def load_set(self, project: Project, artifact_id: str, *, require_current: bool = False) -> dict[str, Any]:
        row, payload = self._json_payload(artifact_id, logical_id=f"project/{project.project_id}/visual_references")
        value = self.validator.validate("visual_reference_set_v1.schema.json", payload, error_code="VISUAL_REFERENCE_SET_INVALID")
        if value["project_id"] != project.project_id or value["visual_reference_set_artifact_id"] != artifact_id:
            raise AppError("VISUAL_REFERENCE_SET_INVALID", "Visual-reference set identity is invalid.", 409, retryable=False)
        if require_current and (
            project.style_bible_visual_reference_set_artifact_id != artifact_id
            or not row.is_current
            or row.superseded_by_artifact_id is not None
        ):
            raise AppError("VISUAL_REFERENCE_SET_NOT_CURRENT", "Visual-reference set is not current.", 409, retryable=False)
        self._validate_lineage(project, value)
        self._validate_candidates(project, value)
        return deepcopy(value)

    def load_candidate(self, project: Project, artifact_id: str) -> dict[str, Any]:
        row, payload = self._json_payload(
            artifact_id,
            logical_id=f"project/{project.project_id}/visual_references/candidates/{artifact_id}",
        )
        value = self.validator.validate(
            "visual_reference_candidate_v1.schema.json",
            payload,
            error_code="VISUAL_REFERENCE_CANDIDATE_INVALID",
        )
        if value["project_id"] != project.project_id or value["reference_artifact_id"] != artifact_id:
            raise AppError("VISUAL_REFERENCE_CANDIDATE_INVALID", "Visual-reference candidate identity is invalid.", 409, retryable=False)
        self._validate_lineage(project, value)
        if row.is_current is not True or row.superseded_by_artifact_id is not None:
            raise AppError("VISUAL_REFERENCE_CANDIDATE_INVALID", "Visual-reference candidate Artifact is not current.", 409, retryable=False)
        return deepcopy(value)

    def load_approval_set(self, project: Project, artifact_id: str, *, require_current: bool = False) -> dict[str, Any]:
        row, payload = self._json_payload(artifact_id, logical_id=f"project/{project.project_id}/visual_reference_approvals")
        value = self.validator.validate("visual_reference_approval_set_v1.schema.json", payload, error_code="VISUAL_REFERENCE_APPROVAL_SET_INVALID")
        if value["project_id"] != project.project_id or value["approval_set_artifact_id"] != artifact_id:
            raise AppError("VISUAL_REFERENCE_APPROVAL_SET_INVALID", "Visual-reference approval identity is invalid.", 409, retryable=False)
        if require_current and (
            project.style_bible_visual_reference_approval_set_artifact_id != artifact_id
            or not row.is_current
            or row.superseded_by_artifact_id is not None
        ):
            raise AppError("VISUAL_REFERENCE_APPROVAL_SET_NOT_CURRENT", "Visual-reference approval set is not current.", 409, retryable=False)
        self._validate_lineage(project, value)
        if value["source_visual_reference_set_artifact_id"] != project.style_bible_visual_reference_set_artifact_id:
            raise AppError("VISUAL_REFERENCE_APPROVAL_SET_STALE", "Visual-reference approval set does not match the current reference set.", 409, retryable=False)
        visual_set = self.load_set(project, value["source_visual_reference_set_artifact_id"], require_current=True)
        candidate_index = {
            candidate["reference_artifact_id"]: (subject, candidate)
            for subject in visual_set["subjects"]
            for candidate in subject["candidates"]
        }
        seen: set[tuple[str, str]] = set()
        for selection in value["selections"]:
            key = (selection["anchor_type"], selection["anchor_id"])
            if key in seen:
                raise AppError("VISUAL_REFERENCE_APPROVAL_SET_INVALID", "Approval selections must be unique by anchor.", 409, retryable=False)
            seen.add(key)
            pair = candidate_index.get(selection["reference_artifact_id"])
            if pair is None:
                raise AppError("VISUAL_REFERENCE_APPROVAL_SET_INVALID", "Approval selection is not part of the current reference set.", 409, retryable=False)
            subject, candidate = pair
            expected = {
                "anchor_type": subject["anchor_type"],
                "anchor_id": subject["anchor_id"],
                "media_artifact_id": candidate["media_artifact_id"],
                "source_definition_hash": subject["source_definition_hash"],
                "media_sha256": candidate["media_sha256"],
                "mime_type": candidate["mime_type"],
                "image_artifact_id": candidate["media_artifact_id"],
            }
            for field, expected_value in expected.items():
                if selection[field] != expected_value:
                    raise AppError("VISUAL_REFERENCE_APPROVAL_SET_INVALID", "Approval selection metadata is inconsistent.", 409, retryable=False, details={"field": field})
        return deepcopy(value)

    @staticmethod
    def _validate_lineage(project: Project, payload: dict[str, Any]) -> None:
        if (
            payload["source_script_revision_id"] != project.script_artifact_id
            or payload["source_script_analysis_revision_id"] != project.script_analysis_artifact_id
            or payload["source_style_bible_revision_id"] != project.style_bible_artifact_id
        ):
            raise AppError("VISUAL_REFERENCE_UPSTREAM_STALE", "Visual-reference lineage is stale.", 409, retryable=False)

    def _validate_candidates(self, project: Project, payload: dict[str, Any]) -> None:
        seen_refs: set[str] = set()
        for subject in payload["subjects"]:
            for candidate in subject["candidates"]:
                ref_id = candidate["reference_artifact_id"]
                if ref_id in seen_refs:
                    raise AppError("VISUAL_REFERENCE_SET_INVALID", "Candidate reference IDs must be unique.", 409, retryable=False)
                seen_refs.add(ref_id)
                reference = self.load_candidate(project, candidate["reference_artifact_id"])
                expected_reference = {
                    "anchor_type": subject["anchor_type"],
                    "anchor_id": subject["anchor_id"],
                    "source_definition_hash": subject["source_definition_hash"],
                    "media_artifact_id": candidate["media_artifact_id"],
                    "media_task_id": candidate["media_task_id"],
                    "candidate_index": candidate["candidate_index"],
                    "mime_type": candidate["mime_type"],
                    "media_sha256": candidate["media_sha256"],
                    "file_url": candidate["file_url"],
                }
                for field, expected in expected_reference.items():
                    if reference[field] != expected:
                        raise AppError("VISUAL_REFERENCE_SET_INVALID", "Candidate metadata is inconsistent with its reference Artifact.", 409, retryable=False, details={"field": field})
                media = self.artifacts.get_required(candidate["media_artifact_id"])
                if media.artifact_type != ArtifactType.IMAGE or media.status != ArtifactStatus.READY or media.deleted_at is not None:
                    raise AppError("VISUAL_REFERENCE_MEDIA_INVALID", "Approved reference media is unavailable.", 409, retryable=False)
                if media.sha256 != candidate["media_sha256"] or media.mime_type != candidate["mime_type"]:
                    raise AppError("VISUAL_REFERENCE_MEDIA_INVALID", "Reference media metadata is inconsistent.", 409, retryable=False)
                if not media.logical_id.startswith(f"project/{project.project_id}/visual_references/"):
                    raise AppError("VISUAL_REFERENCE_MEDIA_INVALID", "Reference media belongs to another project or Visual References namespace.", 409, retryable=False)
                self.artifacts.resolve_file_path(media)

    def approved_bindings(self, project: Project) -> tuple[str | None, list[dict[str, Any]]]:
        approval_id = project.style_bible_visual_reference_approval_set_artifact_id
        if approval_id is None:
            return None, []
        approval = self.load_approval_set(project, approval_id, require_current=True)
        return approval_id, [
            {
                **deepcopy(selection),
                "approval_set_artifact_id": approval_id,
            }
            for selection in approval["selections"]
        ]
