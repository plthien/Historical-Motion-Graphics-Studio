import type { VoiceJobStatusResponse, VoiceWorkspaceResponse } from '../../../api/client';

type Section = VoiceWorkspaceResponse['sections'][number];

function pacingRecommendationMessage(reason: string | null | undefined): string | null {
  if (reason === 'provider_pacing_limit_reached') {
    return 'Provider speed limit reached. This section cannot enter the +/-5% raw-WPM QC band using provider speed alone.';
  }
  if (reason === 'provider_pacing_no_progress') {
    return 'Another provider regeneration would use the same pacing rate and is not expected to improve this section.';
  }
  if (reason === 'kokoro_duration_model_invalid') {
    return 'This section cannot produce a valid Kokoro pacing recommendation from its frozen duration model.';
  }
  if (reason) {
    return 'Provider pacing remediation is unavailable for this section.';
  }
  return null;
}

function remediationBlockMessage(reason: string | null | undefined): string | null {
  if (reason === 'source_pacing_contract_stale') {
    return 'This Voice was generated with an older pacing contract. Generate a fresh Voice before remediating individual sections.';
  }
  if (reason === 'source_pacing_plan_invalid') {
    return 'The frozen pacing plan for this Voice is invalid. Generate a fresh Voice before remediating individual sections.';
  }
  if (reason === 'kokoro_duration_model_invalid') {
    return 'This Voice uses an incompatible Kokoro duration model. Generate a fresh Voice before remediating individual sections.';
  }
  return null;
}

export function VoiceSummary({ workspace, timingLabel }: { workspace: VoiceWorkspaceResponse | undefined; timingLabel: string }) {
  const ready = workspace?.sections.filter((item) => item.pacing_status === 'pass').length ?? 0;
  const total = workspace?.sections.length ?? 0;
  return <header className="rounded-2xl border border-white/10 bg-white/[0.03] p-5" data-testid="voice-summary">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><p className="text-xs uppercase tracking-[0.22em] text-cyan-300">Phase 12</p><h2 className="mt-1 text-xl font-semibold text-white">Narration Review</h2><p className="mt-2 max-w-3xl text-sm text-slate-300">Generate narration, review section pacing and timing from one master player, regenerate only sections flagged by pacing QC, then approve the audio/timing pair.</p></div>
      <div className="text-right text-sm text-slate-300"><div>{ready}/{total} sections ready</div><div data-testid="voice-timing-source">{timingLabel}</div></div>
    </div>
  </header>;
}


export function NarrationMasterPlayer({ source, label }: { source: string | null; label: string }) {
  return <section className="rounded-2xl border border-white/10 bg-black/20 p-4" data-testid="narration-master-player">
    <div className="mb-2 flex items-center justify-between gap-3 text-sm"><strong className="text-white">Master player</strong><span className="truncate text-slate-400">{label}</span></div>
    {source ? <audio key={source} data-testid="voice-master-player" controls preload="metadata" src={source} className="w-full" /> : <p className="text-sm text-slate-400">Generate narration to enable playback.</p>}
  </section>;
}

