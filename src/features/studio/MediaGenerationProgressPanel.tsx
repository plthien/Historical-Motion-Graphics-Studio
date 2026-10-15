import type { MediaGenerationProgressV4 } from '../../api/client';

function updatedTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString();
}

export function MediaGenerationProgressPanel({
  progress,
  title = 'Generation status',
}: {
  progress: MediaGenerationProgressV4;
  title?: string;
}) {
  const waiting = progress.queued + progress.submitted + progress.provider_pending;
  const failed = progress.failed + progress.download_failed;
  const metrics = [
    ['Waiting', waiting],
    ['Generating', progress.provider_running],
    ['Downloading', progress.downloading],
    ['Downloaded', progress.completed],
    ['Failed', failed],
  ] as const;

  return (
    <div className="rounded-xl border border-white/10 bg-slate-950/30 p-3" data-testid="media-generation-progress">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">{title}</div>
        <div className="text-[11px] text-slate-500">Poll cycle interval {progress.effective_poll_interval_seconds}s - updated {updatedTime(progress.updated_at)}</div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
        {metrics.map(([label, value]) => (
          <div key={label} className="rounded-lg border border-white/10 bg-white/[0.025] px-2.5 py-2">
            <div className="text-base font-semibold text-white">{value}</div>
            <div className="text-[11px] text-slate-500">{label}</div>
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
        <span>{progress.completed} / {progress.total} stored locally</span>
        {progress.download_failed ? <span className="text-rose-300">{progress.download_failed} download failed</span> : null}
        {progress.canceled ? <span>{progress.canceled} canceled</span> : null}
      </div>
    </div>
  );
}
