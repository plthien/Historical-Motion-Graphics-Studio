import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const source = fs.readFileSync(new URL('../src/features/studio/SceneAssetsPanel.tsx', import.meta.url), 'utf8');

test('terminal scene asset beats refresh through the beat-local endpoint while an operation stays active', () => {
  assert.match(source, /const refreshBeat = async \(beatId: string\) => \{[\s\S]*sceneAssetsV2Client\.getBeat\(projectId, beatId\)/);
  assert.match(source, /terminalBeatIds\.push\(beat\.beat_id\)/);
  assert.match(source, /Promise\.allSettled\(terminalBeatIds\.map\(\(beatId\) => refreshBeat\(beatId\)\)\)/);
  assert.doesNotMatch(source, /terminalBeatObserved/);
});

test('beat mutations patch the workspace cache and refresh invalidated or direct-dependent beats locally', () => {
  assert.match(source, /onSuccess: \(response, variables\) => applyBeatMutationResponse/);
  assert.match(source, /const refreshTargets = new Set\(response\.invalidated_beat_ids\)/);
  assert.match(source, /directDependentBeatIds\(\[refreshDependentsOf\]\)/);
  assert.match(source, /refreshBeatIds\(refreshTargets\)/);
  assert.doesNotMatch(source, /applyBeatMutationResponse\(response\); void refreshWorkspace\(\)/);
});

test('section approval stays targeted and does not restore aggregate workspace reloads', () => {
  assert.match(source, /sceneAssetsV2Client\.approveSectionReady\(projectId, sectionId, beats\)/);
  assert.match(source, /response\.approved_beat_ids[\s\S]*response\.invalidated_beat_ids[\s\S]*directDependentBeatIds\(response\.approved_beat_ids\)/);
  const mutationStart = source.indexOf('const approveSection = useMutation');
  const mutationEnd = source.indexOf('const publish = useMutation', mutationStart);
  assert.ok(mutationStart >= 0 && mutationEnd > mutationStart);
  assert.doesNotMatch(source.slice(mutationStart, mutationEnd), /refreshWorkspace\(\)/);
});

test('prompt edits use beat-local refresh and operation boundaries retain full refresh', () => {
  assert.match(source, /savePrompt[\s\S]*await refreshBeat\(variables\.beatId\)/);
  assert.match(source, /restorePrompt[\s\S]*await refreshBeat\(variables\.beatId\)/);
  assert.match(source, /if \(!data\.active \|\| terminalOperationObserved\) \{[\s\S]*void refreshWorkspace\(\)/);
  assert.match(source, /registerOperation[\s\S]*invalidateQueries\(\{ queryKey: workspaceKey \}\)/);
});
