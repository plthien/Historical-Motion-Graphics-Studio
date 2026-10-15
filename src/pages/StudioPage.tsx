import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Clapperboard, RadioTower } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  apiClient,
  ApiError,
  type ExternalImportPayload,
  type ExternalImportValidationResponse,
  type ExternalPromptPackage,
  type Project,
  type ProjectListResponse,
  type PromptBundleAspectRatio,
  type SceneBreakdownEditableSectionV4,
  type SceneBreakdownReadResponseV4,
  type SceneBreakdownSectionEditValidationResponseV4,
  type ScriptAnalysisContinuityReviewResponse,
  type ScriptAnalysisReadResponse,
  type ScriptResponse,
  type StyleBibleArtifactResponseV4,
  type StyleBibleReadResponseV4,
  type StyleBibleTextUpdatePayloadV4,
  type TopicIdea,
  type TopicIdeasResponse,
} from '../api/client';
import { type AppView, csvToList, type WorkspaceContext } from '../app/workspace';
import { EmptyState } from '../components/ui/EmptyState';
import { ErrorBanner } from '../components/ui/ErrorBanner';
import { AssemblyPanel } from '../features/studio/AssemblyPanel';
import { RenderPanel } from '../features/studio/RenderPanel';
import { SceneAssetsPanel } from '../features/studio/SceneAssetsPanel';
import { cachePublishedScenePair } from '../features/studio/sceneAssetsQueryCache';
import { SceneBreakdownPanel, type SceneBreakdownStageState } from '../features/studio/SceneBreakdownPanel';
import { ScriptPanel } from '../features/studio/ScriptPanel';
import { StudioPipeline } from '../features/studio/StudioPipeline';
import { StyleBiblePanel } from '../features/studio/StyleBiblePanel';
import { selectDisplayedStyleBible } from '../features/studio/styleBibleState';
import { TopicIdeasPanel } from '../features/studio/TopicIdeasPanel';
import { resolveSelectedTopicIdea } from '../features/studio/topicIdeaResolution';
import type { ScriptFormState, StudioStageId, TopicFormState } from '../features/studio/types';
import { VoicePanel } from '../features/studio/VoicePanel';
import type { PipelineStep } from '../layout/PipelineStepper';

function errorMessage(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) return `${error.errorCode}: ${error.message}`;
  return error instanceof Error ? error.message : 'Unknown error';
}

function topicResultFromRead(data: { generated: boolean; artifact: TopicIdeasResponse['artifact'] | null; artifact_id: string | null } | undefined): TopicIdeasResponse | null {
  return data?.generated && data.artifact && data.artifact_id ? { artifact: data.artifact, artifact_id: data.artifact_id } : null;
}

function scriptResultFromRead(data: { generated: boolean; artifact: ScriptResponse['artifact'] | null; artifact_id: string | null } | undefined): ScriptResponse | null {
  return data?.generated && data.artifact && data.artifact_id ? { artifact: data.artifact, artifact_id: data.artifact_id } : null;
}

function styleResultFromRead(data: StyleBibleReadResponseV4 | undefined): StyleBibleArtifactResponseV4 | null {
  return data?.generated && data.artifact && data.artifact_id ? { artifact: data.artifact, artifact_id: data.artifact_id } : null;
}

function externalPayload(resultJson: string): ExternalImportPayload {
  return { result_json: resultJson };
}

function externalValidationFromError(error: unknown): ExternalImportValidationResponse {
  const issues: ExternalImportValidationResponse['errors'] = [];
  if (error instanceof ApiError) {
    const rawErrors = error.details?.errors;
    if (Array.isArray(rawErrors)) {
      for (const item of rawErrors) {
        if (!item || typeof item !== 'object') continue;
        const issue = item as Record<string, unknown>;
        issues.push({
          path: String(issue.path ?? '$'),
          code: String(issue.code ?? issue.keyword ?? error.errorCode),
          message: String(issue.message ?? error.message),
        });
      }
    }
    if (!issues.length) issues.push({ path: '$', code: error.errorCode, message: error.message });
  } else {
    issues.push({ path: '$', code: 'IMPORT_FAILED', message: error instanceof Error ? error.message : 'Import failed.' });
  }
  return { valid: false, errors: issues, warnings: [], summary: {} };
}

