import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ActiveOperationsDrawer } from './ActiveOperationsDrawer';
import { sceneAssetsExecutionQueryKey, sceneAssetsWorkspaceQueryKey } from './sceneAssetsQueryCache';
import { useProgressiveLimit } from './useProgressiveLimit';
import {
  sceneAssetsV2Client,
  SceneAssetV2ApiError,
  type SceneAssetV2Beat,
  type SceneAssetV2BeatResponse,
  type SceneAssetV2Candidate,
  type SceneAssetV2ExecutionStatusResponse,
  type SceneAssetV2OperationStatusResponse,
  type SceneAssetV2MutationResponse,
  type SceneAssetV2SectionApprovalItem,
  type SceneAssetV2ResolutionStatus,
  type SceneAssetV2Scene,
  type SceneAssetV2WorkspaceResponse,
} from '../../api/sceneAssetsV2';

const ACTIVE_OPERATION_STATUSES = new Set(['queued', 'running', 'finalizing']);
const TERMINAL_OPERATION_BEAT_STATUSES = new Set<string>(['candidate_ready', 'failed', 'cancelled']);
const POLL_INTERVAL_MS = 2000;
const SCENE_ASSET_INITIAL_SCENE_WINDOW = 4;
const SCENE_ASSET_SCENE_WINDOW_STEP = 4;
const COLLAPSED_SECONDARY_CANDIDATE_LIMIT = 2;

type BeatFilter = 'all' | SceneAssetV2ResolutionStatus | 'ready';
type BusyKind = 'generate' | 'upload' | 'decision' | 'prompt';

type SectionSummary = {
  key: string;
  sectionId: string | null;
  sectionOrder: number | null;
  sectionTitle: string | null;
  scenes: SceneAssetV2Scene[];
  total: number;
  notGenerated: number;
  queued: number;
  generating: number;
  ready: number;
  approved: number;
  failed: number;
  eligibleGenerate: number;
  eligibleRetry: number;
  eligibleRegenerate: number;
  eligibleApprove: number;
  blockedGenerate: number;
};

function errorMessage(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof SceneAssetV2ApiError) return `${error.errorCode}: ${error.message}`;
  return error instanceof Error ? error.message : 'Unknown Scene Assets error.';
}

function statusTone(status: SceneAssetV2ResolutionStatus): string {
  if (status === 'approved') return 'border-emerald-400/30 bg-emerald-400/10 text-emerald-200';
  if (status === 'failed') return 'border-rose-400/30 bg-rose-400/10 text-rose-200';
  if (status === 'candidate_ready') return 'border-sky-400/30 bg-sky-400/10 text-sky-200';
  if (status === 'queued' || status === 'generating') return 'border-amber-400/30 bg-amber-400/10 text-amber-200';
  return 'border-white/10 bg-white/[0.03] text-slate-400';
}

function statusLabel(status: SceneAssetV2ResolutionStatus): string {
  if (status === 'candidate_ready') return 'candidate ready';
  return status.split('_').join(' ');
}

function candidateLabel(candidate: SceneAssetV2Candidate): string {
  if (candidate.is_approved) return 'Approved';
  if (candidate.is_current_draft) return 'Current draft';
  if (candidate.is_rejected) return 'Rejected';
  return 'Candidate';
}

function candidateThumbnailTone(candidate: SceneAssetV2Candidate): string {
  if (candidate.is_approved) return 'border-emerald-400/50 ring-1 ring-emerald-400/30';
  if (candidate.is_current_draft) return 'border-sky-400/50 ring-1 ring-sky-400/30';
  if (candidate.is_rejected) return 'border-rose-400/25 opacity-60';
  return 'border-white/10 hover:border-white/30';
}

function primaryCandidate(beat: SceneAssetV2Beat): SceneAssetV2Candidate | null {
  return beat.candidates.find((candidate) => candidate.candidate_id === beat.current_draft_candidate_id)
    ?? beat.candidates.find((candidate) => candidate.candidate_id === beat.selected_candidate_id)
    ?? beat.candidates.find((candidate) => candidate.is_approved)
    ?? beat.candidates.find((candidate) => candidate.is_current_draft)
    ?? beat.candidates.find((candidate) => !candidate.is_rejected)
    ?? beat.candidates[0]
    ?? null;
}

function sectionApprovalItems(section: SectionSummary): SceneAssetV2SectionApprovalItem[] {
  const result: SceneAssetV2SectionApprovalItem[] = [];
  for (const scene of section.scenes) {
    for (const beat of scene.beats) {
      if (!beat.can_approve) continue;
      const candidate = primaryCandidate(beat);
      if (!candidate) continue;
      result.push({
        beat_id: beat.beat_id,
        candidate_id: candidate.candidate_id,
        expected_beat_version: beat.beat_state_version,
      });
    }
  }
  return result;
}

function matchesFilter(beat: SceneAssetV2Beat, filter: BeatFilter): boolean {
  if (filter === 'all') return true;
  if (filter === 'ready') return beat.resolution_status === 'candidate_ready';
  return beat.resolution_status === filter;
}

function operationProgress(operation: SceneAssetV2OperationStatusResponse): number {
  const terminal = operation.completed_count + operation.failed_count + operation.cancelled_count;
  return Math.round((terminal / Math.max(1, operation.total_beat_count)) * 100);
}

function activeOperationBeatIds(execution: SceneAssetV2ExecutionStatusResponse | undefined): Set<string> {
  const result = new Set<string>();
  for (const operation of execution?.operations ?? []) {
    if (!ACTIVE_OPERATION_STATUSES.has(operation.status)) continue;
    for (const beat of operation.beats) {
      if (beat.status === 'queued' || beat.status === 'running') result.add(beat.beat_id);
    }
  }
  return result;
}

function referenceLabel(anchorType: string, anchorId: string): string {
  return `${anchorType === 'recurring_visual_subject' ? 'subject' : anchorType}: ${anchorId}`;
}

function shortArtifactId(artifactId: string): string {
  return artifactId.length > 24 ? `${artifactId.slice(0, 18)}...${artifactId.slice(-6)}` : artifactId;
}

function continuityLabel(beat: SceneAssetV2Beat): string {
  if (!beat.continuity.required) return '';
  const source = beat.continuity.source_beat_id ?? 'previous beat';
  if (beat.continuity.source_ready) return `Continuity ready - ${source} approved`;
  return `Waiting for approval of ${source}${beat.continuity.block_reason ? ` (${beat.continuity.block_reason})` : ''}`;
}

function SceneAssetImage({ projectId, candidate, alt, className }: { projectId: string; candidate: SceneAssetV2Candidate; alt: string; className: string }) {
  return <img loading="lazy" decoding="async" draggable={false} src={sceneAssetsV2Client.getCandidateContentUrl(projectId, candidate.artifact_id)} alt={alt} width={candidate.width ?? 1280} height={candidate.height ?? 720} className={className} />;
}

