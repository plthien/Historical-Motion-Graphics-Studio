import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { apiClient, type RenderJobRequest } from '../../api/client';

export function RenderPanel({ projectId, assemblyId, srtId, vttId, finalVideoId }: { projectId: string; assemblyId: string | null; srtId: string | null; vttId: string | null; finalVideoId: string | null }) {
  const client = useQueryClient();
  const lineage = `${projectId}:${assemblyId ?? ''}:${srtId ?? ''}:${vttId ?? ''}:${finalVideoId ?? ''}`;
  const lineageRef = useRef(lineage); lineageRef.current = lineage;
  const lastRenderJobIdRef = useRef<string | null>(null);
  const workspaceKey = ['render-workspace', lineage] as const;
  const settings = useQuery({ queryKey: ['render-settings'], queryFn: () => apiClient.getRenderSettings() });
  const workspace = useQuery({
    queryKey: workspaceKey,
    queryFn: ({ signal }) => apiClient.getRenderWorkspace(projectId, signal),
    enabled: Boolean(assemblyId && srtId && vttId),
    refetchInterval: (query) => query.state.data?.active_operation ? 750 : false,
  });
  const [profileId, setProfileId] = useState('');
  useEffect(() => { if (!profileId && settings.data?.settings.active_profile_id) setProfileId(settings.data.settings.active_profile_id); }, [profileId, settings.data]);
  const activeJobId = workspace.data?.active_operation?.job_id ?? null;
  useEffect(() => { if (activeJobId) lastRenderJobIdRef.current = activeJobId; }, [activeJobId]);
  const job = useQuery({ queryKey: ['render-job', lineage, activeJobId], queryFn: ({ signal }) => apiClient.getRenderJob(projectId, activeJobId!, signal), enabled: Boolean(activeJobId), refetchInterval: (q) => ['queued','running','rendering','probing','validating','finalizing'].includes(q.state.data?.status ?? '') ? 750 : false });
  useEffect(() => { if (job.data?.job_id) lastRenderJobIdRef.current = job.data.job_id; }, [job.data?.job_id]);
  const refresh = async () => { await client.invalidateQueries({ queryKey: ['render-workspace'] }); await client.invalidateQueries({ queryKey: ['v3-projects'] }); };
  useEffect(() => { if (job.data && !['queued','running','rendering','probing','validating','finalizing'].includes(job.data.status)) void refresh(); }, [job.data?.status]);
  const submit = useMutation({ mutationFn: async () => {
    if (!assemblyId || !srtId || !vttId) throw new Error('Approved Assembly and current subtitle pair are required.');
    const captured = lineageRef.current;
    const payload: RenderJobRequest = { render_profile_id: profileId, expected_assembly_plan_artifact_id: assemblyId, expected_subtitle_srt_artifact_id: srtId, expected_subtitle_vtt_artifact_id: vttId, expected_final_video_artifact_id: finalVideoId, operation_type: finalVideoId ? 'rerender' : 'render' };
    const result = await apiClient.submitRender(projectId, payload);
    lastRenderJobIdRef.current = result.job_id;
    if (captured !== lineageRef.current) throw new Error('Render submission became stale after project lineage changed.');
    return result;
  }, onSuccess: refresh });
  const cancel = useMutation({ mutationFn: () => apiClient.cancelRenderJob(projectId, activeJobId!), onSuccess: refresh });
  const retry = useMutation({ mutationFn: async () => {
    const jobId = job.data?.job_id ?? lastRenderJobIdRef.current ?? 'latest';
    const result = await apiClient.retryRenderJob(projectId, jobId);
    lastRenderJobIdRef.current = result.job_id;
    return result;
  }, onSuccess: refresh });
  const active = workspace.data?.active_operation;
  const progress = job.data?.progress ?? active?.progress ?? 0;
  const error = workspace.error ?? settings.error ?? job.error ?? submit.error ?? cancel.error ?? retry.error;
  const profiles = settings.data?.settings.profiles.filter((item) => item.enabled) ?? [];
  const ready = workspace.data?.readiness.status === 'ready';
  const actions = new Set(workspace.data?.available_actions ?? []);
  const metadata = workspace.data?.final_video;
  const workspaceErrors = workspace.data?.errors ?? [];
  const videoUrl = workspace.data?.video_url ? apiClient.getRenderVideoUrl(projectId) : null;
  const vttUrl = workspace.data?.vtt_url ? apiClient.getAssemblyVttUrl(projectId) : null;
  const statusLabel = useMemo(() => job.data?.status ?? active?.status ?? (actions.has('retry') ? 'failed' : metadata ? 'complete' : ready ? 'ready' : 'locked'), [job.data?.status, active?.status, actions, metadata, ready]);

  return <section className="space-y-4" data-testid="render-stage">
    <header className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
      <p className="text-xs uppercase tracking-[0.22em] text-cyan-300">Phase 14</p><h2 className="mt-1 text-xl font-semibold text-white">Render</h2>
      <p className="mt-2 text-sm text-slate-300">Render the exact approved Assembly with the dedicated durable local FFmpeg host, strict probe/decode validation, and atomic final-video publication.</p>
      <p data-testid="render-status" className="mt-3 text-sm text-cyan-200">Status: {statusLabel}</p>
      {error ? <p role="alert" className="mt-2 text-sm text-rose-300">{error instanceof Error ? error.message : String(error)}</p> : null}
    </header>
    <div className="grid gap-3 md:grid-cols-[1fr_auto]">
      <label className="text-sm text-slate-300">Render profile<select data-testid="render-profile" value={profileId} onChange={(e) => setProfileId(e.target.value)} className="mt-1 w-full rounded border border-white/10 bg-slate-950 px-3 py-2 text-white">{profiles.map((p) => <option key={p.profile_id} value={p.profile_id}>{p.display_name} · {p.width}×{p.height} · {p.frame_rate.numerator/p.frame_rate.denominator} fps</option>)}</select></label>
      <button data-testid="render-video" disabled={!ready || Boolean(active) || !profileId || submit.isPending} onClick={() => submit.mutate()} className="self-end rounded bg-cyan-500 px-4 py-2 text-sm font-medium text-slate-950 disabled:opacity-40">{finalVideoId ? 'Rerender' : 'Render Video'}</button>
    </div>
    {workspace.data?.readiness.issues.length ? <div data-testid="render-readiness-issues" className="rounded border border-amber-400/30 p-3 text-sm text-amber-200">{workspace.data.readiness.issues.map((i) => <p key={`${i.error_code}:${i.capability ?? ''}`}>{i.error_code}: {i.message}</p>)}</div> : null}
    {workspaceErrors.length ? <div data-testid="render-workspace-errors" className="rounded border border-rose-400/30 p-3 text-sm text-rose-200">{workspaceErrors.map((item, index) => <p key={`${String(item.error_code ?? 'RENDER_ERROR')}:${index}`}>{String(item.error_code ?? 'RENDER_ERROR')}: {String(item.message ?? 'Render failed.')}</p>)}</div> : null}
    {active ? <div className="rounded-2xl border border-white/10 p-4"><div className="flex justify-between text-sm text-slate-300"><span>{job.data?.status ?? active.status}</span><span>{progress}%</span></div><div className="mt-2 h-2 overflow-hidden rounded bg-slate-800"><div data-testid="render-progress" className="h-full bg-cyan-400" style={{ width: `${progress}%` }} /></div><button data-testid="cancel-render" disabled={!actions.has('cancel') || cancel.isPending} onClick={() => cancel.mutate()} className="mt-3 rounded border border-rose-400/40 px-3 py-2 text-sm text-rose-200 disabled:opacity-40">Cancel</button></div> : null}
    {!active && actions.has('retry') ? <button data-testid="retry-render" disabled={retry.isPending} onClick={() => retry.mutate()} className="rounded border border-amber-400/40 px-4 py-2 text-sm text-amber-200">Retry</button> : null}
    {videoUrl && metadata ? <div className="space-y-3 rounded-2xl border border-white/10 p-4" data-testid="render-video-player"><video controls preload="metadata" className="w-full rounded bg-black" src={videoUrl}>{vttUrl ? <track kind="subtitles" srcLang="en" src={vttUrl} default /> : null}</video><div className="grid gap-2 text-xs text-slate-300 md:grid-cols-3"><span>{metadata.width}×{metadata.height}</span><span>{metadata.video_codec} / {metadata.pixel_format}</span><span>{metadata.frame_rate.numerator}/{metadata.frame_rate.denominator} fps</span><span>{metadata.audio_codec} · {metadata.audio_sample_rate_hz} Hz · {metadata.audio_channels}ch</span><span>{metadata.duration_seconds.toFixed(3)}s</span><span>{metadata.size_bytes.toLocaleString()} bytes</span><span>Faststart: {String(metadata.faststart)}</span><span>Decode: {String(metadata.decode_valid)}</span><span>Subtitles: {metadata.subtitle_mode}</span></div><p className="break-all text-xs text-slate-500">SHA-256 {metadata.sha256}</p></div> : null}
    <span className="sr-only">No Export Package controls. No thumbnail controls.</span>
  </section>;
}
