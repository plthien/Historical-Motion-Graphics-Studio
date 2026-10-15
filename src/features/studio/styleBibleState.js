/** Active V4 Style Bible state helpers. Strict current-schema ownership. */
export function selectDisplayedStyleBible(result, projectId, artifactId) {
  if (!result || !projectId || !artifactId) return null;
  if (result.artifact_id !== artifactId) return null;
  if (!result.artifact || result.artifact.project_id !== projectId) return null;
  if (result.artifact.schema_version !== '2.0') return null;
  if (result.artifact.style_bible_revision_id !== artifactId) return null;
  return result;
}

export function acceptStyleBibleHydration(projectId, artifactId, response) {
  if (!response?.generated || !response.artifact || !response.artifact_id) return null;
  return selectDisplayedStyleBible({ artifact_id: response.artifact_id, artifact: response.artifact }, projectId, artifactId);
}

export function acceptStyleBibleMutation(projectId, currentArtifactId, expectedArtifactId, response) {
  if (!projectId || !currentArtifactId || currentArtifactId !== expectedArtifactId) return null;
  if (!response?.artifact || response.artifact.schema_version !== '2.0' || response.artifact.project_id !== projectId) return null;
  if (response.artifact.parent_style_bible_revision_id !== expectedArtifactId) return null;
  if (response.artifact.style_bible_revision_id !== response.artifact_id) return null;
  return response;
}

export function deriveStyleBibleStageStatus({ active, hasScriptAnalysisArtifact, styleBibleArtifactId, styleBibleStatus }) {
  if (active) return 'active';
  if (styleBibleArtifactId && styleBibleStatus === 'text_approved') return 'text_approved';
  if (styleBibleArtifactId) return 'draft';
  if (hasScriptAnalysisArtifact) return 'ready';
  return 'locked';
}

export function createStyleBibleEditableContent(artifact) {
  const pick = (item, fields) => Object.fromEntries(fields.map((field) => [field, structuredClone(item[field])]));
  return {
    characters: artifact.characters.map((item) => pick(item, [
      'char_id', 'label', 'description', 'explicit_visual_traits', 'identity_markers', 'continuity_notes', 'physical_constraints', 'entity_kind', 'narrative_role',
    ])),
    locations: artifact.locations.map((item) => pick(item, [
      'loc_id', 'label', 'description', 'explicit_visual_traits', 'identity_markers', 'continuity_notes', 'physical_constraints', 'location_kind',
    ])),
    recurring_props: artifact.recurring_props.map((item) => pick(item, [
      'prop_id', 'label', 'description', 'explicit_visual_traits', 'identity_markers', 'continuity_notes', 'physical_constraints', 'prop_kind',
    ])),
    recurring_visual_subjects: artifact.recurring_visual_subjects.map((item) => pick(item, [
      'subject_id', 'label', 'description', 'visual_type', 'explicit_visual_traits', 'continuity_notes', 'physical_constraints',
    ])),
    motifs: artifact.motifs.map((item) => pick(item, ['motif_id', 'label', 'description'])),
  };
}

function allNonEmpty(value) {
  if (typeof value === 'string') return value.trim().length > 0;
  if (Array.isArray(value)) return value.every(allNonEmpty);
  if (value && typeof value === 'object') return Object.values(value).every(allNonEmpty);
  return true;
}

export function isStyleBibleEditableContentValid(content) {
  return Boolean(content) && allNonEmpty(content);
}

export function isStyleBibleEditableContentChanged(artifact, content) {
  if (!artifact || !content) return false;
  return JSON.stringify(createStyleBibleEditableContent(artifact)) !== JSON.stringify(content);
}
