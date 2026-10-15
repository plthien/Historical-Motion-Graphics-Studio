import { useEffect, useMemo, useState } from 'react';
import { Braces, CheckCircle2, Eye, RefreshCw, Save, ShieldCheck, SplitSquareVertical, X } from 'lucide-react';
import type {
  ExternalImportValidationResponse,
  ExternalPromptPackage,
  PromptBundleAspectRatio,
  SceneBreakdownEditableSectionV4,
  SceneBreakdownRevisionResponseV4,
  SceneBreakdownSectionEditValidationResponseV4,
  ScenePlanArtifactV4,
  ScenePlanBeatV4,
  ScenePlanSceneV4,
  VisualPlanArtifactV4,
  VisualPlanBeatV4,
  VisualPlanSceneV4,
} from '../../api/client';
import {
  SceneBreakdownAuthoringApiError,
  sceneBreakdownTwoStepApi,
  type ScenePlanCandidateResponseV4,
} from '../../api/sceneBreakdownTwoStep';
import { IconButton } from '../../components/ui/IconButton';
import { MetricCard } from '../../components/ui/MetricCard';
import { SectionCard } from '../../components/ui/SectionCard';
import { StatusBadge } from '../../components/ui/StatusBadge';
import { PromptImportWorkspace } from './PromptImportWorkspace';

export type SceneBreakdownStageState = 'locked' | 'ready' | 'draft_ready' | 'approved' | 'failed';
type AuthoringStep = 'scene' | 'visual';

type EditorState = {
  text: string;
  validatedText: string | null;
  validationArtifactId: string | null;
  errors: Array<{ path: string; code: string; message: string }>;
  warnings: string[];
  validating: boolean;
  saving: boolean;
};

const NO_LIGHTING_OVERRIDE = 'No beat-specific lighting override.';
const NO_COLOR_OVERRIDE = 'No beat-specific color override.';
type DurationStats = { average: number; median: number; p90: number; max: number };

function durationStats(values: number[]): DurationStats {
  if (!values.length) return { average: 0, median: 0, p90: 0, max: 0 };
  const ordered = [...values].sort((a, b) => a - b);
  const average = values.reduce((sum, value) => sum + value, 0) / values.length;
  const middle = Math.floor(ordered.length / 2);
  const median = ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
  const p90 = ordered[Math.max(0, Math.ceil(ordered.length * 0.9) - 1)];
  return { average, median, p90, max: ordered[ordered.length - 1] };
}

function validationFromError(error: unknown): ExternalImportValidationResponse {
  if (error instanceof SceneBreakdownAuthoringApiError) {
    const rawErrors = error.details?.errors;
    const errors = Array.isArray(rawErrors)
      ? rawErrors.map((item) => {
          const issue = item && typeof item === 'object' ? item as Record<string, unknown> : {};
          return { path: String(issue.path ?? '$'), code: String(issue.code ?? error.errorCode), message: String(issue.message ?? error.message) };
        })
      : [{ path: '$', code: error.errorCode, message: error.message }];
    return { valid: false, errors, warnings: [], summary: {} };
  }
  return { valid: false, errors: [{ path: '$', code: 'SCENE_BREAKDOWN_AUTHORING_FAILED', message: error instanceof Error ? error.message : 'Scene Breakdown authoring failed.' }], warnings: [], summary: {} };
}

function RefList({ label, values }: { label: string; values: string[] }) {
  return values.length ? <div className="flex flex-wrap items-center gap-1.5"><span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">{label}</span>{values.map((value) => <span key={value} className="rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1 font-mono text-[10px] text-slate-300">{value}</span>)}</div> : null;
}

function visualForEdit(beat: VisualPlanBeatV4): Record<string, unknown> {
  return {
    representation_family: beat.representation_family,
    selected_character_refs: beat.selected_character_refs,
    selected_location_refs: beat.selected_location_refs,
    selected_prop_refs: beat.selected_prop_refs,
    selected_recurring_visual_subject_refs: beat.selected_recurring_visual_subject_refs,
    selected_motif_refs: beat.selected_motif_refs,
    visual_concept: beat.visual_concept,
    subject_action: beat.subject_action,
    environment_emphasis: beat.environment_emphasis,
    visible_text: beat.visible_text,
    framing: beat.framing,
    camera_angle: beat.camera_angle,
    camera_movement: beat.camera_movement,
    composition: beat.composition,
    lighting_direction: beat.lighting_direction === NO_LIGHTING_OVERRIDE ? null : beat.lighting_direction,
    color_emphasis: beat.color_emphasis === NO_COLOR_OVERRIDE ? null : beat.color_emphasis,
    continuity_with_previous: beat.continuity_with_previous,
    previous_beat_image_reference_required: beat.previous_beat_image_reference_required,
  };
}

