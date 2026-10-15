import type {
  ProjectStyleBibleArtifactV4,
  StyleBibleCharacter,
  StyleBibleLocation,
  StyleBibleMotifV4,
  StyleBibleRecurringProp,
  StyleBibleRecurringVisualSubject,
  VisualReferenceAnchorTypeV4,
  VisualReferenceCandidateV4,
  VisualReferenceJobStatusV4,
  VisualReferenceSelectionV4,
  VisualReferenceSubjectV4,
  VisualReferenceWorkspaceV4,
  VisualStyleSnapshot,
} from '../../api/client';
import type { DefinitionCategoryKey } from './definitionCategories';

export type ReferenceCardStatus =
  | 'loading'
  | 'approved'
  | 'draft_ready'
  | 'generating'
  | 'failed'
  | 'no_reference'
  | 'stale';

interface DefinitionBase<Category extends DefinitionCategoryKey, Source> {
  category: Category;
  id: string;
  label: string;
  visualSummary: string;
  source: Source;
}

export type StyleBibleReferenceDefinition =
  | (DefinitionBase<'characters', StyleBibleCharacter> & { anchorType: 'character' })
  | (DefinitionBase<'locations', StyleBibleLocation> & { anchorType: 'location' })
  | (DefinitionBase<'recurring_props', StyleBibleRecurringProp> & { anchorType: 'prop' })
  | (DefinitionBase<'recurring_visual_subjects', StyleBibleRecurringVisualSubject> & { anchorType: 'recurring_visual_subject' })
  | (DefinitionBase<'visual_motifs', StyleBibleMotifV4> & { anchorType: null });

export interface InheritedChannelDnaSummary {
  available: boolean;
  style: string;
  negative: string;
}

export interface ReferenceCardState {
  status: ReferenceCardStatus;
  statusLabel: string;
  subject: VisualReferenceSubjectV4 | null;
  approvedSelection: VisualReferenceSelectionV4 | null;
  candidateHistory: VisualReferenceCandidateV4[];
  approvedReferenceArtifactId: string | null;
  defaultSelectedReferenceArtifactId: string | null;
  displayedCandidate: VisualReferenceCandidateV4 | null;
  reviewCandidate: VisualReferenceCandidateV4 | null;
  previewUrl: string | null;
  canApprove: boolean;
  referenceSupported: boolean;
  generationLabel: 'Generate' | 'Regenerate';
  error: string | null;
}

const VISUAL_IDENTITY_ORDER: ReadonlyArray<keyof VisualStyleSnapshot['visual_identity']> = [
  'medium',
  'linework',
  'rendering',
  'palette',
  'shape_language',
  'texture',
  'editorial_language',
];

function joinParts(parts: Array<string | null | undefined>): string {
  return parts.filter((part): part is string => Boolean(part?.trim())).join(' · ');
}

function listPart(values: string[]): string | null {
  return values.length ? values.join(', ') : null;
}

export function buildInheritedChannelDnaSummary(snapshot: VisualStyleSnapshot | null | undefined): InheritedChannelDnaSummary {
  if (!snapshot) return { available: false, style: 'Inherited style guidance is unavailable.', negative: 'Inherited negative guidance is unavailable.' };
  const style = VISUAL_IDENTITY_ORDER.flatMap((key) => snapshot.visual_identity[key]).filter(Boolean).join(', ');
  const negative = snapshot.global_negative_constraints.filter(Boolean).join(', ');
  return {
    available: Boolean(style || negative),
    style: style || 'No inherited style guidance recorded.',
    negative: negative || 'No inherited negative guidance recorded.',
  };
}

