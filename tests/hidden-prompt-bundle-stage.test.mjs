import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
const read = (path) => readFileSync(resolve(process.cwd(), path), 'utf8');
const studio = read('src/pages/StudioPage.tsx');
const scene = read('src/features/studio/SceneBreakdownPanel.tsx');
const stages = read('src/features/studio/types.ts');
const client = read('src/api/client.ts');

test('Prompt Bundle remains backend-owned but is absent from Studio navigation', () => {
  assert.doesNotMatch(stages, /'prompt'/);
  assert.doesNotMatch(studio, /PromptComposerPanel|id: 'prompt'|label: 'Prompt Bundle'/);
  assert.equal(existsSync(resolve(process.cwd(), 'src/features/studio/PromptComposerPanel.tsx')), false);
  assert.match(client, /getPromptBundleV4/);
  assert.match(client, /prompt_bundle_artifact_id/);
});

test('Scene Breakdown approval owns aspect ratio and automatic prompt compilation UX', () => {
  assert.match(scene, /Image aspect ratio/);
  assert.match(scene, /Approve & Build Prompts/);
  assert.match(studio, /aspect_ratio: imageAspectRatio/);
  assert.match(studio, /project\?\.prompt_bundle_artifact_id/);
  assert.match(studio, /getPromptBundleV4\(projectId, signal\)/);
  assert.match(studio, /loaded\.artifact\.options\.aspect_ratio/);
});

test('active frontend has no asset classification or local-render workflow', () => {
  for (const source of [studio, scene, read('src/features/studio/SceneAssetsPanel.tsx'), read('src/features/studio/AssemblyPanel.tsx'), client]) {
    assert.doesNotMatch(source, /asset_strategy|asset_details|excluded_beats|transition_only|local_asset_ready|bindExistingSceneAsset/);
  }
});
