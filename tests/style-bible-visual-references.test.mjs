import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const entrypoint = readFileSync(new URL('../src/features/studio/StyleBibleVisualReferencesWorkspace.tsx', import.meta.url), 'utf8');
const workspace = readFileSync(new URL('../src/features/studio/StyleBibleVisualReferencesWorkspaceV2.tsx', import.meta.url), 'utf8');
const panel = readFileSync(new URL('../src/features/studio/StyleBiblePanel.tsx', import.meta.url), 'utf8');
const client = readFileSync(new URL('../src/api/visualReferencesV2.ts', import.meta.url), 'utf8');

test('Style Bible canonical visual-reference entrypoint delegates to durable V2 workspace', () => {
  assert.match(panel, /StyleBibleVisualReferencesWorkspace/);
  assert.match(panel, /styleBibleArtifactId=\{styleBibleResult!\.artifact_id\}/);
  assert.match(entrypoint, /StyleBibleVisualReferencesWorkspaceV2/);
  assert.match(entrypoint, /<StyleBibleVisualReferencesWorkspaceV2/);
  assert.doesNotMatch(entrypoint, /getVisualReferencesV4|generateVisualReferencesV4|getVisualReferenceJobV4/);
  assert.match(workspace, /data-testid="style-bible-visual-references-v2"/);
});

test('V2 workspace exposes four anchor categories and durable generation actions', () => {
  assert.match(workspace, /Generate Reference Set/);
  assert.match(workspace, /Retry Failed/);
  assert.match(workspace, /Visual Reference category/);
  assert.match(workspace, /character: '1:1'/);
  assert.match(workspace, /location: '16:9'/);
  assert.match(workspace, /prop: '1:1'/);
  assert.match(workspace, /recurring_visual_subject: '1:1'/);
  assert.match(workspace, /candidate_count: 1/);
  assert.match(workspace, /aspect_ratios_by_anchor_type: DEFAULT_RATIOS/);
  assert.match(workspace, /visualReferencesV2Client\.submitGeneration/);
  assert.match(workspace, /visualReferencesV2Client\.retryFailed/);
  assert.match(workspace, /visualReferencesV2Client\.regenerateAnchor/);
  assert.doesNotMatch(workspace, /generateVisualReferencesV4|regenerateVisualReferenceV4/);
});

test('V2 candidate selection, upload, prompt edit, approval, and clear are anchor-local', () => {
  assert.match(workspace, /selectedByAnchor/);
  assert.match(workspace, /anchor\.candidate_id|candidate\.candidate_id/);
  assert.match(workspace, /visualReferencesV2Client\.upload/);
  assert.match(workspace, /visualReferencesV2Client\.updatePrompt/);
  assert.match(workspace, /visualReferencesV2Client\.approve/);
  assert.match(workspace, /visualReferencesV2Client\.clearApproval/);
  assert.match(workspace, /anchor\.version/);
  assert.match(workspace, /refreshAnchor\(variables\.anchor\.anchor_type, variables\.anchor\.anchor_id\)/);
  assert.doesNotMatch(workspace, /expected_approval_set_artifact_id/);
  assert.doesNotMatch(workspace, /approveVisualReferenceV4|clearVisualReferenceV4/);
});

test('frontend V2 API exposes durable workspace, execution, mutation, and CAS contracts', () => {
  for (const token of [
    'VisualReferenceV2Workspace',
    'VisualReferenceV2Anchor',
    'VisualReferenceV2Execution',
    'VisualReferenceV2OperationSubmission',
    'getWorkspace',
    'getAnchor',
    'getExecutions',
    'submitGeneration',
    'regenerateAnchor',
    'retryFailed',
    'updatePrompt',
    'upload',
    'approve',
    'clearApproval',
    'cancelOperation',
    'patchVisualReferenceAnchor',
  ]) {
    assert.match(client, new RegExp(token));
  }
  assert.match(client, /expected_version: expectedVersion/);
  assert.match(client, /candidate_id: candidateId/);
  assert.match(client, /visual-references\/v2\/executions/);
  assert.match(client, /visual-references\/v2\/anchors\/\$\{anchorType\}\/\$\{anchorId\}/);
  assert.doesNotMatch(client, /getVisualReferenceJobV4|VisualReferenceJobStatusResponseV4/);
});