export function buildStyleBibleReferenceDefinitions(artifact: ProjectStyleBibleArtifactV4): StyleBibleReferenceDefinition[] {
  return [
    ...artifact.characters.map((item): StyleBibleReferenceDefinition => ({
      category: 'characters',
      anchorType: 'character',
      id: item.char_id,
      label: item.label,
      source: item,
      visualSummary: joinParts([
        item.description,
        listPart(item.explicit_visual_traits),
        listPart(item.identity_markers),
        listPart(item.continuity_notes),
      ]),
    })),
    ...artifact.locations.map((item): StyleBibleReferenceDefinition => ({
      category: 'locations',
      anchorType: 'location',
      id: item.loc_id,
      label: item.label,
      source: item,
      visualSummary: joinParts([
        item.description,
        item.location_kind,
        listPart(item.explicit_visual_traits),
        listPart(item.identity_markers),
        listPart(item.continuity_notes),
      ]),
    })),
    ...artifact.recurring_props.map((item): StyleBibleReferenceDefinition => ({
      category: 'recurring_props',
      anchorType: 'prop',
      id: item.prop_id,
      label: item.label,
      source: item,
      visualSummary: joinParts([
        item.description,
        item.prop_kind,
        item.significance,
        listPart(item.explicit_visual_traits),
        listPart(item.identity_markers),
        listPart(item.continuity_notes),
      ]),
    })),
    ...artifact.recurring_visual_subjects.map((item): StyleBibleReferenceDefinition => ({
      category: 'recurring_visual_subjects',
      anchorType: 'recurring_visual_subject',
      id: item.subject_id,
      label: item.label,
      source: item,
      visualSummary: joinParts([
        item.description,
        item.visual_type,
        listPart(item.explicit_visual_traits),
        listPart(item.continuity_notes),
      ]),
    })),
    ...artifact.motifs.map((item): StyleBibleReferenceDefinition => ({
      category: 'visual_motifs',
      anchorType: null,
      id: item.motif_id,
      label: item.label,
      source: item,
      visualSummary: item.description,
    })),
  ];
}

export function buildStyleBibleCategoryCounts(definitions: StyleBibleReferenceDefinition[]): Record<DefinitionCategoryKey, number> {
  return definitions.reduce<Record<DefinitionCategoryKey, number>>((counts, definition) => {
    counts[definition.category] += 1;
    return counts;
  }, {
    characters: 0,
    locations: 0,
    recurring_props: 0,
    recurring_visual_subjects: 0,
    visual_motifs: 0,
  });
}

export function hasPublishedVisualReferenceSet(
  baselineSetArtifactId: string | null | undefined,
  workspace: VisualReferenceWorkspaceV4 | null | undefined,
): boolean {
  if (baselineSetArtifactId === undefined) return false;
  const currentSetArtifactId = workspace?.visual_reference_set_artifact_id ?? null;
  return currentSetArtifactId !== null && currentSetArtifactId !== baselineSetArtifactId;
}

function exactSubject(
  subjects: VisualReferenceSubjectV4[],
  anchorType: VisualReferenceAnchorTypeV4,
  anchorId: string,
): VisualReferenceSubjectV4 | null | 'duplicate' {
  const matches = subjects.filter((subject) => subject.anchor_type === anchorType && subject.anchor_id === anchorId);
  if (matches.length > 1) return 'duplicate';
  return matches[0] ?? null;
}

function exactSelection(
  selections: VisualReferenceSelectionV4[],
  anchorType: VisualReferenceAnchorTypeV4,
  anchorId: string,
): VisualReferenceSelectionV4 | null | 'duplicate' {
  const matches = selections.filter((selection) => selection.anchor_type === anchorType && selection.anchor_id === anchorId);
  if (matches.length > 1) return 'duplicate';
  return matches[0] ?? null;
}

function statusLabel(status: ReferenceCardStatus): string {
  return {
    loading: 'Loading',
    approved: 'Approved',
    draft_ready: 'Draft Ready',
    generating: 'Generating',
    failed: 'Failed',
    no_reference: 'No Reference',
    stale: 'Stale',
  }[status];
}

function state(
  status: ReferenceCardStatus,
  values: Omit<ReferenceCardState, 'status' | 'statusLabel'>,
): ReferenceCardState {
  return { status, statusLabel: statusLabel(status), ...values };
}

