import { CheckCircle2, RefreshCcw, Upload } from 'lucide-react';
import type { Channel, ChannelDnaV3, PacingDensity, VideoPacingMode } from '../../api/client';
import { LoadingBlock } from '../../components/ui/LoadingBlock';
import { StatusBadge } from '../../components/ui/StatusBadge';
import { FieldLabel, TextArea, TextInput } from './forms';
import type { ChannelDnaArrayPath } from './channelDna';
import { ChannelDnaNarrationFields } from './ChannelDnaNarrationFields';
import { ChannelDnaPacingFields } from './ChannelDnaPacingFields';
import { ChannelDnaVisualFields } from './ChannelDnaVisualFields';

type LoadState = 'idle' | 'loading' | 'ready' | 'missing' | 'invalid' | 'error';

function Button({
  children,
  onClick,
  disabled,
  variant = 'secondary',
  type = 'button',
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: 'primary' | 'secondary';
  type?: 'button' | 'submit' | 'reset';
}) {
  const styles =
    variant === 'primary'
      ? 'border-cyan-400/30 bg-cyan-500/15 text-cyan-50 hover:bg-cyan-500/25'
      : 'border-white/10 bg-white/[0.03] text-slate-200 hover:bg-white/[0.06]';

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:border-white/5 disabled:bg-white/[0.02] disabled:text-slate-500 ${styles}`}
    >
      {children}
    </button>
  );
}

export function ChannelDnaEditor({
  selectedChannel,
  draft,
  loadState,
  updatedAt,
  loadError,
  pasteValue,
  onPasteValueChange,
  onApplyPastedJson,
  onImportFile,
  onChangeChannelField,
  onChangePacingRange,
  onChangeVisualArray,
  onChangeNarrationWpm,
  onReset,
  onValidate,
  onSave,
  isValidating,
  isSaving,
}: {
  selectedChannel: Channel | null;
  draft: ChannelDnaV3 | null;
  loadState: LoadState;
  updatedAt: string | null;
  loadError: string | null;
  pasteValue: string;
  onPasteValueChange: (value: string) => void;
  onApplyPastedJson: () => void;
  onImportFile: (file: File | null) => Promise<void>;
  onChangeChannelField: (field: keyof ChannelDnaV3['channel'], value: string) => void;
  onChangePacingRange: (mode: VideoPacingMode, density: PacingDensity, field: 'min' | 'max', value: number) => void;
  onChangeVisualArray: (path: ChannelDnaArrayPath, values: string[]) => void;
  onChangeNarrationWpm: (field: 'min' | 'max', value: number) => void;
  onReset: () => void;
  onValidate: () => void;
  onSave: () => void;
  isValidating: boolean;
  isSaving: boolean;
}) {
  const canEdit = Boolean(selectedChannel && draft && (loadState === 'ready' || loadState === 'missing' || loadState === 'invalid'));

  return (
    <section className="panel-surface sticky top-4 flex max-h-[calc(100vh-15rem)] flex-col overflow-hidden">
      <div className="flex flex-col gap-4 border-b border-white/10 px-5 py-4">
        <div className="space-y-1">
          <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Selected Channel</div>
          <h2 className="text-lg font-semibold text-slate-100">Channel DNA</h2>
          <p className="text-sm leading-6 text-slate-400">
            Edit the current Channel DNA 3.0 contract. Channel context keeps its existing meaning; narration and visual guidance are structured, line-based, and free-form.
          </p>
        </div>

        {selectedChannel ? (
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge
              status={
                loadState === 'ready'
                  ? 'approved'
                  : loadState === 'missing'
                    ? 'pending'
                    : loadState === 'invalid' || loadState === 'error'
                      ? 'failed'
                      : 'draft'
              }
              label={
                loadState === 'ready'
                  ? 'DNA Loaded'
                  : loadState === 'missing'
                    ? 'No Channel DNA Saved Yet'
                    : loadState === 'invalid'
                      ? 'Stored DNA Invalid'
                      : loadState === 'error'
                        ? 'Load Error'
                        : 'Loading'
              }
            />
            {updatedAt ? <StatusBadge status="draft" label={`Updated ${new Date(updatedAt).toLocaleString()}`} /> : null}
          </div>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        {!selectedChannel ? (
          <div className="rounded-[1.25rem] border border-dashed border-white/10 bg-white/[0.02] p-6 text-sm leading-6 text-slate-400">
            Select a channel from the left card grid to load its real saved Channel DNA.
          </div>
        ) : null}

        {selectedChannel && loadState === 'loading' ? <LoadingBlock label="Loading saved Channel DNA..." /> : null}

        {selectedChannel && (loadState === 'invalid' || loadState === 'error') ? (
          <div className="rounded-[1.25rem] border border-rose-500/30 bg-rose-500/10 p-6 text-sm leading-6 text-rose-100">
            {loadError ?? 'The selected channel DNA could not be loaded safely.'}
          </div>
        ) : null}

        {selectedChannel && canEdit && draft ? (
          <div className="space-y-5">
            {loadState === 'missing' ? (
              <div className="rounded-[1.25rem] border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm leading-6 text-amber-50">
                No Channel DNA saved yet. Fill the Channel DNA 3.0 fields below, or import/paste JSON before saving.
              </div>
            ) : null}

            <div className="flex flex-wrap gap-2">
              <label className="inline-flex cursor-pointer items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.03] px-3 py-2 text-sm font-medium text-slate-100 transition hover:bg-white/[0.05]">
                <Upload className="h-4 w-4" />
                Import Channel DNA JSON
                <input
                  hidden
                  type="file"
                  accept=".json,application/json"
                  onChange={(event) => void onImportFile(event.target.files?.[0] ?? null)}
                />
              </label>
              <Button onClick={onApplyPastedJson} disabled={!pasteValue.trim()}>
                Apply Pasted JSON
              </Button>
              <Button onClick={onReset}>
                <RefreshCcw className="h-4 w-4" />
                Reset
              </Button>
              <Button onClick={onValidate} disabled={isValidating}>
                <CheckCircle2 className="h-4 w-4" />
                Validate DNA
              </Button>
              <Button onClick={onSave} disabled={isSaving} variant="primary">
                Save Changes
              </Button>
            </div>

            <div className="grid gap-2">
              <FieldLabel>Paste Channel DNA JSON</FieldLabel>
              <TextArea value={pasteValue} onChange={(event) => onPasteValueChange(event.target.value)} rows={6} />
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="grid gap-2">
                <FieldLabel>Channel Name</FieldLabel>
                <TextInput value={draft.channel.name} onChange={(event) => onChangeChannelField('name', event.target.value)} />
              </div>
              <div className="grid gap-2">
                <FieldLabel>Language</FieldLabel>
                <TextInput value={draft.channel.language} onChange={(event) => onChangeChannelField('language', event.target.value)} />
              </div>
              <div className="grid gap-2 md:col-span-2">
                <FieldLabel>Niche</FieldLabel>
                <TextArea value={draft.channel.niche} onChange={(event) => onChangeChannelField('niche', event.target.value)} rows={3} />
              </div>
              <div className="grid gap-2 md:col-span-2">
                <FieldLabel>Target Audience</FieldLabel>
                <TextArea value={draft.channel.audience} onChange={(event) => onChangeChannelField('audience', event.target.value)} rows={3} />
              </div>
            </div>

            <ChannelDnaPacingFields dna={draft} onChange={onChangePacingRange} />

            <ChannelDnaNarrationFields dna={draft} onChange={onChangeVisualArray} onChangeNarrationWpm={onChangeNarrationWpm} />

            <ChannelDnaVisualFields dna={draft} onChange={onChangeVisualArray} />
          </div>
        ) : null}
      </div>
    </section>
  );
}
