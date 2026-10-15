import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { QueryClient } from '@tanstack/react-query';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';

let server;
let cachePublishedScenePair;
let sceneAssetsExecutionQueryKey;
let sceneAssetsWorkspaceQueryKey;

before(async () => {
  server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    logLevel: 'error',
    server: { middlewareMode: true },
    appType: 'custom',
  });
  ({
    cachePublishedScenePair,
    sceneAssetsExecutionQueryKey,
    sceneAssetsWorkspaceQueryKey,
  } = await server.ssrLoadModule('/src/features/studio/sceneAssetsQueryCache.ts'));
});

after(async () => {
  if (server) await server.close();
});

test('publishing a new Scene/Visual pair removes the old Scene Assets lineage cache', () => {
  const queryClient = new QueryClient();
  const oldWorkspaceKey = sceneAssetsWorkspaceQueryKey('project_a', 'prompt_bundle_a');
  const oldExecutionKey = sceneAssetsExecutionQueryKey('project_a', 'prompt_bundle_a');
  queryClient.setQueryData(oldWorkspaceKey, {
    current_prompt_bundle_artifact_id: 'prompt_bundle_a',
    scenes: [{ beats: [{ candidates: [{ artifact_id: 'old_image_a' }] }] }],
  });
  queryClient.setQueryData(oldExecutionKey, { active: false, operations: [] });

  cachePublishedScenePair(queryClient, 'project_a', {
    scene_plan_artifact_id: 'scene_plan_b',
    scene_plan: { scene_plan_revision_id: 'scene_plan_b' },
    visual_plan_artifact_id: 'visual_plan_b',
    visual_plan: { visual_plan_revision_id: 'visual_plan_b' },
  });

  assert.equal(queryClient.getQueryData(oldWorkspaceKey), undefined);
  assert.equal(queryClient.getQueryData(oldExecutionKey), undefined);
  assert.equal(
    queryClient.getQueryData(['studio-scene-breakdown-v4', 'project_a', 'scene_plan_b', 'visual_plan_b']).visual_plan_artifact_id,
    'visual_plan_b',
  );

  const newWorkspaceKey = sceneAssetsWorkspaceQueryKey('project_a', 'prompt_bundle_b');
  assert.notDeepEqual(newWorkspaceKey, oldWorkspaceKey);
  queryClient.setQueryData(newWorkspaceKey, {
    current_prompt_bundle_artifact_id: 'prompt_bundle_b',
    scenes: [{ beats: [{ candidates: [] }] }],
  });
  assert.equal(JSON.stringify(queryClient.getQueryData(newWorkspaceKey)).includes('old_image_a'), false);
  queryClient.clear();
});
