import { useEffect, useMemo, useState } from 'react';
import type { Dispatch, ReactNode, SetStateAction } from 'react';
import { Download, ExternalLink, Image, RefreshCw, Sparkles } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { apiClient, type MediaActiveBatch, type MediaTaskCard, type MediaTaskStatus, type MediaTaskSummaryResponse } from '../../api/client';
import { CardGrid } from '../../components/ui/CardGrid';
import { DetailPanel } from '../../components/ui/DetailPanel';
import { IconButton } from '../../components/ui/IconButton';
import { MetricCard } from '../../components/ui/MetricCard';
import { SectionCard } from '../../components/ui/SectionCard';
import { StatusBadge } from '../../components/ui/StatusBadge';
import { cx } from '../../lib/cx';
import type { MediaFormState, MediaStatusFilter } from './types';

const TERMINAL_MEDIA_STATUSES = new Set<MediaTaskStatus>(['completed', 'failed', 'canceled', 'download_failed']);

function statusLabel(status: MediaTaskStatus): string {
  const labels: Record<MediaTaskStatus, string> = {
    queued: 'Queued',
    submitted: 'Submitted',
    provider_pending: 'Generating — pending',
    provider_running: 'Generating — running',
    downloading: 'Downloading',
    completed: 'Completed',
    failed: 'Failed',
    canceled: 'Canceled',
    download_failed: 'Download failed',
  };
  return labels[status];
}

function FieldLabel({ children }: { children: ReactNode }) {
  return <label className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">{children}</label>;
}

function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={cx(
        'w-full rounded-2xl border border-white/10 bg-cockpit-950/70 px-3 py-2.5 text-sm text-slate-100 outline-none transition placeholder:text-slate-500 focus:border-cyan-400/30 focus:bg-cockpit-950',
        props.className,
      )}
    />
  );
}

function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={cx(
        'w-full rounded-2xl border border-white/10 bg-cockpit-950/70 px-3 py-2.5 text-sm text-slate-100 outline-none transition placeholder:text-slate-500 focus:border-cyan-400/30 focus:bg-cockpit-950',
        props.className,
      )}
    />
  );
}

function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={cx(
        'w-full rounded-2xl border border-white/10 bg-cockpit-950/70 px-3 py-2.5 text-sm text-slate-100 outline-none transition focus:border-cyan-400/30 focus:bg-cockpit-950',
        props.className,
      )}
    />
  );
}

