import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const read = (path) => readFileSync(resolve(process.cwd(), path), 'utf8');
const panel = read('src/features/studio/SceneAssetsPanel.tsx');
const studio = read('src/pages/StudioPage.tsx');
const v2Client = read('src/api/sceneAssetsV2.ts');
const progress = read('src/features/projects/projectProgress.ts');
const stageTypes = read('src/features/studio/types.ts');

test('Scene Assets follows Scene Breakdown while Prompt Bundle stays hidden', () => {
  assert.match(studio, /id: 'scene_assets'/);
  assert.match(studio, /label: 'Scene Assets'/);
  assert.match(studio, /project\?\.prompt_bundle_artifact_id \? 'ready' : 'locked'/);
  assert.match(studio, /<SceneAssetsPanel/);
  assert.match(progress, /'Scene Assets'/);
  assert.match(progress, /prompt_bundle_artifact_id/);
  assert.doesNotMatch(stageTypes, /'prompt'/);
  assert.doesNotMatch(studio, /PromptComposerPanel|id: 'prompt'|label: 'Prompt Bundle'/);
});

test('Scene Assets V2 keeps V1 review presentation while exposing V2 actions', () => {
  for (const text of [
    'Phase 11',
    'Scene Assets',
    'Review one generated image per beat in script order.',
    'Generate All Remaining',
    'Generate Missing',
    'Retry All Failed',
    'Publish Scene Assets',
    'Generate This Section',
    'Regenerate This Section',
    'Retry Failed in Section',
    'Generate Image',
    'Retry Image',
    'Regenerate Image',
    'Upload Image',
    'Clear Approval',
    'Review details',
    'Approve Beat Image',
    'Save Prompt',
    'Restore Compiled Prompt',
    'Set as Current Draft',
    'Approve',
    'Reject',
    'No generated image',
  ]) assert.match(panel, new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));

  assert.match(panel, /scene-assets-summary/);
  assert.match(panel, /scene-assets-section-selector/);
  assert.match(panel, /scene-assets-section-actions/);
  assert.match(panel, /scene-asset-candidate-strip/);
  assert.match(panel, /scene-asset-beat-details/);
  assert.match(panel, /ACTIVE_OPERATION_STATUSES/);
  assert.match(panel, /active_operation_beat_id/);
  assert.match(panel, /beat\.can_generate/);
  assert.match(panel, /beat\.can_retry/);
  assert.match(panel, /beat\.can_regenerate/);
  assert.match(panel, /beat\.can_review/);
  assert.match(panel, /beat\.can_upload/);
  assert.match(panel, /generated_from_current_prompt/);
  assert.match(panel, /beat\.prompt_revision_id/);
  assert.doesNotMatch(panel, /current_prompt_bundle_artifact_id/);
  assert.match(panel, /published_manifest_artifact_id/);
  assert.doesNotMatch(panel, /current_manifest|resume_processing|waiting_for_media|retry_remaining|rebuildSceneAssetPromptV4/);
});

test('frontend Scene Assets panel uses only the dedicated V2 client and current V2 routes', () => {
  assert.match(panel, /sceneAssetsV2Client/);
  assert.doesNotMatch(panel, /apiClient\./);
  for (const method of [
    'getWorkspace',
    'getBeat',
    'getExecutionStatus',
    'submitBulk',
    'generateBeat',
    'retryBeat',
    'regenerateBeat',
    'cancelOperation',
    'upload',
    'decide',
    'publishManifest',
    'editPrompt',
    'restorePrompt',
  ]) assert.match(v2Client, new RegExp(`${method}\\(`));
  assert.match(v2Client, /\/scene-assets/);
  assert.match(v2Client, /expected_beat_version/);
  assert.match(v2Client, /expected_prompt_revision_id/);
  assert.doesNotMatch(v2Client, /bindExistingSceneAsset/);
});

test('Scene Assets V2 mounts one V1-style section workspace without duplicating the tree', () => {
  assert.match(panel, /selectedSection/);
  assert.match(panel, /const sectionSummaries = useMemo/);
  assert.match(panel, /activeSection/);
  assert.match(panel, /data-section-key/);
  assert.match(panel, /selectSection/);
  assert.match(panel, /operationType: 'generate_section', sectionId: activeSection\.sectionId/);
  assert.match(panel, /Scene Asset section/);
  assert.match(panel, /data-filter=/);
  assert.doesNotMatch(panel, /<select/);
  assert.doesNotMatch(panel, /sectionGroups\.map/);
});

test('Scene Assets V2 uses V1 thumbnail-first scene-beat cards with lazy candidates and expandable details', () => {
  assert.match(panel, /mountedScenes\.map/);
  assert.match(panel, /scene\.beats\.map/);
  assert.match(panel, /primaryCandidate\(beat\)/);
  assert.match(panel, /loading="lazy"/);
  assert.match(panel, /sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4/);
  assert.match(panel, /lg:grid-cols-\[minmax\(280px,420px\)_minmax\(0,1fr\)\]/);
  assert.match(panel, /visibleSecondaryCandidates\.map/);
  assert.match(panel, /beat\.candidates\.map/);
  assert.match(panel, /Visual concept/);
  assert.match(panel, /Subject action/);
  assert.match(panel, /Prompt hash/);
  assert.match(panel, /Approval impact/);
  assert.match(panel, /getCandidateContentUrl/);
});
