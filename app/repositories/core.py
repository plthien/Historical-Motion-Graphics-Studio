from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case as sa_case, delete, desc, func, or_, select, update
from sqlalchemy.orm import Session

from app.db.enums import JobStatus
from app.db.models import Artifact, Channel, Job, MediaBatch, MediaTask, Project, Setting, StyleBibleVisualOperation, SceneAssetOperation, SceneAssetBeatClaim, SceneAssetBeatOperationLease, SceneAssetCandidateReview, VoiceGenerationOperation, VoiceSegmentTask, RenderOperation

MEDIA_TASK_ORPHANED_ERROR_CODE = "MEDIA_TASK_ORPHANED"
ACTIVE_SCENE_ASSET_OPERATION_STATUSES = ("queued", "running", "waiting_for_media", "finalizing")
ACTIVE_VISUAL_REFERENCE_OPERATION_STATUSES = ("queued", "generating")
ACTIVE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)


class RepositoryBase:
    def __init__(self, session: Session) -> None:
        self.session = session


class ChannelRepository(RepositoryBase):
    def create(self, channel: Channel) -> Channel:
        self.session.add(channel)
        self.session.flush()
        return channel

    def get(self, channel_id: str) -> Channel | None:
        return self.session.get(Channel, channel_id)

    def list_all(self, *, offset: int = 0, limit: int = 50) -> list[Channel]:
        stmt = select(Channel).order_by(desc(Channel.created_at)).offset(offset).limit(limit)
        return list(self.session.scalars(stmt))

    def count_all(self) -> int:
        return int(self.session.scalar(select(func.count()).select_from(Channel)) or 0)


@dataclass(frozen=True, slots=True)
class TopicIdeasCommitGuard:
    previous_topic_ideas_artifact_id: str | None


@dataclass(frozen=True, slots=True)
class ScriptGenerationCommitGuard:
    topic_ideas_artifact_id: str
    selected_topic_ideas_artifact_id: str
    selected_topic_idea_id: str
    previous_script_artifact_id: str | None


@dataclass(frozen=True, slots=True)
class ScriptAnalysisPairCommitGuard:
    topic_ideas_artifact_id: str
    selected_topic_ideas_artifact_id: str
    selected_topic_idea_id: str
    previous_script_artifact_id: str | None
    previous_script_analysis_artifact_id: str | None


@dataclass(frozen=True, slots=True)
class ScriptRevisionCommitGuard:
    expected_script_artifact_id: str


@dataclass(frozen=True, slots=True)
class ScriptAnalysisCommitGuard:
    script_artifact_id: str
    previous_script_analysis_artifact_id: str | None






@dataclass(frozen=True, slots=True)
class StyleBibleScriptAnalysisCommitGuard:
    script_analysis_artifact_id: str
    script_artifact_id: str
    channel_dna_json: dict[str, Any]
    previous_style_bible_artifact_id: str | None






@dataclass(frozen=True, slots=True)
class SceneVisualPlanCommitGuard:
    script_artifact_id: str
    script_analysis_artifact_id: str
    style_bible_artifact_id: str
    visual_reference_approval_set_artifact_id: str | None
    narration_audio_artifact_id: str
    narration_word_timing_map_artifact_id: str
    previous_scene_plan_artifact_id: str | None
    previous_visual_plan_artifact_id: str | None


@dataclass(frozen=True, slots=True)
class SceneVisualPromptBundleCommitGuard:
    script_artifact_id: str
    script_analysis_artifact_id: str
    style_bible_artifact_id: str
    visual_reference_approval_set_artifact_id: str | None
    narration_audio_artifact_id: str
    narration_word_timing_map_artifact_id: str
    previous_scene_plan_artifact_id: str
    previous_visual_plan_artifact_id: str
    previous_prompt_bundle_artifact_id: str | None
    previous_beat_timing_map_artifact_id: str | None


@dataclass(frozen=True, slots=True)
class PromptBundleScriptAnalysisCommitGuard:
    script_artifact_id: str
    script_analysis_artifact_id: str
    style_bible_artifact_id: str
    scene_plan_artifact_id: str
    visual_plan_artifact_id: str
    style_bible_visual_reference_approval_set_artifact_id: str | None
    previous_prompt_bundle_artifact_id: str | None


@dataclass(frozen=True, slots=True)
class SceneAssetManifestCommitGuard:
    script_artifact_id: str
    script_analysis_artifact_id: str
    style_bible_artifact_id: str
    scene_plan_artifact_id: str
    visual_plan_artifact_id: str
    prompt_bundle_artifact_id: str
    visual_reference_approval_set_artifact_id: str | None
    previous_scene_asset_manifest_artifact_id: str | None


@dataclass(frozen=True, slots=True)
class AssemblyOutputSetCommitGuard:
    script_artifact_id: str
    scene_plan_artifact_id: str
    scene_asset_manifest_artifact_id: str
    narration_audio_artifact_id: str
    narration_word_timing_map_artifact_id: str
    beat_timing_map_artifact_id: str
    previous_assembly_plan_artifact_id: str | None
    previous_subtitle_srt_artifact_id: str | None
    previous_subtitle_vtt_artifact_id: str | None


@dataclass(frozen=True, slots=True)
class FinalVideoCommitGuard:
    assembly_plan_artifact_id: str
    subtitle_srt_artifact_id: str
    subtitle_vtt_artifact_id: str
    previous_final_video_artifact_id: str | None


@dataclass(frozen=True, slots=True)
class VoiceOutputPairCommitGuard:
    script_artifact_id: str
    previous_narration_audio_artifact_id: str | None
    previous_narration_word_timing_map_artifact_id: str | None


@dataclass(frozen=True, slots=True)
class VoiceRealignmentCommitGuard:
    script_artifact_id: str
    narration_audio_artifact_id: str
    previous_narration_word_timing_map_artifact_id: str
    scene_plan_artifact_id: str | None
    visual_plan_artifact_id: str | None
    prompt_bundle_artifact_id: str | None
    scene_asset_manifest_artifact_id: str | None
    previous_beat_timing_map_artifact_id: str | None
    previous_assembly_plan_artifact_id: str | None
    previous_subtitle_srt_artifact_id: str | None
    previous_subtitle_vtt_artifact_id: str | None
    previous_final_video_artifact_id: str | None


@dataclass(frozen=True, slots=True)
class StyleBibleVisualReferenceSetCommitGuard:
    script_artifact_id: str
    script_analysis_artifact_id: str
    style_bible_artifact_id: str
    previous_visual_reference_set_artifact_id: str | None


@dataclass(frozen=True, slots=True)
class StyleBibleVisualReferenceApprovalSetCommitGuard:
    script_artifact_id: str
    script_analysis_artifact_id: str
    style_bible_artifact_id: str
    visual_reference_set_artifact_id: str
    previous_approval_set_artifact_id: str | None


@dataclass(frozen=True, slots=True)
class ProviderPollSnapshot:
    media_task_id: str
    claim_token: str
    poll_revision: int
    observed_status: str
    external_task_id: str
    provider_submitted_at: str
    poll_claimed_at: str
    poll_claim_expires_at: str


@dataclass(frozen=True, slots=True)
class DownloadOwnerSnapshot:
    media_task_id: str
    batch_id: str
    purpose: str
    project_id: str | None
    external_task_id: str
    provider_submitted_at: str
    claimed_downloading_at: str
    style_bible_revision_id: str | None
    domain_revision_id: str | None
    storage_namespace: str | None
    entity_type: str
    entity_id: str


@dataclass(frozen=True, slots=True)
class ActiveMediaBatchSnapshot:
    batch_id: str
    purpose: str
    total: int
    active_task_count: int
    created_at: str
    updated_at: str


