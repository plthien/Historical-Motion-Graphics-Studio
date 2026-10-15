import test from 'node:test';
import assert from 'node:assert/strict';
import {
  acceptStyleBibleHydration,
  acceptStyleBibleMutation,
  createStyleBibleEditableContent,
  deriveStyleBibleStageStatus,
  isStyleBibleEditableContentChanged,
  isStyleBibleEditableContentValid,
  selectDisplayedStyleBible,
} from '../src/features/studio/styleBibleState.js';

function artifact(projectId, artifactId, status = 'draft', parent = null) {
  return {
    schema_version: '2.0',
    project_id: projectId,
    style_bible_revision_id: artifactId,
    parent_style_bible_revision_id: parent,
    status,
    visual_style_snapshot: {
      visual_identity: { medium: ['m'], linework: ['l'], rendering: ['r'], palette: ['p'], shape_language: ['s'], texture: ['t'], editorial_language: ['e'] },
      entity_styles: {
        character: { appearance: ['ca'], avoid: ['cn'] },
        location: { appearance: ['la'], avoid: ['ln'] },
        prop: { appearance: ['pa'], avoid: ['pn'] },
        recurring_visual_subject: { appearance: ['ra'], avoid: ['rn'] },
      },
      global_negative_constraints: ['g'],
    },
    characters: [{ char_id: 'char_001', label: 'A', description: 'D', explicit_visual_traits: ['v'], identity_markers: ['i'], continuity_notes: ['c'], physical_constraints: ['fixed adult height'], entity_kind: 'human_character', narrative_role: 'lead', first_mention_quote: 'q', occurrence_quotes: [], approval_status: status }],
    locations: [], recurring_props: [], recurring_visual_subjects: [], motifs: [],
  };
}
function result(projectId, artifactId, status = 'draft', parent = null) {
  return { artifact_id: artifactId, artifact: artifact(projectId, artifactId, status, parent) };
}

test('non-null pointer replacement hides old Style Bible immediately and accepts only matching new data', () => {
  const oldResult = result('project_a', 'artifact_aaaaaaaaaaaa');
  assert.equal(selectDisplayedStyleBible(oldResult, 'project_a', 'artifact_aaaaaaaaaaaa'), oldResult);
  assert.equal(selectDisplayedStyleBible(oldResult, 'project_a', 'artifact_bbbbbbbbbbbb'), null);
  const newResult = result('project_a', 'artifact_bbbbbbbbbbbb');
  assert.equal(selectDisplayedStyleBible(newResult, 'project_a', 'artifact_bbbbbbbbbbbb'), newResult);
});

test('late old hydration response is ignored after pointer or project identity changes', () => {
  const lateOld = { project_id: 'project_a', generated: true, artifact_id: 'artifact_aaaaaaaaaaaa', artifact: result('project_a', 'artifact_aaaaaaaaaaaa').artifact };
  assert.equal(acceptStyleBibleHydration('project_a', 'artifact_bbbbbbbbbbbb', lateOld), null);
  assert.equal(acceptStyleBibleHydration('project_b', 'artifact_aaaaaaaaaaaa', lateOld), null);
});

test('late mutation response cannot overwrite a newer pointer or another project', () => {
  const response = result('project_a', 'artifact_bbbbbbbbbbbb', 'draft', 'artifact_aaaaaaaaaaaa');
  assert.equal(acceptStyleBibleMutation('project_a', 'artifact_aaaaaaaaaaaa', 'artifact_aaaaaaaaaaaa', response), response);
  assert.equal(acceptStyleBibleMutation('project_a', 'artifact_cccccccccccc', 'artifact_aaaaaaaaaaaa', response), null);
  assert.equal(acceptStyleBibleMutation('project_b', 'artifact_aaaaaaaaaaaa', 'artifact_aaaaaaaaaaaa', response), null);
});

test('pipeline distinguishes draft and text-approved readiness', () => {
  assert.equal(deriveStyleBibleStageStatus({ active: false, hasScriptAnalysisArtifact: false, styleBibleArtifactId: null, styleBibleStatus: null }), 'locked');
  assert.equal(deriveStyleBibleStageStatus({ active: false, hasScriptAnalysisArtifact: true, styleBibleArtifactId: null, styleBibleStatus: null }), 'ready');
  assert.equal(deriveStyleBibleStageStatus({ active: false, hasScriptAnalysisArtifact: true, styleBibleArtifactId: 'artifact_aaaaaaaaaaaa', styleBibleStatus: 'draft' }), 'draft');
  assert.equal(deriveStyleBibleStageStatus({ active: false, hasScriptAnalysisArtifact: true, styleBibleArtifactId: 'artifact_aaaaaaaaaaaa', styleBibleStatus: 'text_approved' }), 'text_approved');
});

test('edit session contains editable fields only, detects changes, and rejects empty list items', () => {
  const current = artifact('project_a', 'artifact_aaaaaaaaaaaa');
  const editable = createStyleBibleEditableContent(current);
  assert.equal('lineage' in editable, false);
  assert.equal('first_mention_quote' in editable.characters[0], false);
  assert.equal('reference_images' in editable.characters[0], false);
  assert.deepEqual(editable.characters[0].physical_constraints, ['fixed adult height']);
  assert.equal(isStyleBibleEditableContentChanged(current, editable), false);
  editable.characters[0].label = 'Changed';
  assert.equal(isStyleBibleEditableContentChanged(current, editable), true);
  assert.equal(isStyleBibleEditableContentValid(editable), true);
  editable.characters[0].continuity_notes = [''];
  assert.equal(isStyleBibleEditableContentValid(editable), false);
});

test('cancel can restore the current revision by recreating editable state', () => {
  const current = artifact('project_a', 'artifact_aaaaaaaaaaaa', 'text_approved');
  const edited = createStyleBibleEditableContent(current);
  edited.characters[0].description = 'changed';
  const restored = createStyleBibleEditableContent(current);
  assert.equal(restored.characters[0].description, 'D');
  assert.equal(isStyleBibleEditableContentChanged(current, restored), false);
});
