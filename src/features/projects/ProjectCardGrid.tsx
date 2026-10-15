import { Clapperboard, FolderKanban } from 'lucide-react';
import type { Project } from '../../api/client';
import { CardGrid } from '../../components/ui/CardGrid';
import { StatusBadge } from '../../components/ui/StatusBadge';
import { cx } from '../../lib/cx';
import { projectCardState } from './projectProgress';

function formatTimestamp(value: string) {
  return new Date(value).toLocaleString();
}

export function ProjectCardGrid({
  projects,
  selectedProjectId,
  onOpenProject,
}: {
  projects: Project[];
  selectedProjectId: string;
  onOpenProject: (projectId: string) => void;
}) {
  return (
    <div data-testid="project-card-grid" className="space-y-4">
      {projects.length === 0 ? (
        <div className="rounded-[1.5rem] border border-dashed border-white/10 bg-white/[0.02] p-8 text-sm leading-6 text-slate-400">
          <div className="text-base font-semibold text-slate-100">No projects yet</div>
          <div className="mt-2">Create your first project to open the Topic Ideas and Script pipeline in Studio.</div>
        </div>
      ) : (
        <CardGrid>
          {projects.map((project) => {
            const isSelected = project.project_id === selectedProjectId;
            const state = projectCardState(project);

            return (
              <article
                key={project.project_id}
                data-testid={isSelected ? 'project-card-selected' : 'project-card'}
                className={cx(
                  'group overflow-hidden rounded-[1.5rem] border shadow-soft transition',
                  isSelected
                    ? 'border-cyan-400/40 bg-cyan-500/[0.08] shadow-[0_0_0_1px_rgba(56,189,248,0.12),0_16px_38px_rgba(8,145,178,0.15)]'
                    : 'border-white/10 bg-white/[0.03] hover:border-cyan-400/20 hover:bg-white/[0.05] hover:shadow-[0_14px_40px_rgba(15,23,42,0.45)]',
                )}
              >
                <button type="button" onClick={() => onOpenProject(project.project_id)} className="block w-full text-left">
                  <div className="relative h-40 overflow-hidden border-b border-white/10 bg-[radial-gradient(circle_at_top_left,rgba(34,211,238,0.22),transparent_40%),linear-gradient(145deg,rgba(15,23,42,0.96),rgba(30,41,59,0.82))]">
                    <div className="absolute inset-0 bg-[linear-gradient(140deg,transparent,rgba(14,165,233,0.12),transparent)]" />
                    <div className="absolute left-4 top-4 rounded-full border border-white/10 bg-black/20 px-3 py-1 font-mono text-[11px] uppercase tracking-[0.22em] text-slate-300">
                      {project.project_id}
                    </div>
                    <div className="absolute right-4 top-4">
                      <StatusBadge status={state.status} label={state.statusLabel} />
                    </div>
                    <div className="absolute bottom-4 left-4 flex items-center gap-3">
                      <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-white/10 bg-slate-950/55 text-cyan-200">
                        <Clapperboard className="h-6 w-6" />
                      </div>
                      <div>
                        <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-400">Pipeline Stage</div>
                        <div className="mt-1 text-sm font-medium text-slate-100">{state.currentStageLabel}</div>
                      </div>
                    </div>
                  </div>

                  <div className="space-y-4 p-4">
                    <div className="space-y-2">
                      <div className="line-clamp-2 text-lg font-semibold leading-6 text-slate-50">{project.name}</div>
                      <div className="text-sm text-slate-400">{project.channel_context.name}</div>
                    </div>

                    <div className="space-y-3">
                      <div className="flex items-center justify-between gap-3">
                        <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Pipeline Progress</div>
                        <div className="text-xs text-slate-400">{state.completedCount}/{state.segments.length}</div>
                      </div>

                      <div data-testid="project-progress" className="grid grid-cols-8 gap-1.5">
                        {state.segments.map((segment) => (
                          <div
                            key={segment.label}
                            title={segment.label}
                            className={cx(
                              'h-2 rounded-full',
                              segment.state === 'complete'
                                ? 'bg-emerald-400'
                                : segment.state === 'current'
                                  ? 'bg-amber-300'
                                  : 'bg-slate-800',
                            )}
                          />
                        ))}
                      </div>

                      <div className="text-xs leading-5 text-slate-400">{state.progressLabel}</div>
                    </div>

                    <div className="flex items-end justify-between gap-4 border-t border-white/10 pt-4">
                      <div className="space-y-1 text-xs text-slate-500">
                        <div>Updated: {formatTimestamp(project.updated_at)}</div>
                        <div>Created: {formatTimestamp(project.created_at)}</div>
                      </div>
                      <div
                        data-testid="open-studio-project"
                        className="inline-flex items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.03] px-3 py-2 text-sm font-medium text-slate-100 transition group-hover:border-cyan-400/20 group-hover:text-cyan-100"
                      >
                        <FolderKanban className="h-4 w-4" />
                        Open Pipeline -&gt;
                      </div>
                    </div>
                  </div>
                </button>
              </article>
            );
          })}
        </CardGrid>
      )}
    </div>
  );
}
