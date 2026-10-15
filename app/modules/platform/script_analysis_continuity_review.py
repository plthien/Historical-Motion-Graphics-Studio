from __future__ import annotations

import hashlib
import json
import logging
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.config import get_settings
from app.db.enums import ArtifactStatus, ArtifactType
from app.modules.platform.artifact_loader import ProjectArtifactLoader
from app.modules.platform.contracts import PlatformContractValidator
from app.modules.platform.invalidation import ProjectPipelineInvalidationService
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.script_analysis_loader import ScriptAnalysisArtifactLoader
from app.modules.v4.script_analysis_validator import ScriptAnalysisSemanticValidator
from app.repositories import ArtifactRepository, ProjectRepository
from app.services.artifacts import ArtifactService, cleanup_staged_artifact_safely, rollback_safely

logger = logging.getLogger(__name__)


SUPPORTED_COLLECTIONS: tuple[tuple[str, str], ...] = (
    ("characters", "char_id"),
    ("locations", "loc_id"),
    ("recurring_props", "prop_id"),
    ("recurring_visual_subjects", "subject_id"),
)


class ScriptAnalysisContinuityReviewService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.settings = get_settings()
        self.projects = ProjectRepository(session)
        self.artifacts_repo = ArtifactRepository(session)
        self.artifacts = ArtifactService(session, self.artifacts_repo, self.settings)
        self.platform_validator = PlatformContractValidator()
        self.script_loader = ProjectArtifactLoader(self.artifacts, self.platform_validator)
        self.analysis_loader = ScriptAnalysisArtifactLoader(
            self.artifacts, V4ContractValidator(), ScriptAnalysisSemanticValidator()
        )
        self.invalidation = ProjectPipelineInvalidationService(session)

    @staticmethod
    def logical_id(project_id: str) -> str:
        return f"project/{project_id}/script_analysis_continuity_review"

    def get(self, project_id: str) -> dict[str, Any]:
        project, _script, analysis = self._load_current_sources(project_id)
        row = self.artifacts_repo.get_current_by_logical_id(self.logical_id(project_id))
        artifact = self._load_review_row(row, project_id=project_id) if row is not None else None
        if artifact is not None and (
            artifact["source_script_artifact_id"] != project.script_artifact_id
            or artifact["source_script_analysis_artifact_id"] != project.script_analysis_artifact_id
        ):
            artifact = None
        removed = list(artifact["removed_continuity_refs"]) if artifact else []
        return {
            "project_id": project_id,
            "script_analysis_artifact_id": str(project.script_analysis_artifact_id),
            "review_artifact_id": artifact["review_revision_id"] if artifact else None,
            "removed_continuity_refs": removed,
            "available_continuity_refs": self._available_refs(analysis),
        }

    def mutate(self, project_id: str, request: dict[str, Any]) -> dict[str, Any]:
        project, _script, analysis = self._load_current_sources(project_id)
        expected_analysis = request.get("expected_script_analysis_artifact_id")
        expected_review = request.get("expected_review_artifact_id")
        continuity_ref = request.get("continuity_ref")
        removed = request.get("removed")
        if expected_analysis != project.script_analysis_artifact_id:
            raise AppError("CONTINUITY_REVIEW_SOURCE_CHANGED", "Script Analysis changed before continuity review update.", 409, retryable=False)
        if not isinstance(continuity_ref, str) or not isinstance(removed, bool):
            raise AppError("CONTINUITY_REVIEW_REQUEST_INVALID", "Continuity review request is invalid.", 422, retryable=False)
        available = set(self._available_refs(analysis))
        if continuity_ref not in available:
            raise AppError("CONTINUITY_REVIEW_REF_UNSUPPORTED", "Continuity reference is not removable for the current Analysis.", 422, retryable=False, details={"continuity_ref": continuity_ref})

        logical_id = self.logical_id(project_id)
        current_row = self.artifacts_repo.get_current_by_logical_id(logical_id)
        current = self._load_review_row(current_row, project_id=project_id) if current_row is not None else None
        cas_current_id = current_row.artifact_id if current_row is not None else None
        current_id = current["review_revision_id"] if current else None
        if current is not None and (
            current["source_script_artifact_id"] != project.script_artifact_id
            or current["source_script_analysis_artifact_id"] != project.script_analysis_artifact_id
        ):
            current = None
            current_id = None
        if expected_review != current_id:
            raise AppError("CONTINUITY_REVIEW_REVISION_CONFLICT", "Continuity review changed before this update.", 409, retryable=False)

        refs = set(current["removed_continuity_refs"] if current else [])
        if removed:
            refs.add(continuity_ref)
        else:
            refs.discard(continuity_ref)
        artifact_id = self.artifacts.preallocate_artifact_id()
        payload = {
            "artifact_type": "script_analysis_continuity_review",
            "schema_version": "1.0",
            "project_id": project_id,
            "review_revision_id": artifact_id,
            "source_script_artifact_id": str(project.script_artifact_id),
            "source_script_analysis_artifact_id": str(project.script_analysis_artifact_id),
            "removed_continuity_refs": sorted(refs),
        }
        self.platform_validator.validate("script_analysis_continuity_review_v1.schema.json", payload, error_code="CONTINUITY_REVIEW_INVALID")
        staged = None
        try:
            staged = self.artifacts.stage_artifact_revision(
                logical_id=logical_id,
                artifact_type=ArtifactType.JSON,
                filename="script_analysis_continuity_review.json",
                content=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                artifact_id=artifact_id,
                provider_source="deterministic",
                content_hash=str(project.script_analysis_artifact_id),
            )
            if project.script_analysis_artifact_id != expected_analysis:
                raise AppError("CONTINUITY_REVIEW_SOURCE_CHANGED", "Script Analysis changed before continuity review publication.", 409, retryable=False)
            if not self.artifacts_repo.compare_and_set_current_artifact(
                logical_id=logical_id,
                new_artifact_id=artifact_id,
                expected_current_artifact_id=cas_current_id,
            ):
                raise AppError("CONTINUITY_REVIEW_REVISION_CONFLICT", "Continuity review changed before publication.", 409, retryable=False)
            # Project pointer updates are published through guarded bulk SQL elsewhere and
            # this session intentionally uses expire_on_commit=False. Refresh before
            # invalidating so stale ORM pointer values cannot accidentally preserve an
            # already-built Style Bible or downstream artifact.
            self.session.refresh(project)
            if project.script_analysis_artifact_id != expected_analysis:
                raise AppError("CONTINUITY_REVIEW_SOURCE_CHANGED", "Script Analysis changed before continuity review invalidation.", 409, retryable=False)
            self.invalidation.invalidate_from_continuity_review(project)
            self.session.commit()
        except Exception:
            rollback_safely(self.session, logger=logger)
            cleanup_staged_artifact_safely(staged, logger=logger)
            raise
        return {
            "project_id": project_id,
            "script_analysis_artifact_id": str(project.script_analysis_artifact_id),
            "review_artifact_id": artifact_id,
            "removed_continuity_refs": sorted(refs),
            "available_continuity_refs": sorted(available),
        }

    def get_effective_analysis(
        self,
        *,
        project_id: str,
        script_analysis_artifact_id: str,
        analysis: dict[str, Any],
    ) -> tuple[dict[str, Any], str | None, list[str]]:
        row = self.artifacts_repo.get_current_by_logical_id(self.logical_id(project_id))
        review = self._load_review_row(row, project_id=project_id) if row is not None else None
        if review is None or review["source_script_analysis_artifact_id"] != script_analysis_artifact_id:
            return deepcopy(analysis), None, []
        removed = list(review["removed_continuity_refs"])
        effective = deepcopy(analysis)
        catalog = effective["continuity_catalog"]
        removed_set = set(removed)
        for collection, id_key in SUPPORTED_COLLECTIONS:
            catalog[collection] = [item for item in catalog[collection] if item[id_key] not in removed_set]
        return effective, review["review_revision_id"], removed

    @staticmethod
    def _available_refs(analysis: dict[str, Any]) -> list[str]:
        refs: list[str] = []
        catalog = analysis["continuity_catalog"]
        for collection, id_key in SUPPORTED_COLLECTIONS:
            refs.extend(str(item[id_key]) for item in catalog[collection])
        return refs

    def _load_current_sources(self, project_id: str):
        project = self.projects.get(project_id)
        if project is None:
            raise AppError("PROJECT_NOT_FOUND", f"Project '{project_id}' was not found.", 404, retryable=False)
        if project.script_artifact_id is None or project.script_analysis_artifact_id is None:
            raise AppError("CONTINUITY_REVIEW_SCRIPT_ANALYSIS_REQUIRED", "Current Script and Script Analysis are required.", 409, retryable=False)
        script = self.script_loader.load_script(project, project.script_artifact_id, require_current=True)
        analysis = self.analysis_loader.load(
            project=project,
            artifact_id=project.script_analysis_artifact_id,
            script_artifact=script,
            expected_script_revision_id=project.script_artifact_id,
            require_current=True,
        )
        return project, script, analysis

    def _load_review_row(self, row, *, project_id: str) -> dict[str, Any]:
        if row.artifact_type != ArtifactType.JSON or row.status != ArtifactStatus.READY or row.deleted_at is not None:
            raise AppError("CONTINUITY_REVIEW_ARTIFACT_INVALID", "Stored continuity review is invalid.", 409, retryable=False)
        try:
            raw = self.artifacts.read_artifact_bytes(row.artifact_id)
            if row.size_bytes is not None and len(raw) != row.size_bytes:
                raise ValueError("size mismatch")
            if row.sha256 is not None and hashlib.sha256(raw).hexdigest() != row.sha256:
                raise ValueError("hash mismatch")
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise AppError("CONTINUITY_REVIEW_ARTIFACT_INVALID", "Stored continuity review is unreadable.", 409, retryable=False) from exc
        self.platform_validator.validate("script_analysis_continuity_review_v1.schema.json", payload, error_code="CONTINUITY_REVIEW_ARTIFACT_INVALID")
        if payload["project_id"] != project_id or payload["review_revision_id"] != row.artifact_id:
            raise AppError("CONTINUITY_REVIEW_ARTIFACT_INVALID", "Stored continuity review identity is invalid.", 409, retryable=False)
        return payload
