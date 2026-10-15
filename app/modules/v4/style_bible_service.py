from __future__ import annotations

import json
import logging
from copy import deepcopy
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.config import get_settings
from app.core.time import utc_now_iso
from app.db.enums import ArtifactRole, ArtifactType
from app.db.models import Project
from app.modules.platform.contracts import PlatformContractValidator
from app.modules.platform.artifact_loader import ProjectArtifactLoader
from app.modules.platform.script_analysis_continuity_review import ScriptAnalysisContinuityReviewService
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.script_analysis_loader import ScriptAnalysisArtifactLoader
from app.modules.v4.script_analysis_validator import ScriptAnalysisSemanticValidator
from app.modules.v4.style_bible_draft_builder import StyleBibleDraftBuilderV4, channel_dna_snapshot_sha256
from app.modules.v4.style_bible_input_builder import StyleBibleInputBuilderV4
from app.modules.v4.style_bible_loader import StyleBibleArtifactLoaderV4
from app.modules.v4.style_bible_text_revision import apply_text_approval, apply_text_update
from app.repositories import ArtifactRepository, ProjectRepository, StyleBibleScriptAnalysisCommitGuard
from app.services.artifacts import ArtifactService, StagedArtifact, cleanup_staged_artifact_safely, rollback_safely

logger = logging.getLogger(__name__)