export function StudioPage({ workspace, onNavigate }: { workspace: WorkspaceContext; onNavigate: (view: AppView) => void }) {
  const queryClient = useQueryClient();
  const project = workspace.selectedProject;
  const projectId = workspace.selectedProjectId;
  const [activeStage, setActiveStage] = useState<StudioStageId>('topic');
  const [topicForm, setTopicForm] = useState<TopicFormState>({ seed_topic: '', topic_count: 10, focus_keywords: '', focus: 'balanced' });
  const [scriptForm, setScriptForm] = useState<ScriptFormState>({ target_duration_minutes: 8, pov_style: 'mixed second-person and third-person narrator', avoid: '', section_count_mode: 'llm_decides', pacing_mode: 'medium', narration_wpm: project?.channel_dna_narration_wpm ?? { min: 0, max: 0 } });
  const [topicPrompt, setTopicPrompt] = useState<ExternalPromptPackage | null>(null);
  const [topicJson, setTopicJson] = useState('');
  const [topicValidation, setTopicValidation] = useState<ExternalImportValidationResponse | null>(null);
  const [scriptPrompt, setScriptPrompt] = useState<ExternalPromptPackage | null>(null);
  const [scriptJson, setScriptJson] = useState('');
  const [scriptValidation, setScriptValidation] = useState<ExternalImportValidationResponse | null>(null);
  const [imageAspectRatio, setImageAspectRatio] = useState<PromptBundleAspectRatio>('16:9');

  const patchProject = (next: Project) => queryClient.setQueryData<ProjectListResponse>(['v3-projects'], (current) => current ? { ...current, items: current.items.map((item) => item.project_id === next.project_id ? next : item) } : current);
  const refreshProject = async () => { if (!projectId) return null; const next = await apiClient.getProject(projectId); patchProject(next); return next; };

  const topicQuery = useQuery({ queryKey: ['studio-topic-v4', projectId, project?.topic_ideas_artifact_id], queryFn: () => apiClient.getProjectTopicIdeas(projectId), enabled: Boolean(projectId && project?.topic_ideas_artifact_id), retry: false });
  const scriptQuery = useQuery({ queryKey: ['studio-script-v4', projectId, project?.script_artifact_id], queryFn: () => apiClient.getProjectScript(projectId), enabled: Boolean(projectId && project?.script_artifact_id), retry: false });
  const analysisQuery = useQuery({ queryKey: ['studio-script-analysis-v4', projectId, project?.script_analysis_artifact_id], queryFn: () => apiClient.getScriptAnalysis(projectId), enabled: Boolean(projectId && project?.script_analysis_artifact_id), retry: false });
  const continuityReviewQuery = useQuery({ queryKey: ['studio-script-analysis-continuity-review-v4', projectId, project?.script_analysis_artifact_id], queryFn: () => apiClient.getScriptAnalysisContinuityReview(projectId), enabled: Boolean(projectId && project?.script_analysis_artifact_id), retry: false });
  const styleQuery = useQuery({ queryKey: ['studio-style-bible-v4', projectId, project?.style_bible_artifact_id], queryFn: () => apiClient.getStyleBibleV4(projectId), enabled: Boolean(projectId && project?.style_bible_artifact_id), retry: false });
  const sceneQuery = useQuery({ queryKey: ['studio-scene-breakdown-v4', projectId, project?.scene_plan_artifact_id, project?.visual_plan_artifact_id], queryFn: () => apiClient.getSceneBreakdownV4(projectId), enabled: Boolean(projectId && project?.scene_plan_artifact_id && project?.visual_plan_artifact_id), retry: false });
  const promptBundleQuery = useQuery({ queryKey: ['studio-prompt-bundle-options-v4', projectId, project?.prompt_bundle_artifact_id], queryFn: ({ signal }) => apiClient.getPromptBundleV4(projectId, signal), enabled: Boolean(projectId && project?.prompt_bundle_artifact_id), retry: false });
  const topicPromptDraftQuery = useQuery({ queryKey: ['studio-topic-prompt-draft', projectId, project?.topic_ideas_artifact_id], queryFn: () => apiClient.getPendingTopicIdeasPrompt(projectId), enabled: Boolean(projectId), retry: false });
  const scriptPromptDraftQuery = useQuery({ queryKey: ['studio-script-prompt-draft', projectId, project?.selected_topic_ideas_artifact_id, project?.selected_topic_idea_id, project?.script_artifact_id, project?.script_analysis_artifact_id], queryFn: () => apiClient.getPendingScriptAnalysisPrompt(projectId), enabled: Boolean(projectId && project?.selected_topic_ideas_artifact_id && project?.selected_topic_idea_id), retry: false });

  const topicIdeasResult = topicResultFromRead(topicQuery.data);
  const scriptResult = scriptResultFromRead(scriptQuery.data);
  const loadedAnalysis: ScriptAnalysisReadResponse | null = analysisQuery.data ?? null;
  const scriptAnalysisResult = loadedAnalysis?.generated && loadedAnalysis.artifact_id === project?.script_analysis_artifact_id && loadedAnalysis.artifact?.project_id === projectId && loadedAnalysis.artifact.source_script_revision_id === project?.script_artifact_id ? loadedAnalysis : null;
  const loadedStyle = styleResultFromRead(styleQuery.data);
  const selectedStyle = selectDisplayedStyleBible(loadedStyle, projectId, project?.style_bible_artifact_id ?? null);
  const styleBibleResult = selectedStyle && selectedStyle.artifact.lineage.script_analysis_artifact_id === project?.script_analysis_artifact_id && selectedStyle.artifact.lineage.source_script_artifact_id === project?.script_artifact_id ? selectedStyle : null;
  const sceneBreakdownResult: SceneBreakdownReadResponseV4 | null = sceneQuery.data ?? null;
  const scenePlan = sceneBreakdownResult?.scene_plan ?? null;
  const visualPlan = sceneBreakdownResult?.visual_plan ?? null;
  const topicIdeaResolution = resolveSelectedTopicIdea({ project, topicIdeasResult: topicQuery.data });
  const selectedIdea = topicIdeaResolution.idea;
  const continuityReview: ScriptAnalysisContinuityReviewResponse | null = continuityReviewQuery.data && continuityReviewQuery.data.script_analysis_artifact_id === project?.script_analysis_artifact_id ? continuityReviewQuery.data : null;
  const hasScriptDownstream = Boolean(project?.style_bible_artifact_id || project?.style_bible_visual_reference_set_artifact_id || project?.style_bible_visual_reference_approval_set_artifact_id || project?.scene_plan_artifact_id || project?.visual_plan_artifact_id || project?.prompt_bundle_artifact_id || project?.scene_asset_manifest_artifact_id || project?.narration_audio_artifact_id || project?.narration_word_timing_map_artifact_id || project?.beat_timing_map_artifact_id || project?.assembly_plan_artifact_id || project?.subtitle_srt_artifact_id || project?.subtitle_vtt_artifact_id || project?.final_video_artifact_id);

  useEffect(() => {
    setTopicPrompt(null);
    setTopicValidation(null);
  }, [projectId, project?.topic_ideas_artifact_id]);

  useEffect(() => {
    if (!project?.channel_dna_narration_wpm) return;
    setScriptForm((current) => ({ ...current, narration_wpm: project.channel_dna_narration_wpm }));
  }, [projectId, project?.channel_dna_narration_wpm]);

  const appliedTopicDefaultsRef = useRef<string | null>(null);
  useEffect(() => {
    if (!selectedIdea || !projectId || !project?.selected_topic_ideas_artifact_id) return;
    const selectedTopicKey = `${projectId}:${project.selected_topic_ideas_artifact_id}:${selectedIdea.idea_id}`;
    if (appliedTopicDefaultsRef.current === selectedTopicKey) return;
    appliedTopicDefaultsRef.current = selectedTopicKey;
    setScriptForm((current) => ({
      ...current,
      pov_style: selectedIdea.pov_style,
      avoid: selectedIdea.avoid.join(', '),
      pacing_mode: selectedIdea.recommended_pacing_mode,
    }));
  }, [projectId, project?.selected_topic_ideas_artifact_id, selectedIdea]);

  useEffect(() => {
    setScriptPrompt(null);
    setScriptValidation(null);
  }, [projectId, project?.selected_topic_ideas_artifact_id, project?.selected_topic_idea_id, project?.script_artifact_id, project?.script_analysis_artifact_id]);

  const restoredTopicInputRef = useRef<string | null>(null);
  useEffect(() => {
    const restored = topicPromptDraftQuery.data;
    const packageId = restored?.prompt_package?.artifact_ids.topic_ideas;
    const input = restored?.request_input;
    if (!restored?.generated || !packageId || !input || restoredTopicInputRef.current === packageId) return;
    restoredTopicInputRef.current = packageId;
    setTopicForm({
      seed_topic: typeof input.seed_topic === 'string' ? input.seed_topic : '',
      topic_count: typeof input.topic_count === 'number' ? input.topic_count : 10,
      focus_keywords: Array.isArray(input.focus_keywords) ? input.focus_keywords.map(String).join(', ') : '',
      focus: input.focus === 'highest_ctr' || input.focus === 'most_evergreen' || input.focus === 'easiest_to_make' || input.focus === 'most_unique' ? input.focus : 'balanced',
    });
  }, [projectId, topicPromptDraftQuery.data]);

  const restoredScriptInputRef = useRef<string | null>(null);
  useEffect(() => {
    const restored = scriptPromptDraftQuery.data;
    const packageId = restored?.prompt_package?.artifact_ids.script;
    const input = restored?.request_input;
    if (!restored?.generated || !packageId || !input || restoredScriptInputRef.current === packageId) return;
    restoredScriptInputRef.current = packageId;
    setScriptForm((current) => ({
      ...current,
      target_duration_minutes: typeof input.target_duration_minutes === 'number' ? input.target_duration_minutes : current.target_duration_minutes,
      pov_style: typeof input.pov_style === 'string' ? input.pov_style : current.pov_style,
      avoid: Array.isArray(input.avoid) ? input.avoid.map(String).join(', ') : current.avoid,
      section_count_mode: input.section_count_mode === 'backend_plan' ? 'backend_plan' : 'llm_decides',
      pacing_mode: input.pacing_mode === 'fast' || input.pacing_mode === 'slow' ? input.pacing_mode : 'medium',
      narration_wpm: (() => {
        const value = input.narration_wpm;
        if (!value || typeof value !== 'object' || Array.isArray(value)) return current.narration_wpm;
        const range = value as Record<string, unknown>;
        return typeof range.min === 'number' && typeof range.max === 'number'
          ? { min: range.min, max: range.max }
          : current.narration_wpm;
      })(),
    }));
  }, [projectId, scriptPromptDraftQuery.data]);

  useEffect(() => {
    const restored = topicPromptDraftQuery.data;
    if (restored?.generated && restored.prompt_package) setTopicPrompt(restored.prompt_package);
  }, [projectId, topicPromptDraftQuery.data]);

  useEffect(() => {
    const restored = scriptPromptDraftQuery.data;
    if (restored?.generated && restored.prompt_package) setScriptPrompt(restored.prompt_package);
  }, [projectId, selectedIdea?.idea_id, scriptPromptDraftQuery.data]);

  useEffect(() => {
    setImageAspectRatio('16:9');
  }, [projectId]);

  useEffect(() => {
    const loaded = promptBundleQuery.data;
    if (!loaded?.generated || !loaded.artifact || loaded.artifact_id !== project?.prompt_bundle_artifact_id || loaded.artifact.project_id !== projectId) return;
    setImageAspectRatio(loaded.artifact.options.aspect_ratio);
  }, [projectId, project?.prompt_bundle_artifact_id, promptBundleQuery.data]);

  const buildTopic = useMutation({ mutationFn: () => apiClient.buildTopicIdeasPrompt(projectId, { user_input: { seed_topic: topicForm.seed_topic, topic_count: topicForm.topic_count, focus_keywords: csvToList(topicForm.focus_keywords), focus: topicForm.focus } }), onSuccess: (value) => { setTopicPrompt(value); queryClient.setQueryData(['studio-topic-prompt-draft', projectId, project?.topic_ideas_artifact_id], { generated: true, prompt_package: value, request_input: { seed_topic: topicForm.seed_topic, topic_count: topicForm.topic_count, focus_keywords: csvToList(topicForm.focus_keywords), focus: topicForm.focus } }); setTopicValidation(null); } });
  const importTopic = useMutation({ mutationFn: () => apiClient.importTopicIdeas(projectId, externalPayload(topicJson)), onMutate: () => setTopicValidation(null), onError: (error) => setTopicValidation(externalValidationFromError(error)), onSuccess: async (response) => { queryClient.setQueryData(['studio-topic-v4', projectId, response.artifact_id], { generated: true, ...response }); await refreshProject(); setTopicPrompt(null); queryClient.removeQueries({ queryKey: ['studio-topic-prompt-draft', projectId] }); setTopicJson(''); setTopicValidation(null); } });
  const selectTopic = useMutation({ mutationFn: (idea: TopicIdea) => apiClient.selectTopic(projectId, { topic_ideas_artifact_id: topicIdeasResult!.artifact_id, idea_id: idea.idea_id }), onSuccess: (next) => { patchProject(next); setActiveStage('script'); } });

  const scriptPayload = () => { if (!selectedIdea) throw new Error('Select a topic first.'); return { selected_topic_idea_id: selectedIdea.idea_id, user_input: { target_duration_minutes: scriptForm.target_duration_minutes, pov_style: scriptForm.pov_style, avoid: csvToList(scriptForm.avoid), section_count_mode: scriptForm.section_count_mode, pacing_mode: scriptForm.pacing_mode, narration_wpm: scriptForm.narration_wpm } }; };
  const buildScript = useMutation({ mutationFn: () => apiClient.buildScriptAnalysisPrompt(projectId, scriptPayload()), onSuccess: (value) => { setScriptPrompt(value); queryClient.setQueryData(['studio-script-prompt-draft', projectId, project?.selected_topic_ideas_artifact_id, project?.selected_topic_idea_id, project?.script_artifact_id, project?.script_analysis_artifact_id], { generated: true, prompt_package: value, request_input: scriptPayload().user_input }); setScriptValidation(null); } });
  const importScript = useMutation({ mutationFn: () => apiClient.importScriptAnalysis(projectId, externalPayload(scriptJson)), onMutate: () => setScriptValidation(null), onError: (error) => setScriptValidation(externalValidationFromError(error)), onSuccess: async (response) => { queryClient.setQueryData(['studio-script-v4', projectId, response.script_artifact_id], { generated: true, artifact_id: response.script_artifact_id, artifact: response.script }); queryClient.setQueryData<ScriptAnalysisReadResponse>(['studio-script-analysis-v4', projectId, response.script_analysis_artifact_id], { project_id: projectId, generated: true, artifact_id: response.script_analysis_artifact_id, artifact: response.script_analysis }); await refreshProject(); setScriptPrompt(null); queryClient.removeQueries({ queryKey: ['studio-script-prompt-draft', projectId] }); setScriptJson(''); setScriptValidation(null); } });

  const updateContinuity = useMutation({
    mutationFn: ({ continuityRef, removed }: { continuityRef: string; removed: boolean }) => {
      if (!project?.script_analysis_artifact_id || !continuityReview) throw new Error('Current Script Analysis continuity review is unavailable.');
      return apiClient.mutateScriptAnalysisContinuityReview(projectId, {
        expected_script_analysis_artifact_id: project.script_analysis_artifact_id,
        expected_review_artifact_id: continuityReview.review_artifact_id,
        continuity_ref: continuityRef,
        removed,
      });
    },
    onSuccess: async (response) => {
      queryClient.setQueryData<ScriptAnalysisContinuityReviewResponse>(['studio-script-analysis-continuity-review-v4', projectId, response.script_analysis_artifact_id], response);
      await refreshProject();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['studio-style-bible-v4', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['studio-scene-breakdown-v4', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['studio-prompt-bundle-options-v4', projectId] }),
      ]);
    },
  });

  const generateStyle = useMutation({ mutationFn: () => { if (!project?.script_analysis_artifact_id) throw new Error('Import Script & Analysis first.'); return apiClient.generateStyleBibleDraftV4(projectId, { script_analysis_artifact_id: project.script_analysis_artifact_id }); }, onSuccess: async (response) => { queryClient.setQueryData<StyleBibleReadResponseV4>(['studio-style-bible-v4', projectId, response.artifact_id], { project_id: projectId, generated: true, artifact_id: response.artifact_id, artifact: response.artifact }); await refreshProject(); } });
  const updateStyle = useMutation({ mutationFn: (payload: StyleBibleTextUpdatePayloadV4) => apiClient.updateStyleBibleTextV4(projectId, payload), onSuccess: async (response) => { queryClient.setQueryData<StyleBibleReadResponseV4>(['studio-style-bible-v4', projectId, response.artifact_id], { project_id: projectId, generated: true, artifact_id: response.artifact_id, artifact: response.artifact }); await refreshProject(); } });
  const approveStyle = useMutation({ mutationFn: () => apiClient.approveStyleBibleTextV4(projectId, styleBibleResult!.artifact_id), onSuccess: async (response) => { queryClient.setQueryData<StyleBibleReadResponseV4>(['studio-style-bible-v4', projectId, response.artifact_id], { project_id: projectId, generated: true, artifact_id: response.artifact_id, artifact: response.artifact }); await refreshProject(); } });

  const publishScenePair = async (response: import('../api/client').SceneBreakdownRevisionResponseV4) => { cachePublishedScenePair(queryClient, projectId, response); await refreshProject(); };
  const approveScene = useMutation({ mutationFn: () => { if (!sceneBreakdownResult?.scene_plan_artifact_id || !sceneBreakdownResult.visual_plan_artifact_id) throw new Error('A current paired draft is required.'); return apiClient.approveSceneBreakdownV4(projectId, { expected_scene_plan_artifact_id: sceneBreakdownResult.scene_plan_artifact_id, expected_visual_plan_artifact_id: sceneBreakdownResult.visual_plan_artifact_id, aspect_ratio: imageAspectRatio }); }, onSuccess: publishScenePair });
  const validateSceneSectionEdit = async (sectionId: string, section: SceneBreakdownEditableSectionV4): Promise<SceneBreakdownSectionEditValidationResponseV4> => { if (!sceneBreakdownResult?.scene_plan_artifact_id || !sceneBreakdownResult.visual_plan_artifact_id) throw new Error('A current paired draft is required.'); return apiClient.validateSceneBreakdownSectionEditV4(projectId, sectionId, { expected_scene_plan_artifact_id: sceneBreakdownResult.scene_plan_artifact_id, expected_visual_plan_artifact_id: sceneBreakdownResult.visual_plan_artifact_id, section }); };
  const saveSceneSectionEdit = async (sectionId: string, section: SceneBreakdownEditableSectionV4, validationArtifactId: string): Promise<void> => { if (!sceneBreakdownResult?.scene_plan_artifact_id || !sceneBreakdownResult.visual_plan_artifact_id) throw new Error('A current paired draft is required.'); await publishScenePair(await apiClient.saveSceneBreakdownSectionEditV4(projectId, sectionId, { expected_scene_plan_artifact_id: sceneBreakdownResult.scene_plan_artifact_id, expected_visual_plan_artifact_id: sceneBreakdownResult.visual_plan_artifact_id, validation_artifact_id: validationArtifactId, section })); };

  const pairedSceneStatus = scenePlan && visualPlan && visualPlan.source_scene_plan_revision_id === scenePlan.scene_plan_revision_id && scenePlan.status === visualPlan.status ? scenePlan.status : null;
  const hasApprovedVoicePair = Boolean(project?.narration_audio_artifact_id && project?.narration_word_timing_map_artifact_id);
  const hasApprovedVisualReferences = Boolean(project?.style_bible_visual_reference_approval_set_artifact_id);
  const sceneState: SceneBreakdownStageState = pairedSceneStatus ?? (styleBibleResult?.artifact.status === 'text_approved' && hasApprovedVoicePair && hasApprovedVisualReferences ? 'ready' : 'locked');

  const steps = useMemo<PipelineStep[]>(() => {
    const status = (id: StudioStageId, value: PipelineStep['status']): PipelineStep['status'] => activeStage === id ? 'active' : value;
    return [
      { id: 'topic', label: 'Topic Ideas', status: status('topic', selectedIdea ? 'complete' : 'ready'), hint: 'External prompt and validated import.' },
      { id: 'script', label: 'Script & Analysis', status: status('script', scriptResult && scriptAnalysisResult?.artifact ? 'complete' : selectedIdea ? 'ready' : 'locked'), hint: 'One prompt, one atomic import.' },
      { id: 'style_bible', label: 'Style Bible', status: status('style_bible', styleBibleResult?.artifact.status === 'text_approved' ? 'text_approved' : styleBibleResult ? 'draft' : scriptAnalysisResult?.artifact ? 'ready' : 'locked'), hint: 'Deterministic style and continuity.' },
      { id: 'voice', label: 'Voice', status: status('voice', project?.narration_audio_artifact_id && project.narration_word_timing_map_artifact_id ? 'complete' : scriptAnalysisResult?.artifact ? 'ready' : 'locked'), hint: 'Narration audio and word timing.' },
      { id: 'scene', label: 'Scene Breakdown', status: status('scene', sceneState === 'draft_ready' ? 'draft' : sceneState), hint: 'Requires approved Voice pair and visual references.' },
      { id: 'scene_assets', label: 'Scene Assets', status: status('scene_assets', project?.scene_asset_manifest_artifact_id ? 'complete' : project?.prompt_bundle_artifact_id ? 'ready' : 'locked'), hint: 'Generate and review every beat image.' },
      { id: 'assembly', label: 'Assembly', status: status('assembly', project?.assembly_plan_artifact_id ? 'complete' : project?.scene_asset_manifest_artifact_id && project.narration_audio_artifact_id && project.narration_word_timing_map_artifact_id && project.beat_timing_map_artifact_id ? 'ready' : 'locked'), hint: 'Timeline and subtitles.' },
      { id: 'render', label: 'Render', status: status('render', project?.final_video_artifact_id ? 'complete' : project?.assembly_plan_artifact_id ? 'ready' : 'locked'), hint: 'Final approved video.' },
    ];
  }, [activeStage, project, selectedIdea, scriptResult, scriptAnalysisResult, styleBibleResult, sceneState, scenePlan]);

  if (!workspace.selectedChannel) return <EmptyState icon={RadioTower} title="Select a channel" body="Choose a channel before opening Studio." actionLabel="Open Channels" onAction={() => onNavigate('channels')} />;
  if (!project) return <EmptyState icon={Clapperboard} title="Select a project" body="Choose a project before entering the production pipeline." actionLabel="Open Projects" onAction={() => onNavigate('projects')} />;

  const commonError = [topicQuery.error, scriptQuery.error, analysisQuery.error, continuityReviewQuery.error, styleQuery.error, sceneQuery.error, promptBundleQuery.error, buildTopic.error, buildScript.error, approveScene.error].find(Boolean);
  let panel: ReactNode;
  if (activeStage === 'topic') panel = <TopicIdeasPanel topicForm={topicForm} setTopicForm={setTopicForm} topicIdeasResult={topicIdeasResult} selectedIdea={selectedIdea} onSelectIdea={(idea) => selectTopic.mutate(idea)} promptPackage={topicPrompt} resultJson={topicJson} validation={topicValidation} onResultJsonChange={(value) => { setTopicJson(value); setTopicValidation(null); }} onBuildPrompt={() => buildTopic.mutate()} onImport={() => importTopic.mutate()} isBuilding={buildTopic.isPending} isImporting={importTopic.isPending} />;
  else if (activeStage === 'script') panel = <ScriptPanel topicIdeaResolution={topicIdeaResolution} scriptForm={scriptForm} setScriptForm={setScriptForm} promptPackage={scriptPrompt} resultJson={scriptJson} validation={scriptValidation} onResultJsonChange={(value) => { setScriptJson(value); setScriptValidation(null); }} onBuildPrompt={() => buildScript.mutate()} onImport={() => importScript.mutate()} isBuilding={buildScript.isPending} isImporting={importScript.isPending} scriptResult={scriptResult} analysisResult={scriptAnalysisResult} continuityReview={continuityReview} onToggleContinuity={(continuityRef, removed) => updateContinuity.mutate({ continuityRef, removed })} isUpdatingContinuity={updateContinuity.isPending} continuityError={errorMessage(updateContinuity.error ?? continuityReviewQuery.error)} hasDownstream={hasScriptDownstream} />;
  else if (activeStage === 'style_bible') panel = <StyleBiblePanel projectId={projectId} styleBibleResult={styleBibleResult} onGenerate={() => generateStyle.mutate()} onSaveDraft={(payload) => updateStyle.mutate(payload)} onApproveText={() => approveStyle.mutate()} isGenerating={generateStyle.isPending} isSaving={updateStyle.isPending} isApproving={approveStyle.isPending} isHydrating={styleQuery.isFetching} hydrationError={null} mutationError={errorMessage(generateStyle.error ?? updateStyle.error ?? approveStyle.error)} canGenerate={Boolean(project.script_analysis_artifact_id)} generationBlockedReason={null} scriptAnalysisArtifactId={project.script_analysis_artifact_id} />;
  else if (activeStage === 'scene') panel = <SceneBreakdownPanel projectId={projectId} state={sceneState} canImport={Boolean(project.script_artifact_id && project.script_analysis_artifact_id && hasApprovedVoicePair && hasApprovedVisualReferences && styleBibleResult?.artifact.status === 'text_approved')} scenePlan={scenePlan} visualPlan={visualPlan} onPublished={publishScenePair} onApprove={() => approveScene.mutate()} onValidateSection={validateSceneSectionEdit} onSaveSection={saveSceneSectionEdit} aspectRatio={imageAspectRatio} onAspectRatioChange={setImageAspectRatio} pending={approveScene.isPending} />;
  else if (activeStage === 'scene_assets') panel = <SceneAssetsPanel projectId={projectId} promptBundleArtifactId={project.prompt_bundle_artifact_id} enabled={Boolean(project.prompt_bundle_artifact_id)} />;
  else if (activeStage === 'voice') panel = <VoicePanel projectId={projectId} enabled={Boolean(project.script_artifact_id)} />;
  else if (activeStage === 'assembly') panel = <AssemblyPanel projectId={projectId} enabled={Boolean(project.scene_asset_manifest_artifact_id && project.narration_audio_artifact_id && project.narration_word_timing_map_artifact_id && project.beat_timing_map_artifact_id)} />;
  else panel = <RenderPanel projectId={projectId} assemblyId={project.assembly_plan_artifact_id} srtId={project.subtitle_srt_artifact_id} vttId={project.subtitle_vtt_artifact_id} finalVideoId={project.final_video_artifact_id} />;

  return <div className="space-y-5">{commonError ? <ErrorBanner message={errorMessage(commonError) ?? 'Failed to load Studio artifact.'} /> : null}<StudioPipeline steps={steps} activeStepId={activeStage} onSelectStep={(id) => setActiveStage(id as StudioStageId)} panel={panel} /></div>;
}
