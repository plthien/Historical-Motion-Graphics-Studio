import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiClient, type VoiceGenerationProvider, type VoiceJobStatusResponse, type VoicePreset, type VoiceWorkspaceResponse } from '../../api/client';
import { voicePacingAcceptanceApi } from '../../api/voicePacingAcceptance';
import {
  NarrationMasterPlayer,
  VoiceGenerationProgress,
  VoiceReviewGate,
  VoiceSectionRemediationList,
  VoiceSummary,
} from './voice/VoiceReviewComponents';
import { ChannelVoiceProfileSetup } from './voice/ChannelVoiceProfileSetup';

const ACTIVE_JOB = new Set(['queued', 'running', 'waiting_for_provider', 'assembling', 'finalizing']);
const TERMINAL_JOB = new Set(['completed', 'failed', 'cancelled', 'superseded']);
const PARKED_JOB_ACTION = 'submission_outcome_unknown';
const SYNTHESIS_JOB_ACTION = new Set(['submit', 'poll', 'materialize', 'retry_wait']);

type VoiceLaunchState = {
  jobId: string | null;
  operationType: 'generate_all' | 'generate_missing' | 'regenerate_section';
  operationLabel: string;
  status: string;
  providerLabel: string;
};

export function VoicePanel({ projectId, enabled }: { projectId: string; enabled: boolean }) {
  const queryClient = useQueryClient();
  const [generationProvider, setGenerationProvider] = useState<VoiceGenerationProvider>('elevenlabs');
  const [voicePreset, setVoicePreset] = useState<VoicePreset>('quality_cloned_voice');
  const [kokoroVoiceId, setKokoroVoiceId] = useState<string>('');
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [launchState, setLaunchState] = useState<VoiceLaunchState | null>(null);
  const [lastTerminalJob, setLastTerminalJob] = useState<VoiceJobStatusResponse | null>(null);
  const [queuedSectionIds, setQueuedSectionIds] = useState<string[]>([]);
  const [regeneratingSectionId, setRegeneratingSectionId] = useState<string | null>(null);
  const [acceptingPacingSectionId, setAcceptingPacingSectionId] = useState<string | null>(null);
  const [queueReadyToDrain, setQueueReadyToDrain] = useState(false);
  const settledTerminalJobIdsRef = useRef<Set<string>>(new Set());
  const providerTouchedRef = useRef(false);
  const queryKey = ['voice-workspace', projectId] as const;

  const workspace = useQuery({
    queryKey,
    queryFn: ({ signal }) => apiClient.getVoiceWorkspace(projectId, signal),
    enabled,
  });

  const discoveredJobId = workspace.data?.active_operation?.job_id ?? null;
  const discoverableJobId = discoveredJobId && !settledTerminalJobIdsRef.current.has(discoveredJobId) ? discoveredJobId : null;
  const providerStatuses = workspace.data?.available_generation_providers ?? [];
  const selectedProviderStatus = providerStatuses.find((item) => item.generation_provider === generationProvider) ?? null;
  const selectedPresetStatus = generationProvider === 'elevenlabs' ? selectedProviderStatus?.generation_presets?.[voicePreset] ?? null : null;
  const selectedGenerationReady = generationProvider === 'elevenlabs' && selectedPresetStatus
    ? selectedPresetStatus.ready
    : selectedProviderStatus?.generation_ready;
  const selectedAlignmentReady = selectedProviderStatus?.alignment_ready;
  const selectedProviderIssue = generationProvider === 'elevenlabs' && selectedPresetStatus
    ? selectedPresetStatus.issue
    : selectedProviderStatus?.issue ?? null;
  const generationReadinessLabel = selectedGenerationReady === undefined ? 'Unknown' : (selectedGenerationReady ? 'Ready' : 'Blocked');
  const alignmentReadinessLabel = selectedAlignmentReady === undefined ? 'Unknown' : (selectedAlignmentReady ? 'Ready' : 'Blocked');
  const kokoroVoiceCatalog = workspace.data?.kokoro_voice_catalog ?? [];

  useEffect(() => {
    if (discoverableJobId && !activeJobId) setActiveJobId(discoverableJobId);
  }, [discoverableJobId, activeJobId]);

  useEffect(() => {
    const provider = workspace.data?.selected_generation_provider;
    if (provider && !providerTouchedRef.current) setGenerationProvider(provider);
  }, [workspace.data?.selected_generation_provider]);

  useEffect(() => {
    if (generationProvider !== 'kokoro_local') return;
    if (kokoroVoiceId && kokoroVoiceCatalog.some((voice) => voice.voice_id === kokoroVoiceId)) return;
    setKokoroVoiceId(kokoroVoiceCatalog[0]?.voice_id ?? '');
  }, [generationProvider, kokoroVoiceCatalog, kokoroVoiceId]);

  const job = useQuery({
    queryKey: ['voice-job', projectId, activeJobId],
    queryFn: () => apiClient.getVoiceJob(projectId, activeJobId!),
    enabled: Boolean(activeJobId),
    refetchInterval: (query) => {
      const current = query.state.data;
      if (!current || !ACTIVE_JOB.has(current.status)) return false;
      if (workspace.data?.alignment_status === 'submit_outcome_unknown') return false;
      if (current.action === PARKED_JOB_ACTION && !current.next_retry_at && !current.cancel_requested) return false;
      return 800;
    },
  });

  const currentJob = activeJobId && job.data?.job_id === activeJobId ? job.data : undefined;
  const activeSynthesisSectionId = useMemo(() => {
    const action = currentJob?.action ?? null;
    const segmentId = currentJob?.current_segment_id ?? null;
    if (action && SYNTHESIS_JOB_ACTION.has(action) && segmentId) {
      return workspace.data?.sections.find((section) => section.segment_ids.includes(segmentId))?.section_id ?? null;
    }
    if (!currentJob && launchState?.operationType === 'regenerate_section') return regeneratingSectionId;
    return null;
  }, [currentJob, launchState?.operationType, regeneratingSectionId, workspace.data?.sections]);

  useEffect(() => {
    if (!launchState?.jobId || currentJob?.job_id !== launchState.jobId) return;
    setLaunchState(null);
  }, [launchState?.jobId, currentJob?.job_id]);

  const refreshWorkspace = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey });
  }, [queryClient, queryKey]);
  const refreshProject = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: ['v3-projects'] });
  }, [queryClient]);

  const terminalJobId = currentJob?.job_id ?? null;
  const terminalJobStatus = currentJob?.status ?? null;

  useEffect(() => {
    if (!terminalJobId || !terminalJobStatus || !TERMINAL_JOB.has(terminalJobStatus)) return;
    if (settledTerminalJobIdsRef.current.has(terminalJobId)) return;
    settledTerminalJobIdsRef.current.add(terminalJobId);
    if (currentJob && terminalJobStatus !== 'completed') setLastTerminalJob(currentJob);
    else setLastTerminalJob(null);
    setActiveJobId((current) => current === terminalJobId ? null : current);
    setRegeneratingSectionId(null);
    void refreshWorkspace().then(() => setQueueReadyToDrain(true));
  }, [terminalJobId, terminalJobStatus, currentJob, refreshWorkspace]);

  const expectedAudio = workspace.data?.current_narration_audio_artifact_id ?? null;
  const expectedTiming = workspace.data?.current_narration_word_timing_map_artifact_id ?? null;
  const draftAudio = workspace.data?.draft_narration_audio_artifact_id ?? null;
  const draftTiming = workspace.data?.draft_narration_word_timing_map_artifact_id ?? null;
  const beginLaunch = (operationType: VoiceLaunchState['operationType'], operationLabel: string) => {
    setLastTerminalJob(null);
    const providerLabel = generationProvider === 'kokoro_local'
      ? `Kokoro Local${kokoroVoiceId ? `: ${kokoroVoiceId}` : ''}`
      : `ElevenLabs: ${voicePreset === 'economy' ? 'Economy' : 'Quality cloned voice'}`;
    setLaunchState({
      jobId: null,
      operationType,
      operationLabel,
      status: 'submitting',
      providerLabel,
    });
  };
  const captureJob = (result: { job_id: string; status?: string }) => {
    settledTerminalJobIdsRef.current.delete(result.job_id);
    setLaunchState((current) => current ? { ...current, jobId: result.job_id, status: result.status ?? 'queued' } : current);
    setActiveJobId(result.job_id);
    void queryClient.invalidateQueries({ queryKey });
  };

  const generate = useMutation({
    mutationFn: (operationType: 'generate_all' | 'generate_missing') => apiClient.generateVoice(projectId, {
      operation_type: operationType,
      generation_provider: generationProvider,
      voice_preset: generationProvider === 'elevenlabs' ? voicePreset : null,
      generation_voice_id: generationProvider === 'kokoro_local' ? kokoroVoiceId : null,
      expected_narration_audio_artifact_id: expectedAudio,
      expected_narration_word_timing_map_artifact_id: expectedTiming,
    }),
    onMutate: (operationType) => beginLaunch(operationType, operationType === 'generate_all' ? 'Generate all narration' : 'Generate missing narration'),
    onSuccess: captureJob,
    onError: () => setLaunchState(null),
  });
  const regenerate = useMutation({
    mutationFn: (section: NonNullable<typeof workspace.data>['sections'][number]) => {
      if (!section.source_remediation_operation_id || !section.remediation_report_sha256) throw new Error('VOICE_PACING_REMEDIATION_STALE: Missing remediation lineage.');
      return apiClient.regenerateVoiceSection(projectId, section.section_id, expectedAudio, expectedTiming, section.source_remediation_operation_id, section.remediation_report_sha256);
    },
    onMutate: (section) => {
      setRegeneratingSectionId(section.section_id);
      beginLaunch('regenerate_section', `Regenerate section ${section.section_order}`);
    },
    onSuccess: captureJob,
    onError: () => {
      setLaunchState(null);
      setRegeneratingSectionId(null);
      void refreshWorkspace().then(() => setQueueReadyToDrain(true));
    },
  });
  const acceptPacingDeviation = useMutation({
    mutationFn: (section: VoiceWorkspaceResponse['sections'][number]) => {
      if (!section.source_remediation_operation_id || !section.remediation_report_sha256 || !section.remediation_identity) {
        throw new Error('VOICE_PACING_ACCEPTANCE_STALE: Missing exact pacing remediation lineage.');
      }
      return voicePacingAcceptanceApi.acceptSection(
        projectId,
        section.section_id,
        section.source_remediation_operation_id,
        section.remediation_report_sha256,
        section.remediation_identity,
      );
    },
    onMutate: (section) => setAcceptingPacingSectionId(section.section_id),
    onSuccess: async (result) => {
      setAcceptingPacingSectionId(null);
      if (result.status === 'resuming') {
        settledTerminalJobIdsRef.current.delete(result.job_id);
        setActiveJobId(result.job_id);
        await queryClient.invalidateQueries({ queryKey: ['voice-job', projectId, result.job_id] });
      }
      await refreshWorkspace();
    },
    onError: () => {
      setAcceptingPacingSectionId(null);
      void refreshWorkspace();
    },
  });
  const cancel = useMutation({
    mutationFn: () => apiClient.cancelVoiceJob(projectId, activeJobId ?? discoverableJobId!),
    onSuccess: (result) => {
      setLaunchState(null);
      setActiveJobId(result.job_id);
      queryClient.setQueryData(['voice-job', projectId, result.job_id], result);
    },
  });
  const approve = useMutation({
    mutationFn: () => apiClient.approveVoice(projectId, draftAudio!, draftTiming!, expectedAudio, expectedTiming),
    onSuccess: async () => {
      await Promise.all([refreshWorkspace(), refreshProject()]);
    },
  });
  const retryAlignment = useMutation({
    mutationFn: () => apiClient.retryVoiceAlignment(projectId, workspace.data!.active_operation!.job_id),
    onSuccess: async () => {
      const jobId = workspace.data?.active_operation?.job_id ?? null;
      if (jobId) {
        settledTerminalJobIdsRef.current.delete(jobId);
        setActiveJobId(jobId);
        await queryClient.invalidateQueries({ queryKey: ['voice-job', projectId, jobId] });
      }
      await queryClient.invalidateQueries({ queryKey });
    },
  });
  const realignCurrent = useMutation({
    mutationFn: () => apiClient.realignCurrentVoice(projectId, expectedAudio!, expectedTiming!),
    onSuccess: async () => {
      await Promise.all([
        refreshWorkspace(),
        refreshProject(),
        queryClient.invalidateQueries({ queryKey: ['assembly-workspace', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['render-workspace', projectId] }),
      ]);
    },
  });

  const operationActive = Boolean(activeJobId || discoverableJobId);
  const pending = generate.isPending || regenerate.isPending || acceptPacingDeviation.isPending || cancel.isPending || approve.isPending || realignCurrent.isPending || operationActive;
  const requestSectionRegeneration = useCallback((section: VoiceWorkspaceResponse['sections'][number]) => {
    const busy = Boolean(activeJobId || discoverableJobId || regenerate.isPending);
    const activeTargetSectionId = workspace.data?.active_operation?.target_section_id ?? null;
    if (busy) {
      if (section.section_id === activeTargetSectionId) return;
      if (!section.regeneration_supported) return;
      setQueuedSectionIds((current) => (
        current.includes(section.section_id) || regeneratingSectionId === section.section_id
          ? current
          : [...current, section.section_id]
      ));
      return;
    }
    if (section.can_regenerate) regenerate.mutate(section);
  }, [activeJobId, discoverableJobId, regenerate, regeneratingSectionId, workspace.data?.active_operation?.target_section_id]);

  useEffect(() => {
    if (!queueReadyToDrain || workspace.isFetching || operationActive || regenerate.isPending) return;
    const nextSectionId = queuedSectionIds[0];
    if (!nextSectionId) {
      setQueueReadyToDrain(false);
      return;
    }
    const remaining = queuedSectionIds.slice(1);
    const latestWorkspace = queryClient.getQueryData<VoiceWorkspaceResponse>(queryKey);
    const latestSection = latestWorkspace?.sections.find((item) => item.section_id === nextSectionId);
    const latestActiveTargetSectionId = latestWorkspace?.active_operation?.target_section_id ?? null;
    setQueuedSectionIds(remaining);
    setQueueReadyToDrain(false);
    if (latestSection?.regeneration_supported && latestSection.can_regenerate && latestSection.section_id !== latestActiveTargetSectionId && latestSection.source_remediation_operation_id && latestSection.remediation_report_sha256) {
      regenerate.mutate(latestSection);
      return;
    }
    if (remaining.length) setQueueReadyToDrain(true);
  }, [operationActive, queryClient, queryKey, queueReadyToDrain, queuedSectionIds, regenerate, workspace.isFetching]);

  const error = [workspace.error, job.error, generate.error, regenerate.error, acceptPacingDeviation.error, cancel.error, approve.error, retryAlignment.error, realignCurrent.error].find(Boolean);
  const readinessIssues = workspace.data?.readiness.issues ?? [];
  const timingLabel = workspace.data?.timing_source ?? (workspace.data?.alignment_status ? `Forced Alignment: ${workspace.data.alignment_status}` : 'Not generated');
  const providerReady = (
    generationProvider === 'elevenlabs'
      ? selectedPresetStatus?.ready === true
      : selectedProviderStatus?.ready === true && Boolean(kokoroVoiceId)
  );
  const canGenerate = enabled && !pending && workspace.data?.readiness?.status === 'ready' && providerReady && workspace.data.available_actions.includes('generate_all');
  const canRealignCurrent = enabled && !pending && Boolean(expectedAudio && expectedTiming) && selectedAlignmentReady === true;
  const reviewAudioId = draftAudio ?? expectedAudio;
  const masterSource = reviewAudioId ? apiClient.getVoiceAudioUrl(projectId, draftAudio ? draftAudio : null) : null;
  const activeFallback = useMemo(() => discoverableJobId ? workspace.data?.active_operation?.status ?? null : null, [discoverableJobId, workspace.data?.active_operation?.status]);
  const hasLiveGeneration = Boolean(launchState || currentJob || discoverableJobId);
  const progressJob = currentJob ?? (!hasLiveGeneration ? lastTerminalJob ?? undefined : undefined);
  const hasApprovedSnapshot = Boolean(expectedAudio && expectedTiming);
  const alignmentUnknown = workspace.data?.alignment_status === 'submit_outcome_unknown';
  const currentOutputProblem = workspace.data?.current_output_problem ?? null;
  const resolvingPacingProblem = workspace.data?.active_operation?.operation_type === 'regenerate_section'
    && ['VOICE_PACING_REGENERATION_REQUIRED', 'VOICE_PACING_REVIEW_REQUIRED'].includes(currentOutputProblem?.error_code ?? '');
  const visibleOutputProblem = resolvingPacingProblem ? null : currentOutputProblem;

  return <section className="space-y-4" data-testid="voice-stage">
    <VoiceSummary workspace={workspace.data} timingLabel={timingLabel} />
    {!enabled ? <p className="rounded border border-amber-400/30 p-3 text-sm text-amber-300">Requires current Script & Analysis.</p> : null}
    {readinessIssues.length ? <div role="status" data-testid="voice-readiness-problem" className="rounded border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200">{readinessIssues.map((item) => <p key={item.error_code}><strong>{item.error_code}</strong><span className="ml-2">{item.message}</span></p>)}</div> : null}
    <div className="grid gap-3 md:grid-cols-2">
      <label className="block text-sm text-slate-300">Voice generation provider<select data-testid="voice-generation-provider-select" disabled={pending} value={generationProvider} onChange={(event) => { providerTouchedRef.current = true; setGenerationProvider(event.target.value as VoiceGenerationProvider); }} className="mt-1 w-full rounded border border-white/10 bg-slate-950 px-3 py-2 text-white disabled:opacity-50"><option value="elevenlabs">ElevenLabs</option><option value="kokoro_local">Kokoro Local</option></select></label>
      <div className="rounded-xl border border-white/10 bg-white/[0.025] p-3 text-xs text-slate-300" data-testid="voice-provider-workflow-readiness"><p className="uppercase tracking-wide text-slate-400">Workflow readiness</p><p className="mt-1">Generation: {generationReadinessLabel}</p><p>Alignment: {alignmentReadinessLabel}</p><p className="mt-1 text-slate-400">ElevenLabs Forced Alignment</p></div>
    </div>
    {selectedProviderIssue ? <p role="status" data-testid="voice-provider-readiness-problem" className="rounded border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200"><strong>{selectedProviderIssue.error_code}</strong><span className="ml-2">{selectedProviderIssue.message}</span></p> : null}
    {generationProvider === 'elevenlabs' ? <ChannelVoiceProfileSetup projectId={projectId} voicePreset={voicePreset} disabled={pending} /> : null}
    {generationProvider === 'kokoro_local' ? <label className="block text-sm text-slate-300">Kokoro voice<select data-testid="kokoro-voice-select" disabled={pending || !kokoroVoiceCatalog.length} value={kokoroVoiceId} onChange={(event) => setKokoroVoiceId(event.target.value)} className="mt-1 w-full rounded border border-white/10 bg-slate-950 px-3 py-2 text-white disabled:opacity-50">{kokoroVoiceCatalog.length ? kokoroVoiceCatalog.map((voice) => <option key={voice.voice_id} value={voice.voice_id}>{voice.display_name} ({voice.voice_id})</option>) : <option value="">No Kokoro voices available</option>}</select></label> : null}
    <div className="grid gap-3 md:grid-cols-2" data-testid="voice-approved-draft-state">
      <div className="rounded-xl border border-white/10 bg-white/[0.025] p-3"><p className="text-xs uppercase tracking-wide text-slate-400">Current approved Voice</p><p className="mt-1 font-mono text-xs text-slate-300">Audio: {expectedAudio ?? 'none'}</p><p className="font-mono text-xs text-slate-300">Word timing: {expectedTiming ?? 'none'}</p></div>
      <div className="rounded-xl border border-cyan-400/20 bg-cyan-400/[0.035] p-3"><p className="text-xs uppercase tracking-wide text-cyan-300">New draft candidate</p><p className="mt-1 font-mono text-xs text-cyan-100">Audio: {draftAudio ?? 'none'}</p><p className="font-mono text-xs text-cyan-100">Word timing: {draftTiming ?? 'none'}</p>{workspace.data?.draft_generation_provider ? <p className="mt-1 text-xs text-slate-400">Generation: {workspace.data.draft_generation_provider}{workspace.data.draft_generation_voice_id ? ` / ${workspace.data.draft_generation_voice_id}` : ''}</p> : null}{workspace.data?.draft_voice_preset ? <p className="mt-1 text-xs text-slate-400">Preset: {workspace.data.draft_voice_preset}</p> : null}<p className="mt-1 text-xs text-slate-400">Alignment: ElevenLabs Forced Alignment</p></div>
    </div>
    {workspace.data?.alignment_total_section_count ? <div className="rounded-xl border border-white/10 bg-black/20 p-3 text-sm text-slate-300" data-testid="voice-alignment-progress"><strong>Forced Alignment</strong><span className="ml-2">{workspace.data.alignment_completed_section_count}/{workspace.data.alignment_total_section_count} sections · {workspace.data.alignment_status ?? 'pending'}</span></div> : null}
    {alignmentUnknown ? <div className="rounded border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200" data-testid="voice-alignment-unknown"><strong>VOICE_ALIGNMENT_SUBMIT_OUTCOME_UNKNOWN</strong><p className="mt-1">The alignment provider may have accepted the billable request. Automatic resubmission is disabled.</p><button data-testid="retry-voice-alignment" disabled={retryAlignment.isPending} onClick={() => retryAlignment.mutate()} className="mt-3 rounded border border-amber-300/40 px-3 py-2 text-xs text-amber-100 disabled:opacity-40">Explicitly retry Forced Alignment</button></div> : null}
    {visibleOutputProblem ? <p role="status" data-testid="voice-current-output-problem" className="rounded border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200"><strong>{visibleOutputProblem.error_code}</strong><span className="ml-2">{visibleOutputProblem.message}</span></p> : null}
    {error ? <p role="alert" data-testid="voice-action-error" className="rounded border border-rose-400/30 p-3 text-sm text-rose-300">{error instanceof Error ? error.message : String(error)}</p> : null}

    {generationProvider === 'elevenlabs' ? <label className="block text-sm text-slate-300">Voice preset<select data-testid="voice-preset-select" disabled={pending} value={voicePreset} onChange={(event) => setVoicePreset(event.target.value as VoicePreset)} className="mt-1 w-full rounded border border-white/10 bg-slate-950 px-3 py-2 text-white disabled:opacity-50"><option value="quality_cloned_voice">Quality cloned voice</option><option value="economy">Economy</option></select></label> : null}

    <div className="flex flex-wrap gap-2">
      <button data-testid="generate-voice" disabled={!canGenerate} onClick={() => generate.mutate('generate_all')} className="rounded bg-cyan-500 px-4 py-2 text-sm font-medium text-slate-950 disabled:opacity-40">{generate.isPending && launchState?.operationType === 'generate_all' ? 'Starting…' : 'Generate Voice'}</button>
      <button data-testid="generate-missing-voice" disabled={!canGenerate} onClick={() => generate.mutate('generate_missing')} className="rounded border border-white/15 px-4 py-2 text-sm text-white disabled:opacity-40">{generate.isPending && launchState?.operationType === 'generate_missing' ? 'Starting…' : 'Generate Missing'}</button>
      <button data-testid="realign-current-voice" disabled={!canRealignCurrent} onClick={() => realignCurrent.mutate()} className="rounded border border-amber-300/30 px-4 py-2 text-sm text-amber-100 disabled:opacity-40">{realignCurrent.isPending ? 'Repairing timing…' : 'Repair Timing (Reuse Audio)'}</button>
    </div>
    {hasApprovedSnapshot ? <p className="text-xs text-slate-400">Repair Timing reuses the approved audio without TTS and preserves Scene Breakdown and Scene Assets when timing revalidation passes.</p> : null}

    {launchState && !currentJob ? <section className="rounded-2xl border border-cyan-400/20 bg-cyan-400/[0.04] p-4" data-testid="voice-generation-launch" aria-live="polite">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-sm font-medium text-cyan-100">{launchState.jobId ? 'New generation queued' : 'Starting new generation…'}</p><p className="mt-1 text-xs text-slate-400">{launchState.operationLabel} · {launchState.providerLabel}</p></div><span className="text-xs uppercase tracking-wide text-cyan-200">{launchState.jobId ? launchState.status : 'submitting'}</span></div>
      <div className="mt-3 h-2 overflow-hidden rounded bg-slate-800"><div className="h-full w-1/3 animate-pulse rounded bg-cyan-400" /></div>
      {launchState.jobId ? <div className="mt-3 flex flex-wrap items-center justify-between gap-3"><p data-testid="voice-generation-job-id" className="text-xs text-slate-400">Job {launchState.jobId}</p><button data-testid="cancel-voice" disabled={cancel.isPending} onClick={() => cancel.mutate()} className="rounded border border-rose-400/40 px-3 py-2 text-xs text-rose-200 disabled:opacity-40">Cancel generation</button></div> : <p className="mt-2 text-xs text-slate-400">Submitting a new durable Voice operation…</p>}
    </section> : <VoiceGenerationProgress job={progressJob} fallbackStatus={activeFallback} onCancel={() => cancel.mutate()} cancelPending={cancel.isPending} />}

    {hasLiveGeneration && hasApprovedSnapshot ? <p data-testid="voice-published-snapshot-note" className="rounded-xl border border-white/10 bg-white/[0.025] px-4 py-3 text-xs text-slate-400">The current approved Voice remains unchanged while synthesis, pacing, and Forced Alignment create a new draft candidate.</p> : null}

    <NarrationMasterPlayer source={masterSource} label="Full narration" />
    <VoiceSectionRemediationList sections={workspace.data?.sections ?? []} operationActive={operationActive} activeOperationId={workspace.data?.active_operation?.operation_id ?? null} activeSectionId={activeSynthesisSectionId} activeTargetSectionId={workspace.data?.active_operation?.target_section_id ?? null} activeTargetAction={currentJob?.action ?? null} acceptingSectionId={acceptingPacingSectionId} queuedSectionIds={queuedSectionIds} getAudioUrl={(sectionId) => apiClient.getVoiceSectionAudioUrl(projectId, sectionId)} onRegenerate={requestSectionRegeneration} onAcceptDeviation={(section) => acceptPacingDeviation.mutate(section)} />

    <VoiceReviewGate approvalEligible={Boolean(workspace.data?.approval_eligible)} pending={pending} expectedAudio={draftAudio} expectedTiming={draftTiming} onApprove={() => approve.mutate()} />
    <span className="sr-only">Voice review separates approved current Voice from the operation-owned draft candidate. Forced Alignment uses final paced section audio and an ambiguous billable submission stays parked until explicit recovery.</span>
  </section>;
}
