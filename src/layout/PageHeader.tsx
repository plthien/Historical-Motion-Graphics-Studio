import type { ReactNode } from 'react';

export function PageHeader({
  eyebrow,
  title,
  description,
  context,
  actions,
}: {
  eyebrow: string;
  title: string;
  description: string;
  context?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="panel-surface panel-grid overflow-hidden px-5 py-5 md:px-6">
      <div className="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
        <div className="space-y-3">
          <div className="text-[11px] font-semibold uppercase tracking-[0.26em] text-cyan-300">{eyebrow}</div>
          <div>
            <h1 className="text-3xl font-semibold tracking-tight text-slate-50 md:text-4xl">{title}</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400 md:text-base">{description}</p>
          </div>
          {context ? <div className="flex flex-wrap gap-3">{context}</div> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-3">{actions}</div> : null}
      </div>
    </header>
  );
}
