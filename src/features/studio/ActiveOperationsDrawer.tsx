import type { ReactNode } from 'react';

export function ActiveOperationsDrawer({
  open,
  onOpenChange,
  activeCount,
  averageProgress,
  testId,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  activeCount: number;
  averageProgress: number;
  testId: string;
  children: ReactNode;
}) {
  if (activeCount <= 0) return null;
  const progress = Math.max(0, Math.min(100, Math.round(averageProgress)));
  return <>
    {!open ? <button
      type="button"
      data-action="toggle-operations-drawer"
      aria-expanded={false}
      onClick={() => onOpenChange(true)}
      className="fixed right-0 top-1/2 z-40 -translate-y-1/2 rounded-l-xl border border-r-0 border-cyan-400/30 bg-slate-950/95 px-3 py-3 text-left shadow-2xl backdrop-blur"
    >
      <span className="block text-[10px] font-semibold uppercase tracking-[0.14em] text-cyan-200">Active operations</span>
      <span className="mt-1 block text-xs text-slate-300">{activeCount} active · {progress}%</span>
    </button> : null}
    {open ? <button
      type="button"
      aria-label="Close active operations"
      onClick={() => onOpenChange(false)}
      className="fixed inset-0 z-40 bg-black/40"
    /> : null}
    <aside
      data-testid={testId}
      aria-hidden={!open}
      className={`fixed inset-y-0 right-0 z-50 w-full max-w-[26rem] overflow-y-auto border-l border-white/10 bg-slate-950/95 p-4 shadow-2xl backdrop-blur transition-transform ${open ? 'visible translate-x-0' : 'invisible pointer-events-none translate-x-full'}`}
    >
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.14em] text-cyan-200">Active operations</div>
          <div className="mt-1 text-xs text-slate-400">{activeCount} active · {progress}% average progress</div>
        </div>
        <button
          type="button"
          data-action="close-operations-drawer"
          onClick={() => onOpenChange(false)}
          className="rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-slate-200"
        >
          Close
        </button>
      </div>
      <div className="space-y-3" data-testid={`${testId}-items`}>
        {children}
      </div>
    </aside>
  </>;
}