export function resolveDefinitionReferenceState({
  definition,
  workspace,
  styleBibleRevisionId,
  job,
  workspaceLoading = false,
}: {
  definition: StyleBibleReferenceDefinition;
  workspace: VisualReferenceWorkspaceV4 | null;
  styleBibleRevisionId: string;
  job: VisualReferenceJobStatusV4 | null;
  workspaceLoading?: boolean;
}): ReferenceCardState {
  const activeJob = job?.status === 'queued' || job?.status === 'running';
  const failedJob = job?.status === 'failed' || job?.status === 'stale_failed';
  const base = {
    subject: null,
    approvedSelection: null,
    candidateHistory: [],
    approvedReferenceArtifactId: null,
    defaultSelectedReferenceArtifactId: null,
    displayedCandidate: null,
    reviewCandidate: null,
    previewUrl: null,
    canApprove: false,
    referenceSupported: definition.anchorType !== null,
    generationLabel: 'Generate' as const,
    error: null,
  };
  if (definition.anchorType === null) return state('no_reference', base);
  if (workspaceLoading && !workspace) return state('loading', base);
  if (!workspace?.visual_reference_set) {
    if (activeJob) return state('generating', base);
    if (failedJob) return state('failed', { ...base, error: job?.error_message ?? 'Reference generation failed.' });
    return state('no_reference', base);
  }

  const visualSet = workspace.visual_reference_set;
  if (
    workspace.visual_reference_set_artifact_id !== visualSet.visual_reference_set_artifact_id
    || visualSet.source_style_bible_revision_id !== styleBibleRevisionId
  ) {
    return state('stale', { ...base, error: 'The visual-reference set does not belong to the current Style Bible revision.' });
  }
  const subject = exactSubject(visualSet.subjects, definition.anchorType, definition.id);
  if (subject === 'duplicate') return state('stale', { ...base, error: 'Duplicate exact visual-reference subjects were returned.' });
  if (!subject) return state('stale', { ...base, error: 'The current reference set is missing this exact definition identity.' });

  const subjectBase = { ...base, subject, generationLabel: 'Regenerate' as const };
  const approvalSet = workspace.approval_set;
  if (approvalSet && (
    workspace.approval_set_artifact_id !== approvalSet.approval_set_artifact_id
    || approvalSet.source_style_bible_revision_id !== styleBibleRevisionId
    || approvalSet.source_visual_reference_set_artifact_id !== visualSet.visual_reference_set_artifact_id
  )) {
    return state('stale', { ...subjectBase, error: 'The approval set does not belong to the current Style Bible and reference-set lineage.' });
  }
  const selection = exactSelection(approvalSet?.selections ?? [], definition.anchorType, definition.id);
  if (selection === 'duplicate') return state('stale', { ...subjectBase, error: 'Duplicate exact approval selections were returned.' });
  if (selection) {
    const approvedCandidate = subject.candidates.find((candidate) => candidate.reference_artifact_id === selection.reference_artifact_id) ?? null;
    if (!approvedCandidate || selection.source_definition_hash !== subject.source_definition_hash) {
      return state('stale', { ...subjectBase, error: 'The approved reference does not match this exact current definition.' });
    }
    const approvedBase = {
      ...subjectBase,
      approvedSelection: selection,
      candidateHistory: subject.candidates,
      approvedReferenceArtifactId: selection.reference_artifact_id,
      defaultSelectedReferenceArtifactId: approvedCandidate.reference_artifact_id,
      displayedCandidate: approvedCandidate,
      reviewCandidate: approvedCandidate,
      previewUrl: approvedCandidate.file_url,
    };
    if (activeJob) return state('generating', approvedBase);
    if (failedJob) return state('failed', { ...approvedBase, error: job?.error_message ?? 'Reference generation failed.' });
    return state('approved', approvedBase);
  }

  const candidate = subject.candidates[0] ?? null;
  const candidateBase = {
    ...subjectBase,
    candidateHistory: subject.candidates,
    defaultSelectedReferenceArtifactId: candidate?.reference_artifact_id ?? null,
    displayedCandidate: candidate,
    reviewCandidate: candidate,
    previewUrl: candidate?.file_url ?? null,
  };
  if (activeJob) return state('generating', candidateBase);
  if (failedJob) return state('failed', { ...candidateBase, error: job?.error_message ?? 'Reference generation failed.' });
  if (!candidate) return state('no_reference', candidateBase);
  return state('draft_ready', { ...candidateBase, canApprove: true });
}
