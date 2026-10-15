import type { Dispatch, ReactNode, SetStateAction } from 'react';
import { CheckCircle2, Lightbulb, Sparkles } from 'lucide-react';
import type { ExternalImportValidationResponse, ExternalPromptPackage, TopicIdea, TopicIdeasResponse } from '../../api/client';
import { topicIdeaFocusOptions } from '../../app/workspace';
import { StatusBadge } from '../../components/ui/StatusBadge';
import { cx } from '../../lib/cx';
import { PromptImportWorkspace } from './PromptImportWorkspace';
import type { TopicFormState } from './types';

function FieldLabel({ children }: { children: ReactNode }) {
  return <label className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">{children}</label>;
}

export function TopicIdeasPanel({
  topicForm,
  setTopicForm,
  topicIdeasResult,
  selectedIdea,
  onSelectIdea,
  promptPackage,
  resultJson,
  validation,
  onResultJsonChange,
  onBuildPrompt,
  onImport,
  isBuilding,
  isImporting,
}: {
  topicForm: TopicFormState;
  setTopicForm: Dispatch<SetStateAction<TopicFormState>>;
  topicIdeasResult: TopicIdeasResponse | null;
  selectedIdea: TopicIdea | null;
  onSelectIdea: (idea: TopicIdea) => void;
  promptPackage: ExternalPromptPackage | null;
  resultJson: string;
  validation: ExternalImportValidationResponse | null;
  onResultJsonChange: (value: string) => void;
  onBuildPrompt: () => void;
  onImport: () => void;
  isBuilding: boolean;
  isImporting: boolean;
}) {
  const ideas = topicIdeasResult?.artifact.ideas ?? [];

  return (
    <section className="panel-surface overflow-hidden">
      <header className="flex flex-col gap-3 border-b border-white/10 px-5 py-4 md:flex-row md:items-start md:justify-between">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Authoring Stage</div>
          <h2 className="mt-1 text-lg font-semibold text-slate-100">Topic Ideas</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-400">Build a schema-bound prompt, use ChatGPT externally, then review every imported planning field before selecting one topic.</p>
        </div>
        <div className="rounded-2xl border border-white/10 bg-white/[0.03] px-3 py-2 text-xs uppercase tracking-[0.22em] text-slate-400">
          {ideas.length ? `${ideas.length} ideas ready` : 'Awaiting import'}
        </div>
      </header>

      <div className="grid items-start gap-5 p-5 xl:grid-cols-[380px_minmax(0,1fr)]">
        <div className="space-y-4">
          <section className="space-y-4 rounded-[1.5rem] border border-white/10 bg-white/[0.03] p-4">
            <div className="space-y-1">
              <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Topic Parameters</div>
              <p className="text-sm leading-6 text-slate-400">Configure the inputs used to build the external Topic Ideas prompt.</p>
            </div>
            <label className="grid gap-2 text-sm text-slate-300"><FieldLabel>Seed Topic</FieldLabel><input value={topicForm.seed_topic} onChange={(event) => setTopicForm((current) => ({ ...current, seed_topic: event.target.value }))} placeholder="Ancient human life experiences" className="rounded-2xl border border-white/10 bg-cockpit-950/70 px-3 py-2.5" /></label>
            <div className="grid grid-cols-2 gap-3">
              <label className="grid gap-2 text-sm text-slate-300"><FieldLabel>Count</FieldLabel><input type="number" min={1} max={20} value={topicForm.topic_count} onChange={(event) => setTopicForm((current) => ({ ...current, topic_count: Number(event.target.value) }))} className="rounded-2xl border border-white/10 bg-cockpit-950/70 px-3 py-2.5" /></label>
              <label className="grid gap-2 text-sm text-slate-300"><FieldLabel>Focus</FieldLabel><select value={topicForm.focus} onChange={(event) => setTopicForm((current) => ({ ...current, focus: event.target.value as TopicFormState['focus'] }))} className="rounded-2xl border border-white/10 bg-cockpit-950/70 px-3 py-2.5">{topicIdeaFocusOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
            </div>
            <label className="grid gap-2 text-sm text-slate-300"><FieldLabel>Focus Keywords</FieldLabel><textarea rows={4} value={topicForm.focus_keywords} onChange={(event) => setTopicForm((current) => ({ ...current, focus_keywords: event.target.value }))} placeholder="first discovery, survival, daily life" className="rounded-2xl border border-white/10 bg-cockpit-950/70 px-3 py-2.5" /></label>
          </section>
          <PromptImportWorkspace title="Topic Ideas Prompt" description="Generate candidate topics externally, then import the complete JSON file." promptPackage={promptPackage} resultJson={resultJson} validation={validation} isBuilding={isBuilding} isImporting={isImporting} buildDisabled={!topicForm.seed_topic.trim()} onBuildPrompt={onBuildPrompt} onResultJsonChange={onResultJsonChange} onImport={onImport} />
        </div>

        <section className="flex min-h-0 flex-col gap-4 rounded-[1.5rem] border border-white/10 bg-white/[0.03] p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Generated Ideas</div>
              <p className="mt-1 text-sm leading-6 text-slate-400">Review the question, hook, angle, production difficulty, risks, suggested POV, and Script avoid guidance.</p>
            </div>
            <div className="rounded-2xl border border-white/10 bg-cockpit-950/50 px-3 py-2 text-xs uppercase tracking-[0.22em] text-slate-400">{ideas.length ? `${ideas.length} ideas ready` : 'No ideas yet'}</div>
          </div>

          {selectedIdea ? (
            <div className="rounded-[1.25rem] border border-cyan-400/20 bg-cyan-500/[0.06] p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-cyan-200">Selected For Script</div>
                  <h3 className="mt-2 text-lg font-semibold text-slate-50">{selectedIdea.title}</h3>
                  <p className="mt-2 text-sm leading-6 text-slate-300">{selectedIdea.topic_angle}</p>
                  <p className="mt-2 text-xs text-slate-400">POV: {selectedIdea.pov_style}</p><p className="mt-2 text-xs text-cyan-200">Suggested pacing: <span className="font-semibold uppercase">{selectedIdea.recommended_pacing_mode}</span> · {selectedIdea.pacing_mode_reason}</p>
                </div>
                <StatusBadge status="selected" label="Selected" />
              </div>
            </div>
          ) : null}

          {ideas.length ? (
            <div data-testid="generated-ideas-panel" className="min-h-[36rem] flex-1 space-y-4 overflow-y-auto pr-1">
              {ideas.map((idea) => {
                const selected = idea.idea_id === selectedIdea?.idea_id;
                return (
                  <article key={idea.idea_id} data-testid="topic-idea-card" className={cx('rounded-[1.25rem] border p-4 shadow-soft transition', selected ? 'border-cyan-400/30 bg-cyan-500/[0.08]' : 'border-white/10 bg-cockpit-950/55 hover:bg-cockpit-900/80')}>
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <h3 className="text-xl font-semibold text-slate-50">{idea.title}</h3>
                        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                          <span className="rounded-full border border-white/10 bg-white/[0.03] px-2 py-1 font-mono uppercase tracking-[0.2em]">{idea.idea_id}</span>
                          <span>Tone: <span className="font-medium text-slate-300">{idea.tone}</span></span>
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-2"><StatusBadge status={idea.difficulty} label={idea.difficulty} /><StatusBadge status="ready" label={`CTR ${idea.ctr_score}/10`} /></div>
                    </div>

                    <div className="mt-4 grid gap-4 lg:grid-cols-2">
                      <div><div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Core Question</div><p className="mt-2 text-sm leading-6 text-slate-200">{idea.core_question}</p></div>
                      <div><div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Topic Angle</div><p className="mt-2 text-sm leading-6 text-slate-200">{idea.topic_angle}</p></div>
                    </div>

                    <div className="mt-4 rounded-2xl border border-violet-400/15 bg-violet-500/[0.05] p-4"><div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-violet-300">Viewer Hook</div><p className="mt-2 text-sm italic leading-6 text-slate-100">{idea.viewer_hook}</p></div>

                    <div className="mt-4 grid gap-4 lg:grid-cols-3">
                      <div className="rounded-2xl border border-white/10 bg-cockpit-950/60 p-4"><div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-cyan-300">Suggested POV Style</div><p className="mt-2 text-sm leading-6 text-slate-200">{idea.pov_style}</p></div>
                      <div className="rounded-2xl border border-cyan-400/15 bg-cyan-500/[0.05] p-4"><div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-cyan-300">Suggested Pacing</div><p className="mt-2 text-sm font-semibold uppercase text-cyan-100">{idea.recommended_pacing_mode}</p><p className="mt-2 text-sm leading-6 text-slate-300">{idea.pacing_mode_reason}</p></div>
                      <div className="rounded-2xl border border-white/10 bg-cockpit-950/60 p-4"><div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-amber-300">Script Avoid</div>{idea.avoid.length ? <ul className="mt-2 space-y-1 text-sm leading-6 text-slate-200">{idea.avoid.map((item) => <li key={item}>• {item}</li>)}</ul> : <p className="mt-2 text-sm text-slate-400">No candidate-specific exclusions.</p>}</div>
                    </div>

                    <div className="mt-4 flex flex-wrap items-center gap-2">{idea.content_tags.map((tag) => <span key={tag} className="rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 text-[11px] uppercase tracking-[0.18em] text-slate-400">{tag}</span>)}</div>

                    {idea.risk_notes.length ? <div className="mt-4 rounded-2xl border border-amber-500/20 bg-amber-500/[0.06] p-3"><div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.24em] text-amber-200"><Sparkles className="h-3.5 w-3.5" />Risk Notes</div><ul className="mt-2 space-y-1 text-sm leading-6 text-amber-100/90">{idea.risk_notes.map((note) => <li key={note}>• {note}</li>)}</ul></div> : null}

                    <button type="button" data-testid="select-for-script-button" onClick={() => onSelectIdea(idea)} className={cx('mt-4 inline-flex items-center justify-center gap-2 rounded-2xl border px-4 py-2 text-sm font-medium transition', selected ? 'border-cyan-400/30 bg-cyan-500/15 text-cyan-50' : 'border-white/10 bg-white/[0.03] text-slate-100 hover:bg-white/[0.06]')}>{selected ? <><CheckCircle2 className="h-4 w-4" />Selected For Script</> : <><Lightbulb className="h-4 w-4" />Select for Script</>}</button>
                  </article>
                );
              })}
            </div>
          ) : <div className="rounded-[1.25rem] border border-dashed border-white/10 bg-white/[0.02] p-8 text-sm leading-6 text-slate-400">Import Topic Ideas to review the complete planning data here.</div>}
        </section>
      </div>
    </section>
  );
}
