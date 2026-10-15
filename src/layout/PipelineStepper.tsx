import { CheckCircle2, Lock, Sparkles } from 'lucide-react';
import { cx } from '../lib/cx';

export type PipelineStep = {
  id: string;
  label: string;
  status: 'complete' | 'draft' | 'text_approved' | 'active' | 'ready' | 'preview' | 'locked' | 'unknown' | 'queued' | 'running' | 'validation_failed' | 'provider_failed' | 'approved' | 'failed';
  hint?: string;
};

function iconForStatus(status: PipelineStep['status']) {
  if (status === 'complete' || status === 'text_approved' || status === 'approved') return CheckCircle2;
  if (status === 'locked' || status === 'preview') return Lock;
  return Sparkles;
}

function labelForStatus(status: PipelineStep['status']) {
  if (status === 'text_approved' || status === 'approved') return 'approved';
  if (status === 'validation_failed' || status === 'failed') return 'failed';
  if (status === 'provider_failed') return 'provider';
  if (status === 'active') return 'current';
  return status;
}

export function PipelineStepper({
  steps,
  activeStepId,
  onSelect,
}: {
  steps: PipelineStep[];
  activeStepId: string;
  onSelect: (stepId: string) => void;
}) {
  return (
    <div className="studio-compact-pipeline panel-surface p-2">
      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 lg:grid-cols-4">
        {steps.map((step, index) => {
          const Icon = iconForStatus(step.status);
          const selected = step.id === activeStepId;
          const disabledTone = step.status === 'locked' || step.status === 'preview';
          const statusLabel = labelForStatus(step.status);
          return (
            <button
              key={step.id}
              type="button"
              title={step.hint}
              aria-current={selected ? 'step' : undefined}
              disabled={disabledTone}
              onClick={() => {
                if (!disabledTone) onSelect(step.id);
              }}
              className={cx(
                'pipeline-step group flex min-h-[58px] min-w-0 flex-col justify-center gap-1 rounded-lg border px-2.5 py-2 text-left transition',
                selected && 'pipeline-step-active',
                selected
                  ? 'border-cyan-400/30 bg-cyan-500/10 shadow-soft'
                  : 'border-white/10 bg-white/[0.03] hover:bg-white/[0.05]',
                disabledTone && !selected && 'opacity-80',
              )}
            >
              <div className="flex min-w-0 items-center gap-1.5">
                <span className="w-4 shrink-0 text-[10px] font-semibold leading-none text-slate-500 tabular-nums">
                  {String(index + 1).padStart(2, '0')}
                </span>
                <span
                  className={cx(
                    'grid h-6 w-6 shrink-0 place-items-center rounded-md border',
                    step.status === 'complete' || step.status === 'text_approved' || step.status === 'approved'
                      ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-100'
                      : step.status === 'active' || step.status === 'ready' || step.status === 'draft'
                        ? 'border-cyan-400/30 bg-cyan-500/10 text-cyan-100'
                        : 'border-white/10 bg-cockpit-800/70 text-slate-400',
                  )}
                >
                  <Icon className="h-3.5 w-3.5" />
                </span>
                <span className="min-w-0 truncate text-xs font-semibold leading-4 text-slate-100">{step.label}</span>
              </div>
              <div className="flex min-w-0 items-center justify-between gap-2 pl-[47px]">
                <span className="min-w-0 truncate text-[10px] leading-4 text-slate-500">{selected ? 'Selected' : `Step ${index + 1}`}</span>
                <span
                  className={cx(
                    'shrink-0 rounded-md px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.08em]',
                    step.status === 'complete' || step.status === 'text_approved' || step.status === 'approved'
                      ? 'bg-emerald-500/10 text-emerald-200'
                      : step.status === 'active' || step.status === 'ready' || step.status === 'draft'
                        ? 'bg-cyan-500/10 text-cyan-200'
                        : 'bg-white/[0.04] text-slate-400',
                  )}
                >
                  {statusLabel}
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
