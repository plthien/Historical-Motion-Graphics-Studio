import { FolderPlus, X } from 'lucide-react';
import { FieldLabel, TextArea, TextInput } from '../channels/forms';

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

export function CreateProjectModal({
  isOpen,
  selectedChannelLabel,
  form,
  errorMessage,
  isSubmitting,
  onClose,
  onUpdateField,
  onSubmit,
}: {
  isOpen: boolean;
  selectedChannelLabel: string;
  form: { name: string; description: string };
  errorMessage: string;
  isSubmitting: boolean;
  onClose: () => void;
  onUpdateField: (field: 'name' | 'description', value: string) => void;
  onSubmit: () => void;
}) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm">
      <div data-testid="create-project-modal" className="panel-surface flex max-h-[calc(100vh-4rem)] w-full max-w-2xl flex-col overflow-hidden">
        <div className="flex items-start justify-between gap-4 border-b border-white/10 px-5 py-4">
          <div className="space-y-1">
            <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Create Project</div>
            <h2 className="text-lg font-semibold text-slate-100">New Project</h2>
            <p className="text-sm leading-6 text-slate-400">
              Create a project under the active channel, then open Studio to begin Topic Ideas and Script generation.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            className="rounded-xl border border-white/10 bg-white/[0.03] p-2 text-slate-400 transition hover:text-slate-100 disabled:cursor-not-allowed"
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

          <div className="grid gap-4">
            <div className="grid gap-2">
              <FieldLabel>Selected Channel</FieldLabel>
              <TextInput value={selectedChannelLabel} disabled />
            </div>
            <div className="grid gap-2">
              <FieldLabel>Project Title</FieldLabel>
              <TextInput
                data-testid="project-title-input"
                value={form.name}
                placeholder="Give this production a clear working title"
                onChange={(event) => onUpdateField('name', event.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <FieldLabel>Project Notes</FieldLabel>
              <TextArea
                rows={5}
                value={form.description}
                placeholder="Optional notes, brief, or kickoff context supported by the existing backend description field."
                onChange={(event) => onUpdateField('description', event.target.value)}
              />
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/10 px-5 py-4">
          <div className="text-sm leading-6 text-slate-400">
            {isSubmitting ? 'Creating project and selecting it for Studio...' : 'Create the project first. Topic Ideas generation still starts explicitly from Studio.'}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={onClose} disabled={isSubmitting}>Cancel</Button>
            <Button dataTestId="create-project-submit" onClick={onSubmit} disabled={isSubmitting} variant="primary">
              <FolderPlus className="h-4 w-4" />
              {isSubmitting ? 'Creating Project...' : 'Create & Start Pipeline'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
