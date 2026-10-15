import type { ChannelDnaV3 } from '../../api/client';
import { narrationIdentityFields, type ChannelDnaArrayPath } from './channelDna';
import { LineArrayEditor } from './LineArrayEditor';

export function ChannelDnaNarrationFields({ dna, onChange, onChangeNarrationWpm }: { dna: ChannelDnaV3; onChange: (path: ChannelDnaArrayPath, values: string[]) => void; onChangeNarrationWpm: (field: 'min' | 'max', value: number) => void }) {
  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold text-slate-100">Narration Identity</h3>
        <p className="text-xs leading-5 text-slate-500">Free-form channel-level narration intent. The application does not impose or infer a creative style.</p>
      </div>
      <div className="grid gap-2 text-sm text-slate-300">
        <span>Default narration WPM range</span>
        <div className="grid grid-cols-2 gap-2">
          <label className="grid gap-1 text-xs text-slate-400">Min<input type="number" min={80} max={300} step={1} value={dna.narration_wpm.min} onChange={(event) => onChangeNarrationWpm('min', Number(event.target.value))} className="rounded-2xl border border-white/10 bg-cockpit-950/70 px-3 py-2.5 text-sm text-slate-200" /></label>
          <label className="grid gap-1 text-xs text-slate-400">Max<input type="number" min={80} max={300} step={1} value={dna.narration_wpm.max} onChange={(event) => onChangeNarrationWpm('max', Number(event.target.value))} className="rounded-2xl border border-white/10 bg-cockpit-950/70 px-3 py-2.5 text-sm text-slate-200" /></label>
        </div>
        <span className="text-xs leading-5 text-slate-500">Used to prefill Script & Analysis builds. ChatGPT chooses one exact narration WPM per section inside this inclusive range.</span>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {narrationIdentityFields.map(([field, label]) => (
          <LineArrayEditor key={field} label={label} value={dna.narration_identity[field]} onChange={(values) => onChange({ kind: 'narration_identity', field }, values)} />
        ))}
      </div>
    </section>
  );
}