class StyleBibleServiceV4:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.settings = get_settings()
        self.projects = ProjectRepository(session)
        self.artifacts_repo = ArtifactRepository(session)
        self.artifacts = ArtifactService(session, self.artifacts_repo, self.settings)
        self.v3 = PlatformContractValidator()
        self.v4 = V4ContractValidator()
        self.project_loader = ProjectArtifactLoader(self.artifacts, self.v3)
        self.analysis_loader = ScriptAnalysisArtifactLoader(
            self.artifacts,
            self.v4,
            ScriptAnalysisSemanticValidator(),
        )
        self.style_loader = StyleBibleArtifactLoaderV4(self.artifacts)
        self.input_builder = StyleBibleInputBuilderV4()
        self.draft_builder = StyleBibleDraftBuilderV4()
        self.continuity_reviews = ScriptAnalysisContinuityReviewService(session)

    def generate(self, project_id: str, script_analysis_artifact_id: str) -> tuple[dict[str, Any], str]:
        self.v4.validate(
            "style_bible_build_request_v1.schema.json",
            {"script_analysis_artifact_id": script_analysis_artifact_id},
            error_code="STYLE_BIBLE_BUILD_REQUEST_INVALID",
        )
        project = self._project(project_id)
        if project.script_artifact_id is None or project.script_analysis_artifact_id is None:
            raise AppError("STYLE_BIBLE_SCRIPT_ANALYSIS_REQUIRED", "Current Script and Script Analysis are required.", 409, retryable=False)
        if project.script_analysis_artifact_id != script_analysis_artifact_id:
            raise AppError("STYLE_BIBLE_SCRIPT_ANALYSIS_NOT_CURRENT", "The requested Script Analysis is not current.", 409, retryable=False)
        script = self.project_loader.load_script(project, project.script_artifact_id, require_current=True, require_downstream_ready=True)
        analysis = self.analysis_loader.load(
            project=project,
            artifact_id=script_analysis_artifact_id,
            script_artifact=script,
            expected_script_revision_id=project.script_artifact_id,
            require_current=True,
        )
        effective_analysis, continuity_review_artifact_id, removed_continuity_refs = self.continuity_reviews.get_effective_analysis(
            project_id=project_id,
            script_analysis_artifact_id=script_analysis_artifact_id,
            analysis=analysis,
        )
        channel = self.v3.validate("channel_dna.schema.json", deepcopy(project.channel_dna_json), error_code="STYLE_BIBLE_CHANNEL_DNA_INVALID")
        style_input = self.input_builder.build(
            project_id=project_id,
            script_analysis_artifact_id=script_analysis_artifact_id,
            script_analysis_payload=effective_analysis,
        )
        artifact_id = self.artifacts.preallocate_artifact_id()
        payload = self.draft_builder.build(
            project_id=project_id,
            style_bible_revision_id=artifact_id,
            style_bible_input=style_input,
            channel_dna_snapshot=channel,
            channel_dna_snapshot_sha256_value=channel_dna_snapshot_sha256(channel),
            continuity_review_artifact_id=continuity_review_artifact_id,
            removed_continuity_refs=removed_continuity_refs,
        )
        self.style_loader.semantic.validate(
            style_bible=payload,
            script_analysis=analysis,
            channel_dna=channel,
        )
        self.v4.validate("style_bible_build_response_v1.schema.json", {"artifact_id": artifact_id, "artifact": payload}, error_code="STYLE_BIBLE_BUILD_RESPONSE_INVALID")
        guard = StyleBibleScriptAnalysisCommitGuard(
            script_analysis_artifact_id=script_analysis_artifact_id,
            script_artifact_id=project.script_artifact_id,
            channel_dna_json=deepcopy(channel),
            previous_style_bible_artifact_id=project.style_bible_artifact_id,
        )
        return self._publish(project, payload, artifact_id, guard, lambda: AppError("STYLE_BIBLE_SOURCE_CHANGED", "Style Bible sources changed before publication.", 409, retryable=False))

    def get(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        if project.style_bible_artifact_id is None:
            result = {"project_id": project_id, "generated": False, "artifact_id": None, "artifact": None}
        else:
            analysis = self._load_current_analysis(project)
            artifact = self.style_loader.load(project=project, artifact_id=project.style_bible_artifact_id, script_analysis=analysis, require_current=True)
            result = {"project_id": project_id, "generated": True, "artifact_id": project.style_bible_artifact_id, "artifact": artifact}
        return self.v4.validate("style_bible_read_response_v1.schema.json", result, error_code="STYLE_BIBLE_READ_RESPONSE_INVALID")

    def update_text(self, project_id: str, request: dict[str, Any]) -> tuple[dict[str, Any], str]:
        validated = self.v4.validate("style_bible_text_update_request_v1.schema.json", deepcopy(request), error_code="STYLE_BIBLE_TEXT_INVALID")
        project, current, analysis = self._current(project_id, str(validated["expected_style_bible_revision_id"]))
        revision_id = self.artifacts.preallocate_artifact_id()
        revised = apply_text_update(current, validated["editable_content"], revision_id)
        self.v4.validate("project_style_bible_v1.schema.json", revised, error_code="STYLE_BIBLE_TEXT_INVALID")
        self.style_loader.semantic.validate(style_bible=revised, script_analysis=analysis, channel_dna=project.channel_dna_json)
        return self._publish_mutation(project, revised, revision_id, current)

    def approve_text(self, project_id: str, request: dict[str, Any]) -> tuple[dict[str, Any], str]:
        validated = self.v4.validate("style_bible_text_approve_request_v1.schema.json", deepcopy(request), error_code="STYLE_BIBLE_TEXT_INVALID")
        project, current, analysis = self._current(project_id, str(validated["expected_style_bible_revision_id"]))
        revision_id = self.artifacts.preallocate_artifact_id()
        revised = apply_text_approval(current, revision_id)
        self.v4.validate("project_style_bible_v1.schema.json", revised, error_code="STYLE_BIBLE_TEXT_INVALID")
        self.style_loader.semantic.validate(style_bible=revised, script_analysis=analysis, channel_dna=project.channel_dna_json)
        return self._publish_mutation(project, revised, revision_id, current)

    def _current(self, project_id: str, expected: str) -> tuple[Project, dict[str, Any], dict[str, Any]]:
        project = self._project(project_id)
        if project.style_bible_artifact_id != expected:
            raise AppError("STYLE_BIBLE_REVISION_CONFLICT", "The expected Style Bible revision is no longer current.", 409, retryable=False)
        analysis = self._load_current_analysis(project)
        return project, self.style_loader.load(project=project, artifact_id=expected, script_analysis=analysis, require_current=True), analysis

    def _load_current_analysis(self, project: Project) -> dict[str, Any]:
        if project.script_artifact_id is None or project.script_analysis_artifact_id is None:
            raise AppError("STYLE_BIBLE_UPSTREAM_STALE", "Current Script Analysis is unavailable.", 409, retryable=False)
        script = self.project_loader.load_script(project, project.script_artifact_id, require_current=True)
        return self.analysis_loader.load(project=project, artifact_id=project.script_analysis_artifact_id, script_artifact=script, expected_script_revision_id=project.script_artifact_id, require_current=True)

    def _publish_mutation(self, project: Project, payload: dict[str, Any], artifact_id: str, current: dict[str, Any]) -> tuple[dict[str, Any], str]:
        lineage = current["lineage"]
        guard = StyleBibleScriptAnalysisCommitGuard(
            script_analysis_artifact_id=lineage["script_analysis_artifact_id"],
            script_artifact_id=lineage["source_script_artifact_id"],
            channel_dna_json=deepcopy(project.channel_dna_json),
            previous_style_bible_artifact_id=current["style_bible_revision_id"],
        )
        return self._publish(project, payload, artifact_id, guard, lambda: AppError("STYLE_BIBLE_REVISION_CONFLICT", "The Style Bible revision or upstream sources changed.", 409, retryable=False))

    def _publish(self, project: Project, payload: dict[str, Any], artifact_id: str, guard: StyleBibleScriptAnalysisCommitGuard, conflict: Callable[[], AppError]) -> tuple[dict[str, Any], str]:
        staged: StagedArtifact | None = None
        try:
            staged = self.artifacts.stage_artifact_revision(
                logical_id=f"project/{project.project_id}/style_bible",
                artifact_type=ArtifactType.JSON,
                artifact_role=ArtifactRole.PROJECT_STYLE_BIBLE,
                filename="style_bible.json",
                artifact_id=artifact_id,
                content=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                provider_source="deterministic",
                content_hash=guard.script_analysis_artifact_id,
            )
            current_review = self.continuity_reviews.get(project.project_id)
            if current_review["review_artifact_id"] != payload["lineage"]["continuity_review_artifact_id"]:
                raise conflict()
            if not self.projects.compare_and_set_style_bible_v4(project_id=project.project_id, new_style_bible_artifact_id=artifact_id, guard=guard, updated_at=utc_now_iso()):
                raise conflict()
            previous = self.artifacts_repo.get_current_by_logical_id(staged.logical_id)
            if previous is not None and previous.artifact_id != artifact_id:
                previous.is_current = False
                previous.superseded_by_artifact_id = artifact_id
            staged.artifact.is_current = True
            staged.artifact.superseded_by_artifact_id = None
            self.session.flush()
            self.session.commit()
            return payload, artifact_id
        except Exception:
            rollback_safely(self.session, logger=logger)
            cleanup_staged_artifact_safely(staged, logger=logger)
            raise

    def _project(self, project_id: str) -> Project:
        project = self.projects.get(project_id)
        if project is None:
            raise AppError("PROJECT_NOT_FOUND", f"Project '{project_id}' was not found.", 404, retryable=False)
        return project