export function VoiceSectionRemediationList({ sections, operationActive, activeOperationId, activeSectionId, activeTargetSectionId, activeTargetAction, acceptingSectionId, queuedSectionIds, getAudioUrl, onRegenerate, onAcceptDeviation }: {
  sections: Section[]; operationActive: boolean; activeOperationId: string | null; activeSectionId: string | null; activeTargetSectionId: string | null; activeTargetAction: string | null; acceptingSectionId: string | null; queuedSectionIds: string[];
  getAudioUrl: (sectionId: string) => string; onRegenerate: (section: Section) => void; onAcceptDeviation: (section: Section) => void;
}) {
  if (!sections.length) return null;
  return <section className="rounded-2xl border border-white/10 bg-black/20 p-4" data-testid="voice-section-remediation">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
      <strong className="text-sm text-white">Sections</strong>
      <span className="text-xs text-slate-400">{sections.filter((item) => item.pacing_status === 'pass').length}/{sections.length} ready · QC ±5% WPM{queuedSectionIds.length ? ` · ${queuedSectionIds.length} queued` : ''}</span>
    </div>
    <div className="space-y-2">
      {sections.map((section) => {
        const deviation = section.raw_wpm != null && section.target_wpm != null && section.target_wpm > 0
          ? ((section.raw_wpm / section.target_wpm) - 1) * 100
          : null;
        const warning = section.pacing_status === 'regeneration_required';
        const terminalReasonMessage = warning
          ? remediationBlockMessage(section.remediation_block_reason) ?? (!section.regeneration_supported ? pacingRecommendationMessage(section.recommendation_reason) : null)
          : null;
        const queueIndex = queuedSectionIds.indexOf(section.section_id);
        const queued = queueIndex >= 0;
        const regenerating = activeSectionId === section.section_id;
        const targetOperation = activeTargetSectionId === section.section_id;
        const showingPriorAttempt = targetOperation
          && section.source_remediation_operation_id !== activeOperationId;
        const targetPhaseLabel = targetOperation && !regenerating
          ? activeTargetAction === 'submission_outcome_unknown' || activeTargetAction === 'alignment_failed'
            ? 'Needs attention'
            : ['forced_alignment', 'alignment_queue', 'alignment_retry_wait'].includes(activeTargetAction ?? '')
              ? 'Aligning…'
              : ['finalize', 'candidate_finalize'].includes(activeTargetAction ?? '')
                ? 'Finalizing…'
                : 'Processing…'
          : null;
        const acceptingDeviation = acceptingSectionId === section.section_id;
        const manualAcceptanceSafe = warning && section.can_accept_pacing_deviation;
        const sectionStatusLabel = targetOperation
          ? 'Regeneration in progress'
          : warning
            ? 'Pacing regeneration required'
            : section.pacing_status === 'pass'
              ? 'Ready'
              : 'Pending';
        const hasAcceptanceLineage = Boolean(section.source_remediation_operation_id && section.remediation_report_sha256 && section.remediation_identity);
        const canRequest = section.can_regenerate || (operationActive && section.regeneration_supported && section.section_id !== activeTargetSectionId);
        const audioRevision = section.remediation_identity
          ?? section.remediation_report_sha256
          ?? section.source_remediation_operation_id
          ?? `${section.current_provider_pacing_value ?? 'published'}:${section.raw_wpm ?? 'pending'}`;
        const baseAudioSource = section.raw_wpm != null ? getAudioUrl(section.section_id) : null;
        const audioSource = baseAudioSource
          ? `${baseAudioSource}${baseAudioSource.includes('?') ? '&' : '?'}revision=${encodeURIComponent(audioRevision)}`
          : null;
        return <div key={section.section_id} data-testid={`voice-section-${section.section_id}`} className={`rounded border p-3 ${warning ? 'border-amber-400/30 bg-amber-400/5' : 'border-white/10 bg-white/[0.025]'}`}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <p className="font-mono text-xs text-cyan-200">Section {section.section_order} · {section.section_id}</p>
              <p className={`mt-1 text-sm font-medium ${warning || targetOperation ? 'text-amber-100' : 'text-emerald-100'}`}>{sectionStatusLabel}</p>
              {showingPriorAttempt ? <p className="mt-1 text-xs text-slate-400" data-testid={`voice-section-prior-attempt-${section.section_id}`}>Previous attempt is shown below while the new regeneration is still being evaluated.</p> : null}
              <div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-400">
                {showingPriorAttempt ? <span className="font-medium text-slate-300">Previous attempt:</span> : null}
                {section.target_wpm != null ? <span>Target: {section.target_wpm} WPM</span> : null}
                {section.raw_wpm != null ? <span>Generated: {section.raw_wpm.toFixed(0)} WPM</span> : null}
                {deviation != null ? <span>WPM deviation: {deviation > 0 ? '+' : ''}{deviation.toFixed(1)}%</span> : null}
                {section.recommended_provider_pacing_value != null ? <span>Recommended provider rate: {section.recommended_provider_pacing_value.toFixed(3)}</span> : null}
              </div>
              {terminalReasonMessage && !showingPriorAttempt ? <p className="mt-2 text-xs text-amber-200" data-testid={`voice-section-terminal-reason-${section.section_id}`}>{terminalReasonMessage}</p> : null}
              {showingPriorAttempt ? <p className="mt-2 text-xs text-cyan-200">The previous attempt failed the ±5% raw-WPM QC fence. Wait for the active regeneration to finish before evaluating the new result.</p> : manualAcceptanceSafe ? <p className="mt-2 text-xs text-violet-200" data-testid={`voice-section-manual-acceptance-${section.section_id}`}>This raw result is outside the ±5% WPM QC fence, but the required deterministic correction remains within the ±15% final-pacing safety fence. You can accept this exact deviation instead of resynthesizing.</p> : warning && section.correction_relative != null && Math.abs(section.correction_relative) > 0.15 + 0.000001 ? <p className="mt-2 text-xs text-rose-200">Manual acceptance is unavailable because this section exceeds the 15% final-pacing safety fence.</p> : null}
              {audioSource ? <audio key={audioSource} data-testid={`voice-section-audio-${section.section_id}`} controls preload="metadata" src={audioSource} className="mt-3 w-full max-w-xl" /> : <p className="mt-3 text-xs text-slate-500">Section audio becomes available after synthesis.</p>}
            </div>
            {warning ? <div className="flex flex-wrap gap-2">
              <button
                data-testid={`regenerate-voice-section-${section.section_id}`}
                disabled={regenerating || targetOperation || queued || acceptingDeviation || !canRequest}
                onClick={() => onRegenerate(section)}
                className="rounded border border-amber-300/40 px-3 py-2 text-xs text-amber-100 disabled:opacity-40"
              >{regenerating ? 'Regenerating…' : targetPhaseLabel ?? (queued ? `Queued #${queueIndex + 1}` : operationActive ? 'Queue Regeneration' : 'Regenerate Section')}</button>
              {manualAcceptanceSafe && !targetOperation ? <button
                data-testid={`accept-voice-pacing-deviation-${section.section_id}`}
                disabled={operationActive || regenerating || queued || acceptingDeviation || !hasAcceptanceLineage}
                onClick={() => onAcceptDeviation(section)}
                title="Accept this exact raw-provider WPM deviation and continue with deterministic final pacing."
                className="rounded border border-violet-300/40 px-3 py-2 text-xs text-violet-100 disabled:opacity-40"
              >{acceptingDeviation ? 'Accepting…' : 'Accept deviation'}</button> : null}
            </div> : null}
          </div>
        </div>;
      })}
    </div>
  </section>;
}