class ProjectRepository(RepositoryBase):
    def create(self, project: Project) -> Project:
        self.session.add(project)
        self.session.flush()
        return project

    def get(self, project_id: str) -> Project | None:
        return self.session.get(Project, project_id)

    def list_all(self, *, channel_id: str | None = None, offset: int = 0, limit: int = 50) -> list[Project]:
        stmt = select(Project)
        if channel_id is not None:
            stmt = stmt.where(Project.channel_id == channel_id)
        stmt = stmt.order_by(desc(Project.created_at)).offset(offset).limit(limit)
        return list(self.session.scalars(stmt))

    def count_all(self, *, channel_id: str | None = None) -> int:
        stmt = select(func.count()).select_from(Project)
        if channel_id is not None:
            stmt = stmt.where(Project.channel_id == channel_id)
        return int(self.session.scalar(stmt) or 0)

    def compare_and_set_topic_ideas(
        self,
        *,
        project_id: str,
        new_topic_ideas_artifact_id: str,
        guard: TopicIdeasCommitGuard,
        updated_at: str,
    ) -> bool:
        stmt = update(Project).where(Project.project_id == project_id)
        if guard.previous_topic_ideas_artifact_id is None:
            stmt = stmt.where(Project.topic_ideas_artifact_id.is_(None))
        else:
            stmt = stmt.where(Project.topic_ideas_artifact_id == guard.previous_topic_ideas_artifact_id)
        stmt = stmt.values(
            topic_ideas_artifact_id=new_topic_ideas_artifact_id,
            selected_topic_ideas_artifact_id=None,
            selected_topic_idea_id=None,
            script_artifact_id=None,
            script_analysis_artifact_id=None,
            style_bible_artifact_id=None,
            scene_plan_artifact_id=None,
            visual_plan_artifact_id=None,
            prompt_bundle_artifact_id=None,
            style_bible_visual_reference_set_artifact_id=None,
            style_bible_visual_reference_approval_set_artifact_id=None,
            scene_asset_manifest_artifact_id=None,
            narration_audio_artifact_id=None,
            narration_word_timing_map_artifact_id=None,
            assembly_plan_artifact_id=None,
            subtitle_srt_artifact_id=None,
            subtitle_vtt_artifact_id=None,
            final_video_artifact_id=None,
            updated_at=updated_at,
        )
        return self.session.execute(stmt, execution_options={"synchronize_session": False}).rowcount == 1

    def compare_and_set_generated_script(
        self,
        *,
        project_id: str,
        new_script_artifact_id: str,
        guard: ScriptGenerationCommitGuard,
        updated_at: str,
    ) -> bool:
        stmt = update(Project).where(
            Project.project_id == project_id,
            Project.topic_ideas_artifact_id == guard.topic_ideas_artifact_id,
            Project.selected_topic_ideas_artifact_id == guard.selected_topic_ideas_artifact_id,
            Project.selected_topic_idea_id == guard.selected_topic_idea_id,
        )
        if guard.previous_script_artifact_id is None:
            stmt = stmt.where(Project.script_artifact_id.is_(None))
        else:
            stmt = stmt.where(Project.script_artifact_id == guard.previous_script_artifact_id)
        
        stmt = stmt.values(
            script_artifact_id=new_script_artifact_id,
            script_analysis_artifact_id=None,
            style_bible_artifact_id=None,
            scene_plan_artifact_id=None,
            visual_plan_artifact_id=None,
            prompt_bundle_artifact_id=None,
                scene_asset_manifest_artifact_id=None,
            narration_audio_artifact_id=None,
            narration_word_timing_map_artifact_id=None,
            assembly_plan_artifact_id=None,
            subtitle_srt_artifact_id=None,
            subtitle_vtt_artifact_id=None,
            final_video_artifact_id=None,
            style_bible_visual_reference_set_artifact_id=None,
            style_bible_visual_reference_approval_set_artifact_id=None,
            updated_at=updated_at,
        )
        return self.session.execute(stmt, execution_options={"synchronize_session": False}).rowcount == 1

    def compare_and_set_script_analysis_pair(
        self,
        *,
        project_id: str,
        new_script_artifact_id: str,
        new_script_analysis_artifact_id: str,
        guard: ScriptAnalysisPairCommitGuard,
        updated_at: str,
    ) -> bool:
        stmt = update(Project).where(
            Project.project_id == project_id,
            Project.topic_ideas_artifact_id == guard.topic_ideas_artifact_id,
            Project.selected_topic_ideas_artifact_id == guard.selected_topic_ideas_artifact_id,
            Project.selected_topic_idea_id == guard.selected_topic_idea_id,
        )
        if guard.previous_script_artifact_id is None:
            stmt = stmt.where(Project.script_artifact_id.is_(None))
        else:
            stmt = stmt.where(Project.script_artifact_id == guard.previous_script_artifact_id)
        if guard.previous_script_analysis_artifact_id is None:
            stmt = stmt.where(Project.script_analysis_artifact_id.is_(None))
        else:
            stmt = stmt.where(Project.script_analysis_artifact_id == guard.previous_script_analysis_artifact_id)
        stmt = stmt.values(
            script_artifact_id=new_script_artifact_id,
            script_analysis_artifact_id=new_script_analysis_artifact_id,
            style_bible_artifact_id=None,
            scene_plan_artifact_id=None,
            visual_plan_artifact_id=None,
            prompt_bundle_artifact_id=None,
            style_bible_visual_reference_set_artifact_id=None,
            style_bible_visual_reference_approval_set_artifact_id=None,
            scene_asset_manifest_artifact_id=None,
            narration_audio_artifact_id=None,
            narration_word_timing_map_artifact_id=None,
            assembly_plan_artifact_id=None,
            subtitle_srt_artifact_id=None,
            subtitle_vtt_artifact_id=None,
            final_video_artifact_id=None,
            updated_at=updated_at,
        )
        return self.session.execute(stmt, execution_options={"synchronize_session": False}).rowcount == 1

    def compare_and_set_script_revision(
        self,
        *,
        project_id: str,
        new_script_artifact_id: str,
        guard: ScriptRevisionCommitGuard,
        updated_at: str,
    ) -> bool:
        stmt = update(Project).where(
            Project.project_id == project_id,
            Project.script_artifact_id == guard.expected_script_artifact_id,
        )
        stmt = stmt.values(
            script_artifact_id=new_script_artifact_id,
            script_analysis_artifact_id=None,
            style_bible_artifact_id=None,
            scene_plan_artifact_id=None,
            visual_plan_artifact_id=None,
            prompt_bundle_artifact_id=None,
            scene_asset_manifest_artifact_id=None,
            narration_audio_artifact_id=None,
            narration_word_timing_map_artifact_id=None,
            assembly_plan_artifact_id=None,
            subtitle_srt_artifact_id=None,
            subtitle_vtt_artifact_id=None,
            final_video_artifact_id=None,
            style_bible_visual_reference_set_artifact_id=None,
            style_bible_visual_reference_approval_set_artifact_id=None,
            updated_at=updated_at,
        )
        return self.session.execute(stmt, execution_options={"synchronize_session": False}).rowcount == 1

    def compare_and_set_script_analysis(
        self,
        *,
        project_id: str,
        new_script_analysis_artifact_id: str,
        guard: ScriptAnalysisCommitGuard,
        updated_at: str,
    ) -> bool:
        stmt = update(Project).where(
            Project.project_id == project_id,
            Project.script_artifact_id == guard.script_artifact_id,
        )
        if guard.previous_script_analysis_artifact_id is None:
            stmt = stmt.where(Project.script_analysis_artifact_id.is_(None))
        else:
            stmt = stmt.where(
                Project.script_analysis_artifact_id
                == guard.previous_script_analysis_artifact_id
            )
        stmt = stmt.values(
            script_analysis_artifact_id=new_script_analysis_artifact_id,
            style_bible_artifact_id=None,
            scene_plan_artifact_id=None,
            visual_plan_artifact_id=None,
            prompt_bundle_artifact_id=None,
            scene_asset_manifest_artifact_id=None,
            narration_audio_artifact_id=None,
            narration_word_timing_map_artifact_id=None,
            assembly_plan_artifact_id=None,
            subtitle_srt_artifact_id=None,
            subtitle_vtt_artifact_id=None,
            final_video_artifact_id=None,
            style_bible_visual_reference_set_artifact_id=None,
            style_bible_visual_reference_approval_set_artifact_id=None,
            updated_at=updated_at,
        )
        return (
            self.session.execute(
                stmt, execution_options={"synchronize_session": False}
            ).rowcount
            == 1
        )


    def compare_and_set_style_bible_v4(
        self,
        *,
        project_id: str,
        new_style_bible_artifact_id: str,
        guard: StyleBibleScriptAnalysisCommitGuard,
        updated_at: str,
    ) -> bool:
        stmt = update(Project).where(
            Project.project_id == project_id,
            Project.script_analysis_artifact_id == guard.script_analysis_artifact_id,
            Project.script_artifact_id == guard.script_artifact_id,
            Project.channel_dna_json == guard.channel_dna_json,
        )
        if guard.previous_style_bible_artifact_id is None:
            stmt = stmt.where(Project.style_bible_artifact_id.is_(None))
        else:
            stmt = stmt.where(Project.style_bible_artifact_id == guard.previous_style_bible_artifact_id)
        stmt = stmt.values(
            style_bible_artifact_id=new_style_bible_artifact_id,
            scene_plan_artifact_id=None,
            visual_plan_artifact_id=None,
            prompt_bundle_artifact_id=None,
            scene_asset_manifest_artifact_id=None,
            beat_timing_map_artifact_id=None,
            assembly_plan_artifact_id=None,
            subtitle_srt_artifact_id=None,
            subtitle_vtt_artifact_id=None,
            final_video_artifact_id=None,
            style_bible_visual_reference_set_artifact_id=None,
            style_bible_visual_reference_approval_set_artifact_id=None,
            updated_at=updated_at,
        )
        return self.session.execute(stmt, execution_options={"synchronize_session": False}).rowcount == 1


    def compare_and_set_scene_visual_plan_v4(
        self,
        *,
        project_id: str,
        new_scene_plan_artifact_id: str,
        new_visual_plan_artifact_id: str,
        guard: SceneVisualPlanCommitGuard,
        updated_at: str,
    ) -> bool:
        stmt = update(Project).where(
            Project.project_id == project_id,
            Project.script_artifact_id == guard.script_artifact_id,
            Project.script_analysis_artifact_id == guard.script_analysis_artifact_id,
            Project.style_bible_artifact_id == guard.style_bible_artifact_id,
            Project.narration_audio_artifact_id == guard.narration_audio_artifact_id,
            Project.narration_word_timing_map_artifact_id == guard.narration_word_timing_map_artifact_id,
        )
        if guard.visual_reference_approval_set_artifact_id is None:
            stmt = stmt.where(Project.style_bible_visual_reference_approval_set_artifact_id.is_(None))
        else:
            stmt = stmt.where(
                Project.style_bible_visual_reference_approval_set_artifact_id
                == guard.visual_reference_approval_set_artifact_id
            )
        if guard.previous_scene_plan_artifact_id is None:
            stmt = stmt.where(Project.scene_plan_artifact_id.is_(None))
        else:
            stmt = stmt.where(Project.scene_plan_artifact_id == guard.previous_scene_plan_artifact_id)
        if guard.previous_visual_plan_artifact_id is None:
            stmt = stmt.where(Project.visual_plan_artifact_id.is_(None))
        else:
            stmt = stmt.where(Project.visual_plan_artifact_id == guard.previous_visual_plan_artifact_id)
        stmt = stmt.values(
            scene_plan_artifact_id=new_scene_plan_artifact_id,
            visual_plan_artifact_id=new_visual_plan_artifact_id,
            prompt_bundle_artifact_id=None,
            scene_asset_manifest_artifact_id=None,
            beat_timing_map_artifact_id=None,
            assembly_plan_artifact_id=None,
            subtitle_srt_artifact_id=None,
            subtitle_vtt_artifact_id=None,
            final_video_artifact_id=None,
            updated_at=updated_at,
        )
        return self.session.execute(
            stmt,
            execution_options={"synchronize_session": False},
        ).rowcount == 1


    def compare_and_set_scene_visual_prompt_bundle_v4(
        self,
        *,
        project_id: str,
        new_scene_plan_artifact_id: str,
        new_visual_plan_artifact_id: str,
        new_prompt_bundle_artifact_id: str,
        new_beat_timing_map_artifact_id: str,
        guard: SceneVisualPromptBundleCommitGuard,
        updated_at: str,
    ) -> bool:
        stmt = update(Project).where(
            Project.project_id == project_id,
            Project.script_artifact_id == guard.script_artifact_id,
            Project.script_analysis_artifact_id == guard.script_analysis_artifact_id,
            Project.style_bible_artifact_id == guard.style_bible_artifact_id,
            Project.narration_audio_artifact_id == guard.narration_audio_artifact_id,
            Project.narration_word_timing_map_artifact_id == guard.narration_word_timing_map_artifact_id,
            Project.scene_plan_artifact_id == guard.previous_scene_plan_artifact_id,
            Project.visual_plan_artifact_id == guard.previous_visual_plan_artifact_id,
        )
        if guard.visual_reference_approval_set_artifact_id is None:
            stmt = stmt.where(Project.style_bible_visual_reference_approval_set_artifact_id.is_(None))
        else:
            stmt = stmt.where(Project.style_bible_visual_reference_approval_set_artifact_id == guard.visual_reference_approval_set_artifact_id)
        if guard.previous_prompt_bundle_artifact_id is None:
            stmt = stmt.where(Project.prompt_bundle_artifact_id.is_(None))
        else:
            stmt = stmt.where(Project.prompt_bundle_artifact_id == guard.previous_prompt_bundle_artifact_id)
        if guard.previous_beat_timing_map_artifact_id is None:
            stmt = stmt.where(Project.beat_timing_map_artifact_id.is_(None))
        else:
            stmt = stmt.where(Project.beat_timing_map_artifact_id == guard.previous_beat_timing_map_artifact_id)
        stmt = stmt.values(
            scene_plan_artifact_id=new_scene_plan_artifact_id,
            visual_plan_artifact_id=new_visual_plan_artifact_id,
            prompt_bundle_artifact_id=new_prompt_bundle_artifact_id,
            beat_timing_map_artifact_id=new_beat_timing_map_artifact_id,
            scene_asset_manifest_artifact_id=None,
            assembly_plan_artifact_id=None,
            subtitle_srt_artifact_id=None,
            subtitle_vtt_artifact_id=None,
            final_video_artifact_id=None,
            updated_at=updated_at,
        )
        return self.session.execute(stmt, execution_options={"synchronize_session": False}).rowcount == 1


    def compare_and_set_prompt_bundle_v4(
        self,
        *,
        project_id: str,
        new_prompt_bundle_artifact_id: str,
        guard: PromptBundleScriptAnalysisCommitGuard,
        updated_at: str,
        preserve_scene_asset_manifest: bool = False,
    ) -> bool:
        stmt = update(Project).where(
            Project.project_id == project_id,
            Project.script_artifact_id == guard.script_artifact_id,
            Project.script_analysis_artifact_id == guard.script_analysis_artifact_id,
            Project.style_bible_artifact_id == guard.style_bible_artifact_id,
            Project.scene_plan_artifact_id == guard.scene_plan_artifact_id,
            Project.visual_plan_artifact_id == guard.visual_plan_artifact_id,
        )
        if guard.style_bible_visual_reference_approval_set_artifact_id is None:
            stmt = stmt.where(Project.style_bible_visual_reference_approval_set_artifact_id.is_(None))
        else:
            stmt = stmt.where(Project.style_bible_visual_reference_approval_set_artifact_id == guard.style_bible_visual_reference_approval_set_artifact_id)
        if guard.previous_prompt_bundle_artifact_id is None:
            stmt = stmt.where(Project.prompt_bundle_artifact_id.is_(None))
        else:
            stmt = stmt.where(Project.prompt_bundle_artifact_id == guard.previous_prompt_bundle_artifact_id)
        values: dict[str, object] = {
            "prompt_bundle_artifact_id": new_prompt_bundle_artifact_id,
            "updated_at": updated_at,
        }
        if not preserve_scene_asset_manifest:
            values["scene_asset_manifest_artifact_id"] = None
        stmt = stmt.values(**values)
        return self.session.execute(stmt, execution_options={"synchronize_session": False}).rowcount == 1

    def compare_and_set_style_bible_visual_reference_set(
        self,
        *,
        project_id: str,
        new_visual_reference_set_artifact_id: str,
        guard: StyleBibleVisualReferenceSetCommitGuard,
        updated_at: str,
        invalidate_approval_and_downstream: bool = False,
    ) -> bool:
        stmt = update(Project).where(
            Project.project_id == project_id,
            Project.script_artifact_id == guard.script_artifact_id,
            Project.script_analysis_artifact_id == guard.script_analysis_artifact_id,
            Project.style_bible_artifact_id == guard.style_bible_artifact_id,
        )
        if guard.previous_visual_reference_set_artifact_id is None:
            stmt = stmt.where(Project.style_bible_visual_reference_set_artifact_id.is_(None))
        else:
            stmt = stmt.where(
                Project.style_bible_visual_reference_set_artifact_id
                == guard.previous_visual_reference_set_artifact_id
            )
        values = {
            "style_bible_visual_reference_set_artifact_id": new_visual_reference_set_artifact_id,
            "updated_at": updated_at,
        }
        if invalidate_approval_and_downstream:
            values.update(
                style_bible_visual_reference_approval_set_artifact_id=None,
                scene_plan_artifact_id=None,
                visual_plan_artifact_id=None,
                prompt_bundle_artifact_id=None,
                scene_asset_manifest_artifact_id=None,
                beat_timing_map_artifact_id=None,
                assembly_plan_artifact_id=None,
                subtitle_srt_artifact_id=None,
                subtitle_vtt_artifact_id=None,
                final_video_artifact_id=None,
            )
        stmt = stmt.values(**values)
        return self.session.execute(stmt, execution_options={"synchronize_session": False}).rowcount == 1

    def compare_and_set_style_bible_visual_reference_set_with_carried_approval(
        self,
        *,
        project_id: str,
        new_visual_reference_set_artifact_id: str,
        new_approval_set_artifact_id: str | None,
        previous_approval_set_artifact_id: str | None,
        guard: StyleBibleVisualReferenceSetCommitGuard,
        updated_at: str,
    ) -> bool:
        stmt = update(Project).where(
            Project.project_id == project_id,
            Project.script_artifact_id == guard.script_artifact_id,
            Project.script_analysis_artifact_id == guard.script_analysis_artifact_id,
            Project.style_bible_artifact_id == guard.style_bible_artifact_id,
        )
        if guard.previous_visual_reference_set_artifact_id is None:
            stmt = stmt.where(Project.style_bible_visual_reference_set_artifact_id.is_(None))
        else:
            stmt = stmt.where(
                Project.style_bible_visual_reference_set_artifact_id
                == guard.previous_visual_reference_set_artifact_id
            )
        if previous_approval_set_artifact_id is None:
            stmt = stmt.where(Project.style_bible_visual_reference_approval_set_artifact_id.is_(None))
        else:
            stmt = stmt.where(
                Project.style_bible_visual_reference_approval_set_artifact_id
                == previous_approval_set_artifact_id
            )
        stmt = stmt.values(
            style_bible_visual_reference_set_artifact_id=new_visual_reference_set_artifact_id,
            style_bible_visual_reference_approval_set_artifact_id=new_approval_set_artifact_id,
            scene_plan_artifact_id=None,
            visual_plan_artifact_id=None,
            prompt_bundle_artifact_id=None,
            scene_asset_manifest_artifact_id=None,
            beat_timing_map_artifact_id=None,
            assembly_plan_artifact_id=None,
            subtitle_srt_artifact_id=None,
            subtitle_vtt_artifact_id=None,
            final_video_artifact_id=None,
            updated_at=updated_at,
        )
        return self.session.execute(
            stmt,
            execution_options={"synchronize_session": False},
        ).rowcount == 1

    def compare_and_set_style_bible_visual_reference_approval_set(
        self,
        *,
        project_id: str,
        new_approval_set_artifact_id: str,
        guard: StyleBibleVisualReferenceApprovalSetCommitGuard,
        updated_at: str,
    ) -> bool:
        stmt = update(Project).where(
            Project.project_id == project_id,
            Project.script_artifact_id == guard.script_artifact_id,
            Project.script_analysis_artifact_id == guard.script_analysis_artifact_id,
            Project.style_bible_artifact_id == guard.style_bible_artifact_id,
            Project.style_bible_visual_reference_set_artifact_id
            == guard.visual_reference_set_artifact_id,
        )
        if guard.previous_approval_set_artifact_id is None:
            stmt = stmt.where(
                Project.style_bible_visual_reference_approval_set_artifact_id.is_(None)
            )
        else:
            stmt = stmt.where(
                Project.style_bible_visual_reference_approval_set_artifact_id
                == guard.previous_approval_set_artifact_id
            )
        stmt = stmt.values(
            style_bible_visual_reference_approval_set_artifact_id=(
                new_approval_set_artifact_id
            ),
            scene_plan_artifact_id=None,
            visual_plan_artifact_id=None,
            prompt_bundle_artifact_id=None,
            scene_asset_manifest_artifact_id=None,
            beat_timing_map_artifact_id=None,
            assembly_plan_artifact_id=None,
            subtitle_srt_artifact_id=None,
            subtitle_vtt_artifact_id=None,
            final_video_artifact_id=None,
            updated_at=updated_at,
        )
        return (
            self.session.execute(
                stmt, execution_options={"synchronize_session": False}
            ).rowcount
            == 1
        )

    def compare_and_set_scene_asset_manifest(
        self,
        *,
        project_id: str,
        new_scene_asset_manifest_artifact_id: str,
        guard: SceneAssetManifestCommitGuard,
        updated_at: str,
    ) -> bool:
        stmt = update(Project).where(
            Project.project_id == project_id,
            Project.script_artifact_id == guard.script_artifact_id,
            Project.script_analysis_artifact_id == guard.script_analysis_artifact_id,
            Project.style_bible_artifact_id == guard.style_bible_artifact_id,
            Project.scene_plan_artifact_id == guard.scene_plan_artifact_id,
            Project.visual_plan_artifact_id == guard.visual_plan_artifact_id,
        )
        if guard.visual_reference_approval_set_artifact_id is None:
            stmt = stmt.where(Project.style_bible_visual_reference_approval_set_artifact_id.is_(None))
        else:
            stmt = stmt.where(
                Project.style_bible_visual_reference_approval_set_artifact_id
                == guard.visual_reference_approval_set_artifact_id
            )
        if guard.previous_scene_asset_manifest_artifact_id is None:
            stmt = stmt.where(Project.scene_asset_manifest_artifact_id.is_(None))
        else:
            stmt = stmt.where(
                Project.scene_asset_manifest_artifact_id
                == guard.previous_scene_asset_manifest_artifact_id
            )
        stmt = stmt.values(
            scene_asset_manifest_artifact_id=new_scene_asset_manifest_artifact_id,
            updated_at=updated_at,
        )
        return self.session.execute(stmt, execution_options={"synchronize_session": False}).rowcount == 1


    def compare_and_set_voice_output_pair(
        self,
        *,
        project_id: str,
        new_narration_audio_artifact_id: str,
        new_narration_word_timing_map_artifact_id: str,
        guard: VoiceOutputPairCommitGuard,
        updated_at: str,
    ) -> bool:
        stmt = update(Project).where(
            Project.project_id == project_id,
            Project.script_artifact_id == guard.script_artifact_id,
        )
        if guard.previous_narration_audio_artifact_id is None:
            stmt = stmt.where(Project.narration_audio_artifact_id.is_(None))
        else:
            stmt = stmt.where(Project.narration_audio_artifact_id == guard.previous_narration_audio_artifact_id)
        if guard.previous_narration_word_timing_map_artifact_id is None:
            stmt = stmt.where(Project.narration_word_timing_map_artifact_id.is_(None))
        else:
            stmt = stmt.where(Project.narration_word_timing_map_artifact_id == guard.previous_narration_word_timing_map_artifact_id)
        stmt = stmt.values(
            narration_audio_artifact_id=new_narration_audio_artifact_id,
            narration_word_timing_map_artifact_id=new_narration_word_timing_map_artifact_id,
            updated_at=updated_at,
        )
        return self.session.execute(stmt, execution_options={"synchronize_session": False}).rowcount == 1

    def compare_and_set_voice_realignment(
        self,
        *,
        project_id: str,
        new_narration_word_timing_map_artifact_id: str,
        new_beat_timing_map_artifact_id: str | None,
        guard: VoiceRealignmentCommitGuard,
        updated_at: str,
    ) -> bool:
        """Publish timing-only repair while preserving every visual authority."""
        stmt = update(Project).where(
            Project.project_id == project_id,
            Project.script_artifact_id == guard.script_artifact_id,
            Project.narration_audio_artifact_id == guard.narration_audio_artifact_id,
            Project.narration_word_timing_map_artifact_id
            == guard.previous_narration_word_timing_map_artifact_id,
        )
        for column, expected in (
            (Project.scene_plan_artifact_id, guard.scene_plan_artifact_id),
            (Project.visual_plan_artifact_id, guard.visual_plan_artifact_id),
            (Project.prompt_bundle_artifact_id, guard.prompt_bundle_artifact_id),
            (Project.scene_asset_manifest_artifact_id, guard.scene_asset_manifest_artifact_id),
            (Project.beat_timing_map_artifact_id, guard.previous_beat_timing_map_artifact_id),
            (Project.assembly_plan_artifact_id, guard.previous_assembly_plan_artifact_id),
            (Project.subtitle_srt_artifact_id, guard.previous_subtitle_srt_artifact_id),
            (Project.subtitle_vtt_artifact_id, guard.previous_subtitle_vtt_artifact_id),
            (Project.final_video_artifact_id, guard.previous_final_video_artifact_id),
        ):
            stmt = stmt.where(column.is_(None) if expected is None else column == expected)
        stmt = stmt.values(
            narration_word_timing_map_artifact_id=new_narration_word_timing_map_artifact_id,
            beat_timing_map_artifact_id=new_beat_timing_map_artifact_id,
            assembly_plan_artifact_id=None,
            subtitle_srt_artifact_id=None,
            subtitle_vtt_artifact_id=None,
            final_video_artifact_id=None,
            updated_at=updated_at,
        )
        return self.session.execute(
            stmt,
            execution_options={"synchronize_session": False},
        ).rowcount == 1

    def compare_and_set_assembly_output_set(
        self,
        *,
        project_id: str,
        new_assembly_plan_artifact_id: str,
        new_subtitle_srt_artifact_id: str,
        new_subtitle_vtt_artifact_id: str,
        guard: AssemblyOutputSetCommitGuard,
        updated_at: str,
    ) -> bool:
        stmt = update(Project).where(
            Project.project_id == project_id,
            Project.script_artifact_id == guard.script_artifact_id,
            Project.scene_plan_artifact_id == guard.scene_plan_artifact_id,
            Project.scene_asset_manifest_artifact_id == guard.scene_asset_manifest_artifact_id,
            Project.narration_audio_artifact_id == guard.narration_audio_artifact_id,
                Project.narration_word_timing_map_artifact_id == guard.narration_word_timing_map_artifact_id,
            Project.beat_timing_map_artifact_id == guard.beat_timing_map_artifact_id,
        )
        for column, expected in (
            (Project.assembly_plan_artifact_id, guard.previous_assembly_plan_artifact_id),
            (Project.subtitle_srt_artifact_id, guard.previous_subtitle_srt_artifact_id),
            (Project.subtitle_vtt_artifact_id, guard.previous_subtitle_vtt_artifact_id),
        ):
            stmt = stmt.where(column.is_(None) if expected is None else column == expected)
        stmt = stmt.values(
            assembly_plan_artifact_id=new_assembly_plan_artifact_id,
            subtitle_srt_artifact_id=new_subtitle_srt_artifact_id,
            subtitle_vtt_artifact_id=new_subtitle_vtt_artifact_id,
            final_video_artifact_id=None,
            updated_at=updated_at,
        )
        return self.session.execute(stmt, execution_options={"synchronize_session": False}).rowcount == 1

    def compare_and_set_final_video(
        self,
        *,
        project_id: str,
        new_final_video_artifact_id: str,
        guard: FinalVideoCommitGuard,
        updated_at: str,
    ) -> bool:
        stmt = update(Project).where(
            Project.project_id == project_id,
            Project.assembly_plan_artifact_id == guard.assembly_plan_artifact_id,
            Project.subtitle_srt_artifact_id == guard.subtitle_srt_artifact_id,
            Project.subtitle_vtt_artifact_id == guard.subtitle_vtt_artifact_id,
        )
        if guard.previous_final_video_artifact_id is None:
            stmt = stmt.where(Project.final_video_artifact_id.is_(None))
        else:
            stmt = stmt.where(Project.final_video_artifact_id == guard.previous_final_video_artifact_id)
        stmt = stmt.values(final_video_artifact_id=new_final_video_artifact_id, updated_at=updated_at)
        return self.session.execute(stmt, execution_options={"synchronize_session": False}).rowcount == 1

    def clear_style_bible_visual_reference_set_if_current(
        self,
        *,
        project_id: str,
        expected_visual_reference_set_artifact_id: str,
        updated_at: str,
    ) -> bool:
        """Invalidate a corrupt current workspace without deleting immutable Artifacts."""
        stmt = (
            update(Project)
            .where(
                Project.project_id == project_id,
                Project.style_bible_visual_reference_set_artifact_id
                == expected_visual_reference_set_artifact_id,
            )
            .values(
                style_bible_visual_reference_set_artifact_id=None,
                updated_at=updated_at,
            )
        )
        return (
            self.session.execute(
                stmt, execution_options={"synchronize_session": False}
            ).rowcount
            == 1
        )


