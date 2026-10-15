import type { ChannelDnaV3, PacingDensity, VideoPacingMode } from '../../api/client';
import { FieldLabel } from './forms';

const MODES: VideoPacingMode[] = ['fast', 'medium', 'slow'];
const DENSITIES: PacingDensity[] = ['high', 'medium', 'low'];

export function ChannelDnaPacingFields({ dna, onChange }: {
  dna: ChannelDnaV3;
  onChange: (mode: VideoPacingMode, density: PacingDensity, field: 'min' | 'max', value: number) => void;
}) {
  return (
    <section className="space-y-3 rounded-[1.25rem] border border-white/10 bg-white/[0.02] p-4">
      <div>
        <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Video Pacing Profiles</div>
        <p className="mt-1 text-xs leading-5 text-slate-500">Channel-owned calibration. Topic Ideas recommends FAST/MEDIUM/SLOW from these exact ranges; the Script user may override the recommended mode.</p>
      </div>
      <div className="grid gap-3 xl:grid-cols-3">
        {MODES.map((mode) => (
          <div key={mode} className="rounded-2xl border border-white/10 bg-cockpit-950/40 p-3">
            <div className="mb-3 text-xs font-semibold uppercase tracking-[0.2em] text-cyan-200">{mode}</div>
            <div className="space-y-3">
              {DENSITIES.map((density) => {
                const hold = dna.pacing_profiles[mode][density].preferred_hold_seconds;
                return (
                  <div key={density} className="grid grid-cols-[72px_1fr_1fr] items-end gap-2">
                    <div className="pb-2 text-xs font-medium uppercase text-slate-400">{density}</div>
                    <label className="grid gap-1 text-xs text-slate-500"><FieldLabel>Min sec</FieldLabel><input type="number" min="0" step="0.1" value={hold.min || ''} onChange={(event) => onChange(mode, density, 'min', Number(event.target.value))} className="rounded-xl border border-white/10 bg-slate-950/80 px-2 py-2 text-sm text-slate-100" /></label>
                    <label className="grid gap-1 text-xs text-slate-500"><FieldLabel>Max sec</FieldLabel><input type="number" min="0" step="0.1" value={hold.max || ''} onChange={(event) => onChange(mode, density, 'max', Number(event.target.value))} className="rounded-xl border border-white/10 bg-slate-950/80 px-2 py-2 text-sm text-slate-100" /></label>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