function voiceActionLabel(action: string | null | undefined): string | null {
  if (action === 'submit') return 'Submitting synthesis';
  if (action === 'poll') return 'Waiting for synthesis provider';
  if (action === 'materialize') return 'Materializing audio';
  if (action === 'retry_wait') return 'Waiting to retry synthesis';
  if (action === 'finalize') return 'Finalizing section audio';
  if (action === 'alignment_queue') return 'Preparing Forced Alignment';
  if (action === 'forced_alignment') return 'Forced Alignment';
  if (action === 'alignment_retry_wait') return 'Waiting to retry Forced Alignment';
  if (action === 'candidate_finalize') return 'Finalizing Voice candidate';
  if (action === 'submission_outcome_unknown') return 'Forced Alignment needs attention';
  if (action === 'alignment_failed' || action === 'failed') return 'Voice operation failed';
  if (action === 'cancel') return 'Cancelling';
  return action ?? null;
}

function voiceProviderLabel(provider: string | null | undefined, action: string | null | undefined): string {
  if (provider === 'elevenlabs') return 'ElevenLabs';
  if (provider === 'kokoro_local') return 'Kokoro Local';
  if (provider) return provider;
  if (['finalize', 'candidate_finalize'].includes(action ?? '')) return 'Local processing';
  return 'Provider not assigned';
}

