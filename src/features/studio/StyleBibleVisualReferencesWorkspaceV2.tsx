import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  patchVisualReferenceAnchor,
  visualReferencesV2Client,
  visualReferenceV2Keys,
  VisualReferenceV2ApiError,
  type VisualReferenceV2Anchor,
  type VisualReferenceV2AnchorType,
  type VisualReferenceV2Execution,
  type VisualReferenceV2GenerateRequest,
  type VisualReferenceV2Workspace,
} from '../../api/visualReferencesV2';
import { ActiveOperationsDrawer } from './ActiveOperationsDrawer';

const POLL_INTERVAL_MS = 2000;
const ACTIVE_OPERATION_STATUSES = new Set(['queued', 'running', 'finalizing']);
const DEFAULT_RATIOS: Record<VisualReferenceV2AnchorType, '1:1' | '16:9'> = {
  character: '1:1',
  location: '16:9',
  prop: '1:1',
  recurring_visual_subject: '1:1',
};
const CATEGORY_LABELS: Record<VisualReferenceV2AnchorType, string> = {
  character: 'Characters',
  location: 'Locations',
  prop: 'Props',
  recurring_visual_subject: 'Visual subjects',
};

function errorMessage(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof VisualReferenceV2ApiError) return `${error.errorCode}: ${error.message}`;
  return error instanceof Error ? error.message : 'Unknown Visual Reference error.';
}

function executionProgress(operation: VisualReferenceV2Execution): number {
  const terminal = operation.completed_count + operation.failed_count + operation.cancelled_count;
  return Math.round((terminal / Math.max(1, operation.total_anchor_count)) * 100);
}

function anchorKey(anchorType: string, anchorId: string): string {
  return `${anchorType}:${anchorId}`;
}

