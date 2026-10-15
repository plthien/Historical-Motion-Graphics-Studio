from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Artifact, Project


class ProjectPipelineInvalidationService:
    """Native V4 downstream invalidation only.

    Upstream Script/Analysis/Style/Scene/Visual/Prompt services own their own
    pointer invalidation. Production consumers must never know about or clear
    Script Analysis lineage.
    """

    def __init__(self, session: Session):
        self.session = session

    def invalidate_from_scene_asset_manifest(self, project: Project) -> None:
        self.invalidate_assembly(project)

    def invalidate_from_voice_output(self, project: Project) -> None:
        self.invalidate_assembly(project)

    def demote_assembly_outputs(self, project: Project) -> None:
        """Remove current Artifact authority before clearing output pointers."""

        for artifact_id in {
            project.assembly_plan_artifact_id,
            project.subtitle_srt_artifact_id,
            project.subtitle_vtt_artifact_id,
            project.final_video_artifact_id,
        }:
            if not artifact_id:
                continue
            artifact = self.session.get(Artifact, artifact_id)
            if artifact is not None:
                artifact.is_current = False
                artifact.superseded_by_artifact_id = None

    def invalidate_assembly(self, project: Project) -> None:
        self.demote_assembly_outputs(project)
        project.assembly_plan_artifact_id = None
        project.subtitle_srt_artifact_id = None
        project.subtitle_vtt_artifact_id = None
        project.final_video_artifact_id = None

    def invalidate_from_assembly(self, project: Project) -> None:
        if project.final_video_artifact_id:
            artifact = self.session.get(Artifact, project.final_video_artifact_id)
            if artifact is not None:
                artifact.is_current = False
                artifact.superseded_by_artifact_id = None
        project.final_video_artifact_id = None