function editableSection(scenePlan: ScenePlanArtifactV4, visualPlan: VisualPlanArtifactV4, sectionId: string): SceneBreakdownEditableSectionV4 {
  const visualByBeat = new Map(visualPlan.scenes.flatMap((scene) => scene.beat_plans).map((beat) => [beat.beat_id, beat]));
  return {
    section_id: sectionId,
    scenes: scenePlan.scenes.filter((scene) => scene.section_id === sectionId).sort((a, b) => a.local_scene_order - b.local_scene_order).map((scene) => ({
      beats: [...scene.beats].sort((a, b) => a.local_beat_order - b.local_beat_order).map((beat) => {
        const visual = visualByBeat.get(beat.beat_id);
        if (!visual) throw new Error(`Missing paired visual for ${beat.beat_id}.`);
        return { script_text: beat.script_quote, visual: visualForEdit(visual) };
      }),
    })),
  };
}

function emptyEditor(scenePlan: ScenePlanArtifactV4, visualPlan: VisualPlanArtifactV4, sectionId: string): EditorState {
  return { text: JSON.stringify(editableSection(scenePlan, visualPlan, sectionId), null, 2), validatedText: null, validationArtifactId: null, errors: [], warnings: [], validating: false, saving: false };
}

function VisualDetails({ beat }: { beat: VisualPlanBeatV4 }) {
  const entries: Array<[string, string | string[]]> = [
    ['Visual concept', beat.visual_concept],
    ['Subject / action', beat.subject_action],
    ['Environment', beat.environment_emphasis],
    ['Visible text', beat.visible_text],
    ['Production notes', beat.production_notes],
    ['Avoid', beat.negative_requirements],
  ];
  return <div className="grid gap-2 text-xs text-slate-400 md:grid-cols-2">{entries.map(([label, value]) => <div key={label} className="rounded-lg border border-white/10 bg-white/[0.025] p-3"><div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">{label}</div><div className="mt-1 whitespace-pre-wrap text-slate-200">{Array.isArray(value) ? (value.length ? value.join(' · ') : 'None') : value}</div></div>)}</div>;
}

function BeatCard({ sceneBeat, visualBeat }: { sceneBeat: ScenePlanBeatV4; visualBeat: VisualPlanBeatV4 }) {
  return <article className="rounded-xl border border-white/10 bg-cockpit-950/45 p-4" data-testid="scene-breakdown-beat-card">
    <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-xs text-violet-200">{sceneBeat.beat_id}</span><StatusBadge status="image" /><StatusBadge status={sceneBeat.visual_priority} /><span className="text-xs text-slate-500">{sceneBeat.estimated_duration_seconds.toFixed(1)}s</span></div>
    <div className="mt-4 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">Narration</div><blockquote className="mt-1 border-l-2 border-slate-600 pl-3 text-sm leading-6 text-slate-200">{sceneBeat.script_quote}</blockquote>
    <div className="mt-4"><VisualDetails beat={visualBeat} /></div>
    <div className="mt-4 grid gap-2 text-xs text-slate-400 md:grid-cols-2"><div><b>Shot:</b> {visualBeat.framing} · {visualBeat.camera_angle}</div><div><b>Movement:</b> {visualBeat.camera_movement}</div><div className="md:col-span-2"><b>Composition:</b> {visualBeat.composition}</div>{visualBeat.lighting_direction !== NO_LIGHTING_OVERRIDE ? <div><b>Lighting:</b> {visualBeat.lighting_direction}</div> : null}{visualBeat.color_emphasis !== NO_COLOR_OVERRIDE ? <div><b>Color:</b> {visualBeat.color_emphasis}</div> : null}<div><b>Continuity:</b> {visualBeat.continuity_with_previous}</div><div className="font-mono text-[10px] text-slate-500">{visualBeat.continuity_group_id}</div></div>
    <div className="mt-3 space-y-1"><RefList label="Characters" values={visualBeat.selected_character_refs} /><RefList label="Locations" values={visualBeat.selected_location_refs} /><RefList label="Props" values={visualBeat.selected_prop_refs} /><RefList label="Subjects" values={visualBeat.selected_recurring_visual_subject_refs} /><RefList label="Motifs" values={visualBeat.selected_motif_refs} /></div>
  </article>;
}

function SceneDrawer({ scene, visualScene, onClose, onPrevious, onNext }: { scene: ScenePlanSceneV4; visualScene: VisualPlanSceneV4; onClose: () => void; onPrevious: (() => void) | null; onNext: (() => void) | null }) {
  const visuals = new Map(visualScene.beat_plans.map((beat) => [beat.beat_id, beat]));
  return <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/70 backdrop-blur-sm" data-testid="scene-breakdown-drawer"><button type="button" className="min-w-0 flex-1" onClick={onClose} aria-label="Close scene details" /><aside className="h-full w-full max-w-3xl overflow-y-auto border-l border-white/10 bg-cockpit-950 p-5 shadow-2xl"><div className="sticky top-0 z-10 -mx-5 -mt-5 mb-5 border-b border-white/10 bg-cockpit-950/95 p-5 backdrop-blur"><div className="flex items-start justify-between gap-3"><div><div className="font-mono text-xs text-cyan-200">{scene.scene_id} · Scene {scene.scene_order}</div><h2 className="mt-2 text-xl font-semibold text-white">Scene {scene.scene_order}</h2><p className="mt-1 text-sm text-slate-400">{scene.beats.length} beats · {scene.beats.reduce((sum, beat) => sum + beat.estimated_duration_seconds, 0).toFixed(1)}s</p></div><IconButton icon={X} label="Close" onClick={onClose} /></div><div className="mt-4 flex justify-end gap-2"><IconButton label="Previous Scene" onClick={onPrevious ?? (() => undefined)} disabled={!onPrevious} /><IconButton label="Next Scene" onClick={onNext ?? (() => undefined)} disabled={!onNext} /></div></div><div className="space-y-4">{scene.beats.map((beat) => { const visual = visuals.get(beat.beat_id); return visual ? <BeatCard key={beat.beat_id} sceneBeat={beat} visualBeat={visual} /> : <div key={beat.beat_id} className="rounded-xl border border-rose-400/25 bg-rose-500/[0.05] p-4 text-sm text-rose-100">Missing paired visual for {beat.beat_id}.</div>; })}</div></aside></div>;
}

export function SceneBreakdownPanel(props: {
  projectId: string;
  state: SceneBreakdownStageState;
  canImport: boolean;
  scenePlan: ScenePlanArtifactV4 | null;
  visualPlan: VisualPlanArtifactV4 | null;
  onPublished: (response: SceneBreakdownRevisionResponseV4) => Promise<void>;
  onApprove: () => void;
  onValidateSection: (sectionId: string, section: SceneBreakdownEditableSectionV4) => Promise<SceneBreakdownSectionEditValidationResponseV4>;
  onSaveSection: (sectionId: string, section: SceneBreakdownEditableSectionV4, validationArtifactId: string) => Promise<void>;
  aspectRatio: PromptBundleAspectRatio;
  onAspectRatioChange: (value: PromptBundleAspectRatio) => void;
  pending: boolean;
}) {
  const projectId = props.projectId;
  const [authoringStep, setAuthoringStep] = useState<AuthoringStep>('scene');
  const [scenePrompt, setScenePrompt] = useState<ExternalPromptPackage | null>(null);
  const [sceneJson, setSceneJson] = useState('');
  const [sceneValidation, setSceneValidation] = useState<ExternalImportValidationResponse | null>(null);
  const [sceneBuilding, setSceneBuilding] = useState(false);
  const [sceneImporting, setSceneImporting] = useState(false);
  const [sceneCandidate, setSceneCandidate] = useState<ScenePlanArtifactV4 | null>(null);
  const [visualPrompt, setVisualPrompt] = useState<ExternalPromptPackage | null>(null);
  const [visualJson, setVisualJson] = useState('');
  const [visualValidation, setVisualValidation] = useState<ExternalImportValidationResponse | null>(null);
  const [visualBuilding, setVisualBuilding] = useState(false);
  const [visualImporting, setVisualImporting] = useState(false);
  const [activeSectionId, setActiveSectionId] = useState<string | null>(null);
  const [selectedSceneId, setSelectedSceneId] = useState<string | null>(null);
  const [editors, setEditors] = useState<Record<string, EditorState>>({});
  const dirtyEditor = Object.keys(editors).length > 0;

  const effectiveScenePlan = props.scenePlan;
  const effectiveVisualPlan = props.visualPlan;
  const pairValid = Boolean(effectiveScenePlan && effectiveVisualPlan && effectiveVisualPlan.source_scene_plan_revision_id === effectiveScenePlan.scene_plan_revision_id && effectiveVisualPlan.scene_count === effectiveScenePlan.scene_count && effectiveVisualPlan.beat_count === effectiveScenePlan.beat_count);

  useEffect(() => {
    if (!projectId || !props.canImport) return;
    let active = true;
    void (async () => {
      try {
        const visual = await sceneBreakdownTwoStepApi.getVisualPrompt(projectId);
        if (!active) return;
        const candidate = visual.request_input?.scene_plan as ScenePlanArtifactV4 | undefined;
        if (candidate) setSceneCandidate(candidate);
        if (visual.rebuild_required) {
          setVisualPrompt(null);
          setVisualJson('');
          setVisualValidation(null);
          setAuthoringStep('visual');
          return;
        }
        if (visual.generated && visual.prompt_package) {
          setVisualPrompt(visual.prompt_package);
          setAuthoringStep('visual');
          return;
        }
        const scene = await sceneBreakdownTwoStepApi.getScenePrompt(projectId);
        if (!active) return;
        if (scene.generated && scene.prompt_package) setScenePrompt(scene.prompt_package);
      } catch {
        // A missing/stale pending reservation is equivalent to no draft prompt.
      }
    })();
    return () => { active = false; };
  }, [projectId, props.canImport]);

  useEffect(() => {
    setScenePrompt(null);
    setSceneJson('');
    setSceneValidation(null);
    setSceneCandidate(null);
    setVisualPrompt(null);
    setVisualJson('');
    setVisualValidation(null);
    setAuthoringStep('scene');
  }, [projectId]);

  useEffect(() => {
    if (!effectiveScenePlan?.sections.length) { setActiveSectionId(null); setSelectedSceneId(null); return; }
    if (!activeSectionId || !effectiveScenePlan.sections.some((section) => section.section_id === activeSectionId)) setActiveSectionId(effectiveScenePlan.sections[0].section_id);
  }, [effectiveScenePlan?.scene_plan_revision_id, activeSectionId]);

  const buildScenePrompt = async () => {
    if (!projectId) return;
    setSceneBuilding(true); setSceneValidation(null);
    try { setScenePrompt(await sceneBreakdownTwoStepApi.buildScenePrompt(projectId)); setSceneCandidate(null); setVisualPrompt(null); setVisualJson(''); setVisualValidation(null); }
    catch (error) { setSceneValidation(validationFromError(error)); }
    finally { setSceneBuilding(false); }
  };

  const buildVisualPrompt = async () => {
    if (!projectId || !sceneCandidate) return;
    setVisualBuilding(true); setVisualValidation(null);
    try {
      setVisualPrompt(await sceneBreakdownTwoStepApi.buildVisualPrompt(projectId));
      setVisualJson('');
      setVisualValidation(null);
      setAuthoringStep('visual');
    } catch (error) { setVisualValidation(validationFromError(error)); }
    finally { setVisualBuilding(false); }
  };

  const importScene = async () => {
    if (!projectId || !sceneJson.trim()) return;
    setSceneImporting(true); setSceneValidation(null);
    try {
      const validation = await sceneBreakdownTwoStepApi.validateSceneImport(projectId, { result_json: sceneJson });
      setSceneValidation(validation);
      if (!validation.valid) return;
      const response: ScenePlanCandidateResponseV4 = await sceneBreakdownTwoStepApi.importScene(projectId, { result_json: sceneJson });
      setSceneCandidate(response.scene_plan);
      setVisualPrompt(response.visual_prompt_package);
      setAuthoringStep('visual');
      setVisualJson(''); setVisualValidation(null);
    } catch (error) { setSceneValidation(validationFromError(error)); }
    finally { setSceneImporting(false); }
  };

  const importVisual = async () => {
    if (!projectId || !visualJson.trim()) return;
    setVisualImporting(true); setVisualValidation(null);
    try {
      const validation = await sceneBreakdownTwoStepApi.validateVisualImport(projectId, { result_json: visualJson });
      setVisualValidation(validation);
      if (!validation.valid) return;
      const response = await sceneBreakdownTwoStepApi.importVisual(projectId, { result_json: visualJson });
      await props.onPublished(response);
      setSceneCandidate(null);
      setVisualPrompt(null);
      setScenePrompt(null);
      setSceneJson(''); setVisualJson('');
    } catch (error) { setVisualValidation(validationFromError(error)); }
    finally { setVisualImporting(false); }
  };

  const activeSection = effectiveScenePlan?.sections.find((section) => section.section_id === activeSectionId) ?? null;
  const activeScenes = useMemo(() => effectiveScenePlan?.scenes.filter((scene) => scene.section_id === activeSectionId).sort((a, b) => a.local_scene_order - b.local_scene_order) ?? [], [effectiveScenePlan, activeSectionId]);
  const visualSceneById = useMemo(() => new Map(effectiveVisualPlan?.scenes.map((scene) => [scene.scene_id, scene]) ?? []), [effectiveVisualPlan]);
  const selectedSceneIndex = activeScenes.findIndex((scene) => scene.scene_id === selectedSceneId);
  const selectedScene = selectedSceneIndex >= 0 ? activeScenes[selectedSceneIndex] : null;
  const selectedVisualScene = selectedScene ? visualSceneById.get(selectedScene.scene_id) ?? null : null;
  const pacingStats = useMemo(() => durationStats(effectiveScenePlan?.scenes.flatMap((scene) => scene.beats.map((beat) => beat.estimated_duration_seconds)) ?? []), [effectiveScenePlan]);
  const activePacingStats = useMemo(() => durationStats(activeScenes.flatMap((scene) => scene.beats.map((beat) => beat.estimated_duration_seconds))), [activeScenes]);
  const timingMetrics = effectiveScenePlan?.backend_qc.timing_metrics ?? null;
  const activeTiming = effectiveScenePlan?.backend_qc.timing_sections?.find((item) => item.section_id === activeSectionId)?.timing_metrics ?? null;

  const actions = <>{effectiveScenePlan?.status === 'draft_ready' && effectiveVisualPlan?.status === 'draft_ready' ? <div className="flex flex-wrap items-end gap-2"><label className="text-xs text-slate-400">Image aspect ratio<select aria-label="Image aspect ratio" value={props.aspectRatio} onChange={(event) => props.onAspectRatioChange(event.target.value as PromptBundleAspectRatio)} disabled={props.pending || dirtyEditor} className="mt-1 block rounded-lg border border-white/10 bg-cockpit-950 px-3 py-2 text-sm text-white"><option value="16:9">16:9</option><option value="9:16">9:16</option><option value="1:1">1:1</option><option value="4:3">4:3</option><option value="3:4">3:4</option></select></label><IconButton icon={CheckCircle2} label={props.pending ? 'Building prompts…' : 'Approve & Build Prompts'} onClick={props.onApprove} disabled={props.pending || dirtyEditor || !pairValid} variant="primary" /></div> : null}</>;

  const updateEditor = (sectionId: string, patch: Partial<EditorState>) => setEditors((current) => ({ ...current, [sectionId]: { ...current[sectionId], ...patch } }));
  const parseEditor = (sectionId: string): SceneBreakdownEditableSectionV4 | null => {
    try { return JSON.parse(editors[sectionId].text) as SceneBreakdownEditableSectionV4; }
    catch (error) { updateEditor(sectionId, { validationArtifactId: null, validatedText: null, errors: [{ path: '$', code: 'invalid_json', message: error instanceof Error ? error.message : 'Invalid JSON.' }], warnings: [] }); return null; }
  };
  const validateEditor = async (sectionId: string) => {
    const parsed = parseEditor(sectionId); if (!parsed) return;
    updateEditor(sectionId, { validating: true, errors: [], warnings: [], validationArtifactId: null, validatedText: null });
    try { const result = await props.onValidateSection(sectionId, parsed); updateEditor(sectionId, { validating: false, errors: result.errors, warnings: result.warnings, validationArtifactId: result.validation_artifact_id, validatedText: result.valid && result.validation_artifact_id ? editors[sectionId].text : null }); }
    catch (error) { updateEditor(sectionId, { validating: false, errors: [{ path: '$', code: 'validation_request_failed', message: error instanceof Error ? error.message : 'Validation request failed.' }] }); }
  };
  const saveEditor = async (sectionId: string) => {
    const editor = editors[sectionId]; if (!editor.validationArtifactId || editor.validatedText !== editor.text) return;
    const parsed = parseEditor(sectionId); if (!parsed) return;
    updateEditor(sectionId, { saving: true });
    try { await props.onSaveSection(sectionId, parsed, editor.validationArtifactId); setEditors((current) => { const next = { ...current }; delete next[sectionId]; return next; }); }
    catch (error) { updateEditor(sectionId, { saving: false, validationArtifactId: null, validatedText: null, errors: [{ path: '$', code: 'save_failed', message: error instanceof Error ? error.message : 'Save failed. Validate again.' }] }); }
  };
  const editor = activeSectionId ? editors[activeSectionId] : undefined;

  return <SectionCard title="Scene Breakdown" description="Two-step authoring in one workspace: lock semantic Scene/Beat states first, then author visuals against those exact Beats. Scene Plan and Visual Plan still publish and approve as one atomic pair." actions={actions}><div className="space-y-5" data-stage-state={props.state}>
    <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.03] p-4"><StatusBadge status={props.state} />{effectiveScenePlan ? <span className="font-mono text-xs text-slate-400">scene {effectiveScenePlan.scene_plan_revision_id}</span> : null}{effectiveVisualPlan ? <span className="font-mono text-xs text-slate-400">visual {effectiveVisualPlan.visual_plan_revision_id}</span> : null}</div>

    <div className="grid gap-3 md:grid-cols-2" data-testid="scene-breakdown-authoring-steps">
      <button type="button" onClick={() => setAuthoringStep('scene')} className={`rounded-2xl border p-4 text-left ${authoringStep === 'scene' ? 'border-cyan-400/40 bg-cyan-500/[0.08]' : 'border-white/10 bg-white/[0.025]'}`}><div className="text-[10px] font-semibold uppercase tracking-[0.2em] text-cyan-300">Step 1</div><div className="mt-1 font-semibold text-white">Scene & Beat Plan</div><div className="mt-1 text-xs leading-5 text-slate-400">Lock exact source partition, Beat meaning/state/delta, timing and continuity. No final visual prose.</div>{sceneCandidate ? <div className="mt-2 text-xs text-emerald-300">Locked · {sceneCandidate.scene_count} scenes · {sceneCandidate.beat_count} beats</div> : null}</button>
      <button type="button" onClick={() => sceneCandidate && setAuthoringStep('visual')} disabled={!sceneCandidate} className={`rounded-2xl border p-4 text-left disabled:cursor-not-allowed disabled:opacity-45 ${authoringStep === 'visual' ? 'border-violet-400/40 bg-violet-500/[0.08]' : 'border-white/10 bg-white/[0.025]'}`}><div className="text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-300">Step 2</div><div className="mt-1 font-semibold text-white">Visual Plan</div><div className="mt-1 text-xs leading-5 text-slate-400">Author one visual for each locked Beat ID. Segmentation, timing, semantic state and reset authority cannot change.</div>{visualPrompt ? <div className="mt-2 text-xs text-emerald-300">Ready from locked Step 1</div> : sceneCandidate ? <div className="mt-2 text-xs text-amber-300">Build or rebuild the Step-2 prompt</div> : <div className="mt-2 text-xs text-slate-500">Complete Step 1 first</div>}</button>
    </div>

    {authoringStep === 'scene' ? <PromptImportWorkspace title="Step 1 · Scene & Beat Plan" description="Use backend-owned micro-Scenes and legal Beat boundaries to choose the semantic Beat partition and lock current-state deltas. Import materializes the exact source/timing spans, then creates the Visual prompt without publishing project Scene/Visual pointers." promptPackage={scenePrompt} resultJson={sceneJson} validation={sceneValidation} isBuilding={sceneBuilding} isImporting={sceneImporting} buildDisabled={!props.canImport || dirtyEditor} importDisabled={dirtyEditor} onBuildPrompt={() => void buildScenePrompt()} onResultJsonChange={(value) => { setSceneJson(value); setSceneValidation(null); }} onImport={() => void importScene()} /> : <PromptImportWorkspace title="Step 2 · Visual Plan" description="The prompt is bound to the exact locked Step-1 Beat IDs and states. It can author visual direction only; it cannot re-segment narration or move semantic/reveal authority." promptPackage={visualPrompt} resultJson={visualJson} validation={visualValidation} isBuilding={visualBuilding} isImporting={visualImporting} buildDisabled={!props.canImport || dirtyEditor || !sceneCandidate} importDisabled={dirtyEditor || !visualPrompt} onBuildPrompt={() => void buildVisualPrompt()} onResultJsonChange={(value) => { setVisualJson(value); setVisualValidation(null); }} onImport={() => void importVisual()} />}

    {sceneCandidate && !effectiveVisualPlan ? <div className="rounded-2xl border border-cyan-400/20 bg-cyan-500/[0.05] p-4 text-sm text-cyan-50/80">Step 1 candidate is locked in the pending authoring reservation. Downstream project pointers are unchanged until Step 2 Visual Plan passes and the pair is published atomically.</div> : null}
    {effectiveScenePlan && effectiveVisualPlan && !pairValid ? <div className="rounded-2xl border border-rose-400/25 bg-rose-500/[0.06] p-4 text-sm text-rose-100">The current Scene Plan and Visual Plan do not form an exact pair. Review and approval are blocked.</div> : null}

    {effectiveScenePlan && effectiveVisualPlan && pairValid ? <>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><MetricCard icon={SplitSquareVertical} label="Sections" value={effectiveScenePlan.sections.length} /><MetricCard icon={SplitSquareVertical} label="Scenes" value={effectiveScenePlan.scene_count} /><MetricCard icon={SplitSquareVertical} label="Beats" value={effectiveScenePlan.beat_count} /><MetricCard icon={RefreshCw} label="Expected Duration" value={`${effectiveScenePlan.total_expected_duration_seconds.toFixed(1)}s`} /></div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5" data-testid="scene-breakdown-pacing-metrics"><MetricCard label="Avg Beat" value={`${pacingStats.average.toFixed(1)}s`} tone="info" /><MetricCard label="Median Beat" value={`${pacingStats.median.toFixed(1)}s`} /><MetricCard label="P90 Beat" value={`${pacingStats.p90.toFixed(1)}s`} /><MetricCard label="Max Beat" value={`${(timingMetrics?.max_beat_duration_seconds ?? pacingStats.max).toFixed(1)}s`} /><MetricCard label="Timing Policy" value={effectiveScenePlan.backend_qc.timing_policy_version ?? '—'} /></div>
      <div className="flex gap-2 overflow-x-auto pb-2" data-testid="scene-breakdown-section-selector">{effectiveScenePlan.sections.map((section) => <button key={section.section_id} type="button" onClick={() => { if (!dirtyEditor || section.section_id === activeSectionId) { setActiveSectionId(section.section_id); setSelectedSceneId(null); } }} disabled={dirtyEditor && section.section_id !== activeSectionId} className={`min-w-52 rounded-xl border px-4 py-3 text-left ${section.section_id === activeSectionId ? 'border-cyan-400/40 bg-cyan-500/[0.08]' : 'border-white/10 bg-white/[0.025]'}`}><div className="font-mono text-[10px] text-cyan-200">Section {section.section_order} · {section.section_id}</div><div className="mt-1 truncate text-sm font-semibold text-slate-100">{section.section_title}</div><div className="mt-1 text-xs text-slate-500">{section.scene_ids.length} scenes · {section.beat_ids.length} beats</div><div className="mt-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-violet-300">{section.pacing_density} density</div></button>)}</div>
      {activeSection ? <div className="rounded-2xl border border-white/10 bg-white/[0.025] p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="font-mono text-xs text-cyan-200">{activeSection.section_id} · Section {activeSection.section_order}</div><h3 className="mt-1 text-lg font-semibold text-white">{activeSection.section_title}</h3><p className="mt-1 text-sm text-slate-400">{activeSection.section_role} · {activeSection.pacing_density} density · {activeScenes.length} scenes · {activeSection.beat_ids.length} beats · {activePacingStats.average.toFixed(1)}s avg{activeTiming ? ` · ${activeTiming.planned_narration_wpm} WPM · preferred ${activeTiming.preferred_hold_seconds.min}–${activeTiming.preferred_hold_seconds.max}s · ${(activeTiming.within_preferred_ratio * 100).toFixed(0)}% in band` : ''}</p></div><div className="flex flex-wrap gap-2">{effectiveScenePlan.status === 'draft_ready' ? <IconButton icon={Braces} label={editor ? 'Editing JSON' : 'Edit JSON'} onClick={() => { if (!editor) setEditors((current) => ({ ...current, [activeSection.section_id]: emptyEditor(effectiveScenePlan, effectiveVisualPlan, activeSection.section_id) })); }} disabled={props.pending} /> : null}</div></div>
        {editor ? <div className="mt-4 space-y-3" data-testid="scene-breakdown-json-editor"><div className="rounded-xl border border-cyan-400/20 bg-cyan-500/[0.05] p-3 text-xs leading-5 text-cyan-50/80">Post-authoring review edits still operate on the published draft pair. Keys, scene count, beat count, array order, <code>section_id</code>, and every Beat <code>script_text</code> source span are immutable. To change segmentation, rebuild/repartition Step 1. Any edit after validation requires validation again.</div><textarea value={editor.text} onChange={(event) => updateEditor(activeSection.section_id, { text: event.target.value, validatedText: null, validationArtifactId: null, errors: [], warnings: [] })} spellCheck={false} className="h-[560px] max-h-[560px] w-full resize-y overflow-auto rounded-xl border border-white/10 bg-cockpit-950/70 p-4 font-mono text-xs leading-5 text-slate-200 outline-none focus:border-cyan-400/40" />{editor.errors.length ? <div className="rounded-xl border border-rose-400/20 bg-rose-500/[0.05] p-3 text-sm text-rose-100">{editor.errors.map((item) => <div key={`${item.path}:${item.code}`}><span className="font-mono text-xs">{item.path}</span> · {item.code}: {item.message}</div>)}</div> : null}{editor.warnings.length ? <div className="rounded-xl border border-amber-400/20 bg-amber-500/[0.05] p-3 text-sm text-amber-100">{editor.warnings.map((item) => <div key={item}>{item}</div>)}</div> : null}<div className="flex flex-wrap justify-end gap-2"><IconButton icon={X} label="Cancel" onClick={() => setEditors((current) => { const next = { ...current }; delete next[activeSection.section_id]; return next; })} disabled={editor.validating || editor.saving} /><IconButton label="Format JSON" onClick={() => { const parsed = parseEditor(activeSection.section_id); if (parsed) updateEditor(activeSection.section_id, { text: JSON.stringify(parsed, null, 2), validatedText: null, validationArtifactId: null }); }} disabled={editor.validating || editor.saving} /><IconButton icon={ShieldCheck} label={editor.validating ? 'Validating…' : 'Validate'} onClick={() => void validateEditor(activeSection.section_id)} disabled={editor.validating || editor.saving} /><IconButton icon={Save} label={editor.saving ? 'Saving…' : 'Save Section'} onClick={() => void saveEditor(activeSection.section_id)} disabled={editor.saving || editor.validating || !editor.validationArtifactId || editor.validatedText !== editor.text} variant="primary" /></div></div> : <div className="mt-4 grid gap-4 md:grid-cols-2 2xl:grid-cols-3" data-testid="scene-breakdown-card-grid">{activeScenes.map((scene) => { const visualScene = visualSceneById.get(scene.scene_id); const selectedRefs = visualScene ? Array.from(new Set(visualScene.beat_plans.flatMap((beat) => [...beat.selected_character_refs, ...beat.selected_location_refs, ...beat.selected_prop_refs, ...beat.selected_recurring_visual_subject_refs, ...beat.selected_motif_refs]))) : []; return <article key={scene.scene_id} className="flex min-h-64 flex-col rounded-2xl border border-white/10 bg-cockpit-950/35 p-4" data-testid="scene-breakdown-scene-card"><div className="flex items-center justify-between gap-2"><span className="font-mono text-xs text-cyan-200">{scene.scene_id} · Scene {scene.scene_order}</span><StatusBadge status={scene.beats.some((beat) => beat.visual_priority === 'critical') ? 'critical' : scene.beats.some((beat) => beat.visual_priority === 'high') ? 'high' : 'normal'} /></div><h4 className="mt-3 max-h-[72px] overflow-hidden text-base font-semibold leading-6 text-white">Scene {scene.scene_order}</h4><div className="mt-3 text-xs text-slate-500">{scene.beats.length} beats · {scene.beats.reduce((sum, beat) => sum + beat.estimated_duration_seconds, 0).toFixed(1)}s</div><div className="mt-3"><span className="rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1 text-[10px] text-slate-300">{scene.beats.length} generated images</span></div><div className="mt-3 flex-1"><RefList label="Selected refs" values={selectedRefs.slice(0, 6)} /></div><div className="mt-4"><IconButton icon={Eye} label="Open Scene" onClick={() => setSelectedSceneId(scene.scene_id)} disabled={!visualScene} /></div></article>; })}</div>}
      </div> : null}
      {effectiveScenePlan.backend_qc.warnings.length || effectiveScenePlan.backend_qc.timing_warnings.length || effectiveVisualPlan.backend_qc.warnings.length ? <div className="rounded-2xl border border-amber-400/20 bg-amber-500/[0.05] p-4 text-sm text-amber-100">{[...effectiveScenePlan.backend_qc.warnings, ...effectiveScenePlan.backend_qc.timing_warnings, ...effectiveVisualPlan.backend_qc.warnings].map((warning) => <div key={warning}>{warning}</div>)}</div> : null}
    </> : null}
    {!effectiveScenePlan && !props.canImport ? <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.02] p-8 text-sm leading-6 text-slate-400">Scene Breakdown is locked until Script Analysis, the text-approved Style Bible, required visual-reference approvals, and approved Voice timing are current.</div> : null}
    {selectedScene && selectedVisualScene ? <SceneDrawer scene={selectedScene} visualScene={selectedVisualScene} onClose={() => setSelectedSceneId(null)} onPrevious={selectedSceneIndex > 0 ? () => setSelectedSceneId(activeScenes[selectedSceneIndex - 1].scene_id) : null} onNext={selectedSceneIndex >= 0 && selectedSceneIndex < activeScenes.length - 1 ? () => setSelectedSceneId(activeScenes[selectedSceneIndex + 1].scene_id) : null} /> : null}
  </div></SectionCard>;
}
