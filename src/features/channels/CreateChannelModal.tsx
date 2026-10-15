import { Upload, X } from 'lucide-react';
import type { ChannelDnaV3, PacingDensity, VideoPacingMode } from '../../api/client';
import { FieldLabel, TextArea, TextInput } from './forms';
import type { ChannelDnaArrayPath } from './channelDna';
import { ChannelDnaNarrationFields } from './ChannelDnaNarrationFields';
import { ChannelDnaPacingFields } from './ChannelDnaPacingFields';
import { ChannelDnaVisualFields } from './ChannelDnaVisualFields';

function Button({
  children,
  onClick,
  disabled,
  variant = 'secondary',
  type = 'button',
  dataTestId,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: 'primary' | 'secondary';
  type?: 'button' | 'submit' | 'reset';
  dataTestId?: string;
}) {
  const styles =
    variant === 'primary'
      ? 'border-cyan-400/30 bg-cyan-500/15 text-cyan-50 hover:bg-cyan-500/25'
      : 'border-white/10 bg-white/[0.03] text-slate-200 hover:bg-white/[0.06]';

  return (
    <button
      data-testid={dataTestId}
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:border-white/5 disabled:bg-white/[0.02] disabled:text-slate-500 ${styles}`}
    >
      {children}
    </button>
  );
}

export function CreateChannelModal({
  isOpen,
  form,
  draft,
  pasteValue,
  errorMessage,
  submitLabel,
  submitPhase,
  isSubmitting,
  onClose,
  onUpdateField,
  onUpdatePacingRange,
  onUpdateVisualArray,
  onUpdateNarrationWpm,
  onPasteValueChange,
  onApplyPastedJson,
  onImportFile,
  onSubmit,
}: {
  isOpen: boolean;
  form: { name: string; language: string; niche: string; audience: string };
  draft: ChannelDnaV3;
  pasteValue: string;
  errorMessage: string;
  submitLabel: string;
  submitPhase: 'idle' | 'creating' | 'saving' | 'refreshing';
  isSubmitting: boolean;
  onClose: () => void;
  onUpdateField: (field: 'name' | 'language' | 'niche' | 'audience', value: string) => void;
  onUpdatePacingRange: (mode: VideoPacingMode, density: PacingDensity, field: 'min' | 'max', value: number) => void;
  onUpdateVisualArray: (path: ChannelDnaArrayPath, values: string[]) => void;
  onUpdateNarrationWpm: (field: 'min' | 'max', value: number) => void;
  onPasteValueChange: (value: string) => void;
  onApplyPastedJson: () => void;
  onImportFile: (file: File | null) => Promise<void>;
  onSubmit: () => void | Promise<void>;
}) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm">
      <div data-testid="create-channel-modal" className="panel-surface flex max-h-[calc(100vh-4rem)] w-full max-w-4xl flex-col overflow-hidden">
        <div className="flex items-start justify-between gap-4 border-b border-white/10 px-5 py-4">
          <div className="space-y-1">
            <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Create Channel</div>
            <h2 className="text-lg font-semibold text-slate-100">New Channel</h2>
            <p className="text-sm leading-6 text-slate-400">
              Create a channel, then optionally attach valid V3 Channel DNA from import, pasted JSON, or manual field entry.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            className="rounded-xl border border-white/10 bg-white/[0.03] p-2 text-slate-400 transition hover:text-slate-100"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
          {errorMessage ? (
            <div className="mb-5 rounded-[1.25rem] border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm leading-6 text-rose-100">
              {errorMessage}
            </div>
          ) : null}

          <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <section className="space-y-4">
              <div className="space-y-1">
                <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Basic Information</div>
                <p className="text-sm leading-6 text-slate-400">These values define the channel record and also populate the `channel` section of Channel DNA.</p>
              </div>

              <div className="grid gap-4">
                <div className="grid gap-2">
                  <FieldLabel>Channel Name</FieldLabel>
                  <TextInput value={form.name} onChange={(event) => onUpdateField('name', event.target.value)} />
                </div>
                <div className="grid gap-2">
                  <FieldLabel>Language</FieldLabel>
                  <TextInput value={form.language} onChange={(event) => onUpdateField('language', event.target.value)} />
                </div>
                <div className="grid gap-2">
                  <FieldLabel>Niche</FieldLabel>
                  <TextArea rows={3} value={form.niche} onChange={(event) => onUpdateField('niche', event.target.value)} />
                </div>
                <div className="grid gap-2">
                  <FieldLabel>Target Audience</FieldLabel>
                  <TextArea rows={3} value={form.audience} onChange={(event) => onUpdateField('audience', event.target.value)} />
                </div>
              </div>
            </section>

            <section className="space-y-4">
              <div className="space-y-1">
                <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Channel DNA Setup</div>
                <p className="text-sm leading-6 text-slate-400">Import JSON, paste JSON, or fill the Channel DNA 3.0 narration and visual fields manually. No AI DNA Generator is active.</p>
              </div>

              <div className="flex flex-wrap gap-2">
                <label className="inline-flex cursor-pointer items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.03] px-3 py-2 text-sm font-medium text-slate-100 transition hover:bg-white/[0.05]">
                  <Upload className="h-4 w-4" />
                  Import Channel DNA JSON
                  <input
                    data-testid="import-channel-dna-input"
                    hidden
                    type="file"
                    accept=".json,application/json"
                    onChange={(event) => void onImportFile(event.target.files?.[0] ?? null)}
                  />
                </label>
                <Button onClick={onApplyPastedJson} disabled={!pasteValue.trim()}>
                  <span data-testid="paste-channel-dna-button">Paste Channel DNA JSON</span>
                </Button>
              </div>

              <div className="grid gap-2">
                <FieldLabel>Paste JSON</FieldLabel>
                <TextArea value={pasteValue} onChange={(event) => onPasteValueChange(event.target.value)} rows={8} />
              </div>

              <ChannelDnaPacingFields dna={draft} onChange={onUpdatePacingRange} />

              <ChannelDnaNarrationFields dna={draft} onChange={onUpdateVisualArray} onChangeNarrationWpm={onUpdateNarrationWpm} />

              <ChannelDnaVisualFields dna={draft} onChange={onUpdateVisualArray} />
            </section>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/10 px-5 py-4">
          <div className="text-sm leading-6 text-slate-400">
            {submitPhase === 'creating'
              ? 'Creating channel...'
              : submitPhase === 'saving'
                ? 'Saving Channel DNA...'
                : submitPhase === 'refreshing'
                  ? 'Refreshing saved channel state...'
                  : 'If Channel DNA fields remain incomplete, the channel will still be created, but Channel DNA will need to be saved later from the editor.'}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={onClose} disabled={isSubmitting}>Cancel</Button>
            <Button dataTestId="create-channel-submit" onClick={onSubmit} disabled={isSubmitting} variant="primary">
              {submitLabel}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
