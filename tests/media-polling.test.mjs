import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const read = (path) => readFileSync(resolve(process.cwd(), path), 'utf8');
const sceneAssets = read('src/features/studio/SceneAssetsPanel.tsx');
const sceneAssetsApi = read('src/api/sceneAssetsV2.ts');
const styleBible = read('src/features/studio/StyleBibleVisualReferencesWorkspace.tsx');
const styleBibleV2 = read('src/features/studio/StyleBibleVisualReferencesWorkspaceV2.tsx');
const visualReferenceV2Api = read('src/api/visualReferencesV2.ts');
const progressPanel = read('src/features/studio/MediaGenerationProgressPanel.tsx');
const mediaService = read('app/modules/media/media_task_service.py');

test('Visual References V2 and Scene Assets V2 poll only durable execution state', () => {
  assert.match(styleBibleV2, /const POLL_INTERVAL_MS = 2000/);
  assert.match(styleBibleV2, /visualReferencesV2Client\.getExecutions/);
  assert.match(styleBibleV2, /refetchInterval: \(query\) =>/);
  assert.match(styleBibleV2, /shouldPollExecutions/);
  assert.doesNotMatch(styleBibleV2, /effective_poll_interval_seconds|media_progress|getVisualReferenceJobV4/);
  assert.doesNotMatch(styleBible, /refetchInterval|setInterval\s*\(/);

  assert.match(sceneAssets, /const POLL_INTERVAL_MS = 2000/);
  assert.match(sceneAssets, /ACTIVE_OPERATION_STATUSES/);
  assert.match(sceneAssets, /getExecutionStatus/);
  assert.match(sceneAssets, /refetchInterval/);
  assert.match(sceneAssets, /workspaceHasOwnedBeat|localOperationIds\.size/);
  assert.doesNotMatch(sceneAssets, /effective_poll_interval_seconds|media_progress/);
  assert.doesNotMatch(sceneAssets, /setInterval\s*\(/);

  assert.match(mediaService, /effective_media_poll_interval_seconds/);
  assert.match(mediaService, /next_poll_after_seconds[\s\S]*effective_media_poll_interval_seconds/);
});

test('generation progress UI distinguishes provider work inside durable media workflows', () => {
  for (const label of ['Waiting', 'Generating', 'Downloading', 'Downloaded', 'Failed']) {
    assert.match(progressPanel, new RegExp(label));
  }
  assert.match(progressPanel, /stored locally/);
  assert.match(progressPanel, /download failed/);
  assert.match(progressPanel, /Poll cycle interval/);
  assert.match(progressPanel, /effective_poll_interval_seconds/);
});

test('frontend contracts keep durable execution projections on Visual References V2 and Scene Assets V2', () => {
  assert.match(visualReferenceV2Api, /interface VisualReferenceV2Workspace/);
  assert.match(visualReferenceV2Api, /interface VisualReferenceV2Execution/);
  assert.match(visualReferenceV2Api, /interface VisualReferenceV2ExecutionTask/);
  assert.match(visualReferenceV2Api, /waiting_provider/);
  assert.match(visualReferenceV2Api, /getExecutions/);
  assert.doesNotMatch(visualReferenceV2Api, /effective_poll_interval_seconds|media_progress|getVisualReferenceJobV4/);

  assert.match(sceneAssetsApi, /interface SceneAssetV2ExecutionStatusResponse/);
  assert.match(sceneAssetsApi, /active_operation_count/);
  assert.match(sceneAssetsApi, /operations: SceneAssetV2OperationStatusResponse\[\]/);
  assert.match(sceneAssetsApi, /interface SceneAssetV2TaskStatus/);
  assert.match(sceneAssetsApi, /waiting_provider/);
  assert.doesNotMatch(sceneAssetsApi, /effective_poll_interval_seconds|media_progress/);
});
