import type { Project } from '../../api/client';

export const projectPipelineStages = [
  'Topic Ideas',
  'Script',
  'Script Analysis',
  'Style Bible',
  'Voice',
  'Scene Breakdown',
  'Scene Assets',
  'Assembly',
  'Render',
  'Media / Reference Images',
] as const;

export type ProjectStageState = 'complete' | 'current' | 'inactive';

export type ProjectStageProgress = {
  label: (typeof projectPipelineStages)[number];
  state: ProjectStageState;
};

export type ProjectCardState = {
  status: 'draft' | 'pending' | 'done' | 'unknown';
  statusLabel: string;
  currentStageLabel: string;
  completedCount: number;
  progressLabel: string;
  segments: ProjectStageProgress[];
};

export function projectCardState(project: Project): ProjectCardState {
  const hasTopicIdeas = Boolean(project.topic_ideas_artifact_id);
  const hasScript = Boolean(project.script_artifact_id);
  const hasScriptAnalysis = Boolean(project.script_analysis_artifact_id);
  const hasStyleBibleDraft = Boolean(project.style_bible_artifact_id);
  const hasScenePlan = Boolean(project.scene_plan_artifact_id);
  const hasVisualPlan = Boolean(project.visual_plan_artifact_id);
  const hasPromptBundle = Boolean(project.prompt_bundle_artifact_id);
  const hasSceneAssets = Boolean(project.scene_asset_manifest_artifact_id);
  const hasVoice = Boolean(project.narration_audio_artifact_id && project.narration_word_timing_map_artifact_id);
  const hasAssembly = Boolean(project.assembly_plan_artifact_id && project.subtitle_srt_artifact_id && project.subtitle_vtt_artifact_id);
  const hasRender = Boolean(project.final_video_artifact_id);

  const hasCombinedSceneBreakdown = hasScenePlan && hasVisualPlan;
  const completeByStage: Record<(typeof projectPipelineStages)[number], boolean> = {
    'Topic Ideas': hasTopicIdeas,
    'Script': hasScript,
    'Script Analysis': hasScriptAnalysis,
    'Voice': hasVoice,
    'Style Bible': hasStyleBibleDraft,
    'Scene Breakdown': hasCombinedSceneBreakdown,
    'Scene Assets': hasSceneAssets || hasPromptBundle,
    'Assembly': hasAssembly,
    'Render': hasRender,
    'Media / Reference Images': Boolean(project.style_bible_visual_reference_set_artifact_id || project.style_bible_visual_reference_approval_set_artifact_id),
  };
  const completedCount = projectPipelineStages.filter((label) => completeByStage[label]).length;
  const currentStageIndex = projectPipelineStages.findIndex((label) => !completeByStage[label]);

  const segments: ProjectStageProgress[] = projectPipelineStages.map((label, index) => {
    const state: ProjectStageState = completeByStage[label] ? 'complete' : index === currentStageIndex ? 'current' : 'inactive';
    return { label, state };
  });

  if (hasRender) {
    return { status: 'done', statusLabel: 'Final Video Current', currentStageLabel: 'Render', completedCount, progressLabel: `${completedCount} of ${projectPipelineStages.length} stages have real backend artifact state. The current validated final video is ready for browser review.`, segments };
  }

  if (hasAssembly) {
    return { status: 'pending', statusLabel: 'Assembly Current', currentStageLabel: 'Render', completedCount, progressLabel: `${completedCount} of ${projectPipelineStages.length} stages have real backend artifact state. Open Studio to render the approved Assembly locally with FFmpeg.`, segments };
  }


  if (hasSceneAssets) {
    return {
      status: 'pending',
      statusLabel: 'Scene Assets Current',
      currentStageLabel: 'Scene Assets',
      completedCount,
      progressLabel: `${completedCount} of ${projectPipelineStages.length} stages have real backend artifact state. Open Studio to review candidate and manifest approval state.`,
      segments,
    };
  }

  if (hasPromptBundle) {
    return {
      status: 'pending',
      statusLabel: 'Scene Breakdown Approved',
      currentStageLabel: 'Scene Assets',
      completedCount,
      progressLabel: `${completedCount} of ${projectPipelineStages.length} stages have real backend artifact state. Image prompts were compiled automatically; open Scene Assets to generate and review beat images.`,
      segments,
    };
  }

  if (hasCombinedSceneBreakdown) {
    return {
      status: 'pending',
      statusLabel: 'Scene Breakdown Current',
      currentStageLabel: 'Scene Breakdown',
      completedCount,
      progressLabel: `${completedCount} of ${projectPipelineStages.length} stages have real backend artifact state. Open Studio to review the paired Scene Plan and beat-level Visual Plan.`,
      segments,
    };
  }

  if (hasScenePlan || hasVisualPlan) {
    return {
      status: 'unknown',
      statusLabel: 'Scene Pair Incomplete',
      currentStageLabel: 'Scene Breakdown',
      completedCount,
      progressLabel: 'Scene Breakdown requires a matching Scene Plan and Visual Plan pair. Refresh or regenerate the combined stage.',
      segments,
    };
  }

  if (hasVoice) {
    return {
      status: 'pending',
      statusLabel: 'Voice Current',
      currentStageLabel: 'Voice',
      completedCount,
      progressLabel: `${completedCount} of ${projectPipelineStages.length} stages have real backend artifact state. Open Studio to review narration timing and approval state.`,
      segments,
    };
  }

  if (hasStyleBibleDraft) {
    return {
      status: 'pending',
      statusLabel: 'Style Bible Draft',
      currentStageLabel: 'Style Bible',
      completedCount,
      progressLabel: `${completedCount} of ${projectPipelineStages.length} stages have real backend artifact state. Style Bible has a current revision. Open Studio to review whether its text is draft or text-approved.`,
      segments,
    };
  }

  if (hasScriptAnalysis) {
    return {
      status: 'pending',
      statusLabel: 'Voice Next',
      currentStageLabel: 'Voice',
      completedCount,
      progressLabel: `${completedCount} of ${projectPipelineStages.length} stages have real backend artifact state.`,
      segments,
    };
  }

  if (hasScript) {
    return {
      status: 'done',
      statusLabel: 'Script Ready',
      currentStageLabel: 'Script Analysis',
      completedCount,
      progressLabel: `${completedCount} of ${projectPipelineStages.length} stages have real backend artifact state.`,
      segments,
    };
  }

  if (hasTopicIdeas) {
    return {
      status: 'pending',
      statusLabel: 'Script Next',
      currentStageLabel: 'Script',
      completedCount,
      progressLabel: `${completedCount} of ${projectPipelineStages.length} stages have real backend artifact state.`,
      segments,
    };
  }

  return {
    status: 'draft',
    statusLabel: 'Topic Ideas Next',
    currentStageLabel: 'Topic Ideas',
    completedCount,
    progressLabel: `No pipeline artifacts saved yet. ${projectPipelineStages.length} stages stay inactive until backend state exists.`,
    segments,
  };
}
