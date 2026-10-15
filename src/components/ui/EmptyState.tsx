import type { LucideIcon } from 'lucide-react';
import { ArrowRight } from 'lucide-react';

export function EmptyState({
  icon: Icon,
  title,
  body,
  actionLabel,
  onAction,
}: {
  icon?: LucideIcon;
  title: string;
  body: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className="panel-surface panel-grid flex min-h-[280px] flex-col items-start justify-center gap-4 p-8">
      {Icon ? (
        <div className="rounded-2xl border border-white/10 bg-cockpit-800/70 p-3 text-cyan-200">
          <Icon className="h-6 w-6" />
        </div>
      ) : null}
      <div className="space-y-2">
        <h3 className="text-2xl font-semibold tracking-tight text-slate-100">{title}</h3>
        <p className="max-w-2xl text-sm leading-6 text-slate-400">{body}</p>
      </div>
      {actionLabel && onAction ? (
        <button
          type="button"
          onClick={onAction}
          className="inline-flex items-center gap-2 rounded-2xl border border-cyan-400/30 bg-cyan-500/10 px-4 py-2 text-sm font-medium text-cyan-100 transition hover:bg-cyan-500/20"
        >
          {actionLabel}
          <ArrowRight className="h-4 w-4" />
        </button>
      ) : null}
    </div>
  );
}
