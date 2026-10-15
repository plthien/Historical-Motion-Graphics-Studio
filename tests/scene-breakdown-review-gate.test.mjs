import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const read = (path) => readFileSync(resolve(process.cwd(), path), 'utf8');
const panel = read('src/features/studio/SceneBreakdownPanel.tsx');
const studio = read('src/pages/StudioPage.tsx');
const client = read('src/api/client.ts');

test('Scene Breakdown mounts one active section as scene cards and opens beat details in a drawer', () => {
  assert.match(panel, /scene-breakdown-section-selector/);
  assert.match(panel, /scene-breakdown-card-grid/);
  assert.match(panel, /scene-breakdown-scene-card/);
  assert.match(panel, /scene-breakdown-drawer/);
  assert.match(panel, /scene-breakdown-beat-card/);
  assert.doesNotMatch(panel, /Expand All|Collapse All/);
});

test('Scene Breakdown JSON edit requires validation before save', () => {
  assert.match(panel, /Post-authoring review edits still operate on the published draft pair/);
  assert.match(panel, /Keys, scene count, beat count, array order/);
  assert.match(panel, /Beat .*script_text.* source span.* immutable/);
  assert.match(panel, /rebuild\/repartition Step 1/);
  assert.match(panel, /validatedText/);
  assert.match(panel, /validationArtifactId/);
  assert.match(panel, /editor\.validatedText !== editor\.text/);
  assert.match(panel, /Save Section/);
  assert.match(panel, /Validate/);
});

test('frontend calls dedicated validate and save endpoints', () => {
  assert.match(client, /validateSceneBreakdownSectionEditV4/);
  assert.match(client, /saveSceneBreakdownSectionEditV4/);
  assert.match(client, /review\/sections\/\$\{sectionId\}\/validate/);
  assert.match(client, /review\/sections\/\$\{sectionId\}/);
  assert.match(studio, /onValidateSection=\{validateSceneSectionEdit\}/);
  assert.match(studio, /onSaveSection=\{saveSceneSectionEdit\}/);
});

test('JSON editor emits only raw external-authoring visual fields', () => {
  const start = panel.indexOf('function visualForEdit');
  const end = panel.indexOf('function editableSection', start);
  const projection = panel.slice(start, end);
  for (const required of ['visual_concept', 'subject_action', 'environment_emphasis', 'visible_text', 'composition', 'continuity_with_previous', 'previous_beat_image_reference_required']) {
    assert.match(projection, new RegExp(required));
  }
  for (const backendDerived of ['motion_guidance', 'continuity_notes', 'production_notes', 'negative_requirements', 'selected_native_visual_reference_ids']) {
    assert.doesNotMatch(projection, new RegExp(backendDerived));
  }
});