export function SceneAssetsPanel({ projectId, promptBundleArtifactId, enabled }: { projectId: string; promptBundleArtifactId: string | null; enabled: boolean }) {
  const queryClient = useQueryClient();
  const workspaceKey = sceneAssetsWorkspaceQueryKey(projectId, promptBundleArtifactId);
  const executionKey = sceneAssetsExecutionQueryKey(projectId, promptBundleArtifactId);
  const [filter, setFilter] = useState<BeatFilter>('all');
  const [selectedSection, setSelectedSection] = useState<string | null>(null);
  const [expandedBeats, setExpandedBeats] = useState<Set<string>>(() => new Set());
  const [busyByKind, setBusyByKind] = useState<Record<BusyKind, Set<string>>>(() => ({
    generate: new Set(), upload: new Set(), decision: new Set(), prompt: new Set(),
  }));
  const [localOperationIds, setLocalOperationIds] = useState<Set<string>>(() => new Set());
  const [cancelPendingOperationIds, setCancelPendingOperationIds] = useState<Set<string>>(() => new Set());
  const [operationsDrawerOpen, setOperationsDrawerOpen] = useState(false);
  const observedTerminalOperationBeats = useRef<Set<string>>(new Set());
  const [editingBeatId, setEditingBeatId] = useState<string | null>(null);
  const [imagePromptDraft, setImagePromptDraft] = useState('');
  const [negativePromptDraft, setNegativePromptDraft] = useState('');
  const uploadRef = useRef<HTMLInputElement | null>(null);
  const [uploadBeatId, setUploadBeatId] = useState<string | null>(null);

  const workspace = useQuery({
    queryKey: workspaceKey,
    queryFn: ({ signal }) => sceneAssetsV2Client.getWorkspace(projectId, signal),
    enabled: enabled && Boolean(promptBundleArtifactId),
    retry: false,
  });
  const workspaceHasOwnedBeat = Boolean(
    workspace.data?.scenes.some((scene) => scene.beats.some((beat) => beat.active_operation_beat_id !== null)),
  );
  const workspaceHasActive = Boolean(
    workspaceHasOwnedBeat
      || (workspace.data?.latest_operation && ACTIVE_OPERATION_STATUSES.has(workspace.data.latest_operation.status)),
  );
  const execution = useQuery({
    queryKey: executionKey,
    queryFn: ({ signal }) => sceneAssetsV2Client.getExecutionStatus(projectId, signal),
    enabled: enabled && Boolean(promptBundleArtifactId) && (workspaceHasActive || localOperationIds.size > 0),
    retry: false,
    refetchInterval: (query) => (query.state.data?.active || workspaceHasOwnedBeat || localOperationIds.size > 0 ? POLL_INTERVAL_MS : false),
  });

  const refreshWorkspace = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: workspaceKey }),
      queryClient.invalidateQueries({ queryKey: ['v3-projects'] }),
    ]);
  };

  const applyBeatResponse = (response: SceneAssetV2BeatResponse | SceneAssetV2MutationResponse) => {
    queryClient.setQueryData<SceneAssetV2WorkspaceResponse>(workspaceKey, (current) => {
      if (!current || current.project_id !== response.project_id) return current;
      const hasPublicationProjection = Object.prototype.hasOwnProperty.call(response, 'manifest_state');
      return {
        ...current,
        published_manifest_artifact_id: hasPublicationProjection ? response.published_manifest_artifact_id : current.published_manifest_artifact_id,
        published_manifest_status: hasPublicationProjection ? response.published_manifest_status : current.published_manifest_status,
        manifest_state: hasPublicationProjection ? response.manifest_state : current.manifest_state,
        actions: response.actions,
        scenes: current.scenes.map((scene) => ({
          ...scene,
          beats: scene.beats.map((beat) => (beat.beat_id === response.beat.beat_id ? response.beat : beat)),
        })),
      };
    });
  };

  const refreshBeat = async (beatId: string) => {
    const response = await sceneAssetsV2Client.getBeat(projectId, beatId);
    applyBeatResponse(response);
  };

  const directDependentBeatIds = (sourceBeatIds: Iterable<string>): string[] => {
    const sources = new Set(sourceBeatIds);
    if (sources.size === 0) return [];
    const current = queryClient.getQueryData<SceneAssetV2WorkspaceResponse>(workspaceKey);
    if (!current) return [];
    const result: string[] = [];
    for (const scene of current.scenes) {
      for (const beat of scene.beats) {
        if (
          beat.continuity.required
          && beat.continuity.source_beat_id
          && sources.has(beat.continuity.source_beat_id)
        ) {
          result.push(beat.beat_id);
        }
      }
    }
    return result;
  };

  const refreshBeatIds = async (beatIds: Iterable<string>): Promise<void> => {
    const unique = Array.from(new Set(beatIds));
    if (unique.length > 0) await Promise.allSettled(unique.map((beatId) => refreshBeat(beatId)));
  };

  useEffect(() => {
    const data = execution.data;
    if (!data) return;
    const activeIds = new Set(data.operations.filter((operation) => ACTIVE_OPERATION_STATUSES.has(operation.status)).map((operation) => operation.operation_id));
    const terminalOperationObserved = Array.from(localOperationIds).some((operationId) => !activeIds.has(operationId));
    const terminalBeatIds: string[] = [];
    for (const operation of data.operations) {
      for (const beat of operation.beats) {
        if (!TERMINAL_OPERATION_BEAT_STATUSES.has(beat.status)) continue;
        const key = `${operation.operation_id}:${beat.operation_beat_id}:${beat.status}`;
        if (observedTerminalOperationBeats.current.has(key)) continue;
        observedTerminalOperationBeats.current.add(key);
        terminalBeatIds.push(beat.beat_id);
      }
    }
    setLocalOperationIds((current) => {
      const next = new Set(current);
      let changed = false;
      for (const operationId of current) {
        if (!activeIds.has(operationId)) changed = next.delete(operationId) || changed;
      }
      return changed ? next : current;
    });
    if (!data.active || terminalOperationObserved) {
      void refreshWorkspace();
      return;
    }
    if (terminalBeatIds.length > 0) {
      void Promise.allSettled(terminalBeatIds.map((beatId) => refreshBeat(beatId)));
    }
  }, [execution.data]);

  const setBeatBusy = (kind: BusyKind, beatId: string, active: boolean) => {
    setBusyByKind((current) => {
      const nextSet = new Set(current[kind]);
      if (active) nextSet.add(beatId); else nextSet.delete(beatId);
      return { ...current, [kind]: nextSet };
    });
  };

  const applyBeatMutationResponse = (response: SceneAssetV2MutationResponse, refreshDependentsOf: string | null = null) => {
    const refreshTargets = new Set(response.invalidated_beat_ids);
    if (refreshDependentsOf) {
      for (const beatId of directDependentBeatIds([refreshDependentsOf])) refreshTargets.add(beatId);
    }
    refreshTargets.delete(response.beat.beat_id);
    applyBeatResponse(response);
    void refreshBeatIds(refreshTargets);
  };

  const registerOperation = async (response: { operation_id: string; scheduled_beat_ids: string[] }) => {
    setLocalOperationIds((current) => new Set(current).add(response.operation_id));
    for (const beatId of response.scheduled_beat_ids) setBeatBusy('generate', beatId, false);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: executionKey }),
      queryClient.invalidateQueries({ queryKey: workspaceKey }),
    ]);
  };

  const bulkGenerate = useMutation({
    mutationFn: ({ operationType, sectionId }: { operationType: 'generate_all' | 'generate_section' | 'generate_missing' | 'retry_failed' | 'regenerate_section'; sectionId?: string }) => sceneAssetsV2Client.submitBulk(projectId, operationType, 1, sectionId ?? null),
    onSuccess: registerOperation,
  });
  const exactGenerate = useMutation({
    mutationFn: async ({ beatId, mode }: { beatId: string; mode: 'generate' | 'retry' | 'regenerate' }) => {
      setBeatBusy('generate', beatId, true);
      try {
        if (mode === 'generate') return await sceneAssetsV2Client.generateBeat(projectId, beatId, 1);
        if (mode === 'retry') return await sceneAssetsV2Client.retryBeat(projectId, beatId, 1);
        return await sceneAssetsV2Client.regenerateBeat(projectId, beatId, 1);
      } catch (error) {
        setBeatBusy('generate', beatId, false);
        throw error;
      }
    },
    onSuccess: registerOperation,
  });
  const cancelOperation = useMutation({
    mutationFn: async (operationId: string) => {
      setCancelPendingOperationIds((current) => new Set(current).add(operationId));
      try {
        return await sceneAssetsV2Client.cancelOperation(projectId, operationId);
      } finally {
        setCancelPendingOperationIds((current) => {
          const next = new Set(current);
          next.delete(operationId);
          return next;
        });
      }
    },
    onSuccess: async (response) => {
      setLocalOperationIds((current) => { const next = new Set(current); next.delete(response.operation_id); return next; });
      await Promise.all([queryClient.invalidateQueries({ queryKey: executionKey }), refreshWorkspace()]);
    },
  });
  const upload = useMutation({
    mutationFn: async ({ beatId, file }: { beatId: string; file: File }) => {
      setBeatBusy('upload', beatId, true);
      return await sceneAssetsV2Client.upload(projectId, beatId, file);
    },
    onSuccess: (response) => applyBeatMutationResponse(response),
    onSettled: (_response, _error, variables) => { if (variables) setBeatBusy('upload', variables.beatId, false); },
  });
  const decide = useMutation({
    mutationFn: async ({ beatId, candidateId, decision }: { beatId: string; candidateId: string | null; decision: 'set-current-draft' | 'approve' | 'reject' | 'clear-approval' }) => {
      setBeatBusy('decision', beatId, true);
      return await sceneAssetsV2Client.decide(projectId, beatId, candidateId, decision);
    },
    onSuccess: (response, variables) => applyBeatMutationResponse(
      response,
      variables.decision === 'approve' || variables.decision === 'clear-approval' ? variables.beatId : null,
    ),
    onSettled: (_response, _error, variables) => { if (variables) setBeatBusy('decision', variables.beatId, false); },
  });
  const approveSection = useMutation({
    mutationFn: async ({ sectionId, beats }: { sectionId: string; beats: SceneAssetV2SectionApprovalItem[] }) => {
      for (const beat of beats) setBeatBusy('decision', beat.beat_id, true);
      return await sceneAssetsV2Client.approveSectionReady(projectId, sectionId, beats);
    },
    onSuccess: async (response) => {
      const refreshTargets = new Set([
        ...response.approved_beat_ids,
        ...response.invalidated_beat_ids,
      ]);
      for (const beatId of directDependentBeatIds(response.approved_beat_ids)) refreshTargets.add(beatId);
      queryClient.setQueryData<SceneAssetV2WorkspaceResponse>(workspaceKey, (current) => (
        current && current.project_id === response.project_id
          ? {
            ...current,
            published_manifest_artifact_id: Object.prototype.hasOwnProperty.call(response, 'manifest_state') ? response.published_manifest_artifact_id : current.published_manifest_artifact_id,
            published_manifest_status: Object.prototype.hasOwnProperty.call(response, 'manifest_state') ? response.published_manifest_status : current.published_manifest_status,
            manifest_state: Object.prototype.hasOwnProperty.call(response, 'manifest_state') ? response.manifest_state : current.manifest_state,
            actions: response.actions,
          }
          : current
      ));
      await refreshBeatIds(refreshTargets);
    },
    onSettled: (_response, _error, variables) => {
      for (const beat of variables?.beats ?? []) setBeatBusy('decision', beat.beat_id, false);
    },
  });
  const publish = useMutation({ mutationFn: () => sceneAssetsV2Client.publishManifest(projectId), onSuccess: refreshWorkspace });
  const savePrompt = useMutation({
    mutationFn: async ({ beatId, promptRevisionId }: { beatId: string; promptRevisionId: string }) => {
      setBeatBusy('prompt', beatId, true);
      try { return await sceneAssetsV2Client.editPrompt(projectId, beatId, promptRevisionId, imagePromptDraft, negativePromptDraft); }
      finally { setBeatBusy('prompt', beatId, false); }
    },
    onSuccess: async (_response, variables) => { if (editingBeatId === variables.beatId) setEditingBeatId(null); await refreshBeat(variables.beatId); },
  });
  const restorePrompt = useMutation({
    mutationFn: async ({ beatId, promptRevisionId }: { beatId: string; promptRevisionId: string }) => {
      setBeatBusy('prompt', beatId, true);
      try { return await sceneAssetsV2Client.restorePrompt(projectId, beatId, promptRevisionId); }
      finally { setBeatBusy('prompt', beatId, false); }
    },
    onSuccess: async (_response, variables) => { if (editingBeatId === variables.beatId) setEditingBeatId(null); await refreshBeat(variables.beatId); },
  });

  const executionBusyBeats = useMemo(() => activeOperationBeatIds(execution.data), [execution.data]);
  const activeOperations = useMemo(() => execution.data?.operations.filter((operation) => ACTIVE_OPERATION_STATUSES.has(operation.status)) ?? [], [execution.data]);
  const activeOperationProgress = useMemo(() => {
    if (!activeOperations.length) return 0;
    return Math.round(activeOperations.reduce((sum, operation) => sum + operationProgress(operation), 0) / activeOperations.length);
  }, [activeOperations]);
  const localBusyBeats = useMemo(() => {
    const result = new Set<string>();
    for (const set of Object.values(busyByKind)) for (const beatId of set) result.add(beatId);
    return result;
  }, [busyByKind]);
  const beatIsBusy = (beat: SceneAssetV2Beat) => Boolean(beat.active_operation_beat_id || executionBusyBeats.has(beat.beat_id) || localBusyBeats.has(beat.beat_id));
  const anyBeatOwned = Boolean(workspace.data?.scenes.some((scene) => scene.beats.some((beat) => beat.active_operation_beat_id !== null)));
  const approvalBlockedByActiveOperation = activeOperations.length > 0 || anyBeatOwned;
  const allBeats = useMemo(() => workspace.data?.scenes.flatMap((scene) => scene.beats) ?? [], [workspace.data]);
  const summary = useMemo(() => ({
    total: allBeats.length,
    notGenerated: allBeats.filter((beat) => beat.resolution_status === 'not_generated').length,
    queued: allBeats.filter((beat) => beat.resolution_status === 'queued').length,
    generating: allBeats.filter((beat) => beat.resolution_status === 'generating').length,
    ready: allBeats.filter((beat) => beat.resolution_status === 'candidate_ready').length,
    approved: allBeats.filter((beat) => beat.resolution_status === 'approved').length,
    failed: allBeats.filter((beat) => beat.resolution_status === 'failed').length,
  }), [allBeats]);

  const sectionSummaries = useMemo<SectionSummary[]>(() => {
    const sections = new Map<string, SectionSummary>();
    for (const scene of workspace.data?.scenes ?? []) {
      const key = scene.section_id ?? 'unsectioned';
      let section = sections.get(key);
      if (!section) {
        section = {
          key,
          sectionId: scene.section_id,
          sectionOrder: scene.section_order,
          sectionTitle: scene.section_title,
          scenes: [],
          total: 0,
          notGenerated: 0,
          queued: 0,
          generating: 0,
          ready: 0,
          approved: 0,
          failed: 0,
          eligibleGenerate: 0,
          eligibleRetry: 0,
          eligibleRegenerate: 0,
          eligibleApprove: 0,
          blockedGenerate: 0,
        };
        sections.set(key, section);
      }
      section.scenes.push(scene);
      for (const beat of scene.beats) {
        section.total += 1;
        if (beat.resolution_status === 'not_generated') section.notGenerated += 1;
        if (beat.resolution_status === 'queued') section.queued += 1;
        if (beat.resolution_status === 'generating') section.generating += 1;
        if (beat.resolution_status === 'candidate_ready') section.ready += 1;
        if (beat.resolution_status === 'approved') section.approved += 1;
        if (beat.resolution_status === 'failed') section.failed += 1;
        if (beat.can_generate) section.eligibleGenerate += 1;
        if (beat.can_retry) section.eligibleRetry += 1;
        if (beat.can_regenerate) section.eligibleRegenerate += 1;
        if (beat.can_approve) section.eligibleApprove += 1;
        if (beat.continuity.required && !beat.continuity.source_ready) section.blockedGenerate += 1;
      }
    }
    return Array.from(sections.values()).sort((a, b) => (a.sectionOrder ?? Number.MAX_SAFE_INTEGER) - (b.sectionOrder ?? Number.MAX_SAFE_INTEGER));
  }, [workspace.data]);

  useEffect(() => {
    if (!sectionSummaries.length) {
      if (selectedSection !== null) setSelectedSection(null);
      return;
    }
    if (!selectedSection || !sectionSummaries.some((section) => section.key === selectedSection)) setSelectedSection(sectionSummaries[0].key);
  }, [sectionSummaries, selectedSection]);

  const activeSection = sectionSummaries.find((section) => section.key === selectedSection) ?? sectionSummaries[0] ?? null;
  const filteredScenes = useMemo(() => (activeSection?.scenes ?? [])
    .map((scene) => ({ ...scene, beats: scene.beats.filter((beat) => matchesFilter(beat, filter)) }))
    .filter((scene) => scene.beats.length > 0), [activeSection, filter]);
  const sceneWindow = useProgressiveLimit(
    filteredScenes.length,
    `${projectId}:${activeSection?.key ?? 'none'}:${filter}`,
    SCENE_ASSET_INITIAL_SCENE_WINDOW,
    SCENE_ASSET_SCENE_WINDOW_STEP,
  );
  const mountedScenes = filteredScenes.slice(0, sceneWindow.visibleCount);
  const mutationError = errorMessage(bulkGenerate.error ?? exactGenerate.error ?? cancelOperation.error ?? upload.error ?? decide.error ?? approveSection.error ?? publish.error ?? savePrompt.error ?? restorePrompt.error);

  const toggleDetails = (beatId: string) => setExpandedBeats((current) => {
    const next = new Set(current);
    if (next.has(beatId)) next.delete(beatId); else next.add(beatId);
    if (editingBeatId === beatId && current.has(beatId)) setEditingBeatId(null);
    return next;
  });
  const startPromptEdit = (beat: SceneAssetV2Beat) => {
    setExpandedBeats((current) => new Set(current).add(beat.beat_id));
    setEditingBeatId(beat.beat_id);
    setImagePromptDraft(beat.image_prompt);
    setNegativePromptDraft(beat.negative_prompt);
  };
  const selectSection = (sectionKey: string) => {
    setSelectedSection(sectionKey);
    setFilter('all');
    setEditingBeatId(null);
  };

  if (!enabled) return <div className="panel-surface p-6 text-sm text-slate-400">Approve Scene Breakdown first so the backend can compile the hidden Prompt Bundle.</div>;
  if (workspace.isLoading) return <div className="panel-surface p-6 text-sm text-slate-400">Loading Scene Assets...</div>;
  if (workspace.error) return <div className="panel-surface p-6 text-sm text-rose-300">{errorMessage(workspace.error)}</div>;
  if (!workspace.data) return <div className="panel-surface p-6 text-sm text-slate-400">Scene Assets workspace is unavailable.</div>;

  const data: SceneAssetV2WorkspaceResponse = workspace.data;
  const publishLabel = publish.isPending ? 'Publishing...' : data.manifest_state === 'current' ? 'Published' : data.manifest_state === 'stale' ? 'Republish Scene Assets' : 'Publish Scene Assets';
  const publishDisabled = publish.isPending || data.manifest_state === 'current' || !data.actions.can_publish_manifest || approvalBlockedByActiveOperation;
  const publicationLabel = data.manifest_state === 'current' ? 'Published' : data.manifest_state === 'stale' ? 'Manifest out of date' : 'Working draft';
  return (
    <section className="space-y-4 font-sans" data-testid="scene-assets-stage">
      <input ref={uploadRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden" data-testid="scene-asset-upload-input" onChange={(event) => {
        const file = event.target.files?.[0] ?? null;
        const beatId = uploadBeatId;
        event.target.value = '';
        setUploadBeatId(null);
        if (file && beatId) upload.mutate({ beatId, file });
      }} />

      <ActiveOperationsDrawer
        open={operationsDrawerOpen}
        onOpenChange={setOperationsDrawerOpen}
        activeCount={activeOperations.length}
        averageProgress={activeOperationProgress}
        testId="scene-assets-operation-drawer"
      >
        <div data-testid="scene-assets-operation">
          {activeOperations.map((operation) => <OperationCard key={operation.operation_id} operation={operation} cancelling={cancelPendingOperationIds.has(operation.operation_id)} onCancel={() => cancelOperation.mutate(operation.operation_id)} />)}
        </div>
      </ActiveOperationsDrawer>

      <header className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Phase 11</p>
            <h2 className="mt-1 text-xl font-semibold text-white">Scene Assets</h2>
            <p className="mt-2 max-w-3xl text-sm text-slate-300">Review one generated image per beat in script order. Open details only when you need candidate comparison, visual treatment, or prompt editing.</p>
          </div>
          <div className="min-w-56 text-right text-sm text-slate-300" data-testid="scene-assets-publication-state">
            <div className={`font-medium ${data.manifest_state === 'current' ? 'text-emerald-200' : data.manifest_state === 'stale' ? 'text-amber-200' : 'text-white'}`}>{data.manifest_state === 'current' ? 'Published' : publicationLabel}</div>
            {data.published_manifest_artifact_id ? <div title={data.published_manifest_artifact_id}>Manifest: {shortArtifactId(data.published_manifest_artifact_id)}</div> : null}
            <div>{summary.approved} / {summary.total} beats approved</div>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-7" data-testid="scene-assets-summary">
          {[
            ['Total', summary.total],
            ['Not generated', summary.notGenerated],
            ['Queued', summary.queued],
            ['Generating', summary.generating],
            ['Ready to review', summary.ready],
            ['Approved', summary.approved],
            ['Failed', summary.failed],
          ].map(([label, value]) => <div key={String(label)} className="rounded-xl border border-white/10 bg-slate-950/30 px-3 py-2"><div className="text-lg font-semibold text-white">{value}</div><div className="text-xs text-slate-400">{label}</div></div>)}
        </div>

        {data.readiness.blockers.map((blocker) => <p key={blocker} className="mt-2 text-sm text-amber-300">{blocker}</p>)}
        {mutationError ? <p role="alert" data-testid="scene-assets-action-error" className="mt-2 text-sm text-rose-300">{mutationError}</p> : null}

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button data-action="generate-all-remaining" disabled={!data.actions.can_generate_all || bulkGenerate.isPending} onClick={() => bulkGenerate.mutate({ operationType: 'generate_all' })} className="rounded-lg bg-white px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-40">Generate All Remaining ({data.actions.eligible_generate_count})</button>
          <button data-action="generate-missing" disabled={!data.actions.can_generate_missing || bulkGenerate.isPending} onClick={() => bulkGenerate.mutate({ operationType: 'generate_missing' })} className="rounded-lg border border-cyan-400/30 px-3 py-2 text-sm text-cyan-100 disabled:opacity-40">Generate Missing</button>
          <button data-action="retry-all-failed" disabled={!data.actions.can_retry_failed || bulkGenerate.isPending} onClick={() => bulkGenerate.mutate({ operationType: 'retry_failed' })} className="rounded-lg border border-rose-400/30 px-3 py-2 text-sm text-rose-200 disabled:opacity-40">Retry All Failed ({summary.failed})</button>
          <button data-action="publish-scene-assets" disabled={publishDisabled} onClick={() => publish.mutate()} className="rounded-lg border border-emerald-400/40 px-3 py-2 text-sm text-emerald-200 disabled:opacity-40">{publishLabel}</button>
          <button disabled={workspace.isFetching} onClick={() => void refreshWorkspace()} className="rounded-lg border border-white/10 px-3 py-2 text-sm text-slate-300 disabled:opacity-40">Refresh</button>
        </div>
        {data.manifest_state === 'current' ? <p className="mt-2 text-xs text-emerald-200">Scene Assets published. Assembly is ready once Voice is complete.</p> : null}
        {data.manifest_state === 'stale' ? <p className="mt-2 text-xs text-amber-200">Last published Scene Assets are out of date with the current working state.</p> : null}
        {summary.total > 0 && summary.approved < summary.total ? <p className="mt-2 text-xs text-slate-500">{summary.total - summary.approved} beats still need approval.</p> : null}

        {sectionSummaries.length ? (
          <div className="mt-4 flex gap-2 overflow-x-auto pb-2" data-testid="scene-assets-section-selector" aria-label="Scene Asset section">
            {sectionSummaries.map((section) => <button key={section.key} type="button" data-section-key={section.key} onClick={() => selectSection(section.key)} className={`min-w-56 rounded-xl border px-4 py-3 text-left ${section.key === activeSection?.key ? 'border-cyan-400/40 bg-cyan-500/[0.08]' : 'border-white/10 bg-white/[0.025]'}`}>
              <div className="text-[10px] font-medium text-cyan-200">{section.sectionOrder ? `Section ${section.sectionOrder}` : 'Section'}{section.sectionId ? ` - ${section.sectionId}` : ''}</div>
              <div className="mt-1 truncate text-sm font-semibold text-slate-100">{section.sectionTitle ?? section.sectionId ?? 'Scene group'}</div>
              <div className="mt-1 text-xs text-slate-500">{section.scenes.length} scenes - {section.total} beats</div>
              <div className="mt-1 text-[11px] text-slate-400">{section.notGenerated} not generated - {section.queued} queued - {section.generating} generating - {section.ready} ready - {section.approved} approved - {section.failed} failed</div>
            </button>)}
          </div>
        ) : null}

        {activeSection ? <>
          <div className="mt-3 flex flex-wrap items-center gap-2" data-testid="scene-assets-section-actions">
            <button data-action="generate-section" disabled={bulkGenerate.isPending || !activeSection.sectionId || activeSection.eligibleGenerate === 0} onClick={() => bulkGenerate.mutate({ operationType: 'generate_section', sectionId: activeSection.sectionId! })} className="rounded-lg border border-cyan-400/30 px-3 py-2 text-sm text-cyan-100 disabled:opacity-40">Generate This Section ({activeSection.eligibleGenerate})</button>
            <button data-action="regenerate-section" disabled={bulkGenerate.isPending || !activeSection.sectionId || activeSection.eligibleRegenerate === 0} onClick={() => bulkGenerate.mutate({ operationType: 'regenerate_section', sectionId: activeSection.sectionId! })} className="rounded-lg border border-white/10 px-3 py-2 text-sm text-white disabled:opacity-40">Regenerate This Section ({activeSection.eligibleRegenerate})</button>
            <button data-action="retry-section-failed" disabled={bulkGenerate.isPending || !activeSection.sectionId || activeSection.eligibleRetry === 0} onClick={() => bulkGenerate.mutate({ operationType: 'retry_failed', sectionId: activeSection.sectionId! })} className="rounded-lg border border-rose-400/30 px-3 py-2 text-sm text-rose-200 disabled:opacity-40">Retry Failed in Section ({activeSection.failed})</button>
            <button
              data-action="approve-section-ready"
              disabled={approveSection.isPending || !activeSection.sectionId || activeSection.eligibleApprove === 0}
              onClick={() => {
                if (!activeSection.sectionId) return;
                const beats = sectionApprovalItems(activeSection);
                if (beats.length) approveSection.mutate({ sectionId: activeSection.sectionId, beats });
              }}
              className="rounded-lg border border-emerald-400/40 bg-emerald-400/[0.06] px-3 py-2 text-sm text-emerald-200 disabled:opacity-40"
            >
              {approveSection.isPending ? 'Approving...' : `Approve All Ready (${activeSection.eligibleApprove})`}
            </button>
          </div>
          {activeSection.blockedGenerate > 0 ? <p className="mt-2 text-xs text-amber-300">{activeSection.blockedGenerate} beats in this section are waiting for previous-beat approval.</p> : null}
        </> : null}

        <div className="mt-4 flex flex-wrap gap-1" aria-label="Scene Asset filter">
          {([
            ['all', `All ${activeSection?.total ?? 0}`],
            ['not_generated', `Not generated ${activeSection?.notGenerated ?? 0}`],
            ['queued', `Queued ${activeSection?.queued ?? 0}`],
            ['generating', `Generating ${activeSection?.generating ?? 0}`],
            ['ready', `Ready ${activeSection?.ready ?? 0}`],
            ['failed', `Failed ${activeSection?.failed ?? 0}`],
            ['approved', `Approved ${activeSection?.approved ?? 0}`],
          ] as Array<[BeatFilter, string]>).map(([value, label]) => <button key={value} data-filter={value} onClick={() => setFilter(value)} className={`rounded-full border px-3 py-1.5 text-xs ${filter === value ? 'border-white/30 bg-white/10 text-white' : 'border-white/10 text-slate-400'}`}>{label}</button>)}
        </div>
      </header>

      {activeSection ? <section key={activeSection.key} data-testid="scene-assets-section" data-section-id={activeSection.sectionId ?? undefined} className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2 px-1">
          <div><span className="text-xs uppercase tracking-[0.16em] text-slate-500">{activeSection.sectionOrder ? `Section ${activeSection.sectionOrder}` : 'Section'}</span><h3 className="mt-0.5 text-base font-semibold text-white">{activeSection.sectionTitle ?? activeSection.sectionId ?? 'Scene group'}</h3></div>
          <span className="text-xs text-slate-500">{activeSection.total} beats - {activeSection.approved} approved - {activeSection.failed} failed</span>
        </div>

        {mountedScenes.map((scene) => {
          const unresolved = scene.beats.filter((beat) => beat.resolution_status !== 'approved').length;
          return <section key={scene.scene_id} className="overflow-hidden rounded-2xl border border-white/10 bg-white/[0.02]" style={{ contentVisibility: 'auto', containIntrinsicSize: '520px' }} data-scene-id={scene.scene_id}>
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/10 px-4 py-3">
              <div className="min-w-0"><div className="flex items-center gap-2"><h4 className="font-semibold text-white">Scene {scene.scene_order}</h4><span className="text-xs text-slate-500">{scene.beats.length} beats</span></div></div>
              <span className="rounded-full border border-white/10 px-2 py-1 text-xs text-slate-400">{unresolved ? `${unresolved} unresolved` : 'Complete'}</span>
            </div>

            <div className="grid gap-3 p-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
              {scene.beats.map((beat) => {
                const primary = primaryCandidate(beat);
                const hasCandidateHistory = beat.candidates.length > 0;
                const busy = beatIsBusy(beat);
                const expanded = expandedBeats.has(beat.beat_id);
                const editing = editingBeatId === beat.beat_id;
                const secondaryCandidates = primary ? beat.candidates.filter((candidate) => candidate.candidate_id !== primary.candidate_id) : beat.candidates;
                const visibleSecondaryCandidates = expanded ? secondaryCandidates : secondaryCandidates.slice(0, COLLAPSED_SECONDARY_CANDIDATE_LIMIT);
                const hiddenSecondaryCount = secondaryCandidates.length - visibleSecondaryCandidates.length;
                return <article key={beat.beat_id} className={`overflow-hidden rounded-lg border border-white/10 bg-slate-950/35 ${expanded ? 'sm:col-span-2 xl:col-span-3 2xl:col-span-4' : ''}`} style={{ contentVisibility: 'auto', containIntrinsicSize: '360px' }} data-testid="scene-asset-beat" data-beat-id={beat.beat_id}>
                  <div className={`grid gap-0 ${expanded ? 'lg:grid-cols-[minmax(280px,420px)_minmax(0,1fr)]' : ''}`}>
                    <div className="overflow-hidden bg-black/20" data-testid="scene-asset-candidate-preview">
                      <div className="relative">
                        {primary ? <SceneAssetImage projectId={projectId} candidate={primary} alt={`Current image for ${beat.beat_id}`} className="aspect-video w-full object-contain" /> : <div className="flex aspect-video items-center justify-center px-3 text-center text-xs text-slate-500">No generated image</div>}
                        {primary ? <span className="absolute left-2 top-2 rounded-md border border-black/40 bg-black/70 px-2 py-0.5 text-[10px] font-medium text-white">{candidateLabel(primary)}</span> : null}
                        {primary?.source_kind === 'uploaded' ? <span className="absolute bottom-2 left-2 rounded-md border border-violet-400/30 bg-black/75 px-2 py-0.5 text-[10px] text-violet-200">Uploaded</span> : primary && !primary.generated_from_current_prompt ? <span className="absolute bottom-2 left-2 rounded-md border border-amber-400/30 bg-black/75 px-2 py-0.5 text-[10px] text-amber-200">Previous prompt</span> : null}
                      </div>
                      {secondaryCandidates.length ? <div className="border-t border-white/10 bg-slate-950/45 p-2" data-testid="scene-asset-candidate-strip"><div className="flex gap-2 overflow-x-auto pb-0.5">
                        {visibleSecondaryCandidates.map((candidate) => <button key={candidate.candidate_id} type="button" data-action="set-current-draft-thumbnail" data-candidate-id={candidate.candidate_id} disabled={busy || !beat.can_review} onClick={() => decide.mutate({ beatId: beat.beat_id, candidateId: candidate.candidate_id, decision: 'set-current-draft' })} className={`relative h-14 w-24 shrink-0 overflow-hidden rounded-lg border bg-black/30 disabled:cursor-not-allowed disabled:opacity-40 ${candidateThumbnailTone(candidate)}`} title={`Set as current draft: ${candidateLabel(candidate)}`}><SceneAssetImage projectId={projectId} candidate={candidate} alt={`Candidate thumbnail for ${beat.beat_id}`} className="h-full w-full object-contain" /></button>)}
                        {hiddenSecondaryCount > 0 ? <button type="button" data-action="show-hidden-candidates" onClick={() => toggleDetails(beat.beat_id)} className="flex h-14 w-24 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-black/20 text-xs text-slate-300">+{hiddenSecondaryCount}</button> : null}
                      </div></div> : null}
                    </div>

                    <div className="flex min-w-0 flex-col gap-3 p-3">
                      <div className="flex flex-wrap items-center gap-2"><span className="text-xs font-semibold text-white">Beat {beat.local_beat_order}</span><span className={`rounded-full border px-2 py-0.5 text-[11px] ${statusTone(beat.resolution_status)}`}>{statusLabel(beat.resolution_status)}</span><span className="text-[11px] text-slate-500">{beat.computed_duration_seconds.toFixed(1)}s</span><span className="text-[11px] text-slate-600">{beat.beat_id}</span></div>
                      {beat.script_quote ? <p className="line-clamp-3 whitespace-pre-line text-sm font-medium leading-5 text-slate-100">{beat.script_quote}</p> : null}
                      <p className="line-clamp-3 text-sm leading-5 text-slate-400">{beat.treatment.visual_concept}</p>
                      {beat.reference_bindings.length ? <div className="flex flex-wrap gap-1" data-testid="scene-asset-reference-bindings">{beat.reference_bindings.map((binding) => <span key={`${binding.anchor_type}:${binding.anchor_id}`} className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[10px] text-slate-400">{referenceLabel(binding.anchor_type, binding.anchor_id)}</span>)}</div> : null}
                      {beat.continuity.required ? <p className={`text-xs ${beat.continuity.source_ready ? 'text-sky-300' : 'text-amber-300'}`} data-testid="scene-asset-continuity-state">{continuityLabel(beat)}</p> : null}
                      {beat.error ? <p className="text-sm text-rose-300">{beat.error.code}: {beat.error.message}</p> : null}
                    </div>

                    <div className={`flex flex-wrap gap-2 px-3 pb-3 ${expanded ? 'lg:col-start-2' : ''}`}>
                      <button data-action="review-details" onClick={() => toggleDetails(beat.beat_id)} className="rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-white">{expanded ? 'Hide details' : 'Review details'}</button>
                      {beat.resolution_status === 'not_generated' && !hasCandidateHistory ? <button data-action="generate-image" disabled={busy || !beat.can_generate} onClick={() => exactGenerate.mutate({ beatId: beat.beat_id, mode: 'generate' })} className="rounded-lg border border-cyan-400/30 px-2.5 py-1.5 text-xs text-cyan-100 disabled:opacity-40">Generate Image</button> : null}
                      {beat.resolution_status === 'queued' ? <button disabled className="rounded-lg border border-amber-400/20 px-2.5 py-1.5 text-xs text-amber-200 opacity-60">Queued</button> : null}
                      {beat.resolution_status === 'generating' ? <button disabled className="rounded-lg border border-amber-400/20 px-2.5 py-1.5 text-xs text-amber-200 opacity-60">Generating...</button> : null}
                      {beat.resolution_status === 'failed' ? <button data-action="retry-image" disabled={busy || !beat.can_retry} onClick={() => exactGenerate.mutate({ beatId: beat.beat_id, mode: 'retry' })} className="rounded-lg border border-rose-400/30 px-2.5 py-1.5 text-xs text-rose-200 disabled:opacity-40">Retry Image</button> : null}
                      <button data-action="upload-image" disabled={busy || !beat.can_upload} onClick={() => { setUploadBeatId(beat.beat_id); uploadRef.current?.click(); }} className="rounded-lg border border-violet-400/30 px-2.5 py-1.5 text-xs text-violet-100 disabled:opacity-40">Upload Image</button>
                      {(['candidate_ready', 'approved'].includes(beat.resolution_status) || (beat.resolution_status === 'not_generated' && hasCandidateHistory)) ? <button data-action="regenerate-image" disabled={busy || !beat.can_regenerate} onClick={() => exactGenerate.mutate({ beatId: beat.beat_id, mode: 'regenerate' })} className="rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-white disabled:opacity-40">Regenerate Image</button> : null}
                      {primary && !primary.is_approved ? <button data-action="approve-beat-image" disabled={busy || !beat.can_approve || !primary.generated_from_current_prompt} onClick={() => {
                        if (beat.selected_candidate_id && beat.selected_candidate_id !== primary.candidate_id && beat.approval_change_impact_count > 0 && !window.confirm(`Approving this image will invalidate ${beat.approval_change_impact_count} later continuity beat${beat.approval_change_impact_count === 1 ? '' : 's'}. Continue?`)) return;
                        decide.mutate({ beatId: beat.beat_id, candidateId: primary.candidate_id, decision: 'approve' });
                      }} className="rounded-lg border border-emerald-400/40 bg-emerald-400/[0.08] px-2.5 py-1.5 text-xs font-medium text-emerald-200 disabled:opacity-40">Approve Beat Image</button> : null}
                      {beat.selected_candidate_id ? <button disabled={busy} onClick={() => {
                        if (beat.approval_change_impact_count > 0 && !window.confirm(`Clearing this approved image will invalidate ${beat.approval_change_impact_count} later continuity beat${beat.approval_change_impact_count === 1 ? '' : 's'}. Continue?`)) return;
                        decide.mutate({ beatId: beat.beat_id, candidateId: beat.selected_candidate_id, decision: 'clear-approval' });
                      }} className="rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-slate-300 disabled:opacity-40">Clear Approval</button> : null}
                    </div>
                  </div>

                  {expanded ? <div className="mx-3 mb-3 grid gap-4 border-t border-white/10 pt-3 xl:grid-cols-[minmax(0,1.8fr)_minmax(280px,0.9fr)]" data-testid="scene-asset-beat-details">
                    <div>
                      <div className="mb-2 flex items-center justify-between gap-2"><h5 className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">Candidates</h5><span className="text-xs text-slate-500">{beat.candidates.length} candidates</span></div>
                      {beat.candidates.length ? <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">{beat.candidates.map((candidate) => <div key={candidate.candidate_id} className={`rounded-xl border p-2 ${candidate.is_approved ? 'border-emerald-400/40' : candidate.is_current_draft ? 'border-sky-400/40' : candidate.is_rejected ? 'border-rose-400/20 opacity-60' : 'border-white/10'}`}>
                        <SceneAssetImage projectId={projectId} candidate={candidate} alt={`Candidate for ${beat.beat_id}`} className="max-h-[28rem] w-full rounded-lg bg-black/30 object-contain" />
                        <div className="mt-2 flex flex-wrap gap-1">
                          {!candidate.is_current_draft ? <TinyButton disabled={busy || !beat.can_review} label="Set as Current Draft" onClick={() => decide.mutate({ beatId: beat.beat_id, candidateId: candidate.candidate_id, decision: 'set-current-draft' })} /> : null}
                          {!candidate.is_approved ? <TinyButton disabled={busy || !beat.can_approve || !candidate.generated_from_current_prompt} label="Approve" onClick={() => decide.mutate({ beatId: beat.beat_id, candidateId: candidate.candidate_id, decision: 'approve' })} /> : null}
                          {!candidate.is_rejected && !candidate.is_approved ? <TinyButton disabled={busy || !beat.can_review} label="Reject" onClick={() => decide.mutate({ beatId: beat.beat_id, candidateId: candidate.candidate_id, decision: 'reject' })} /> : null}
                        </div>
                        <p className="mt-1 text-[11px] text-slate-500">{candidate.source_kind === 'uploaded' ? 'Uploaded - ' : ''}{candidateLabel(candidate) === 'Candidate' ? 'Unreviewed' : candidateLabel(candidate)}</p>
                        {!candidate.generated_from_current_prompt ? <p className="mt-1 text-[11px] text-amber-300">Generated with previous prompt</p> : null}
                      </div>)}</div> : <div className="rounded-xl border border-dashed border-white/10 p-6 text-center text-sm text-slate-500">No candidate completed for this beat.</div>}
                    </div>

                    <aside className="space-y-3 rounded-xl border border-white/10 bg-slate-950/30 p-3">
                      <h5 className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">Visual treatment</h5>
                      <Detail label="Subject action" value={beat.treatment.subject_action} />
                      <Detail label="Environment" value={beat.treatment.environment_emphasis} />
                      <Detail label="Composition" value={beat.treatment.composition} />
                      <div className="grid grid-cols-2 gap-2 text-xs text-slate-400"><Detail label="Framing" value={beat.treatment.framing} /><Detail label="Angle" value={beat.treatment.camera_angle} /><Detail label="Movement" value={beat.treatment.camera_movement} /><Detail label="Continuity" value={beat.treatment.continuity_with_previous} /></div>
                      <Detail label="Visual concept" value={beat.treatment.visual_concept} />
                      <Detail label="Prompt hash" value={beat.prompt_hash} />
                      <Detail label="Approval impact" value={`${beat.approval_change_impact_count} downstream beat(s)`} />

                      <div className="border-t border-white/10 pt-3" data-testid="scene-asset-beat-prompt">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="flex items-center gap-2"><h5 className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">Image prompt</h5><span className={`rounded-full border px-2 py-0.5 text-[10px] ${beat.prompt_origin === 'manual_edit' ? 'border-amber-400/30 text-amber-200' : 'border-white/10 text-slate-400'}`}>{beat.prompt_origin === 'manual_edit' ? 'Edited' : 'Compiled'}</span></div>
                          <div className="flex flex-wrap gap-1">
                            {!editing ? <button type="button" data-action="edit-beat-prompt" disabled={busy} onClick={() => startPromptEdit(beat)} className="rounded border border-white/10 px-2 py-1 text-xs text-white disabled:opacity-40">Edit</button> : null}
                            {beat.prompt_origin === 'manual_edit' && !editing ? <button type="button" data-action="restore-compiled-prompt" disabled={busy || restorePrompt.isPending} onClick={() => restorePrompt.mutate({ beatId: beat.beat_id, promptRevisionId: beat.prompt_revision_id })} className="rounded border border-amber-400/20 px-2 py-1 text-xs text-amber-200 disabled:opacity-40">Restore Compiled Prompt</button> : null}
                          </div>
                        </div>
                        {editing ? <div className="mt-2 space-y-3">
                          <textarea value={imagePromptDraft} onChange={(event) => setImagePromptDraft(event.target.value)} rows={7} className="w-full rounded-lg border border-white/10 bg-slate-950 p-3 text-xs text-slate-200" />
                          <textarea value={negativePromptDraft} onChange={(event) => setNegativePromptDraft(event.target.value)} rows={4} className="w-full rounded-lg border border-white/10 bg-slate-950 p-3 text-xs text-slate-300" />
                          <div className="flex flex-wrap gap-2"><ActionButton disabled={busy || savePrompt.isPending} label="Save Prompt" onClick={() => savePrompt.mutate({ beatId: beat.beat_id, promptRevisionId: beat.prompt_revision_id })} /><ActionButton disabled={busy} label="Cancel" onClick={() => setEditingBeatId(null)} />{beat.prompt_origin === 'manual_edit' ? <ActionButton disabled={busy || restorePrompt.isPending} label="Restore Compiled Prompt" onClick={() => restorePrompt.mutate({ beatId: beat.beat_id, promptRevisionId: beat.prompt_revision_id })} /> : null}</div>
                        </div> : <p className="mt-2 whitespace-pre-line text-xs leading-5 text-slate-300">{beat.image_prompt}</p>}
                      </div>
                    </aside>
                  </div> : null}
                </article>;
              })}
            </div>
          </section>;
        })}
      </section> : null}

      {sceneWindow.hasMore ? <div data-testid="scene-assets-progressive-window" className="panel-surface flex flex-wrap items-center justify-between gap-3 p-4 text-xs text-slate-400"><span>{sceneWindow.hiddenCount} scene(s) not mounted yet.</span><div className="flex gap-2"><button type="button" onClick={sceneWindow.showMore} className="rounded-lg border border-white/10 px-3 py-1.5 text-slate-200">Show more</button><button type="button" onClick={sceneWindow.showAll} className="rounded-lg border border-white/10 px-3 py-1.5 text-slate-200">Show all</button></div></div> : null}
      {!filteredScenes.length ? <div className="panel-surface p-6 text-sm text-slate-400">No beats match the current filter.</div> : null}
    </section>
  );
}

