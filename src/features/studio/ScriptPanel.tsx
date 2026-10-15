import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react';
import type {
  ExternalImportValidationResponse,
  ExternalPromptPackage,
  ScriptAnalysisArtifact,
  ScriptAnalysisContinuityBase,
  ScriptAnalysisContinuityReviewResponse,
  ScriptAnalysisReadResponse,
  ScriptResponse,
} from '../../api/client';
import { PromptImportWorkspace } from './PromptImportWorkspace';
import type { ScriptFormState, TopicIdeaResolution } from './types';

type ReviewTab = 'script' | 'story' | 'continuity' | 'qc';
type ContinuityCard = ScriptAnalysisContinuityBase & { id: string; label: string; kind: string };

function formatDuration(minutes: number): string {
  const totalSeconds = Math.max(0, Math.round(minutes * 60));
  const wholeMinutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${wholeMinutes}:${seconds.toString().padStart(2, '0')}`;
}

function formatDelta(seconds: number): string {
  if (seconds === 0) return '0:00';
  const sign = seconds > 0 ? '+' : '−';
  const absolute = Math.abs(seconds);
  const minutes = Math.floor(absolute / 60);
  const remainder = absolute % 60;
  return `${sign}${minutes}:${remainder.toString().padStart(2, '0')}`;
}

function statusClass(status: 'passed' | 'warning' | 'failed'): string {
  if (status === 'passed') return 'border-emerald-400/20 bg-emerald-500/[0.06] text-emerald-100';
  if (status === 'warning') return 'border-amber-400/20 bg-amber-500/[0.06] text-amber-100';
  return 'border-rose-400/20 bg-rose-500/[0.06] text-rose-100';
}

function densityClass(density: 'low' | 'medium' | 'high'): string {
  if (density === 'high') return 'border-fuchsia-400/20 bg-fuchsia-500/[0.08] text-fuchsia-100';
  if (density === 'medium') return 'border-cyan-400/20 bg-cyan-500/[0.08] text-cyan-100';
  return 'border-slate-400/20 bg-slate-500/[0.08] text-slate-200';
}

function metric(label: string, value: string, detail?: string) {
  return (
    <div className="rounded-2xl border border-white/10 bg-cockpit-950/50 p-3">
      <div className="text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-500">{label}</div>
      <div className="mt-1 text-lg font-semibold text-slate-50">{value}</div>
      {detail ? <div className="mt-1 text-xs text-slate-500">{detail}</div> : null}
    </div>
  );
}

function continuityCards(analysis: ScriptAnalysisArtifact): Array<{ title: string; items: ContinuityCard[] }> {
  const catalog = analysis.continuity_catalog;
  return [
    {
      title: 'Characters',
      items: catalog.characters.map((item) => ({ ...item, id: item.char_id, kind: item.entity_kind })),
    },
    {
      title: 'Locations',
      items: catalog.locations.map((item) => ({ ...item, id: item.loc_id, kind: item.location_kind })),
    },
    {
      title: 'Recurring Props',
      items: catalog.recurring_props.map((item) => ({ ...item, id: item.prop_id, kind: item.prop_kind })),
    },
    {
      title: 'Recurring Visual Subjects',
      items: catalog.recurring_visual_subjects.map((item) => ({ ...item, id: item.subject_id, kind: item.visual_type })),
    },
  ];
}

export function ScriptPanel({
  topicIdeaResolution,
  scriptForm,
  setScriptForm,
  promptPackage,
  resultJson,
  validation,
  onResultJsonChange,
  onBuildPrompt,
  onImport,
  isBuilding,
  isImporting,
  scriptResult,
  analysisResult,
  continuityReview,
  onToggleContinuity,
  isUpdatingContinuity,
  continuityError,
  hasDownstream,
}: {
  topicIdeaResolution: TopicIdeaResolution;
  scriptForm: ScriptFormState;
  setScriptForm: Dispatch<SetStateAction<ScriptFormState>>;
  promptPackage: ExternalPromptPackage | null;
  resultJson: string;
  validation: ExternalImportValidationResponse | null;
  onResultJsonChange: (value: string) => void;
  onBuildPrompt: () => void;
  onImport: () => void;
  isBuilding: boolean;
  isImporting: boolean;
  scriptResult: ScriptResponse | null;
  analysisResult: ScriptAnalysisReadResponse | null;
  continuityReview: ScriptAnalysisContinuityReviewResponse | null;
  onToggleContinuity: (continuityRef: string, removed: boolean) => void;
  isUpdatingContinuity: boolean;
  continuityError: string | null;
  hasDownstream: boolean;
}) {
  const [tab, setTab] = useState<ReviewTab>('script');
  const [activeSectionId, setActiveSectionId] = useState<string | null>(null);
  const script = scriptResult?.artifact ?? null;
  const analysis = analysisResult?.artifact ?? null;

  useEffect(() => {
    if (!script?.script_sections.length) {
      setActiveSectionId(null);
      return;
    }
    if (!activeSectionId || !script.script_sections.some((section) => section.section_id === activeSectionId)) {
      setActiveSectionId(script.script_sections[0].section_id);
    }
  }, [script, activeSectionId]);

  const sectionGuidance = useMemo(
    () => new Map((analysis?.section_guidance ?? []).map((item) => [item.section_id, item])),
    [analysis],
  );
  const sectionWordCounts = useMemo(
    () => new Map((script?.section_word_counts ?? []).map((item) => [item.section_id, item.actual_word_count])),
    [script],
  );
  const removedRefs = useMemo(() => new Set(continuityReview?.removed_continuity_refs ?? []), [continuityReview]);
  const continuityGroups = useMemo(() => (analysis ? continuityCards(analysis) : []), [analysis]);

  const selectStorySection = (sectionId: string) => {
    setActiveSectionId(sectionId);
  };

  const toggleContinuity = (continuityRef: string, remove: boolean) => {
    if (hasDownstream) {
      const action = remove ? 'Removing' : 'Restoring';
      const confirmed = window.confirm(`${action} this continuity lock will invalidate the current Style Bible and all derived downstream artifacts. Continue?`);
      if (!confirmed) return;
    }
    onToggleContinuity(continuityRef, remove);
  };

  return (
    <section className="panel-surface overflow-hidden">
      <header className="border-b border-white/10 px-5 py-4">
        <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Combined Authoring Stage</div>
        <h2 className="mt-1 text-lg font-semibold text-slate-100">Script & Analysis</h2>
        <p className="mt-1 text-sm leading-6 text-slate-400">One prompt uses the selected narration WPM range, chooses an exact WPM for every section before prose, keeps the hook at high visual density, passes the realized-density gate, freezes the Script, then analyzes only that exact Script. Review runtime, story structure, continuity locks, and deterministic QC before Style Bible.</p>
      </header>

      <div className="grid gap-5 p-5 xl:grid-cols-[380px_minmax(0,1fr)]">
        <div className="space-y-4">
          <section className="space-y-4 rounded-[1.5rem] border border-white/10 bg-white/[0.03] p-4">
            <div className="rounded-2xl border border-cyan-400/20 bg-cyan-500/[0.06] p-4 text-sm text-slate-300">
              {topicIdeaResolution.status === 'valid' ? <><div className="font-semibold text-slate-50">{topicIdeaResolution.idea.title}</div><div className="mt-2">{topicIdeaResolution.idea.topic_angle}</div></> : 'Select a current Topic Idea before building this prompt.'}
            </div>
            <label className="grid gap-2 text-sm text-slate-300">Target duration minutes<input type="number" min={1} value={scriptForm.target_duration_minutes} onChange={(e) => setScriptForm((value) => ({ ...value, target_duration_minutes: Number(e.target.value) }))} className="rounded-2xl border border-white/10 bg-cockpit-950/70 px-3 py-2.5" /></label>
            <label className="grid gap-2 text-sm text-slate-300">POV style<input value={scriptForm.pov_style} onChange={(e) => setScriptForm((value) => ({ ...value, pov_style: e.target.value }))} className="rounded-2xl border border-white/10 bg-cockpit-950/70 px-3 py-2.5" /></label>
            <label className="grid gap-2 text-sm text-slate-300">Avoid<textarea rows={4} value={scriptForm.avoid} onChange={(e) => setScriptForm((value) => ({ ...value, avoid: e.target.value }))} className="rounded-2xl border border-white/10 bg-cockpit-950/70 px-3 py-2.5" /></label>
            <div className="grid gap-2 text-sm text-slate-300"><span>Narration WPM range</span><div className="grid grid-cols-2 gap-2"><label className="grid gap-1 text-xs text-slate-400">Min<input type="number" min={80} max={300} step={1} value={scriptForm.narration_wpm.min || ''} onChange={(e) => setScriptForm((value) => ({ ...value, narration_wpm: { ...value.narration_wpm, min: Number(e.target.value) } }))} className="rounded-2xl border border-white/10 bg-cockpit-950/70 px-3 py-2.5 text-sm text-slate-200" /></label><label className="grid gap-1 text-xs text-slate-400">Max<input type="number" min={80} max={300} step={1} value={scriptForm.narration_wpm.max || ''} onChange={(e) => setScriptForm((value) => ({ ...value, narration_wpm: { ...value.narration_wpm, max: Number(e.target.value) } }))} className="rounded-2xl border border-white/10 bg-cockpit-950/70 px-3 py-2.5 text-sm text-slate-200" /></label></div><span className="text-xs leading-5 text-slate-500">Prefilled from this Project&apos;s Channel DNA snapshot. ChatGPT chooses one exact WPM per section inside this inclusive range. Editing the range affects only this prompt build and does not save back to Channel DNA.</span></div>
            <label className="grid gap-2 text-sm text-slate-300">Video pacing<select value={scriptForm.pacing_mode} onChange={(e) => setScriptForm((value) => ({ ...value, pacing_mode: e.target.value as ScriptFormState['pacing_mode'] }))} className="rounded-2xl border border-white/10 bg-cockpit-950/70 px-3 py-2.5"><option value="fast">FAST{topicIdeaResolution.status === 'valid' && topicIdeaResolution.idea.recommended_pacing_mode === 'fast' ? ' — Recommended' : ''}</option><option value="medium">MEDIUM{topicIdeaResolution.status === 'valid' && topicIdeaResolution.idea.recommended_pacing_mode === 'medium' ? ' — Recommended' : ''}</option><option value="slow">SLOW{topicIdeaResolution.status === 'valid' && topicIdeaResolution.idea.recommended_pacing_mode === 'slow' ? ' — Recommended' : ''}</option></select><span className="text-xs leading-5 text-slate-500">Controls the preferred visual-state refresh feel for this video. Visual pacing does not choose narration speed; section WPM is selected independently inside the WPM range.</span></label>
            <label className="grid gap-2 text-sm text-slate-300">Section planning<select value={scriptForm.section_count_mode} onChange={(e) => setScriptForm((value) => ({ ...value, section_count_mode: e.target.value as ScriptFormState['section_count_mode'] }))} className="rounded-2xl border border-white/10 bg-cockpit-950/70 px-3 py-2.5"><option value="llm_decides">ChatGPT decides section count and roles</option><option value="backend_plan">Use fixed backend section plan</option></select><span className="text-xs leading-5 text-slate-500">Controls section count and roles only. Each section chooses its own WPM inside the selected range; actual TTS timing remains final.</span></label>
          </section>
          <PromptImportWorkspace title="Script & Analysis Prompt" description="ChatGPT may use web search when needed, but the imported JSON contains only the Script and Analysis fields defined by the tool." promptPackage={promptPackage} resultJson={resultJson} validation={validation} isBuilding={isBuilding} isImporting={isImporting} buildDisabled={topicIdeaResolution.status !== 'valid' || !Number.isInteger(scriptForm.narration_wpm.min) || !Number.isInteger(scriptForm.narration_wpm.max) || scriptForm.narration_wpm.min < 80 || scriptForm.narration_wpm.max > 300 || scriptForm.narration_wpm.min >= scriptForm.narration_wpm.max} onBuildPrompt={onBuildPrompt} onResultJsonChange={onResultJsonChange} onImport={onImport} />
        </div>

        <div className="min-w-0 space-y-4">
          {script && analysis ? (
            <>
              <section className="rounded-[1.5rem] border border-white/10 bg-white/[0.03] p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Review Summary</div>
                    <h3 className="mt-2 text-xl font-semibold text-slate-50">{script.title}</h3>
                    <p className="mt-2 max-w-4xl text-sm leading-6 text-slate-300">{script.hook}</p>
                    <div className="mt-3 flex flex-wrap gap-2 text-[10px] text-slate-500">
                      <span className="rounded-full border border-white/10 px-2 py-1">Imported Script · <span className="font-mono text-slate-300">{scriptResult?.artifact_id}</span></span>
                      <span className="rounded-full border border-white/10 px-2 py-1">Imported Analysis · <span className="font-mono text-slate-300">{analysisResult?.artifact_id}</span></span>
                    </div>
                  </div>
                  <div className={`rounded-full border px-3 py-1 text-xs font-semibold ${analysis.backend_qc.warnings.length ? 'border-amber-400/20 bg-amber-500/[0.08] text-amber-100' : 'border-emerald-400/20 bg-emerald-500/[0.08] text-emerald-100'}`}>
                    {analysis.backend_qc.warnings.length ? `${analysis.backend_qc.warnings.length} QC warning${analysis.backend_qc.warnings.length === 1 ? '' : 's'}` : 'QC clean'}
                  </div>
                </div>

                <div className="mt-4 grid gap-3 sm:grid-cols-2 2xl:grid-cols-7">
                  {metric('Target runtime', formatDuration(script.runtime_diagnostics.target_duration_minutes))}
                  {metric('Pacing mode', script.source_user_input.pacing_mode.toUpperCase())}
                  {metric('Derived avg pace', `${script.planned_average_wpm} WPM`)}
                  {metric('Estimated runtime', formatDuration(script.runtime_diagnostics.estimated_duration_minutes))}
                  {metric('Runtime delta', formatDelta(script.runtime_diagnostics.runtime_delta_seconds), 'Diagnostic only')}
                  {metric('Words', script.word_count.toLocaleString())}
                  {metric('Sections', script.script_sections.length.toString())}
                </div>
                <div className="mt-3 grid gap-2 sm:grid-cols-3 xl:grid-cols-6">
                  {metric('Markers', analysis.narrative_markers.length.toString())}
                  {metric('Characters', analysis.continuity_catalog.characters.length.toString())}
                  {metric('Locations', analysis.continuity_catalog.locations.length.toString())}
                  {metric('Props', analysis.continuity_catalog.recurring_props.length.toString())}
                  {metric('Subjects', analysis.continuity_catalog.recurring_visual_subjects.length.toString())}
                  {metric('Removed locks', (continuityReview?.removed_continuity_refs.length ?? 0).toString())}
                </div>
              </section>

              <section className="overflow-hidden rounded-[1.5rem] border border-white/10 bg-white/[0.03]">
                <nav className="flex flex-wrap gap-2 border-b border-white/10 p-3">
                  {([
                    ['script', 'Script'],
                    ['story', 'Story Analysis'],
                    ['continuity', 'Continuity'],
                    ['qc', 'QC'],
                  ] as Array<[ReviewTab, string]>).map(([value, label]) => (
                    <button key={value} type="button" onClick={() => setTab(value)} className={`rounded-xl px-3 py-2 text-sm font-medium transition ${tab === value ? 'bg-cyan-500/15 text-cyan-100 ring-1 ring-cyan-400/20' : 'text-slate-400 hover:bg-white/[0.04] hover:text-slate-200'}`}>{label}</button>
                  ))}
                </nav>

                {tab === 'script' ? (
                  <div className="space-y-3 p-4">
                    {script.script_sections.map((section) => {
                      const guidance = sectionGuidance.get(section.section_id);
                      const selected = section.section_id === activeSectionId;
                      return (
                        <article key={section.section_id} onClick={() => setActiveSectionId(section.section_id)} className={`cursor-pointer rounded-2xl border p-4 transition ${selected ? 'border-cyan-400/30 bg-cyan-500/[0.06]' : 'border-white/10 bg-cockpit-950/50 hover:border-white/20'}`}>
                          <div className="flex flex-wrap items-center gap-2 text-xs">
                            <span className="font-mono text-cyan-200">{section.section_id}</span>
                            <span className="text-slate-500">·</span>
                            <span className="uppercase tracking-[0.12em] text-slate-400">{section.section_role}</span>
                            {guidance ? <span className={`rounded-full border px-2 py-0.5 font-semibold uppercase ${densityClass(guidance.pacing_density)}`}>{guidance.pacing_density}</span> : null}
                            <span className="ml-auto text-slate-500">{section.planned_narration_wpm} WPM · {(sectionWordCounts.get(section.section_id) ?? 0).toLocaleString()} words</span>
                          </div>
                          <h4 className="mt-2 font-semibold text-slate-100">{section.section_title}</h4>
                          <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-300">{section.narration}</p>
                        </article>
                      );
                    })}
                  </div>
                ) : null}

                {tab === 'story' ? (
                  <div className="grid gap-4 p-4 2xl:grid-cols-[250px_minmax(0,1fr)]">
                    <aside className="space-y-2">
                      <div className="mb-3 text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">Narrative Arc</div>
                      {script.script_sections.map((section) => {
                        const guidance = sectionGuidance.get(section.section_id);
                        const selected = section.section_id === activeSectionId;
                        return (
                          <button key={section.section_id} type="button" onClick={() => selectStorySection(section.section_id)} className={`w-full rounded-xl border p-3 text-left transition ${selected ? 'border-cyan-400/30 bg-cyan-500/[0.08]' : 'border-white/10 bg-cockpit-950/50 hover:border-white/20'}`}>
                            <div className="flex items-center gap-2"><span className="font-mono text-xs text-cyan-200">{section.section_id}</span>{guidance ? <span className={`ml-auto rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase ${densityClass(guidance.pacing_density)}`}>{guidance.pacing_density}</span> : null}</div>
                            <div className="mt-1 text-sm font-medium text-slate-200">{section.section_role}</div><div className="mt-1 text-xs text-slate-500">{section.planned_narration_wpm} WPM planned narration</div>
                            <div className="mt-1 truncate text-xs text-slate-500">{section.section_title}</div>
                          </button>
                        );
                      })}
                    </aside>
                    <div className="space-y-4">
                      {(() => {
                        const section = script.script_sections.find((item) => item.section_id === activeSectionId) ?? script.script_sections[0];
                        const guidance = sectionGuidance.get(section.section_id);
                        const markers = analysis.narrative_markers.filter((marker) => marker.section_id === section.section_id);
                        return (
                          <>
                            <section className="rounded-2xl border border-white/10 bg-cockpit-950/50 p-4">
                              <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-xs text-cyan-200">{section.section_id}</span><span className="text-xs uppercase tracking-[0.12em] text-slate-400">{section.section_role}</span>{guidance ? <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase ${densityClass(guidance.pacing_density)}`}>{guidance.pacing_density} semantic density</span> : null}</div>
                              <h4 className="mt-2 text-lg font-semibold text-slate-100">{section.section_title}</h4><div className="mt-2 text-xs text-slate-500">Planned narration: {section.planned_narration_wpm} WPM</div>
                              <button type="button" onClick={() => setTab('script')} className="mt-3 text-xs font-medium text-cyan-200 hover:text-cyan-100">View this section in Script →</button>
                            </section>
                            <section>
                              <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">Narrative Markers</div>
                              <div className="mt-3 space-y-3">
                                {markers.length ? markers.map((marker) => (
                                  <article key={marker.marker_id} className="rounded-2xl border border-white/10 bg-cockpit-950/50 p-4">
                                    <div className="flex flex-wrap items-center gap-2"><span className="rounded-full border border-cyan-400/20 bg-cyan-500/[0.08] px-2 py-0.5 text-[10px] font-semibold uppercase text-cyan-100">{marker.marker_type.replace('_', ' ')}</span><span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">{marker.importance} importance</span></div>
                                    <blockquote className="mt-3 border-l-2 border-cyan-400/30 pl-3 text-sm leading-6 text-slate-200">{marker.anchor_quote}</blockquote>
                                    <p className="mt-3 text-sm leading-6 text-slate-400">{marker.purpose}</p>
                                  </article>
                                )) : <div className="rounded-2xl border border-dashed border-white/10 p-6 text-center text-sm text-slate-500">No narrative markers in this section.</div>}
                              </div>
                            </section>
                            {analysis.continuity_catalog.visual_motifs.length ? <section><div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">Visual Motifs</div><div className="mt-3 grid gap-3 md:grid-cols-2">{analysis.continuity_catalog.visual_motifs.map((motif) => <article key={motif.motif_id} className="rounded-2xl border border-white/10 bg-cockpit-950/50 p-3"><div className="font-mono text-xs text-cyan-200">{motif.motif_id}</div><div className="mt-1 text-sm font-semibold text-slate-200">{motif.label}</div><div className="mt-2 text-xs leading-5 text-slate-400">{motif.description}</div><div className="mt-2 text-xs text-slate-500">{motif.occurrences.length} occurrences</div></article>)}</div></section> : null}
                          </>
                        );
                      })()}
                    </div>
                  </div>
                ) : null}

                {tab === 'continuity' ? (
                  <div className="space-y-5 p-4">
                    <div className="rounded-2xl border border-cyan-400/15 bg-cyan-500/[0.05] p-3 text-sm leading-6 text-slate-300">Remove only means <strong className="text-slate-100">do not lock this identity/style downstream</strong>. Script and canonical imported Analysis remain unchanged. Removed items are excluded when Style Bible is rebuilt.</div>
                    {continuityError ? <div className="rounded-2xl border border-rose-400/20 bg-rose-500/[0.06] p-3 text-sm text-rose-100">{continuityError}</div> : null}
                    {continuityGroups.map((group) => (
                      <section key={group.title}>
                        <div className="mb-3 flex items-center gap-2"><h4 className="text-sm font-semibold text-slate-200">{group.title}</h4><span className="rounded-full bg-white/[0.05] px-2 py-0.5 text-xs text-slate-500">{group.items.length}</span></div>
                        {group.items.length ? <div className="grid gap-3 xl:grid-cols-2">{group.items.map((item) => {
                          const removed = removedRefs.has(item.id);
                          return <article key={item.id} className={`rounded-2xl border p-4 ${removed ? 'border-white/10 bg-white/[0.015] opacity-70' : 'border-white/10 bg-cockpit-950/50'}`}>
                            <div className="flex items-start gap-3">
                              <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><span className="font-mono text-xs text-cyan-200">{item.id}</span><span className="rounded-full border border-white/10 px-2 py-0.5 text-[10px] uppercase text-slate-500">{item.kind.replace(/_/g, ' ')}</span>{removed ? <span className="rounded-full border border-amber-400/20 bg-amber-500/[0.08] px-2 py-0.5 text-[10px] font-semibold uppercase text-amber-100">Removed</span> : null}</div><h5 className="mt-2 font-semibold text-slate-100">{item.label}</h5><p className="mt-2 text-sm leading-6 text-slate-400">{item.description}</p><div className="mt-3 text-xs text-slate-500">{item.presence_ranges.length} presence range{item.presence_ranges.length === 1 ? '' : 's'} · {item.occurrences.length} occurrence{item.occurrences.length === 1 ? '' : 's'}</div></div>
                              <button type="button" disabled={isUpdatingContinuity || !continuityReview} onClick={() => toggleContinuity(item.id, !removed)} className={`shrink-0 rounded-xl border px-3 py-2 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-40 ${removed ? 'border-cyan-400/20 bg-cyan-500/[0.08] text-cyan-100 hover:bg-cyan-500/[0.14]' : 'border-rose-400/20 bg-rose-500/[0.06] text-rose-100 hover:bg-rose-500/[0.12]'}`}>{removed ? 'Restore' : 'Remove'}</button>
                            </div>
                          </article>;
                        })}</div> : <div className="rounded-2xl border border-dashed border-white/10 p-5 text-center text-sm text-slate-500">No items.</div>}
                      </section>
                    ))}
                  </div>
                ) : null}

                {tab === 'qc' ? (
                  <div className="space-y-4 p-4">
                    <div className="grid gap-3 md:grid-cols-2">
                      {[
                        ['Section coverage', analysis.backend_qc.section_coverage_status],
                        ['Quote resolution', analysis.backend_qc.quote_resolution_status],
                        ['Entity deduplication', analysis.backend_qc.entity_deduplication_status],
                        ['Marker validation', analysis.backend_qc.marker_validation_status],
                      ].map(([label, status]) => <div key={label} className={`rounded-2xl border p-4 ${statusClass(status as 'passed' | 'warning' | 'failed')}`}><div className="text-sm font-semibold">{label}</div><div className="mt-1 text-xs font-semibold uppercase tracking-[0.14em] opacity-80">{status}</div></div>)}
                    </div>
                    <section>
                      <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">Warnings</div>
                      <div className="mt-3 space-y-2">{analysis.backend_qc.warnings.length ? analysis.backend_qc.warnings.map((warning, index) => <div key={`${index}:${warning}`} className="rounded-xl border border-amber-400/20 bg-amber-500/[0.05] p-3 text-sm leading-6 text-amber-100">{warning}</div>) : <div className="rounded-xl border border-emerald-400/20 bg-emerald-500/[0.05] p-3 text-sm text-emerald-100">No backend QC warnings.</div>}</div>
                    </section>
                  </div>
                ) : null}
              </section>
            </>
          ) : null}

          {!script && !analysis ? <div className="rounded-[1.5rem] border border-dashed border-white/10 p-12 text-center text-sm text-slate-400">No combined Script & Analysis import yet.</div> : null}
          {(script && !analysis) || (!script && analysis) ? <div className="rounded-[1.5rem] border border-rose-400/20 bg-rose-500/[0.06] p-5 text-sm text-rose-100">Script & Analysis must be reviewed as the same atomic import pair.</div> : null}
        </div>
      </div>
    </section>
  );
}
