import type { ReactNode } from 'react';
import { cx } from '../../lib/cx';

const toneMap: Record<string, string> = {
  character: 'border-indigo-400/30 bg-indigo-500/10 text-indigo-100',
  char: 'border-indigo-400/30 bg-indigo-500/10 text-indigo-100',
  location: 'border-teal-400/30 bg-teal-500/10 text-teal-100',
  loc: 'border-teal-400/30 bg-teal-500/10 text-teal-100',
  recurring_prop: 'border-amber-400/30 bg-amber-500/10 text-amber-100',
  prop: 'border-amber-400/30 bg-amber-500/10 text-amber-100',
  motif: 'border-pink-400/30 bg-pink-500/10 text-pink-100',
};

export function EntityTag({
  tone,
  children,
}: {
  tone: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cx(
        'inline-flex items-center rounded-lg border px-2 py-1 font-mono text-[11px]',
        toneMap[tone] ?? 'border-white/10 bg-white/[0.03] text-slate-200',
      )}
    >
      {children}
    </span>
  );
}
