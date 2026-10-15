import {
  Activity,
  Boxes,
  FolderKanban,
  KanbanSquare,
  Settings2,
  Waves,
} from 'lucide-react';
import { cx } from '../lib/cx';
import type { AppView } from '../app/workspace';

const icons = {
  settings: Settings2,
  channels: Waves,
  projects: FolderKanban,
  studio: Activity,
  jobs: KanbanSquare,
  artifacts: Boxes,
} satisfies Record<AppView, typeof Activity>;

export function GlobalSidebar({
  activeView,
  onNavigate,
  routes,
  channelName,
  projectName,
  healthLabel,
}: {
  activeView: AppView;
  onNavigate: (view: AppView) => void;
  routes: Array<{ id: AppView; label: string; description: string }>;
  channelName: string;
  projectName: string;
  healthLabel: string;
}) {
  return (
    <aside className="panel-surface panel-grid sticky top-4 flex h-[calc(100vh-2rem)] flex-col overflow-hidden px-4 py-5">
      <div className="border-b border-white/10 pb-4">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-cyan-400/30 bg-cyan-500/15 text-cyan-50 shadow-soft">
            <Activity className="h-5 w-5" />
          </div>
          <div>
            <div className="text-sm font-semibold text-slate-50">YouTube AI Studio</div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Cockpit V3</div>
          </div>
        </div>
      </div>

      <nav className="mt-1 space-y-1">
        {routes.map((route) => {
          const Icon = icons[route.id];
          const active = route.id === activeView;
          return (
            <button
              key={route.id}
              type="button"
              onClick={() => onNavigate(route.id)}
              className={cx(
                'group w-full rounded-2xl border px-3 py-3 text-left transition',
                active
                  ? 'border-cyan-400/30 bg-cyan-500/12 text-slate-50 shadow-soft'
                  : 'border-transparent bg-transparent text-slate-400 hover:border-white/10 hover:bg-white/[0.03] hover:text-slate-100',
              )}
            >
              <div className="flex items-start gap-3">
                <div
                  className={cx(
                    'mt-0.5 rounded-xl border p-2 transition',
                    active
                      ? 'border-cyan-400/30 bg-cyan-500/12 text-cyan-100'
                      : 'border-white/10 bg-cockpit-800/70 text-slate-400 group-hover:text-slate-100',
                  )}
                >
                  <Icon className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                  <div className="text-sm font-medium">{route.label}</div>
                  <div className="mt-1 text-xs leading-5 text-slate-500">{route.description}</div>
                </div>
              </div>
            </button>
          );
        })}
      </nav>

      <div className="mt-auto space-y-3 border-t border-white/10 pt-4">
        <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-3">
          <div className="text-[10px] font-semibold uppercase tracking-[0.24em] text-slate-500">Workspace</div>
          <div className="mt-2 text-sm font-medium text-slate-100">{channelName}</div>
          <div className="mt-1 text-sm text-slate-400">{projectName}</div>
        </div>
        <div className="flex items-center gap-2 rounded-2xl border border-white/10 bg-cockpit-800/70 px-3 py-2 text-xs text-slate-300">
          <span className="status-dot bg-emerald-400" />
          {healthLabel}
        </div>
      </div>
    </aside>
  );
}
