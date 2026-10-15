import type { ReactNode } from 'react';
import { X } from 'lucide-react';

export function DetailPanel({
  title,
  subtitle,
  children,
  onClose,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  onClose?: () => void;
}) {
  return (
    <aside className="panel-surface sticky top-6 flex h-fit flex-col overflow-hidden">
      <div className="flex items-start justify-between gap-4 border-b border-white/10 px-5 py-4">
        <div className="space-y-1">
          <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Detail</div>
          <h3 className="text-lg font-semibold text-slate-100">{title}</h3>
          {subtitle ? <p className="text-sm text-slate-400">{subtitle}</p> : null}
        </div>
        {onClose ? (
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-white/10 bg-white/[0.03] p-2 text-slate-400 transition hover:text-slate-100"
          >
            <X className="h-4 w-4" />
          </button>
        ) : null}
      </div>
      <div className="space-y-5 p-5">{children}</div>
    </aside>
  );
}