export function VoiceGenerationProgress({ job, fallbackStatus, onCancel, cancelPending }: { job: VoiceJobStatusResponse | undefined; fallbackStatus: string | null; onCancel: () => void; cancelPending: boolean }) {
  if (!job && !fallbackStatus) return null;
  const progress = job?.progress ?? 0;
  const problem = job?.problem ?? (job?.error_code ? {
    error_code: job.error_code,
    message: job.error_message ?? 'Voice operation failed.',
    details: null,
  } : null);
  const phaseLabel = voiceActionLabel(job?.action);
  const title = problem
    ? 'Voice operation needs attention'
    : phaseLabel ?? job?.status ?? fallbackStatus ?? 'Voice processing';
  const providerLabel = job ? voiceProviderLabel(job.provider, job.action) : 'Waiting for job status';
  return <section className="rounded-2xl border border-cyan-400/20 bg-cyan-400/[0.04] p-4" data-testid="voice-generation-progress">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm font-medium text-cyan-100">{title}</p><p className="mt-1 text-xs text-slate-400">{providerLabel}{phaseLabel ? ` · Phase: ${phaseLabel}` : ''}</p></div><span data-testid="voice-progress" className="text-sm text-cyan-200">{progress}%</span></div>
    <div className="mt-3 h-2 overflow-hidden rounded bg-slate-800"><div className="h-full bg-cyan-400" style={{ width: `${Math.max(0, Math.min(100, progress))}%` }} /></div>
    {job && job.max_attempts > 0 ? <p className="mt-2 text-xs text-slate-400">Retry {job.retry_count}/{job.max_attempts}{job.next_retry_at ? ` · next ${job.next_retry_at}` : ''}</p> : null}
    {problem ? <div className="mt-3 rounded border border-rose-400/20 bg-rose-400/5 p-3 text-xs text-rose-200" data-testid="voice-problem"><strong>{problem.error_code}</strong><p className="mt-1">{problem.message}</p></div> : null}
    {job && !job.cancel_requested && ['queued','running','finalizing'].includes(job.status) ? <button data-testid="cancel-voice" disabled={cancelPending} onClick={onCancel} className="mt-3 rounded border border-rose-400/40 px-3 py-2 text-xs text-rose-200 disabled:opacity-40">Cancel generation</button> : null}
    {job?.cancel_requested ? <p className="mt-3 text-xs text-amber-200">Cancellation requested. Durable host will finish cancellation.</p> : null}
  </section>;
}

export function VoiceReviewGate({ approvalEligible, pending, expectedAudio, expectedTiming, onApprove }: { approvalEligible: boolean; pending: boolean; expectedAudio: string | null; expectedTiming: string | null; onApprove: () => void }) {
  const eligible = approvalEligible && Boolean(expectedAudio && expectedTiming) && !pending;
  return <section className="rounded-2xl border border-emerald-400/20 bg-emerald-400/[0.035] p-4" data-testid="voice-review-gate"><div className="flex flex-wrap items-center justify-between gap-3"><div><strong className="text-sm text-emerald-100">Review gate</strong><p className="mt-1 text-xs text-slate-400">Approve only after listening to the master narration and reviewing section pacing and timing issues.</p></div><button data-testid="approve-voice" disabled={!eligible} onClick={onApprove} className="rounded border border-emerald-400/40 px-4 py-2 text-sm text-emerald-200 disabled:opacity-40">Approve Voice</button></div></section>;
}
