import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { apiClient, type AssemblyBuildPayload } from '../../api/client';
import { useProgressiveLimit } from './useProgressiveLimit';

export function AssemblyPanel({ projectId, enabled }: { projectId: string; enabled: boolean }) {
  const queryClient = useQueryClient();
  const [musicArtifactId, setMusicArtifactId] = useState('');
  const [musicGain, setMusicGain] = useState(-12);
  const queryKey = ['assembly-workspace', projectId] as const;
  const workspace = useQuery({ queryKey, queryFn: ({ signal }) => apiClient.getAssemblyWorkspace(projectId, signal), enabled });
  const beatWindow = useProgressiveLimit(
    workspace.data?.beats.length ?? 0,
    workspace.data?.current_assembly_plan_artifact_id ?? projectId,
    40,
    40,
  );
  const refresh = async () => { await queryClient.invalidateQueries({ queryKey }); await queryClient.invalidateQueries({ queryKey: ['v3-projects'] }); };
  const build = useMutation({
    mutationFn: () => {
      const data = workspace.data;
      if (!data) throw new Error('Assembly workspace is unavailable.');
      const payload: AssemblyBuildPayload = {
        expected_assembly_plan_artifact_id: data.current_assembly_plan_artifact_id,
        expected_subtitle_srt_artifact_id: data.current_subtitle_srt_artifact_id,
        expected_subtitle_vtt_artifact_id: data.current_subtitle_vtt_artifact_id,
        expected_scene_plan_artifact_id: data.source_identities.scene_plan_artifact_id!,
        expected_scene_asset_manifest_artifact_id: data.source_identities.scene_asset_manifest_artifact_id!,
        expected_narration_audio_artifact_id: data.source_identities.narration_audio_artifact_id!,
        expected_narration_word_timing_map_artifact_id: data.source_identities.narration_word_timing_map_artifact_id!,
        expected_beat_timing_map_artifact_id: data.source_identities.beat_timing_map_artifact_id!,
        background_music: musicArtifactId.trim() ? {
          artifact_id: musicArtifactId.trim(), start_time_units: 0,
          end_time_units: Math.max(1, Math.round((data.total_duration_seconds ?? 1) * 1_000_000)),
          source_start_time_units: 0, gain_db: musicGain, loop_mode: 'loop',
          fade_in_time_units: 0, fade_out_time_units: 0,
        } : null,
        sound_effects: [],
      };
      return apiClient.buildAssembly(projectId, payload);
    }, onSuccess: refresh,
  });
  const approve = useMutation({ mutationFn: () => apiClient.approveAssembly(projectId, workspace.data!.current_assembly_plan_artifact_id!, workspace.data!.current_subtitle_srt_artifact_id!, workspace.data!.current_subtitle_vtt_artifact_id!), onSuccess: refresh });
  const error = workspace.error ?? build.error ?? approve.error;
  const pending = build.isPending || approve.isPending;

  return <section className="space-y-4" data-testid="assembly-stage">
    <header className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
      <p className="text-xs uppercase tracking-[0.22em] text-cyan-300">Phase 13</p>
      <h2 className="mt-1 text-xl font-semibold text-white">Assembly</h2>
      <p className="mt-2 text-sm text-slate-300">Compile the backend-owned visual timeline and exact subtitles from approved Scene Assets and Voice timing. Phase 14 rendering is not started here.</p>
      {!enabled ? <p className="mt-3 text-sm text-amber-300">Requires approved Scene Assets, the approved Voice pair, and BeatTimingMap.</p> : null}
      {error ? <p role="alert" data-testid="assembly-error" className="mt-3 text-sm text-rose-300">{error instanceof Error ? error.message : String(error)}</p> : null}
    </header>

    <div className="flex flex-wrap gap-2">
      <button data-testid="build-assembly" disabled={!enabled || pending || !workspace.data?.build_eligible} onClick={() => build.mutate()} className="rounded bg-cyan-500 px-4 py-2 text-sm font-medium text-slate-950 disabled:opacity-40">{workspace.data?.current_assembly_plan_artifact_id ? 'Rebuild' : 'Build Assembly'}</button>
      <button data-testid="approve-assembly" disabled={pending || !workspace.data?.approval_eligible} onClick={() => approve.mutate()} className="rounded border border-emerald-400/40 px-4 py-2 text-sm text-emerald-200 disabled:opacity-40">Approve Assembly</button>
      {workspace.data?.current_subtitle_srt_artifact_id ? <a data-testid="download-srt" href={apiClient.getAssemblySrtUrl(projectId)} className="rounded border border-white/15 px-4 py-2 text-sm text-white">Download SRT</a> : null}
      {workspace.data?.current_subtitle_vtt_artifact_id ? <a data-testid="download-vtt" href={apiClient.getAssemblyVttUrl(projectId)} className="rounded border border-white/15 px-4 py-2 text-sm text-white">Download VTT</a> : null}
    </div>

    <div className="grid gap-3 md:grid-cols-4">
      <div className="rounded border border-white/10 p-3 text-sm text-slate-300">Duration: {workspace.data?.total_duration_seconds?.toFixed(3) ?? '—'}s</div>
      <div className="rounded border border-white/10 p-3 text-sm text-slate-300">Scenes: {workspace.data?.scene_count ?? 0}</div>
      <div className="rounded border border-white/10 p-3 text-sm text-slate-300">Beats: {workspace.data?.beat_count ?? 0}</div>
      <div className="rounded border border-white/10 p-3 text-sm text-slate-300">Subtitle cues: {workspace.data?.subtitle_cue_count ?? 0}</div>
    </div>

    <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
      <h3 className="font-medium text-white">Optional explicit audio</h3>
      <p className="mt-1 text-xs text-slate-400">Backend validates exact local Artifact identity and resolves timing. No automatic music or SFX selection.</p>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <label className="text-sm text-slate-300">Background music Artifact ID<input data-testid="assembly-music-artifact" value={musicArtifactId} onChange={(event) => setMusicArtifactId(event.target.value)} className="mt-1 w-full rounded border border-white/10 bg-slate-950 px-3 py-2 text-white" /></label>
        <label className="text-sm text-slate-300">Gain dB<input data-testid="assembly-music-gain" type="number" min="-60" max="12" value={musicGain} onChange={(event) => setMusicGain(Number(event.target.value))} className="mt-1 w-full rounded border border-white/10 bg-slate-950 px-3 py-2 text-white" /></label>
      </div>
    </div>

    <div className="rounded-2xl border border-white/10 p-4" data-testid="assembly-qc">
      <h3 className="font-medium text-white">Backend QC</h3>
      <div className="mt-2 grid gap-2 text-xs text-slate-300 md:grid-cols-3">{Object.entries(workspace.data?.backend_qc ?? {}).filter(([key]) => key.endsWith('_status')).map(([key,value]) => <div key={key}>{key}: {String(value)}</div>)}</div>
    </div>

    <div className="space-y-3" data-testid="assembly-timeline-preview">
      {(workspace.data?.beats ?? []).slice(0, beatWindow.visibleCount).map((beat) => <article key={String(beat.beat_id)} className="rounded-xl border border-white/10 p-3 text-sm text-slate-300">
        <div className="flex justify-between"><strong className="text-white">{String(beat.beat_id)}</strong><span>image</span></div>
        <div className="mt-1">{Number(beat.start_seconds).toFixed(3)}s–{Number(beat.end_seconds).toFixed(3)}s · motion {String((beat.visual_instruction as Record<string,unknown>)?.motion_type ?? 'none')} · transition {String((beat.transition_instruction as Record<string,unknown>)?.transition_type ?? 'cut')}</div>
      </article>)}
      {beatWindow.hasMore ? <div className="flex flex-wrap items-center justify-center gap-3 rounded-xl border border-white/10 p-3 text-sm text-slate-300" data-testid="assembly-progressive-window"><span>{beatWindow.hiddenCount} more beats are not mounted yet.</span><button onClick={beatWindow.showMore} className="rounded border border-white/15 px-3 py-1.5 text-white">Show 40 more</button><button onClick={beatWindow.showAll} className="rounded border border-white/10 px-3 py-1.5">Show all</button></div> : null}
    </div>

    <div className="space-y-2" data-testid="assembly-subtitle-preview"><h3 className="font-medium text-white">Subtitle cue preview</h3>{(workspace.data?.subtitle_cues ?? []).slice(0,8).map((cue) => <p key={String(cue.cue_id)} className="rounded border border-white/10 p-2 text-sm text-slate-300">{String(cue.line_1)}{cue.line_2 ? ` / ${String(cue.line_2)}` : ''}</p>)}</div>
  </section>;
}
