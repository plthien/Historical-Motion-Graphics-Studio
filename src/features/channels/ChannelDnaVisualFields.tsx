import type { ChannelDnaV3 } from '../../api/client';
import { entityStyleGroups, visualIdentityFields, type ChannelDnaArrayPath } from './channelDna';
import { LineArrayEditor } from './LineArrayEditor';

export function ChannelDnaVisualFields({ dna, onChange }: { dna: ChannelDnaV3; onChange: (path: ChannelDnaArrayPath, values: string[]) => void }) {
  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-100">Visual Identity</h3>
          <p className="text-xs leading-5 text-slate-500">One deterministic value per line. Empty lines are removed.</p>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {visualIdentityFields.map(([field, label]) => (
            <LineArrayEditor key={field} label={label} value={dna.visual_identity[field]} onChange={(values) => onChange({ kind: 'visual_identity', field }, values)} />
          ))}
        </div>
      </section>

      <section className="space-y-4">
        <h3 className="text-sm font-semibold text-slate-100">Entity Styles</h3>
        {entityStyleGroups.map(([entity, label]) => (
          <div key={entity} className="rounded-2xl border border-white/10 bg-white/[0.02] p-4">
            <div className="mb-3 text-sm font-medium text-slate-200">{label}</div>
            <div className="grid gap-4 md:grid-cols-2">
              <LineArrayEditor label="Appearance" value={dna.entity_styles[entity].appearance} onChange={(values) => onChange({ kind: 'entity_style', entity, field: 'appearance' }, values)} />
              <LineArrayEditor label="Avoid" value={dna.entity_styles[entity].avoid} onChange={(values) => onChange({ kind: 'entity_style', entity, field: 'avoid' }, values)} />
            </div>
          </div>
        ))}
      </section>

      <section>
        <LineArrayEditor label="Global Negative Constraints" value={dna.global_negative_constraints} onChange={(values) => onChange({ kind: 'global_negative_constraints' }, values)} />
      </section>
    </div>
  );
}