class JobRepository(RepositoryBase):
    def create(self, job: Job) -> Job:
        self.session.add(job)
        self.session.flush()
        return job

    def get(self, job_id: str) -> Job | None:
        return self.session.get(Job, job_id)

    def list_all(self, *, status: str | None = None, job_type: str | None = None, sort: str = "created_at_desc", offset: int = 0, limit: int = 20) -> list[Job]:
        stmt = select(Job)
        if status is not None:
            stmt = stmt.where(Job.status == status)
        if job_type is not None:
            stmt = stmt.where(Job.job_type == job_type)
        stmt = stmt.order_by(desc(Job.created_at) if sort == "created_at_desc" else Job.created_at)
        return list(self.session.scalars(stmt.offset(offset).limit(limit)))

    def count_all(self, *, status: str | None = None, job_type: str | None = None) -> int:
        stmt = select(func.count()).select_from(Job)
        if status is not None:
            stmt = stmt.where(Job.status == status)
        if job_type is not None:
            stmt = stmt.where(Job.job_type == job_type)
        return int(self.session.scalar(stmt) or 0)

    def list_running(self, *, job_types: list[str] | tuple[str, ...] | None = None) -> list[Job]:
        stmt = select(Job).where(Job.status == JobStatus.RUNNING).order_by(Job.updated_at)
        if job_types:
            stmt = stmt.where(Job.job_type.in_(job_types))
        return list(self.session.scalars(stmt))

    def list_queued(self, *, job_types: list[str] | tuple[str, ...] | None = None) -> list[Job]:
        stmt = select(Job).where(Job.status == JobStatus.QUEUED).order_by(Job.updated_at)
        if job_types:
            stmt = stmt.where(Job.job_type.in_(job_types))
        return list(self.session.scalars(stmt))

    def find_active_for_target(
        self,
        *,
        target_type: str,
        target_id: str,
        job_types: list[str] | tuple[str, ...],
    ) -> Job | None:
        stmt = (
            select(Job)
            .where(
                Job.target_type == target_type,
                Job.target_id == target_id,
                Job.job_type.in_(job_types),
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
            .order_by(Job.created_at)
            .limit(1)
        )
        return self.session.scalars(stmt).first()


class ArtifactRepository(RepositoryBase):
    def create(self, artifact: Artifact) -> Artifact:
        self.session.add(artifact)
        self.session.flush()
        return artifact

    def get(self, artifact_id: str) -> Artifact | None:
        return self.session.get(Artifact, artifact_id)

    def list_all(self, *, artifact_type: str | None = None, logical_id: str | None = None, is_current: bool | None = None, sort: str = "created_at_desc", offset: int = 0, limit: int = 20) -> list[Artifact]:
        stmt = select(Artifact).where(Artifact.deleted_at.is_(None))
        if artifact_type is not None:
            stmt = stmt.where(Artifact.artifact_type == artifact_type)
        if logical_id is not None:
            stmt = stmt.where(Artifact.logical_id == logical_id)
        if is_current is not None:
            stmt = stmt.where(Artifact.is_current.is_(is_current))
        stmt = stmt.order_by(desc(Artifact.created_at) if sort == "created_at_desc" else Artifact.created_at)
        return list(self.session.scalars(stmt.offset(offset).limit(limit)))

    def count_all(self, *, artifact_type: str | None = None, logical_id: str | None = None, is_current: bool | None = None) -> int:
        stmt = select(func.count()).select_from(Artifact).where(Artifact.deleted_at.is_(None))
        if artifact_type is not None:
            stmt = stmt.where(Artifact.artifact_type == artifact_type)
        if logical_id is not None:
            stmt = stmt.where(Artifact.logical_id == logical_id)
        if is_current is not None:
            stmt = stmt.where(Artifact.is_current.is_(is_current))
        return int(self.session.scalar(stmt) or 0)

    def get_current_by_logical_id(self, logical_id: str) -> Artifact | None:
        stmt = select(Artifact).where(Artifact.logical_id == logical_id, Artifact.is_current.is_(True), Artifact.deleted_at.is_(None))
        return self.session.scalar(stmt)

    def get_next_version_no(self, logical_id: str) -> int:
        stmt = select(func.max(Artifact.version_no)).where(Artifact.logical_id == logical_id)
        return int(self.session.scalar(stmt) or 0) + 1

    def compare_and_set_current_artifact(
        self,
        *,
        logical_id: str,
        new_artifact_id: str,
        expected_current_artifact_id: str | None,
    ) -> bool:
        candidate = self.get(new_artifact_id)
        if candidate is None or candidate.logical_id != logical_id or candidate.is_current:
            return False
        if expected_current_artifact_id is None:
            # The first publication must also be a real CAS. A separate
            # SELECT-then-UPDATE allows two concurrent writers to both observe
            # "no current revision" before either promotes its candidate. Keep
            # the absence check in the same SQL statement that promotes the
            # candidate so only one writer can win.
            current_exists = (
                select(Artifact.artifact_id)
                .where(
                    Artifact.logical_id == logical_id,
                    Artifact.is_current.is_(True),
                    Artifact.deleted_at.is_(None),
                )
                .exists()
            )
            result = self.session.execute(
                update(Artifact)
                .where(
                    Artifact.artifact_id == new_artifact_id,
                    Artifact.logical_id == logical_id,
                    Artifact.is_current.is_(False),
                    Artifact.deleted_at.is_(None),
                    ~current_exists,
                )
                .values(is_current=True)
            )
            return result.rowcount == 1

        result = self.session.execute(
            update(Artifact)
            .where(
                Artifact.artifact_id == expected_current_artifact_id,
                Artifact.logical_id == logical_id,
                Artifact.is_current.is_(True),
                Artifact.deleted_at.is_(None),
            )
            .values(is_current=False, superseded_by_artifact_id=new_artifact_id)
        )
        if result.rowcount != 1:
            return False
        result = self.session.execute(
            update(Artifact)
            .where(
                Artifact.artifact_id == new_artifact_id,
                Artifact.logical_id == logical_id,
                Artifact.is_current.is_(False),
                Artifact.deleted_at.is_(None),
            )
            .values(is_current=True)
        )
        return result.rowcount == 1

    def promote_exact_current_artifact(
        self,
        *,
        logical_id: str,
        new_artifact_id: str,
    ) -> bool:
        """Promote one revision and repair stale duplicate-current rows.

        Domain publication first wins its Project-pointer CAS, then calls this
        method in the same transaction. That pointer CAS is the concurrency
        boundary; this update makes the Artifact projection exactly match the
        winning pointer even when an earlier upstream invalidation cleared the
        pointer without demoting its former current revision.
        """

        candidate = self.get(new_artifact_id)
        if (
            candidate is None
            or candidate.logical_id != logical_id
            or candidate.deleted_at is not None
        ):
            return False
        self.session.execute(
            update(Artifact)
            .where(
                Artifact.logical_id == logical_id,
                Artifact.artifact_id != new_artifact_id,
                Artifact.is_current.is_(True),
                Artifact.deleted_at.is_(None),
            )
            .values(
                is_current=False,
                superseded_by_artifact_id=new_artifact_id,
            ),
            execution_options={"synchronize_session": "fetch"},
        )
        result = self.session.execute(
            update(Artifact)
            .where(
                Artifact.artifact_id == new_artifact_id,
                Artifact.logical_id == logical_id,
                Artifact.deleted_at.is_(None),
            )
            .values(is_current=True, superseded_by_artifact_id=None),
            execution_options={"synchronize_session": "fetch"},
        )
        return result.rowcount == 1

    def list_by_source_job(self, source_job_id: str) -> list[Artifact]:
        stmt = select(Artifact).where(Artifact.source_job_id == source_job_id, Artifact.deleted_at.is_(None)).order_by(Artifact.created_at, Artifact.artifact_id)
        return list(self.session.scalars(stmt))


class RenderOperationRepository(RepositoryBase):
    ACTIVE_STATUSES = ("queued", "running", "rendering", "probing", "validating", "finalizing")

    def create(self, operation: RenderOperation) -> RenderOperation:
        self.session.add(operation)
        self.session.flush()
        return operation

    def get(self, render_operation_id: str) -> RenderOperation | None:
        return self.session.get(RenderOperation, render_operation_id)

    def get_by_job_id(self, job_id: str) -> RenderOperation | None:
        return self.session.scalar(select(RenderOperation).where(RenderOperation.job_id == job_id))

    def get_active_for_project(self, project_id: str) -> RenderOperation | None:
        return self.session.scalar(
            select(RenderOperation)
            .where(RenderOperation.project_id == project_id, RenderOperation.status.in_(self.ACTIVE_STATUSES))
            .order_by(RenderOperation.created_at.asc(), RenderOperation.render_operation_id.asc())
            .limit(1)
        )

    def list_recoverable(self) -> list[RenderOperation]:
        return list(self.session.scalars(
            select(RenderOperation)
            .where(RenderOperation.status.in_(self.ACTIVE_STATUSES))
            .order_by(RenderOperation.created_at.asc(), RenderOperation.render_operation_id.asc())
        ))

    def list_for_project(self, project_id: str, *, limit: int = 50) -> list[RenderOperation]:
        return list(self.session.scalars(
            select(RenderOperation).where(RenderOperation.project_id == project_id)
            .order_by(RenderOperation.created_at.desc(), RenderOperation.render_operation_id.desc()).limit(limit)
        ))


class SettingsRepository(RepositoryBase):
    def upsert(self, setting: Setting) -> Setting:
        existing = self.session.get(Setting, setting.key)
        if existing is None:
            self.session.add(setting)
            self.session.flush()
            return setting
        existing.value_json = setting.value_json
        existing.updated_at = setting.updated_at
        self.session.flush()
        return existing

    def get(self, key: str) -> Setting | None:
        return self.session.get(Setting, key)

    def list_all(self) -> list[Setting]:
        return list(self.session.scalars(select(Setting).order_by(Setting.key)))

    def replace_all(self, settings_map: dict[str, object], updated_at: str) -> list[Setting]:
        self.session.execute(delete(Setting))
        self.session.flush()
        return [self.upsert(Setting(key=key, value_json=value, updated_at=updated_at)) for key, value in sorted(settings_map.items())]


class MediaBatchRepository(RepositoryBase):
    def create(self, batch: MediaBatch) -> MediaBatch:
        self.session.add(batch)
        self.session.flush()
        return batch

    def get(self, batch_id: str) -> MediaBatch | None:
        return self.session.get(MediaBatch, batch_id)

    def list_active_batches_for_project(
        self,
        project_id: str,
        *,
        active_statuses: set[str] | tuple[str, ...] | list[str],
    ) -> list[ActiveMediaBatchSnapshot]:
        statuses = tuple(sorted(str(value) for value in active_statuses))
        if not statuses:
            return []
        stmt = (
            select(
                MediaBatch.batch_id,
                MediaBatch.purpose,
                MediaBatch.total,
                func.count(MediaTask.media_task_id).label("active_task_count"),
                MediaBatch.created_at,
                MediaBatch.updated_at,
            )
            .join(MediaTask, MediaTask.batch_id == MediaBatch.batch_id)
            .where(MediaBatch.project_id == project_id, MediaTask.status.in_(statuses))
            .group_by(
                MediaBatch.batch_id,
                MediaBatch.purpose,
                MediaBatch.total,
                MediaBatch.created_at,
                MediaBatch.updated_at,
            )
            .order_by(MediaBatch.created_at.asc(), MediaBatch.batch_id.asc())
        )
        return [
            ActiveMediaBatchSnapshot(
                batch_id=str(row.batch_id),
                purpose=str(row.purpose),
                total=int(row.total),
                active_task_count=int(row.active_task_count),
                created_at=str(row.created_at),
                updated_at=str(row.updated_at),
            )
            for row in self.session.execute(stmt)
        ]

    def get_latest_batch_for_project(self, project_id: str) -> MediaBatch | None:
        stmt = (
            select(MediaBatch)
            .where(MediaBatch.project_id == project_id)
            .order_by(MediaBatch.created_at.desc(), MediaBatch.batch_id.desc())
            .limit(1)
        )
        return self.session.scalar(stmt)


class MediaTaskRepository(RepositoryBase):
    def create(self, task: MediaTask) -> MediaTask:
        self.session.add(task)
        self.session.flush()
        return task

    def get(self, media_task_id: str) -> MediaTask | None:
        return self.session.get(MediaTask, media_task_id)

    def list_all(
        self,
        *,
        batch_id: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[MediaTask]:
        stmt = select(MediaTask)
        if batch_id is not None:
            stmt = stmt.where(MediaTask.batch_id == batch_id)
        if status is not None:
            stmt = stmt.where(MediaTask.status == status)
        stmt = stmt.order_by(
            desc(MediaTask.created_at),
            desc(MediaTask.media_task_id),
        ).offset(offset).limit(limit)
        return list(self.session.scalars(stmt))

    def count_all(self, *, batch_id: str | None = None, status: str | None = None) -> int:
        stmt = select(func.count()).select_from(MediaTask)
        if batch_id is not None:
            stmt = stmt.where(MediaTask.batch_id == batch_id)
        if status is not None:
            stmt = stmt.where(MediaTask.status == status)
        return int(self.session.scalar(stmt) or 0)

    def count_by_statuses(self, statuses: list[str] | tuple[str, ...] | set[str], *, batch_id: str | None = None) -> int:
        values = tuple(str(status) for status in statuses)
        if not values:
            return 0
        stmt = select(func.count()).select_from(MediaTask).where(MediaTask.status.in_(values))
        if batch_id is not None:
            stmt = stmt.where(MediaTask.batch_id == batch_id)
        return int(self.session.scalar(stmt) or 0)

    def count_grouped_by_status(self, *, batch_id: str | None = None) -> dict[str, int]:
        stmt = select(MediaTask.status, func.count()).group_by(MediaTask.status)
        if batch_id is not None:
            stmt = stmt.where(MediaTask.batch_id == batch_id)
        rows = self.session.execute(stmt).all()
        return {str(status): int(total) for status, total in rows}

    def count_selected_locked(self, *, batch_id: str | None = None) -> dict[str, int]:
        stmt = select(
            func.sum(sa_case((MediaTask.selected.is_(True), 1), else_=0)),
            func.sum(sa_case((MediaTask.locked.is_(True), 1), else_=0)),
        )
        if batch_id is not None:
            stmt = stmt.where(MediaTask.batch_id == batch_id)
        selected, locked = self.session.execute(stmt).one()
        return {"selected": int(selected or 0), "locked": int(locked or 0)}

    def terminalize_provider_active_tasks_for_batch(
        self,
        batch_id: str,
        *,
        active_statuses: set[str],
        updated_at: str,
        error_detail: str,
    ) -> int:
        statuses = tuple(sorted(str(status) for status in active_statuses))
        if not statuses:
            return 0
        tasks = list(
            self.session.scalars(
                select(MediaTask).where(
                    MediaTask.batch_id == batch_id,
                    MediaTask.status.in_(statuses),
                )
            )
        )
        return self._terminalize_orphaned_tasks(tasks, updated_at=updated_at, error_detail=error_detail)

    def terminalize_orphaned_provider_active_tasks(
        self,
        *,
        provider: str,
        active_statuses: set[str],
        updated_at: str,
    ) -> int:
        statuses = tuple(sorted(str(status) for status in active_statuses))
        if not statuses:
            return 0
        active_tasks = list(
            self.session.scalars(
                select(MediaTask).where(
                    MediaTask.external_provider == provider,
                    MediaTask.status.in_(statuses),
                )
            )
        )
        orphaned = self._orphaned_operation_owned_tasks(active_tasks)
        return self._terminalize_orphaned_tasks(
            orphaned,
            updated_at=updated_at,
            error_detail=(
                "Opportunistic cleanup found a provider-active media task whose owning "
                "operation or job is terminal/missing, so the task was terminalized to "
                "release execution capacity."
            ),
        )

    def _orphaned_operation_owned_tasks(self, tasks: list[MediaTask]) -> list[MediaTask]:
        if not tasks:
            return []
        batch_ids = {task.batch_id for task in tasks}
        scene_operations = {
            operation.media_batch_id: operation
            for operation in self.session.scalars(
                select(SceneAssetOperation).where(SceneAssetOperation.media_batch_id.in_(batch_ids))
            )
        }
        visual_operations = {
            operation.media_batch_id: operation
            for operation in self.session.scalars(
                select(StyleBibleVisualOperation).where(StyleBibleVisualOperation.media_batch_id.in_(batch_ids))
            )
        }
        job_ids = {
            operation.job_id
            for operation in [*scene_operations.values(), *visual_operations.values()]
        }
        jobs = {
            job.job_id: job
            for job in self.session.scalars(select(Job).where(Job.job_id.in_(job_ids)))
        } if job_ids else {}

        orphaned: list[MediaTask] = []
        for task in tasks:
            scene_operation = scene_operations.get(task.batch_id)
            if scene_operation is not None:
                job = jobs.get(scene_operation.job_id)
                if (
                    scene_operation.status not in ACTIVE_SCENE_ASSET_OPERATION_STATUSES
                    or job is None
                    or job.status not in ACTIVE_JOB_STATUSES
                ):
                    orphaned.append(task)
                continue
            visual_operation = visual_operations.get(task.batch_id)
            if visual_operation is not None:
                job = jobs.get(visual_operation.job_id)
                if (
                    visual_operation.status not in ACTIVE_VISUAL_REFERENCE_OPERATION_STATUSES
                    or job is None
                    or job.status not in ACTIVE_JOB_STATUSES
                ):
                    orphaned.append(task)
        return orphaned

    def _terminalize_orphaned_tasks(
        self,
        tasks: list[MediaTask],
        *,
        updated_at: str,
        error_detail: str,
    ) -> int:
        if not tasks:
            return 0
        batch_ids: set[str] = set()
        for task in tasks:
            task.status = "failed"
            task.provider_status = None
            task.error_code = MEDIA_TASK_ORPHANED_ERROR_CODE
            task.error = "Media task owner is no longer active."
            task.error_detail = error_detail
            task.result_json = []
            task.storage_uri = None
            task.mime_type = None
            task.source_result_url = None
            task.provider_error_code = None
            task.poll_claim_token = None
            task.poll_claimed_at = None
            task.poll_claim_expires_at = None
            task.updated_at = updated_at
            task.completed_at = updated_at
            batch_ids.add(task.batch_id)
        for batch in self.session.scalars(select(MediaBatch).where(MediaBatch.batch_id.in_(batch_ids))):
            batch.updated_at = updated_at
        self.session.flush()
        return len(tasks)


    def claim_submit(
        self,
        media_task_id: str,
        *,
        max_parallel_tasks: int,
        active_statuses: set[str],
        updated_at: str,
    ) -> bool:
        provider = self.session.scalar(
            select(MediaTask.external_provider).where(MediaTask.media_task_id == media_task_id)
        )
        if provider is None:
            return False
        self.terminalize_orphaned_provider_active_tasks(
            provider=str(provider),
            active_statuses=active_statuses,
            updated_at=updated_at,
        )
        active_conditions = [
            MediaTask.external_provider == str(provider),
            MediaTask.status.in_(tuple(sorted(active_statuses))),
        ]
        active_count = (
            select(func.count())
            .select_from(MediaTask)
            .where(*active_conditions)
            .scalar_subquery()
        )
        stmt = (
            update(MediaTask)
            .where(
                MediaTask.media_task_id == media_task_id,
                MediaTask.status == "queued",
                active_count < int(max_parallel_tasks),
            )
            .values(status="submitted", updated_at=updated_at)
            .execution_options(synchronize_session=False)
        )
        result = self.session.execute(stmt)
        self.session.commit()
        return int(result.rowcount or 0) == 1

    def claim_download(self, media_task_id: str, *, updated_at: str) -> bool:
        stmt = (
            update(MediaTask)
            .where(
                MediaTask.media_task_id == media_task_id,
                MediaTask.status.in_(("submitted", "provider_pending", "provider_running")),
                MediaTask.external_task_id.is_not(None),
            )
            .values(status="downloading", provider_status="completed", updated_at=updated_at)
            .execution_options(synchronize_session=False)
        )
        result = self.session.execute(stmt)
        self.session.commit()
        return int(result.rowcount or 0) == 1

    def claim_provider_poll(
        self,
        media_task_id: str,
        *,
        claim_token: str,
        claimed_at: str,
        expires_at: str,
    ) -> ProviderPollSnapshot | None:
        # The expiry is persisted when the claim is acquired.  Contenders must
        # never derive ownership from mutable settings or from poll_claimed_at.
        # SQLite julianday(NULL/malformed) returns NULL; malformed historical expiry
        # is therefore treated as recoverable without leaking a raw exception.
        expiry_jd = func.julianday(MediaTask.poll_claim_expires_at)
        now_jd = func.julianday(claimed_at)
        stmt = (
            update(MediaTask)
            .where(
                MediaTask.media_task_id == media_task_id,
                MediaTask.status.in_(("submitted", "provider_pending", "provider_running")),
                MediaTask.external_task_id.is_not(None),
                MediaTask.provider_submitted_at.is_not(None),
                or_(
                    MediaTask.poll_claim_token.is_(None),
                    MediaTask.poll_claim_expires_at.is_(None),
                    expiry_jd.is_(None),
                    expiry_jd <= now_jd,
                ),
            )
            .values(
                poll_claim_token=claim_token,
                poll_claimed_at=claimed_at,
                poll_claim_expires_at=expires_at,
                poll_revision=MediaTask.poll_revision + 1,
                poll_count=MediaTask.poll_count + 1,
                updated_at=claimed_at,
            )
            .returning(
                MediaTask.media_task_id,
                MediaTask.poll_claim_token,
                MediaTask.poll_revision,
                MediaTask.status,
                MediaTask.external_task_id,
                MediaTask.provider_submitted_at,
                MediaTask.poll_claimed_at,
                MediaTask.poll_claim_expires_at,
            )
            .execution_options(synchronize_session=False)
        )
        row = self.session.execute(stmt).one_or_none()
        self.session.commit()
        if row is None:
            return None
        return ProviderPollSnapshot(
            media_task_id=str(row.media_task_id),
            claim_token=str(row.poll_claim_token),
            poll_revision=int(row.poll_revision),
            observed_status=str(row.status),
            external_task_id=str(row.external_task_id),
            provider_submitted_at=str(row.provider_submitted_at),
            poll_claimed_at=str(row.poll_claimed_at),
            poll_claim_expires_at=str(row.poll_claim_expires_at),
        )

    def apply_provider_pending_from_claim(
        self, media_task_id: str, *, claim_token: str, updated_at: str
    ) -> bool:
        return self._apply_poll_claim_update(
            media_task_id,
            claim_token=claim_token,
            allowed_statuses=("submitted", "provider_pending"),
            values={
                "status": "provider_pending",
                "provider_status": "pending",
                "updated_at": updated_at,
            },
        )

    def apply_provider_running_from_claim(
        self, media_task_id: str, *, claim_token: str, updated_at: str
    ) -> bool:
        return self._apply_poll_claim_update(
            media_task_id,
            claim_token=claim_token,
            allowed_statuses=("submitted", "provider_pending", "provider_running"),
            values={
                "status": "provider_running",
                "provider_status": "running",
                "updated_at": updated_at,
            },
        )

    def apply_provider_failure_from_claim(
        self,
        media_task_id: str,
        *,
        claim_token: str,
        provider_error_code: int | None,
        error_code: str,
        error: str,
        error_detail: str,
        updated_at: str,
    ) -> bool:
        return self._apply_poll_claim_update(
            media_task_id,
            claim_token=claim_token,
            allowed_statuses=("submitted", "provider_pending", "provider_running"),
            values={
                "status": "failed",
                "provider_status": "failed",
                "provider_error_code": provider_error_code,
                "error_code": error_code,
                "error": error,
                "error_detail": error_detail,
                "completed_at": updated_at,
                "updated_at": updated_at,
            },
        )

    def claim_download_from_poll_claim_and_touch_batch(
        self, media_task_id: str, *, claim_token: str, updated_at: str
    ) -> DownloadOwnerSnapshot | None:
        task_stmt = (
            update(MediaTask)
            .where(
                MediaTask.media_task_id == media_task_id,
                MediaTask.poll_claim_token == claim_token,
                MediaTask.status.in_(("submitted", "provider_pending", "provider_running")),
                MediaTask.external_task_id.is_not(None),
                MediaTask.provider_submitted_at.is_not(None),
            )
            .values(
                status="downloading",
                provider_status="completed",
                poll_claim_token=None,
                poll_claimed_at=None,
                poll_claim_expires_at=None,
                completed_at=updated_at,
                updated_at=updated_at,
            )
            .returning(
                MediaTask.media_task_id,
                MediaTask.batch_id,
                MediaTask.purpose,
                MediaTask.project_id,
                MediaTask.external_task_id,
                MediaTask.provider_submitted_at,
                MediaTask.style_bible_revision_id,
                MediaTask.domain_revision_id,
                MediaTask.storage_namespace,
                MediaTask.entity_type,
                MediaTask.entity_id,
            )
            .execution_options(synchronize_session=False)
        )
        try:
            row = self.session.execute(task_stmt).one_or_none()
            if row is None:
                self.session.rollback()
                return None
            batch_conditions = [MediaBatch.batch_id == row.batch_id]
            if row.project_id is None:
                batch_conditions.append(MediaBatch.project_id.is_(None))
            else:
                batch_conditions.append(MediaBatch.project_id == row.project_id)
            batch_result = self.session.execute(
                update(MediaBatch)
                .where(*batch_conditions)
                .values(updated_at=updated_at)
                .execution_options(synchronize_session=False)
            )
            if int(batch_result.rowcount or 0) != 1:
                raise RuntimeError("Media batch ownership could not be updated atomically.")
            owner = DownloadOwnerSnapshot(
                media_task_id=str(row.media_task_id),
                batch_id=str(row.batch_id),
                purpose=str(row.purpose),
                project_id=str(row.project_id) if row.project_id is not None else None,
                external_task_id=str(row.external_task_id),
                provider_submitted_at=str(row.provider_submitted_at),
                claimed_downloading_at=updated_at,
                style_bible_revision_id=(
                    str(row.style_bible_revision_id) if row.style_bible_revision_id is not None else None
                ),
                domain_revision_id=(str(row.domain_revision_id) if row.domain_revision_id is not None else None),
                storage_namespace=(str(row.storage_namespace) if row.storage_namespace is not None else None),
                entity_type=str(row.entity_type),
                entity_id=str(row.entity_id),
            )
            self.session.commit()
            return owner
        except Exception:
            self.session.rollback()
            raise

    def claim_download_from_poll_claim(
        self, media_task_id: str, *, claim_token: str, updated_at: str
    ) -> bool:
        # Compatibility entry point for existing internal tests.  Production uses
        # the snapshot-returning atomic handoff above.
        return self.claim_download_from_poll_claim_and_touch_batch(
            media_task_id, claim_token=claim_token, updated_at=updated_at
        ) is not None

    def complete_download_and_touch_batch(
        self,
        media_task_id: str,
        *,
        batch_id: str,
        result_json: list[dict[str, object]],
        storage_uri: str | None,
        mime_type: str | None,
        source_result_url: str | None,
        completed_at: str,
    ) -> bool:
        task_result = self.session.execute(
            update(MediaTask)
            .where(MediaTask.media_task_id == media_task_id, MediaTask.status == "downloading")
            .values(
                status="completed",
                result_json=result_json,
                storage_uri=storage_uri,
                mime_type=mime_type,
                source_result_url=source_result_url,
                completed_at=completed_at,
                updated_at=completed_at,
                error_code=None,
                error=None,
                error_detail=None,
                provider_error_code=None,
            )
            .execution_options(synchronize_session=False)
        )
        if int(task_result.rowcount or 0) != 1:
            self.session.rollback()
            return False
        batch_result = self.session.execute(
            update(MediaBatch)
            .where(MediaBatch.batch_id == batch_id)
            .values(updated_at=completed_at)
            .execution_options(synchronize_session=False)
        )
        if int(batch_result.rowcount or 0) != 1:
            self.session.rollback()
            raise RuntimeError("Media batch completion metadata could not be updated.")
        self.session.commit()
        return True

    def finalize_download_failure_and_touch_batch(
        self,
        media_task_id: str,
        *,
        batch_id: str,
        logical_id_prefix: str | None,
        error_code: str,
        provider_error_code: int | None,
        error: str,
        error_detail: str,
        updated_at: str,
    ) -> bool:
        task_result = self.session.execute(
            update(MediaTask)
            .where(MediaTask.media_task_id == media_task_id, MediaTask.status == "downloading")
            .values(
                status="download_failed",
                result_json=[],
                storage_uri=None,
                mime_type=None,
                source_result_url=None,
                completed_at=updated_at,
                error_code=error_code,
                provider_error_code=provider_error_code,
                error=error,
                error_detail=error_detail,
                updated_at=updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        if int(task_result.rowcount or 0) != 1:
            self.session.rollback()
            return False
        if logical_id_prefix is not None:
            self.session.execute(
                delete(Artifact).where(
                    Artifact.provider_source == "glabs_webhook",
                    Artifact.logical_id.like(f"{logical_id_prefix}/%"),
                )
            )
        batch_result = self.session.execute(
            update(MediaBatch)
            .where(MediaBatch.batch_id == batch_id)
            .values(updated_at=updated_at)
            .execution_options(synchronize_session=False)
        )
        if int(batch_result.rowcount or 0) != 1:
            self.session.rollback()
            raise RuntimeError("Media batch failure metadata could not be updated.")
        self.session.commit()
        return True

    def release_provider_poll_claim(
        self, media_task_id: str, *, claim_token: str, updated_at: str
    ) -> bool:
        stmt = (
            update(MediaTask)
            .where(
                MediaTask.media_task_id == media_task_id,
                MediaTask.poll_claim_token == claim_token,
                MediaTask.status.in_(("submitted", "provider_pending", "provider_running")),
            )
            .values(
                poll_claim_token=None,
                poll_claimed_at=None,
                poll_claim_expires_at=None,
                updated_at=updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        result = self.session.execute(stmt)
        self.session.commit()
        return int(result.rowcount or 0) == 1

    def _apply_poll_claim_update(
        self,
        media_task_id: str,
        *,
        claim_token: str,
        allowed_statuses: tuple[str, ...],
        values: dict[str, object],
        require_external_task_id: bool = False,
    ) -> bool:
        conditions = [
            MediaTask.media_task_id == media_task_id,
            MediaTask.poll_claim_token == claim_token,
            MediaTask.status.in_(allowed_statuses),
        ]
        if require_external_task_id:
            conditions.append(MediaTask.external_task_id.is_not(None))
        claimed_values = dict(values)
        claimed_values["poll_claim_token"] = None
        claimed_values["poll_claimed_at"] = None
        claimed_values["poll_claim_expires_at"] = None
        stmt = (
            update(MediaTask)
            .where(*conditions)
            .values(**claimed_values)
            .execution_options(synchronize_session=False)
        )
        result = self.session.execute(stmt)
        self.session.commit()
        return int(result.rowcount or 0) == 1

class StyleBibleVisualOperationRepository(RepositoryBase):
    def create(self, operation: StyleBibleVisualOperation) -> StyleBibleVisualOperation:
        self.session.add(operation)
        self.session.flush()
        return operation

    def get(self, operation_id: str) -> StyleBibleVisualOperation | None:
        return self.session.get(StyleBibleVisualOperation, operation_id)

    def get_by_job_id(self, job_id: str) -> StyleBibleVisualOperation | None:
        return self.session.scalar(
            select(StyleBibleVisualOperation).where(StyleBibleVisualOperation.job_id == job_id)
        )

    def list_for_project(self, project_id: str, *, limit: int = 100) -> list[StyleBibleVisualOperation]:
        stmt = (
            select(StyleBibleVisualOperation)
            .where(StyleBibleVisualOperation.project_id == project_id)
            .order_by(StyleBibleVisualOperation.created_at.desc(), StyleBibleVisualOperation.operation_id.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def list_active_for_project(self, project_id: str) -> list[StyleBibleVisualOperation]:
        """Return every active operation without applying historical pagination."""
        stmt = (
            select(StyleBibleVisualOperation)
            .where(
                StyleBibleVisualOperation.project_id == project_id,
                StyleBibleVisualOperation.status.in_(("queued", "generating")),
            )
            .order_by(StyleBibleVisualOperation.created_at, StyleBibleVisualOperation.operation_id)
        )
        return list(self.session.scalars(stmt))

    def find_active_equivalent(
        self,
        *,
        project_id: str,
        style_bible_artifact_id: str,
        expected_visual_reference_set_artifact_id: str | None,
        subjects: list[dict[str, Any]],
    ) -> StyleBibleVisualOperation | None:
        normalized = sorted(
            (
                str(item.get("anchor_type")),
                str(item.get("anchor_id")),
                str(item.get("source_definition_hash")),
                str(item.get("prompt")),
                str(item.get("negative_prompt")),
            )
            for item in subjects
        )
        for operation in self.list_active_for_project(project_id):
            if operation.style_bible_artifact_id != style_bible_artifact_id:
                continue
            if operation.expected_visual_reference_set_artifact_id != expected_visual_reference_set_artifact_id:
                continue
            request_subjects = operation.request_json.get("subjects", []) if isinstance(operation.request_json, dict) else []
            operation_normalized = sorted(
                (
                    str(item.get("anchor_type")),
                    str(item.get("anchor_id")),
                    str(item.get("source_definition_hash")),
                    str(item.get("prompt")),
                    str(item.get("negative_prompt")),
                )
                for item in request_subjects
                if isinstance(item, dict)
            )
            if operation_normalized == normalized:
                return operation
        return None


class SceneAssetOperationRepository(RepositoryBase):
    ACTIVE_STATUSES = ("queued", "running", "waiting_for_media", "finalizing")

    def create(self, operation: SceneAssetOperation) -> SceneAssetOperation:
        self.session.add(operation)
        self.session.flush()
        return operation

    def get(self, operation_id: str) -> SceneAssetOperation | None:
        return self.session.get(SceneAssetOperation, operation_id)

    def get_by_job_id(self, job_id: str) -> SceneAssetOperation | None:
        return self.session.scalar(select(SceneAssetOperation).where(SceneAssetOperation.job_id == job_id))

    def list_for_project(self, project_id: str, *, limit: int = 100) -> list[SceneAssetOperation]:
        stmt = (select(SceneAssetOperation).where(SceneAssetOperation.project_id == project_id)
                .order_by(SceneAssetOperation.created_at.desc(), SceneAssetOperation.operation_id.desc()).limit(limit))
        return list(self.session.scalars(stmt))

    def list_active_for_project(self, project_id: str) -> list[SceneAssetOperation]:
        stmt = (select(SceneAssetOperation).where(
            SceneAssetOperation.project_id == project_id,
            SceneAssetOperation.status.in_(self.ACTIVE_STATUSES),
        ).order_by(SceneAssetOperation.created_at, SceneAssetOperation.operation_id))
        return list(self.session.scalars(stmt))


class SceneAssetBeatClaimRepository(RepositoryBase):
    def acquire_many(self, *, project_id: str, operation_id: str, beat_ids: list[str], created_at: str) -> list[SceneAssetBeatClaim]:
        claims: list[SceneAssetBeatClaim] = []
        for beat_id in dict.fromkeys(beat_ids):
            claim = SceneAssetBeatClaim(
                claim_id=f"{operation_id}:{beat_id}",
                project_id=project_id,
                beat_id=beat_id,
                operation_id=operation_id,
                created_at=created_at,
            )
            self.session.add(claim)
            claims.append(claim)
        self.session.flush()
        return claims

    def release_for_operation(self, operation_id: str) -> int:
        result = self.session.execute(
            delete(SceneAssetBeatClaim).where(SceneAssetBeatClaim.operation_id == operation_id),
            execution_options={"synchronize_session": False},
        )
        return int(result.rowcount or 0)

    def list_for_operation(self, operation_id: str) -> list[SceneAssetBeatClaim]:
        return list(self.session.scalars(select(SceneAssetBeatClaim).where(SceneAssetBeatClaim.operation_id == operation_id)))

    def list_for_project(self, project_id: str) -> list[SceneAssetBeatClaim]:
        stmt = select(SceneAssetBeatClaim).where(SceneAssetBeatClaim.project_id == project_id).order_by(SceneAssetBeatClaim.created_at, SceneAssetBeatClaim.beat_id)
        return list(self.session.scalars(stmt))


class SceneAssetBeatOperationLeaseRepository(RepositoryBase):
    def acquire_many(
        self,
        *,
        project_id: str,
        beat_ids: list[str],
        owner_kind: str,
        owner_id: str,
        created_at: str,
    ) -> list[SceneAssetBeatOperationLease]:
        leases: list[SceneAssetBeatOperationLease] = []
        for beat_id in dict.fromkeys(beat_ids):
            lease = SceneAssetBeatOperationLease(
                lease_id=f"{owner_id}:{beat_id}",
                project_id=project_id,
                beat_id=beat_id,
                owner_kind=owner_kind,
                owner_id=owner_id,
                created_at=created_at,
            )
            self.session.add(lease)
            leases.append(lease)
        self.session.flush()
        return leases

    def release_owner(self, owner_id: str) -> int:
        result = self.session.execute(
            delete(SceneAssetBeatOperationLease).where(SceneAssetBeatOperationLease.owner_id == owner_id),
            execution_options={"synchronize_session": False},
        )
        return int(result.rowcount or 0)

    def get_for_beat(self, project_id: str, beat_id: str) -> SceneAssetBeatOperationLease | None:
        return self.session.scalar(select(SceneAssetBeatOperationLease).where(
            SceneAssetBeatOperationLease.project_id == project_id,
            SceneAssetBeatOperationLease.beat_id == beat_id,
        ))

    def list_for_project(self, project_id: str) -> list[SceneAssetBeatOperationLease]:
        stmt = select(SceneAssetBeatOperationLease).where(SceneAssetBeatOperationLease.project_id == project_id).order_by(
            SceneAssetBeatOperationLease.created_at,
            SceneAssetBeatOperationLease.beat_id,
        )
        return list(self.session.scalars(stmt))


class SceneAssetCandidateReviewRepository(RepositoryBase):
    def upsert(
        self,
        *,
        review_id: str,
        project_id: str,
        scene_id: str,
        beat_id: str,
        candidate_artifact_id: str,
        decision: str,
        is_current_draft: bool,
        is_approved: bool,
        created_at: str,
        updated_at: str,
    ) -> SceneAssetCandidateReview:
        row = self.session.scalar(select(SceneAssetCandidateReview).where(
            SceneAssetCandidateReview.project_id == project_id,
            SceneAssetCandidateReview.scene_id == scene_id,
            SceneAssetCandidateReview.beat_id == beat_id,
            SceneAssetCandidateReview.candidate_artifact_id == candidate_artifact_id,
        ))
        if row is None:
            row = SceneAssetCandidateReview(
                review_id=review_id, project_id=project_id, scene_id=scene_id, beat_id=beat_id,
                candidate_artifact_id=candidate_artifact_id, decision=decision,
                is_current_draft=is_current_draft, is_approved=is_approved,
                created_at=created_at, updated_at=updated_at,
            )
            self.session.add(row)
        else:
            row.decision = decision
            row.is_current_draft = is_current_draft
            row.is_approved = is_approved
            row.updated_at = updated_at
        self.session.flush()
        return row

    def list_for_beat(self, project_id: str, scene_id: str, beat_id: str) -> list[SceneAssetCandidateReview]:
        stmt = select(SceneAssetCandidateReview).where(
            SceneAssetCandidateReview.project_id == project_id,
            SceneAssetCandidateReview.scene_id == scene_id,
            SceneAssetCandidateReview.beat_id == beat_id,
        ).order_by(SceneAssetCandidateReview.created_at, SceneAssetCandidateReview.review_id)
        return list(self.session.scalars(stmt))

    def clear_flags(self, project_id: str, scene_id: str, beat_id: str, *, current_draft: bool = False, approved: bool = False) -> None:
        values = {}
        if current_draft:
            values["is_current_draft"] = False
        if approved:
            values["is_approved"] = False
        if values:
            self.session.execute(update(SceneAssetCandidateReview).where(
                SceneAssetCandidateReview.project_id == project_id,
                SceneAssetCandidateReview.scene_id == scene_id,
                SceneAssetCandidateReview.beat_id == beat_id,
            ).values(**values).execution_options(synchronize_session=False))


class VoiceGenerationOperationRepository(RepositoryBase):
    ACTIVE_STATUSES = ("queued", "running", "waiting_for_provider", "assembling", "finalizing")

    def create(self, operation: VoiceGenerationOperation) -> VoiceGenerationOperation:
        self.session.add(operation)
        self.session.flush()
        return operation

    def get(self, operation_id: str) -> VoiceGenerationOperation | None:
        return self.session.get(VoiceGenerationOperation, operation_id)

    def get_by_job_id(self, job_id: str) -> VoiceGenerationOperation | None:
        return self.session.scalar(select(VoiceGenerationOperation).where(VoiceGenerationOperation.job_id == job_id))

    def list_for_project(self, project_id: str, *, limit: int = 100) -> list[VoiceGenerationOperation]:
        stmt = select(VoiceGenerationOperation).where(VoiceGenerationOperation.project_id == project_id).order_by(VoiceGenerationOperation.created_at.desc(), VoiceGenerationOperation.operation_id.desc()).limit(limit)
        return list(self.session.scalars(stmt))

    def list_active_for_project(self, project_id: str) -> list[VoiceGenerationOperation]:
        stmt = select(VoiceGenerationOperation).where(
            VoiceGenerationOperation.project_id == project_id,
            VoiceGenerationOperation.status.in_(self.ACTIVE_STATUSES),
        ).order_by(VoiceGenerationOperation.created_at, VoiceGenerationOperation.operation_id)
        return list(self.session.scalars(stmt))


class VoiceSegmentTaskRepository(RepositoryBase):
    def create(self, task: VoiceSegmentTask) -> VoiceSegmentTask:
        self.session.add(task)
        self.session.flush()
        return task

    def get(self, task_id: str) -> VoiceSegmentTask | None:
        return self.session.get(VoiceSegmentTask, task_id)

    def list_for_operation(self, operation_id: str) -> list[VoiceSegmentTask]:
        stmt = select(VoiceSegmentTask).where(VoiceSegmentTask.operation_id == operation_id).order_by(VoiceSegmentTask.segment_order, VoiceSegmentTask.attempt_no, VoiceSegmentTask.voice_segment_task_id)
        return list(self.session.scalars(stmt))

    def list_for_project_segment(self, project_id: str, segment_id: str) -> list[VoiceSegmentTask]:
        stmt = select(VoiceSegmentTask).where(VoiceSegmentTask.project_id == project_id, VoiceSegmentTask.segment_id == segment_id).order_by(VoiceSegmentTask.created_at, VoiceSegmentTask.attempt_no, VoiceSegmentTask.voice_segment_task_id)
        return list(self.session.scalars(stmt))

    def latest_completed_for_segment(self, project_id: str, segment_id: str) -> VoiceSegmentTask | None:
        stmt = select(VoiceSegmentTask).where(
            VoiceSegmentTask.project_id == project_id, VoiceSegmentTask.segment_id == segment_id,
            VoiceSegmentTask.status == "completed", VoiceSegmentTask.segment_audio_artifact_id.is_not(None),
        ).order_by(VoiceSegmentTask.created_at.desc(), VoiceSegmentTask.attempt_no.desc(), VoiceSegmentTask.voice_segment_task_id.desc())
        return self.session.scalar(stmt)
