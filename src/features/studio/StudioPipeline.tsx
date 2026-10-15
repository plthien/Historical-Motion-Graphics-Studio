import type { ReactNode } from 'react';
import { PipelineStepper, type PipelineStep } from '../../layout/PipelineStepper';

export function StudioPipeline({
  steps,
  activeStepId,
  onSelectStep,
  panel,
}: {
  steps: PipelineStep[];
  activeStepId: string;
  onSelectStep: (stepId: string) => void;
  panel: ReactNode;
}) {
  return (
    <div className="space-y-4">
      <PipelineStepper steps={steps} activeStepId={activeStepId} onSelect={onSelectStep} />
      {panel}
    </div>
  );
}
