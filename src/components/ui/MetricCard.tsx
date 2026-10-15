import type { LucideIcon } from 'lucide-react';
import { cx } from '../../lib/cx';

export function MetricCard({
  icon: Icon,
  label,
  value,
  tone = 'default',
}: {
  icon?: LucideIcon;
  label: string;
  value: string | number;
  tone?: 'default' | 'good' | 'warn' | 'info';
}) {
  const toneClass =
    tone === 'good'
      ? 'text-emerald-200'
      : tone === 'warn'
        ? 'text-amber-200'
        : tone === 'info'
          ? 'text-cyan-200'
          : 'text-slate-100';

  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 shadow-soft">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-[0.24em] text-slate-400">{label}</div>
          <div className={cx('mt-2 text-2xl font-semibold tracking-tight', toneClass)}>{value}</div>
        </div>
        {Icon ? (
          <div className="rounded-2xl border border-white/10 bg-cockpit-800/90 p-2 text-slate-300">
            <Icon className="h-4 w-4" />
          </div>
        ) : null}
      </div>
    </div>
  );
}
