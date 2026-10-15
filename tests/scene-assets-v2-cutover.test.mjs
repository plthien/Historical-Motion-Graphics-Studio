import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

function source(path) {
  return readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');
}

test('Scene Assets UI no longer uses Manifest revision as live mutation token', () => {
  const panel = source('src/features/studio/SceneAssetsPanel.tsx');
  const client = source('src/api/sceneAssetsV2.ts');
  assert.equal(panel.includes('current_manifest_artifact_id'), false);
  assert.equal(panel.includes('expected_scene_asset_manifest_artifact_id'), false);
  assert.equal(client.includes('expected_scene_asset_manifest_artifact_id'), false);
  assert.match(panel, /Cancel/);
  assert.match(panel, /active_operation_beat_id/);
});

test('beat-local upload review and prompt mutations carry an optimistic BeatState version', () => {
  const client = source('src/api/sceneAssetsV2.ts');
  const schemas = source('app/api/scene_asset_workspace_v2_schemas.py');
  const runtime = source('app/modules/v4/scene_asset_workspace_v2_runtime.py');
  const prompt = source('app/modules/v4/scene_asset_prompt_v2.py');
  assert.match(client, /expected_beat_version/);
  assert.match(client, /resolveBeatVersion/);
  assert.match(schemas, /expected_beat_version: PositiveInt/);
  assert.match(runtime, /SCENE_ASSET_V2_BEAT_STATE_CONFLICT/);
  assert.match(prompt, /SCENE_ASSET_V2_BEAT_STATE_CONFLICT/);
});

test('bulk generation remains available while unrelated beat operations are active', () => {
  const panel = source('src/features/studio/SceneAssetsPanel.tsx');
  assert.match(panel, /!data\.actions\.can_generate_all \|\| bulkGenerate\.isPending/);
  assert.match(panel, /!data\.actions\.can_generate_missing \|\| bulkGenerate\.isPending/);
  assert.match(panel, /!data\.actions\.can_retry_failed \|\| bulkGenerate\.isPending/);
  assert.match(panel, /Generate This Section/);
  assert.doesNotMatch(panel, /can_generate_all[^\n]*approvalBlockedByActiveOperation/);
  assert.doesNotMatch(panel, /can_generate_missing[^\n]*approvalBlockedByActiveOperation/);
  assert.doesNotMatch(panel, /can_retry_failed[^\n]*approvalBlockedByActiveOperation/);
  assert.match(panel, /publishDisabled/);
});

test('execution polling remains active when any BeatState is owned, not only when the latest operation is active', () => {
  const panel = source('src/features/studio/SceneAssetsPanel.tsx');
  assert.match(panel, /workspaceHasOwnedBeat/);
  assert.match(panel, /active_operation_beat_id !== null/);
  assert.match(panel, /workspaceHasOwnedBeat \|\| localOperationIds\.size > 0/);
});

test('prompt mutation settlement cannot close a different beat editor', () => {
  const panel = source('src/features/studio/SceneAssetsPanel.tsx');
  const guardedSettles = panel.match(/if \(editingBeatId === variables\.beatId\) setEditingBeatId\(null\)/g) ?? [];
  assert.equal(guardedSettles.length, 2);
});

test('Cancel pending state is operation-local', () => {
  const panel = source('src/features/studio/SceneAssetsPanel.tsx');
  assert.match(panel, /cancelPendingOperationIds/);
  assert.match(panel, /cancelPendingOperationIds\.has\(operation\.operation_id\)/);
  assert.doesNotMatch(panel, /cancelling=\{cancelOperation\.isPending\}/);
});

test('V2 runtime and workspace routes own Scene Assets before legacy production routes', () => {
  const app = source('app/main.py');
  const workspaceIndex = app.indexOf('app.include_router(scene_assets_workspace_v2_router)');
  const runtimeIndex = app.indexOf('app.include_router(scene_assets_v2_router)');
  const legacyIndex = app.indexOf('app.include_router(production_v4_router)');
  assert.ok(workspaceIndex >= 0 && runtimeIndex >= 0 && legacyIndex >= 0);
  assert.ok(workspaceIndex < legacyIndex);
  assert.ok(runtimeIndex < legacyIndex);
});

test('V2 working mutations do not publish live Manifest revisions', () => {
  const workspace = source('app/modules/v4/scene_asset_workspace_v2.py');
  assert.equal(workspace.includes('expected_scene_asset_manifest_artifact_id'), false);
  assert.equal(workspace.includes('publish_scene_asset_manifest('), false);
  assert.match(workspace, /def publish_approved_manifest/);
  assert.match(workspace, /schema_version": "4\.0"/);
});

test('route-facing review mutation owns approval locking and base workspace has no duplicate mutation owner', () => {
  const workspace = source('app/modules/v4/scene_asset_workspace_v2.py');
  const runtime = source('app/modules/v4/scene_asset_workspace_v2_runtime.py');
  assert.equal(workspace.includes('def upload_candidate('), false);
  assert.equal(workspace.includes('def decide_candidate('), false);
  assert.match(runtime, /def upload_candidate\(/);
  assert.match(runtime, /def decide_candidate\(/);
  assert.match(runtime, /self\._is_locked\(state\) and decision != "clear-approval"/);
  assert.match(workspace, /"can_review": not active and not locked/);
});

test('candidate approval and publication revalidate continuity and immutable output', () => {
  const workspace = source('app/modules/v4/scene_asset_workspace_v2.py');
  assert.match(workspace, /_assert_candidate_current_continuity/);
  assert.match(workspace, /SceneAssetPublishedManifestV2Validator\(self\.session\)\.validate/);
  assert.match(workspace, /_owned_beat_state_ids/);
});

test('route facade no longer reintroduces beat-id versus operation-beat-id projection mismatch', () => {
  const runtime = source('app/modules/v4/scene_asset_workspace_v2_runtime.py');
  assert.equal(runtime.includes('select(SceneAssetOperationBeat.beat_id)'), false);
  assert.match(runtime, /class SceneAssetWorkspaceV2Service/);
});

test('Prompt mutation uses BeatState ownership instead of legacy Scene Asset lease', () => {
  const prompt = source('app/modules/v4/scene_asset_prompt_v2.py');
  assert.match(prompt, /active_operation_beat_id/);
  assert.match(prompt, /BEGIN IMMEDIATE/);
  assert.equal(prompt.includes('SceneAssetBeatOperationLeaseRepository'), false);
  assert.equal(prompt.includes('scene_asset_beat_operation_leases'), false);
});

test('production consumers reject legacy live manifests after V2 cutover', () => {
  const loader = source('app/modules/v4/production_artifact_loader.py');
  assert.match(loader, /schema_version.*4\.0/s);
  assert.match(loader, /SCENE_ASSET_MANIFEST_VERSION_UNSUPPORTED/);
  assert.match(loader, /SceneAssetPublishedManifestV2Validator/);
});

// Phase E review marker: this file intentionally contains only executable
// architecture assertions; Phase F adds runner/deletion/upgrade closure checks.
