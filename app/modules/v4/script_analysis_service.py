from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.config import get_settings
from app.modules.platform.artifact_loader import ProjectArtifactLoader
from app.modules.platform.contracts import PlatformContractValidator
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.script_analysis_loader import ScriptAnalysisArtifactLoader
from app.modules.v4.script_analysis_validator import ScriptAnalysisSemanticValidator
from app.repositories import ArtifactRepository, ProjectRepository
from app.services.artifacts import ArtifactService


class ScriptAnalysisService:
    """Read-only access to the current canonical Script Analysis.

    Script Analysis authoring is owned by the external combined Script & Analysis
    import boundary. This service intentionally has no provider execution path.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.settings = get_settings()
        self.projects = ProjectRepository(session)
        self.artifacts = ArtifactService(
            session,
            ArtifactRepository(session),
            self.settings,
        )
        self.script_loader = ProjectArtifactLoader(
            self.artifacts,
            PlatformContractValidator(),
        )
        self.analysis_loader = ScriptAnalysisArtifactLoader(
            self.artifacts,
            V4ContractValidator(),
            ScriptAnalysisSemanticValidator(),
        )

    def get(self, project_id: str) -> dict[str, Any]:
        project = self._get_project_required(project_id)
        artifact_id = project.script_analysis_artifact_id
        if artifact_id is None:
            return {
                "project_id": project_id,
                "generated": False,
                "artifact_id": None,
                "artifact": None,
            }
        if project.script_artifact_id is None:
            raise AppError(
                "SCRIPT_ANALYSIS_SOURCE_SCRIPT_UNAVAILABLE",
                "The current Script Analysis has no active source Script.",
                409,
                retryable=False,
            )
        script = self.script_loader.load_script(
            project,
            project.script_artifact_id,
            require_current=True,
        )
        artifact = self.analysis_loader.load(
            project=project,
            artifact_id=artifact_id,
            script_artifact=script,
            expected_script_revision_id=project.script_artifact_id,
            require_current=True,
        )
        if artifact["source_script_revision_id"] != project.script_artifact_id:
            raise AppError(
                "SCRIPT_ANALYSIS_ARTIFACT_STALE",
                "The current Script Analysis does not match the active Script.",
                409,
                retryable=False,
            )
        return {
            "project_id": project_id,
            "generated": True,
            "artifact_id": artifact_id,
            "artifact": artifact,
        }

    def _get_project_required(self, project_id: str):
        project = self.projects.get(project_id)
        if project is None:
            raise AppError(
                "PROJECT_NOT_FOUND",
                f"Project '{project_id}' was not found.",
                404,
                retryable=False,
            )
        return project