export function MediaReferencePanel({
  mediaForm,
  setMediaForm,
  mediaBatchId,
  availableBatches,
  onSelectBatch,
  summary,
  tasks,
  statusFilter,
  setStatusFilter,
  onGenerate,
  onRefresh,
  isGenerating,
  isRefreshing,
}: {
  mediaForm: MediaFormState;
  setMediaForm: Dispatch<SetStateAction<MediaFormState>>;
  mediaBatchId: string | null;
  availableBatches: MediaActiveBatch[];
  onSelectBatch: (batchId: string) => void;
  summary?: MediaTaskSummaryResponse;
  tasks: MediaTaskCard[];
  statusFilter: MediaStatusFilter;
  setStatusFilter: (value: MediaStatusFilter) => void;
  onGenerate: () => void;
  onRefresh: () => void;
  isGenerating: boolean;
  isRefreshing: boolean;
}) {
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);

  useEffect(() => {
    if (tasks.length === 0) {
      setSelectedTaskId(null);
      return;
    }
    if (!selectedTaskId || !tasks.some((task) => task.media_task_id === selectedTaskId)) {
      setSelectedTaskId(tasks[0].media_task_id);
    }
  }, [selectedTaskId, tasks]);

  const selectedTask = useMemo(
    () => tasks.find((task) => task.media_task_id === selectedTaskId) ?? null,
    [selectedTaskId, tasks],
  );
  const resultQuery = useQuery({
    queryKey: ['media-task-result', mediaBatchId, selectedTask?.media_task_id],
    queryFn: () => apiClient.getMediaTaskResult(selectedTask!.media_task_id),
    enabled: Boolean(selectedTask?.media_task_id && TERMINAL_MEDIA_STATUSES.has(selectedTask.status)),
    retry: false,
  });

  const summaryCards = summary
    ? [
        { label: 'Total', value: summary.total },
        { label: 'Queued', value: summary.queued },
        { label: 'Submitted', value: summary.submitted },
        { label: 'Pending', value: summary.provider_pending },
        { label: 'Running', value: summary.provider_running },
        { label: 'Downloading', value: summary.downloading },
        { label: 'Completed', value: summary.completed },
        { label: 'Failed', value: summary.failed },
        { label: 'Download failed', value: summary.download_failed },
      ]
    : [];

  const filterTabs: Array<{ value: MediaStatusFilter; label: string }> = summary
    ? [
        { value: 'all', label: `All (${summary.total})` },
        { value: 'queued', label: `Queued (${summary.queued})` },
        { value: 'submitted', label: `Submitted (${summary.submitted})` },
        { value: 'provider_pending', label: `Pending (${summary.provider_pending})` },
        { value: 'provider_running', label: `Running (${summary.provider_running})` },
        { value: 'downloading', label: `Downloading (${summary.downloading})` },
        { value: 'completed', label: `Completed (${summary.completed})` },
        { value: 'failed', label: `Failed (${summary.failed})` },
        { value: 'download_failed', label: `Download failed (${summary.download_failed})` },
      ]
    : [];

  const generateDisabled =
    isGenerating ||
    !Number.isInteger(mediaForm.candidate_count) ||
    mediaForm.candidate_count < 1 ||
    mediaForm.candidate_count > 5 ||
    mediaForm.entity_id.trim() === '' ||
    mediaForm.prompt.trim() === '';

  return (
    <SectionCard
      title="Media / Reference Images"
      description="Reference-image tasks use one project-level auto-polling owner across every active batch. Provider files are validated and copied into internal artifact storage before a task becomes completed."
      actions={
        <>
          <IconButton icon={RefreshCw} label={isRefreshing ? 'Refreshing...' : 'Refresh Status'} onClick={onRefresh} disabled={!mediaBatchId || isRefreshing} />
          <IconButton icon={Sparkles} label={isGenerating ? 'Submitting...' : 'Generate Images'} onClick={onGenerate} disabled={generateDisabled} variant="primary" />
        </>
      }
    >
      <div className="grid gap-5 xl:grid-cols-[340px_minmax(0,1fr)]">
        <div className="space-y-4 rounded-[1.25rem] border border-white/10 bg-white/[0.03] p-4">
          <div className="grid gap-4">
            <div className="grid gap-2">
              <FieldLabel>Entity Type</FieldLabel>
              <Select value={mediaForm.entity_type} onChange={(event) => setMediaForm((current) => ({ ...current, entity_type: event.target.value as MediaFormState['entity_type'] }))}>
                <option value="character">character</option>
                <option value="location">location</option>
                <option value="prop">prop</option>
                <option value="recurring_visual_subject">recurring_visual_subject</option>
              </Select>
            </div>
            <div className="grid gap-2">
              <FieldLabel>Entity ID</FieldLabel>
              <Input value={mediaForm.entity_id} onChange={(event) => setMediaForm((current) => ({ ...current, entity_id: event.target.value }))} />
            </div>
            <div className="grid gap-4 sm:grid-cols-3 xl:grid-cols-3">
              <div className="grid gap-2">
                <FieldLabel>Candidates</FieldLabel>
                <Input type="number" min={1} max={5} step={1} value={mediaForm.candidate_count} onChange={(event) => setMediaForm((current) => ({ ...current, candidate_count: Number(event.target.value) }))} />
              </div>
              <div className="grid gap-2">
                <FieldLabel>Model</FieldLabel>
                <Select value={mediaForm.model} onChange={(event) => setMediaForm((current) => ({ ...current, model: event.target.value as MediaFormState['model'] }))}>
                  <option value="nano_banana_2">nano_banana_2</option>
                  <option value="nano_banana_pro">nano_banana_pro</option>
                </Select>
              </div>
              <div className="grid gap-2">
                <FieldLabel>Aspect Ratio</FieldLabel>
                <Select value={mediaForm.aspect_ratio} onChange={(event) => setMediaForm((current) => ({ ...current, aspect_ratio: event.target.value as MediaFormState['aspect_ratio'] }))}>
                  {['1:1', '3:4', '4:3', '9:16', '16:9'].map((ratio) => (
                    <option key={ratio} value={ratio}>{ratio}</option>
                  ))}
                </Select>
              </div>
            </div>
            <div className="grid gap-2">
              <FieldLabel>Prompt</FieldLabel>
              <Textarea rows={5} value={mediaForm.prompt} onChange={(event) => setMediaForm((current) => ({ ...current, prompt: event.target.value }))} />
            </div>
            <div className="grid gap-2">
              <FieldLabel>Negative Prompt</FieldLabel>
              <Textarea rows={3} value={mediaForm.negative_prompt} onChange={(event) => setMediaForm((current) => ({ ...current, negative_prompt: event.target.value }))} />
            </div>
          </div>
          <div className="rounded-2xl border border-white/10 bg-cockpit-950/40 p-4 text-sm text-slate-300">
            <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Batch Scope</div>
            {availableBatches.length > 1 ? (
              <Select
                className="mt-3"
                aria-label="Review media batch"
                value={mediaBatchId ?? ''}
                onChange={(event) => onSelectBatch(event.target.value)}
              >
                {availableBatches.map((batch) => (
                  <option key={batch.batch_id} value={batch.batch_id}>
                    {batch.batch_id} · {batch.active_task_count}/{batch.total} active
                  </option>
                ))}
              </Select>
            ) : null}
            <div className="mt-2 break-all font-mono text-xs text-slate-400">{mediaBatchId ?? 'No batch submitted yet'}</div>
          </div>
          <p className="text-xs leading-5 text-slate-500">Reference selection and locking will be implemented in Phase 8 Style Bible.</p>
        </div>

        <div className="space-y-5">
          {summary ? (
            <>
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                {summaryCards.map((card) => <MetricCard key={card.label} icon={Image} label={card.label} value={card.value} />)}
              </div>

              <div className="flex flex-wrap gap-2">
                {filterTabs.map((tab) => (
                  <button
                    key={tab.value}
                    type="button"
                    onClick={() => setStatusFilter(tab.value)}
                    className={cx(
                      'rounded-full border px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.22em] transition',
                      statusFilter === tab.value
                        ? 'border-cyan-400/30 bg-cyan-500/10 text-cyan-100'
                        : 'border-white/10 bg-white/[0.03] text-slate-400 hover:text-slate-100',
                    )}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              {tasks.length === 0 ? (
                <div className="rounded-[1.25rem] border border-dashed border-white/10 bg-white/[0.02] p-8 text-sm leading-6 text-slate-400">
                  No tasks match the current lifecycle filter.
                </div>
              ) : (
                <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
                  <CardGrid>
                    {tasks.map((task) => (
                      <article
                        key={task.media_task_id}
                        className={cx(
                          'cursor-pointer rounded-[1.25rem] border bg-white/[0.03] p-4 shadow-soft transition hover:bg-white/[0.05]',
                          selectedTask?.media_task_id === task.media_task_id ? 'border-cyan-400/30' : 'border-white/10',
                        )}
                        onClick={() => setSelectedTaskId(task.media_task_id)}
                      >
                        <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-cockpit-950/70">
                          {task.thumbnail_url ? (
                            <img src={apiClient.resolveUrl(task.thumbnail_url)} alt={task.entity_id} className="aspect-video w-full object-cover" />
                          ) : (
                            <div className="flex aspect-video items-center justify-center text-slate-500"><Image className="h-8 w-8" /></div>
                          )}
                          <div className="absolute left-3 top-3"><StatusBadge status={task.status} label={statusLabel(task.status)} /></div>
                        </div>
                        <div className="mt-4 space-y-2">
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <div className="font-mono text-[11px] text-slate-500">{task.media_task_id}</div>
                              <div className="mt-1 text-sm font-medium text-slate-100">{task.entity_id}</div>
                            </div>
                            <span className="rounded-full border border-white/10 px-2 py-1 text-xs text-slate-400">#{task.candidate_index}</span>
                          </div>
                          <p className="text-sm text-slate-300">{task.entity_type} · {task.model} · {task.aspect_ratio}</p>
                          <p className="line-clamp-3 text-sm leading-6 text-slate-400">{task.prompt_preview}</p>
                        </div>
                      </article>
                    ))}
                  </CardGrid>

                  {selectedTask ? (
                    <DetailPanel title={selectedTask.entity_id} subtitle={`${selectedTask.media_task_id} · ${selectedTask.entity_type}`} onClose={() => setSelectedTaskId(null)}>
                      <div className="space-y-4">
                        <div className="rounded-2xl border border-white/10 bg-cockpit-950/55 p-4">
                          <StatusBadge status={selectedTask.status} label={statusLabel(selectedTask.status)} />
                          <dl className="mt-4 space-y-3 text-sm text-slate-300">
                            <div><dt className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Model</dt><dd className="mt-1">{selectedTask.model}</dd></div>
                            <div><dt className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Aspect Ratio</dt><dd className="mt-1">{selectedTask.aspect_ratio}</dd></div>
                            <div><dt className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Prompt Preview</dt><dd className="mt-1 leading-6">{selectedTask.prompt_preview}</dd></div>
                          </dl>
                        </div>

                        {resultQuery.isLoading ? <p className="text-sm text-slate-400">Loading result detail...</p> : null}
                        {resultQuery.isError ? <p className="text-sm text-rose-300">Result detail could not be refreshed.</p> : null}
                        {resultQuery.data ? (
                          <pre className="overflow-x-auto rounded-2xl border border-white/10 bg-cockpit-950/55 p-4 text-xs leading-6 text-slate-300">{JSON.stringify(resultQuery.data, null, 2)}</pre>
                        ) : null}

                        <div className="grid gap-2">
                          {selectedTask.status === 'completed' ? (
                            <a href={apiClient.getMediaTaskFileUrl(selectedTask.media_task_id)} target="_blank" rel="noreferrer" className="inline-flex items-center justify-center gap-2 rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-2 text-sm font-medium text-slate-100 transition hover:bg-white/[0.05]">
                              <Download className="h-4 w-4" />Download / Open Internal File
                            </a>
                          ) : null}
                          {TERMINAL_MEDIA_STATUSES.has(selectedTask.status) ? (
                            <a href={apiClient.resolveUrl(selectedTask.result_url)} target="_blank" rel="noreferrer" className="inline-flex items-center justify-center gap-2 rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-2 text-sm font-medium text-slate-100 transition hover:bg-white/[0.05]">
                              <ExternalLink className="h-4 w-4" />View Internal Result Detail
                            </a>
                          ) : null}
                        </div>
                      </div>
                    </DetailPanel>
                  ) : null}
                </div>
              )}
            </>
          ) : (
            <div className="rounded-[1.25rem] border border-dashed border-white/10 bg-white/[0.02] p-8 text-sm leading-6 text-slate-400">
              Submit a media generation batch to populate lifecycle counts and the task review grid. Active tasks will poll automatically.
            </div>
          )}
        </div>
      </div>
    </SectionCard>
  );
}
