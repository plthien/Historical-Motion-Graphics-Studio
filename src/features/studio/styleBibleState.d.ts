import type {
  ProjectStyleBibleArtifactV4,
  StyleBibleArtifactResponseV4,
  StyleBibleEditableContentV4,
  StyleBibleReadResponseV4,
  StyleBibleStatus,
} from '../../api/client';
import type { PipelineStep } from '../../layout/PipelineStepper';

export function selectDisplayedStyleBible(result: StyleBibleArtifactResponseV4 | null, projectId: string | null, artifactId: string | null): StyleBibleArtifactResponseV4 | null;
export function acceptStyleBibleHydration(projectId: string | null, artifactId: string | null, response: StyleBibleReadResponseV4 | null | undefined): StyleBibleArtifactResponseV4 | null;
export function acceptStyleBibleMutation(projectId: string | null, currentArtifactId: string | null, expectedArtifactId: string, response: StyleBibleArtifactResponseV4 | null | undefined): StyleBibleArtifactResponseV4 | null;
export function deriveStyleBibleStageStatus(input: { active: boolean; hasScriptAnalysisArtifact: boolean; styleBibleArtifactId: string | null; styleBibleStatus: StyleBibleStatus | null }): PipelineStep['status'];
export function createStyleBibleEditableContent(artifact: ProjectStyleBibleArtifactV4): StyleBibleEditableContentV4;
export function isStyleBibleEditableContentValid(content: StyleBibleEditableContentV4 | null): boolean;
export function isStyleBibleEditableContentChanged(artifact: ProjectStyleBibleArtifactV4 | null, content: StyleBibleEditableContentV4 | null): boolean;