function OperationCard({ operation, cancelling, onCancel }: { operation: SceneAssetV2OperationStatusResponse; cancelling: boolean; onCancel: () => void }) {
  const progress = operationProgress(operation);
  return <div className="rounded-xl border border-white/10 bg-slate-950/30 p-3"><div className="flex flex-wrap items-center justify-between gap-3"><div><div className="text-sm font-medium text-white">{operation.operation_type} - {operation.status}</div><div className="text-xs text-slate-500">{operation.completed_count}/{operation.total_beat_count} complete - {operation.failed_count} failed</div></div><button type="button" disabled={cancelling} onClick={onCancel} className="rounded-lg border border-rose-400/30 bg-rose-400/10 px-3 py-1.5 text-xs font-semibold text-rose-200 disabled:opacity-40">Cancel</button></div><div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/10"><div className="h-full rounded-full bg-white/70" style={{ width: `${Math.min(100, progress)}%` }} /></div><div className="mt-2 flex flex-wrap gap-2">{operation.beats.map((beat) => <span key={beat.operation_beat_id} className="rounded-full border border-white/10 px-2 py-1 text-[10px] text-slate-400">{beat.beat_id}: {beat.status}</span>)}</div>{operation.error_message ? <p className="mt-2 text-xs text-rose-300">{operation.error_code ? `${operation.error_code}: ` : ''}{operation.error_message}</p> : null}</div>;
}

function ActionButton({ disabled, label, onClick }: { disabled: boolean; label: string; onClick: () => void }) {
  return <button type="button" disabled={disabled} onClick={onClick} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-slate-200 disabled:cursor-not-allowed disabled:opacity-35">{label}</button>;
}

function TinyButton({ disabled, label, onClick }: { disabled: boolean; label: string; onClick: () => void }) {
  return <button type="button" disabled={disabled} onClick={onClick} className="rounded border border-white/10 px-2 py-1 text-xs text-white disabled:opacity-40">{label}</button>;
}

function Detail({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg border border-white/5 bg-white/[0.02] p-2"><div className="text-[10px] uppercase tracking-[0.12em] text-slate-500">{label}</div><div className="mt-1 break-words text-slate-300">{value}</div></div>;
}
