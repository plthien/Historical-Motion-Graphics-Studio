import { AlertTriangle, CheckCircle2, Clock3, Loader2, Lock, Sparkles } from 'lucide-react';
import { cx } from '../../lib/cx';

const badgeStyles: Record<string, string> = {
  approved: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  text_approved: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  completed: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  done: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  passed: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  ready: 'border-sky-500/30 bg-sky-500/10 text-sky-200',
  running: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200',
  provider_running: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200',
  pending: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
  provider_pending: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
  high: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
  medium: 'border-sky-500/30 bg-sky-500/10 text-sky-200',
  low: 'border-white/10 bg-white/[0.03] text-slate-300',
  easy: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  hard: 'border-rose-500/30 bg-rose-500/10 text-rose-200',
  draft_ready: 'border-sky-500/30 bg-sky-500/10 text-sky-200',
  queued: 'border-slate-500/30 bg-slate-500/10 text-slate-200',
  submitted: 'border-slate-500/30 bg-slate-500/10 text-slate-200',
  downloading: 'border-indigo-500/30 bg-indigo-500/10 text-indigo-200',
  selected: 'border-violet-500/30 bg-violet-500/10 text-violet-200',
  locked: 'border-fuchsia-500/30 bg-fuchsia-500/10 text-fuchsia-200',
  timeout: 'border-rose-500/30 bg-rose-500/10 text-rose-200',
  failed: 'border-rose-500/30 bg-rose-500/10 text-rose-200',
  download_failed: 'border-rose-500/30 bg-rose-500/10 text-rose-200',
  invalidated: 'border-rose-500/30 bg-rose-500/10 text-rose-200',
  draft: 'border-white/10 bg-white/[0.03] text-slate-300',
  unavailable: 'border-white/10 bg-white/[0.03] text-slate-300',
};

function iconForStatus(status: string) {
  if (['approved', 'text_approved', 'completed', 'done', 'passed', 'easy'].includes(status)) return CheckCircle2;
  if (['running', 'provider_running'].includes(status)) return Loader2;
  if (['locked'].includes(status)) return Lock;
  if (['failed', 'download_failed', 'timeout', 'invalidated'].includes(status)) return AlertTriangle;
  if (['selected'].includes(status)) return Sparkles;
  return Clock3;
}

function labelForStatus(status: string) {
  return status.replace(/_/g, ' ');
}

export function StatusBadge({
  status,
  label,
  className,
}: {
  status: string;
  label?: string;
  className?: string;
}) {
  const Icon = iconForStatus(status);

  return (
    <span
      className={cx(
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.24em]',
        badgeStyles[status] ?? badgeStyles.draft,
        className,
      )}
    >
      <Icon className={cx('h-3 w-3', ['running', 'provider_running'].includes(status) && 'animate-spin')} />
      {label ?? labelForStatus(status)}
    </span>
  );
}
