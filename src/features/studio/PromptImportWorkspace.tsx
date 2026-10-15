import { Clipboard, FileJson, Upload } from 'lucide-react';
import { useRef, useState } from 'react';
import type { ExternalImportValidationResponse, ExternalPromptPackage } from '../../api/client';
import { IconButton } from '../../components/ui/IconButton';

export type PromptImportWorkspaceProps = {
  title: string;
  description: string;
  promptPackage: ExternalPromptPackage | null;
  resultJson: string;
  validation: ExternalImportValidationResponse | null;
  isBuilding: boolean;
  isImporting: boolean;
  buildDisabled?: boolean;
  importDisabled?: boolean;
  onBuildPrompt: () => void;
  onResultJsonChange: (value: string) => void;
  onImport: () => void;
};

export function PromptImportWorkspace(props: PromptImportWorkspaceProps) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [copied, setCopied] = useState(false);

  async function copyPrompt() {
    if (!props.promptPackage) return;
    await navigator.clipboard.writeText(props.promptPackage.prompt_text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  function buildOrRebuildPrompt() {
    if (props.promptPackage) {
      const confirmed = window.confirm('Rebuild this prompt? This replaces the reserved Artifact ID(s), so JSON generated from the current prompt will no longer import.');
      if (!confirmed) return;
    }
    props.onBuildPrompt();
  }

  async function readFile(file: File | undefined) {
    if (!file) return;
    props.onResultJsonChange(await file.text());
    if (fileRef.current) fileRef.current.value = '';
  }

  return (
    <section className="space-y-4 rounded-[1.5rem] border border-white/10 bg-white/[0.03] p-4">
      <div className="space-y-1">
        <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">External ChatGPT Authoring</div>
        <h3 className="text-lg font-semibold text-slate-100">{props.title}</h3>
        <p className="text-sm leading-6 text-slate-400">{props.description}</p>
      </div>

      <div className="flex flex-wrap gap-2">
        <IconButton icon={Clipboard} label={props.isBuilding ? 'Building Prompt...' : props.promptPackage ? 'Rebuild Prompt' : 'Build Prompt'} onClick={buildOrRebuildPrompt} disabled={props.buildDisabled || props.isBuilding} variant="primary" />
        <IconButton icon={Clipboard} label={copied ? 'Copied' : 'Copy Prompt'} onClick={copyPrompt} disabled={!props.promptPackage} />
      </div>

      {props.promptPackage ? (
        <div className="space-y-2">
          <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Prompt Preview</div>
          <p className="text-xs leading-5 text-slate-500">This built prompt is frozen with its reserved Artifact ID(s). Reload restores the same reservation; changing stage inputs only takes effect after an explicit Rebuild Prompt.</p>
          <textarea readOnly rows={14} value={props.promptPackage.prompt_text} className="w-full rounded-2xl border border-white/10 bg-cockpit-950/70 p-3 font-mono text-xs leading-5 text-slate-200 outline-none" />
        </div>
      ) : null}

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">ChatGPT JSON Result</div>
          <div>
            <input ref={fileRef} type="file" accept="application/json,.json" className="hidden" onChange={(event) => void readFile(event.target.files?.[0])} />
            <IconButton icon={Upload} label="Upload JSON" onClick={() => fileRef.current?.click()} />
          </div>
        </div>
        <textarea rows={14} value={props.resultJson} onChange={(event) => props.onResultJsonChange(event.target.value)} placeholder="Paste the complete JSON response from ChatGPT here." className="w-full rounded-2xl border border-white/10 bg-cockpit-950/70 p-3 font-mono text-xs leading-5 text-slate-200 outline-none focus:border-cyan-400/30" />
      </div>

      <div className="flex flex-wrap gap-2">
        <IconButton icon={FileJson} label={props.isImporting ? 'Importing...' : 'Import JSON'} onClick={props.onImport} disabled={!props.resultJson.trim() || props.importDisabled || props.isImporting} variant="primary" />
      </div>


      {props.validation ? (
        <div className={props.validation.valid ? 'rounded-2xl border border-emerald-400/20 bg-emerald-500/[0.06] p-4' : 'rounded-2xl border border-rose-400/20 bg-rose-500/[0.06] p-4'}>
          <div className="font-medium text-slate-100">{props.validation.valid ? 'Validation passed' : 'Validation failed'}</div>
          {props.validation.errors.length ? (
            <div className="mt-3 space-y-2">
              {props.validation.errors.map((issue, index) => (
                <div key={`${issue.path}-${index}`} className="rounded-xl border border-white/10 bg-cockpit-950/50 p-3 text-sm text-slate-300">
                  <div className="font-mono text-xs text-rose-200">{issue.path} · {issue.code}</div>
                  <div className="mt-1">{issue.message}</div>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
