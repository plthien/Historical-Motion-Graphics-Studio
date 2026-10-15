from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Project


class ProjectPipelineInvalidationService:
    def __init__(self, session: Session):
        self.session = session

    def invalidate_from_topic_ideas(self, project: Project) -> None:
        project.selected_topic_ideas_artifact_id = None
        project.selected_topic_idea_id = None
        project.script_artifact_id = None
        self._invalidate_after_script(project)

    def invalidate_from_topic_selection(self, project: Project) -> None:
        project.script_artifact_id = None
        self._invalidate_after_script(project)

    def invalidate_from_script(self, project: Project) -> None:
        self._invalidate_after_script(project)

    def invalidate_from_continuity_review(self, project: Project) -> None:
        self._invalidate_after_analysis(project)

    def invalidate_from_voice_approval(self, project: Project) -> None:
        project.scene_plan_artifact_id = None
        project.visual_plan_artifact_id = None
        project.prompt_bundle_artifact_id = None
        project.beat_timing_map_artifact_id = None
        project.scene_asset_manifest_artifact_id = None
        project.assembly_plan_artifact_id = None
        project.subtitle_srt_artifact_id = None
        project.subtitle_vtt_artifact_id = None
        project.final_video_artifact_id = None

    @staticmethod
    def _invalidate_after_script(project: Project) -> None:
        project.script_analysis_artifact_id = None
        ProjectPipelineInvalidationService._invalidate_after_analysis(project)

    @staticmethod
    def _invalidate_after_analysis(project: Project) -> None:
        project.style_bible_artifact_id = None
        project.scene_plan_artifact_id = None
        project.visual_plan_artifact_id = None
        project.prompt_bundle_artifact_id = None
        project.style_bible_visual_reference_set_artifact_id = None
        project.style_bible_visual_reference_approval_set_artifact_id = None
        project.scene_asset_manifest_artifact_id = None
        project.narration_audio_artifact_id = None
        project.narration_word_timing_map_artifact_id = None
        project.beat_timing_map_artifact_id = None
        project.assembly_plan_artifact_id = None
        project.subtitle_srt_artifact_id = None
        project.subtitle_vtt_artifact_id = None
        project.final_video_artifact_id = None
