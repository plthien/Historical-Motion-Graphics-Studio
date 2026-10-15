import type { ReactNode } from 'react';
import { cx } from '../../lib/cx';

export function SectionCard({
  title,
  description,
  actions,
  children,
  className,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cx('panel-surface overflow-hidden', className)}>
      <div className="flex flex-col gap-4 border-b border-white/10 px-5 py-4 md:flex-row md:items-start md:justify-between">
        <div className="space-y-1">
          <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Stage Module</div>
          <h2 className="text-lg font-semibold text-slate-100">{title}</h2>
          {description ? <p className="max-w-3xl text-sm leading-6 text-slate-400">{description}</p> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      <div className="p-5">{children}</div>
    </section>
  );
}
