import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

function read(path) { return fs.readFileSync(new URL(`../${path}`, import.meta.url), 'utf8'); }

test('large production workspaces progressively mount expensive collections', () => {
  const hook = read('src/features/studio/useProgressiveLimit.ts');
  assert.match(hook, /visibleCount/);
  assert.match(hook, /showMore/);
  assert.match(hook, /showAll/);

  const assets = read('src/features/studio/SceneAssetsPanel.tsx');
  assert.match(assets, /useProgressiveLimit/);
  assert.match(assets, /SCENE_ASSET_INITIAL_SCENE_WINDOW = 4/);
  assert.match(assets, /COLLAPSED_SECONDARY_CANDIDATE_LIMIT = 2/);
  assert.match(assets, /filteredScenes\.slice\(0, sceneWindow\.visibleCount\)/);
  assert.match(assets, /scene-assets-progressive-window/);
  assert.match(assets, /visibleSecondaryCandidates/);
  assert.match(assets, /secondaryCandidates\.slice\(0, COLLAPSED_SECONDARY_CANDIDATE_LIMIT\)/);
  assert.match(assets, /loading="lazy"/);
  assert.match(assets, /contentVisibility: 'auto'/);

  const voice = read('src/features/studio/VoicePanel.tsx');
  assert.doesNotMatch(voice, /segmentWindow|voice-progressive-window|useProgressiveLimit/);
  assert.match(voice, /VoiceSectionRemediationList/);

  const assembly = read('src/features/studio/AssemblyPanel.tsx');
  assert.match(assembly, /slice\(0, beatWindow\.visibleCount\)/);
  assert.match(assembly, /assembly-progressive-window/);
});

test('task 9 keeps render polling bounded and Scene Assets V2 polling active-state scoped', () => {
  const assets = read('src/features/studio/SceneAssetsPanel.tsx');
  assert.match(assets, /const POLL_INTERVAL_MS = 2000/);
  assert.match(assets, /ACTIVE_OPERATION_STATUSES/);
  assert.match(assets, /refetchInterval/);
  assert.match(assets, /workspaceHasOwnedBeat|localOperationIds\.size/);
  assert.doesNotMatch(assets, /effective_poll_interval_seconds/);

  const render = read('src/features/studio/RenderPanel.tsx');
  assert.match(render, /refetchInterval/);
  assert.match(render, /\? 750 : false/);
});

test('Scene Assets UI copy stays free of mojibake glyphs', () => {
  const assets = read('src/features/studio/SceneAssetsPanel.tsx');
  assert.doesNotMatch(assets, /Â|Ã|â€¦|�/);
});

test('Scene Asset V2 candidate delivery avoids manifest-wide validation and enables browser reuse', () => {
  const route = read('app/api/routes/scene_assets_workspace_v2.py');
  assert.match(route, /SceneAssetCandidate/);
  assert.match(route, /SceneAssetCandidate\.project_id == project_id/);
  assert.match(route, /SceneAssetCandidate\.artifact_id == artifact_id/);
  assert.doesNotMatch(route, /load_scene_asset_manifest_for_asset_lookup|_read_scene_asset_manifest_payload/);
  assert.match(route, /Cache-Control/);
  assert.match(route, /immutable/);
  assert.match(route, /content_disposition_type="inline"/);
});
