import { Lock } from 'lucide-react';
import { SectionCard } from '../../components/ui/SectionCard';

export function FutureStagePanel({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <SectionCard title={title} description={description}>
      <div className="rounded-[1.25rem] border border-dashed border-white/10 bg-white/[0.02] p-8">
        <div className="flex items-start gap-3">
          <div className="rounded-2xl border border-white/10 bg-cockpit-950/70 p-2 text-slate-300">
            <Lock className="h-4 w-4" />
          </div>
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Coming Next</div>
            <p className="mt-2 text-sm leading-6 text-slate-300">
              This stage stays disabled until the backend contract exists. The cockpit keeps the step visible so producers can see the full V3 pipeline without implying the stage is ready.
            </p>
          </div>
        </div>
      </div>
    </SectionCard>
  );
}