export function StyleBibleVisualReferencesWorkspaceV2({
  projectId,
  styleBibleArtifactId,
  enabled,
}: {
  projectId: string;
  styleBibleArtifactId: string | null;
  enabled: boolean;
}) {
  const queryClient = useQueryClient();
  const workspaceKey = styleBibleArtifactId
    ? visualReferenceV2Keys.workspace(projectId, styleBibleArtifactId)
    : ['visual-references-v2', projectId, 'none'] as const;
  const executionsKey = visualReferenceV2Keys.executions(projectId);
  const [activeCategory, setActiveCategory] = useState<VisualReferenceV2AnchorType>('character');
  const [operationsDrawerOpen, setOperationsDrawerOpen] = useState(false);
  const [localOperations, setLocalOperations] = useState<Map<string, Array<{ anchor_type: string; anchor_id: string }>>>(() => new Map());
  const observedOperations = useRef<Map<string, Array<{ anchor_type: string; anchor_id: string }>>>(new Map());
  const [selectedByAnchor, setSelectedByAnchor] = useState<Record<string, string | null>>({});
  const [editingAnchor, setEditingAnchor] = useState<string | null>(null);
  const [promptDraft, setPromptDraft] = useState('');
  const [negativeDraft, setNegativeDraft] = useState('');

  const workspace = useQuery({
    queryKey: workspaceKey,
    queryFn: ({ signal }) => visualReferencesV2Client.getWorkspace(projectId, signal),
    enabled: enabled && Boolean(styleBibleArtifactId),
    retry: false,
  });
  const shouldPollExecutions = Boolean(
    enabled && ((workspace.data?.active_operation_count ?? 0) > 0 || localOperations.size > 0),
  );
  const executions = useQuery({
    queryKey: executionsKey,
    queryFn: ({ signal }) => visualReferencesV2Client.getExecutions(projectId, signal),
    enabled: shouldPollExecutions,
    retry: false,
    refetchInterval: (query) => (query.state.data?.executions.length || localOperations.size ? POLL_INTERVAL_MS : false),
  });

  const patchAnchor = (anchor: VisualReferenceV2Anchor) => {
    queryClient.setQueryData<VisualReferenceV2Workspace>(workspaceKey, (current) => (
      current ? patchVisualReferenceAnchor(current, anchor) : current
    ));
    queryClient.setQueryData(visualReferenceV2Keys.anchor(projectId, anchor.anchor_type, anchor.anchor_id), anchor);
  };

  const refreshAnchor = async (anchorType: VisualReferenceV2AnchorType, anchorId: string) => {
    const anchor = await visualReferencesV2Client.getAnchor(projectId, anchorType, anchorId);
    patchAnchor(anchor);
  };

  const refreshAnchorList = async (anchors: Array<{ anchor_type: string; anchor_id: string }>) => {
    const unique = new Map<string, { anchor_type: VisualReferenceV2AnchorType; anchor_id: string }>();
    for (const anchor of anchors) {
      unique.set(anchorKey(anchor.anchor_type, anchor.anchor_id), {
        anchor_type: anchor.anchor_type as VisualReferenceV2AnchorType,
        anchor_id: anchor.anchor_id,
      });
    }
    await Promise.all(Array.from(unique.values()).map((anchor) => refreshAnchor(anchor.anchor_type, anchor.anchor_id)));
  };

  useEffect(() => {
    const current = new Map<string, Array<{ anchor_type: string; anchor_id: string }>>();
    for (const operation of executions.data?.executions ?? []) {
      current.set(operation.operation_id, operation.anchors.map((anchor) => ({
        anchor_type: anchor.anchor_type,
        anchor_id: anchor.anchor_id,
      })));
    }
    for (const [operationId, anchors] of localOperations) {
      if (!current.has(operationId)) current.set(operationId, anchors);
    }

    const activeIds = new Set((executions.data?.executions ?? []).map((operation) => operation.operation_id));
    const completed: Array<{ anchor_type: string; anchor_id: string }> = [];
    for (const [operationId, anchors] of observedOperations.current) {
      if (!activeIds.has(operationId)) completed.push(...anchors);
    }
    for (const [operationId, anchors] of localOperations) {
      if (executions.data && !activeIds.has(operationId) && observedOperations.current.has(operationId)) {
        completed.push(...anchors);
      }
    }
    if (completed.length) {
      void refreshAnchorList(completed).then(() => {
        queryClient.setQueryData<VisualReferenceV2Workspace>(workspaceKey, (value) => value ? {
          ...value,
          active_operation_count: Math.max(0, value.active_operation_count - 1),
        } : value);
      });
      setLocalOperations((previous) => {
        const next = new Map(previous);
        for (const operationId of observedOperations.current.keys()) {
          if (!activeIds.has(operationId)) next.delete(operationId);
        }
        return next;
      });
    }
    observedOperations.current = current;
  }, [executions.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const generationRequest = (): VisualReferenceV2GenerateRequest => ({
    style_bible_artifact_id: styleBibleArtifactId!,
    expected_visual_reference_set_artifact_id: workspace.data?.published_visual_reference_set_artifact_id ?? null,
    candidate_count: 1,
    aspect_ratios_by_anchor_type: DEFAULT_RATIOS,
  });

  const trackOperation = (submission: { operation_id: string; scheduled_anchors: Array<{ anchor_type: string; anchor_id: string }> }) => {
    setLocalOperations((previous) => new Map(previous).set(submission.operation_id, submission.scheduled_anchors));
    observedOperations.current.set(submission.operation_id, submission.scheduled_anchors);
    queryClient.setQueryData<VisualReferenceV2Workspace>(workspaceKey, (value) => value ? {
      ...value,
      active_operation_count: value.active_operation_count + 1,
    } : value);
    void queryClient.refetchQueries({ queryKey: executionsKey, type: 'active' });
  };

  const generate = useMutation({
    mutationFn: () => visualReferencesV2Client.submitGeneration(projectId, generationRequest()),
    onSuccess: trackOperation,
  });
  const retryFailed = useMutation({
    mutationFn: () => visualReferencesV2Client.retryFailed(projectId, generationRequest()),
    onSuccess: trackOperation,
  });
  const regenerate = useMutation({
    mutationFn: (anchor: VisualReferenceV2Anchor) => visualReferencesV2Client.regenerateAnchor(
      projectId,
      anchor.anchor_type,
      anchor.anchor_id,
      generationRequest(),
    ),
    onSuccess: trackOperation,
  });
  const savePrompt = useMutation({
    mutationFn: (anchor: VisualReferenceV2Anchor) => visualReferencesV2Client.updatePrompt(
      projectId,
      anchor.anchor_type,
      anchor.anchor_id,
      anchor.version,
      promptDraft,
      negativeDraft,
    ),
    onSuccess: async (_result, anchor) => {
      await refreshAnchor(anchor.anchor_type, anchor.anchor_id);
      setEditingAnchor(null);
    },
  });
  const upload = useMutation({
    mutationFn: ({ anchor, file }: { anchor: VisualReferenceV2Anchor; file: File }) => visualReferencesV2Client.upload(
      projectId,
      anchor.anchor_type,
      anchor.anchor_id,
      anchor.version,
      file,
    ),
    onSuccess: async (_result, variables) => refreshAnchor(variables.anchor.anchor_type, variables.anchor.anchor_id),
  });
  const approve = useMutation({
    mutationFn: ({ anchor, candidateId }: { anchor: VisualReferenceV2Anchor; candidateId: string }) => visualReferencesV2Client.approve(
      projectId,
      anchor.anchor_type,
      anchor.anchor_id,
      anchor.version,
      candidateId,
    ),
    onSuccess: async (_result, variables) => refreshAnchor(variables.anchor.anchor_type, variables.anchor.anchor_id),
  });
  const clearApproval = useMutation({
    mutationFn: (anchor: VisualReferenceV2Anchor) => visualReferencesV2Client.clearApproval(
      projectId,
      anchor.anchor_type,
      anchor.anchor_id,
      anchor.version,
    ),
    onSuccess: async (_result, anchor) => refreshAnchor(anchor.anchor_type, anchor.anchor_id),
  });
  const cancelOperation = useMutation({
    mutationFn: (operationId: string) => visualReferencesV2Client.cancelOperation(projectId, operationId),
    onSuccess: async (_result, operationId) => {
      const anchors = observedOperations.current.get(operationId) ?? localOperations.get(operationId) ?? [];
      await refreshAnchorList(anchors);
      setLocalOperations((previous) => {
        const next = new Map(previous);
        next.delete(operationId);
        return next;
      });
      await queryClient.refetchQueries({ queryKey: executionsKey, type: 'active' });
    },
  });

  const activeOperations = (executions.data?.executions ?? []).filter((operation) => ACTIVE_OPERATION_STATUSES.has(operation.status));
  const activeOperationProgress = activeOperations.length
    ? Math.round(activeOperations.reduce((sum, operation) => sum + executionProgress(operation), 0) / activeOperations.length)
    : 0;
  const categoryCounts = useMemo(() => {
    const counts = new Map<VisualReferenceV2AnchorType, number>();
    for (const anchor of workspace.data?.anchors ?? []) counts.set(anchor.anchor_type, (counts.get(anchor.anchor_type) ?? 0) + 1);
    return counts;
  }, [workspace.data]);
  const anchors = (workspace.data?.anchors ?? []).filter((anchor) => anchor.anchor_type === activeCategory);
  const mutationError = errorMessage(
    generate.error ?? retryFailed.error ?? regenerate.error ?? savePrompt.error ?? upload.error ?? approve.error ?? clearApproval.error ?? cancelOperation.error,
  );

  if (!enabled || !styleBibleArtifactId) return <div className="panel-surface p-6 text-sm text-slate-400">Approve the Style Bible first.</div>;
  if (workspace.isLoading) return <div className="panel-surface p-6 text-sm text-slate-400">Loading Visual References...</div>;
  if (workspace.error) return <div className="panel-surface p-6 text-sm text-rose-300">{errorMessage(workspace.error)}</div>;
  if (!workspace.data) return <div className="panel-surface p-6 text-sm text-slate-400">Visual Reference workspace is unavailable.</div>;

  const data = workspace.data;
  return <section className="space-y-4" data-testid="style-bible-visual-references-v2">
    <ActiveOperationsDrawer
      open={operationsDrawerOpen}
      onOpenChange={setOperationsDrawerOpen}
      activeCount={activeOperations.length}
      averageProgress={activeOperationProgress}
      testId="visual-reference-operation-drawer"
    >
      {activeOperations.map((operation) => <OperationCard
        key={operation.operation_id}
        operation={operation}
        cancelling={cancelOperation.isPending}
        onCancel={() => cancelOperation.mutate(operation.operation_id)}
      />)}
    </ActiveOperationsDrawer>

    <header className="panel-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-slate-400">Style Bible</p>
          <h3 className="mt-1 text-xl font-semibold text-white">Visual References</h3>
          <p className="mt-2 max-w-3xl text-sm text-slate-300">Generate, review, and approve reusable anchor images. Working state is durable; publication is a separate explicit step.</p>
        </div>
        <div className="text-right text-sm text-slate-300">
          <div className="font-medium text-white">{data.readiness.approved_count} / {data.readiness.required_count} approved</div>
          <div>{data.published_visual_reference_set_artifact_id ? 'Published snapshot exists' : 'Working draft'}</div>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" disabled={generate.isPending || data.anchors.length === 0} onClick={() => generate.mutate()} className="rounded-lg bg-white px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-40">Generate Reference Set</button>
        <button type="button" disabled={retryFailed.isPending || !data.anchors.some((anchor) => anchor.resolution_status === 'failed')} onClick={() => retryFailed.mutate()} className="rounded-lg border border-rose-400/30 px-3 py-2 text-sm text-rose-200 disabled:opacity-40">Retry Failed</button>
        <button type="button" disabled={workspace.isFetching} onClick={() => void queryClient.invalidateQueries({ queryKey: workspaceKey })} className="rounded-lg border border-white/10 px-3 py-2 text-sm text-slate-300 disabled:opacity-40">Refresh Workspace</button>
      </div>
      {data.readiness.stale_anchor_keys.length ? <p className="mt-2 text-xs text-amber-300">{data.readiness.stale_anchor_keys.length} anchor(s) are stale against the current Style Bible. Generate to synchronize them.</p> : null}
      {mutationError ? <p role="alert" className="mt-2 text-sm text-rose-300">{mutationError}</p> : null}
    </header>

    <nav className="flex gap-2 overflow-x-auto" aria-label="Visual Reference category">
      {(Object.keys(CATEGORY_LABELS) as VisualReferenceV2AnchorType[]).map((category) => <button
        key={category}
        type="button"
        onClick={() => setActiveCategory(category)}
        className={`rounded-full border px-3 py-1.5 text-xs ${category === activeCategory ? 'border-cyan-300/40 bg-cyan-300/10 text-cyan-100' : 'border-white/10 text-slate-400'}`}
      >
        {CATEGORY_LABELS[category]} ({categoryCounts.get(category) ?? 0})
      </button>)}
    </nav>

    <div className="grid gap-4 xl:grid-cols-2">
      {anchors.map((anchor) => {
        const key = anchorKey(anchor.anchor_type, anchor.anchor_id);
        const selected = selectedByAnchor[key] ?? anchor.selected_candidate_id ?? anchor.approved_candidate_id ?? anchor.candidates[0]?.candidate_id ?? null;
        const editing = editingAnchor === key;
        const busy = Boolean(anchor.active_operation_anchor_id) || regenerate.isPending || upload.isPending || approve.isPending || clearApproval.isPending;
        return <article key={key} className="panel-surface p-4" data-anchor-key={key}>
          <div className="flex items-start justify-between gap-3">
            <div><div className="text-sm font-semibold text-white">{anchor.label}</div><div className="mt-1 text-xs text-slate-500">{anchor.anchor_id} · v{anchor.version} · {anchor.resolution_status}</div></div>
            {anchor.upstream_stale ? <span className="rounded-full border border-amber-400/30 px-2 py-1 text-[10px] text-amber-200">Upstream stale</span> : null}
          </div>

          <div className="mt-3 aspect-video overflow-hidden rounded-xl border border-white/10 bg-slate-950/60">
            {selected ? (() => {
              const candidate = anchor.candidates.find((item) => item.candidate_id === selected);
              return candidate ? <img loading="lazy" src={visualReferencesV2Client.resolveUrl(candidate.file_url)} alt={anchor.label} className="h-full w-full object-contain" /> : null;
            })() : <div className="flex h-full items-center justify-center text-sm text-slate-600">No candidate</div>}
          </div>

          {anchor.candidates.length ? <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
            {anchor.candidates.map((candidate) => <button
              key={candidate.candidate_id}
              type="button"
              onClick={() => setSelectedByAnchor((current) => ({ ...current, [key]: candidate.candidate_id }))}
              className={`relative h-16 w-16 flex-none overflow-hidden rounded-lg border ${candidate.candidate_id === selected ? 'border-cyan-300 ring-1 ring-cyan-300/60' : 'border-white/10'}`}
              title={candidate.source_kind}
            >
              <img loading="lazy" src={visualReferencesV2Client.resolveUrl(candidate.file_url)} alt="" className="h-full w-full object-cover" />
              {candidate.candidate_id === anchor.approved_candidate_id ? <span className="absolute right-1 top-1 rounded bg-emerald-300 px-1 text-[9px] font-bold text-slate-950">OK</span> : null}
            </button>)}
          </div> : null}

          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" disabled={busy || anchor.upstream_stale} onClick={() => regenerate.mutate(anchor)} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-slate-200 disabled:opacity-40">Regenerate</button>
            <label className={`rounded-lg border border-white/10 px-3 py-1.5 text-xs text-slate-200 ${busy || anchor.version < 1 || anchor.upstream_stale ? 'pointer-events-none opacity-40' : 'cursor-pointer'}`}>
              Upload
              <input type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) upload.mutate({ anchor, file });
              }} />
            </label>
            {selected ? <button type="button" disabled={busy || anchor.version < 1 || anchor.upstream_stale} onClick={() => approve.mutate({ anchor, candidateId: selected })} className="rounded-lg border border-emerald-400/40 px-3 py-1.5 text-xs text-emerald-200 disabled:opacity-40">Approve</button> : null}
            {anchor.approved_candidate_id ? <button type="button" disabled={busy} onClick={() => clearApproval.mutate(anchor)} className="rounded-lg border border-amber-400/30 px-3 py-1.5 text-xs text-amber-200 disabled:opacity-40">Clear Approval</button> : null}
          </div>

          <div className="mt-4 border-t border-white/10 pt-3">
            <div className="flex items-center justify-between gap-3"><span className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">Prompt</span>{!editing ? <button type="button" disabled={busy || anchor.version < 1 || anchor.upstream_stale} onClick={() => { setEditingAnchor(key); setPromptDraft(anchor.prompt); setNegativeDraft(anchor.negative_prompt); }} className="rounded border border-white/10 px-2 py-1 text-xs text-slate-200 disabled:opacity-40">Edit</button> : null}</div>
            {editing ? <div className="mt-2 space-y-2"><textarea rows={5} value={promptDraft} onChange={(event) => setPromptDraft(event.target.value)} className="w-full rounded-lg border border-white/10 bg-slate-950 p-2 text-xs text-slate-200" /><textarea rows={3} value={negativeDraft} onChange={(event) => setNegativeDraft(event.target.value)} className="w-full rounded-lg border border-white/10 bg-slate-950 p-2 text-xs text-slate-300" /><div className="flex gap-2"><button type="button" disabled={savePrompt.isPending || !promptDraft.trim()} onClick={() => savePrompt.mutate(anchor)} className="rounded-lg border border-cyan-400/30 px-3 py-1.5 text-xs text-cyan-100 disabled:opacity-40">Save Prompt</button><button type="button" onClick={() => setEditingAnchor(null)} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-slate-300">Cancel</button></div></div> : <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-300">{anchor.prompt}</p>}
          </div>
        </article>;
      })}
    </div>
    {!anchors.length ? <div className="panel-surface p-6 text-sm text-slate-500">No anchors in this category.</div> : null}
  </section>;
}

function OperationCard({ operation, cancelling, onCancel }: { operation: VisualReferenceV2Execution; cancelling: boolean; onCancel: () => void }) {
  const progress = executionProgress(operation);
  return <div className="rounded-xl border border-white/10 bg-slate-950/30 p-3">
    <div className="flex items-center justify-between gap-3"><div><div className="text-sm font-medium text-white">{operation.operation_type} · {operation.status}</div><div className="text-xs text-slate-500">{operation.completed_count}/{operation.total_anchor_count} complete · {operation.failed_count} failed</div></div><button type="button" disabled={cancelling} onClick={onCancel} className="rounded-lg border border-rose-400/30 px-3 py-1.5 text-xs text-rose-200 disabled:opacity-40">Cancel</button></div>
    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/10"><div className="h-full bg-white/70" style={{ width: `${progress}%` }} /></div>
    <div className="mt-2 flex flex-wrap gap-1">{operation.anchors.map((anchor) => <span key={anchor.operation_anchor_id} className="rounded-full border border-white/10 px-2 py-1 text-[10px] text-slate-400">{anchor.anchor_id}: {anchor.status}</span>)}</div>
  </div>;
}
