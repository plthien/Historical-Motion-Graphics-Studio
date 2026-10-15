import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const workspace = readFileSync(new URL('../src/features/studio/StyleBibleVisualReferencesWorkspaceV2.tsx', import.meta.url), 'utf8');

test('V2 workspace polls executions only while durable work is active', () => {
  assert.match(workspace, /queryKey: workspaceKey/);
  assert.match(workspace, /queryKey: executionsKey/);
  assert.match(workspace, /shouldPollExecutions/);
  assert.match(workspace, /refetchInterval: \(query\) =>/);
  assert.match(workspace, /POLL_INTERVAL_MS/);
  const workspaceQueryStart = workspace.indexOf('const workspace = useQuery');
  const executionsQueryStart = workspace.indexOf('const executions = useQuery');
  const workspaceQuery = workspace.slice(workspaceQueryStart, executionsQueryStart);
  assert.doesNotMatch(workspaceQuery, /refetchInterval/);
  assert.doesNotMatch(workspace, /setInterval/);
});

test('terminal operations refresh exact affected anchors instead of the whole workspace', () => {
  assert.match(workspace, /refreshAnchorList/);
  assert.match(workspace, /visualReferencesV2Client\.getAnchor/);
  assert.match(workspace, /Promise\.all\(Array\.from\(unique\.values\(\)\)\.map/);
  assert.match(workspace, /patchVisualReferenceAnchor/);
  assert.match(workspace, /queryClient\.setQueryData<VisualReferenceV2Workspace>\(workspaceKey/);
  assert.doesNotMatch(workspace, /invalidateQueries\(\{ queryKey: workspaceKey \}\)[\s\S]{0,500}onSuccess/);
});

test('V2 working-state mutations use anchor version CAS and exact-anchor refresh', () => {
  assert.match(workspace, /visualReferencesV2Client\.updatePrompt[\s\S]*anchor\.version/);
  assert.match(workspace, /visualReferencesV2Client\.upload[\s\S]*anchor\.version/);
  assert.match(workspace, /visualReferencesV2Client\.approve[\s\S]*anchor\.version/);
  assert.match(workspace, /visualReferencesV2Client\.clearApproval[\s\S]*anchor\.version/);
  assert.match(workspace, /onSuccess: async \(_result, variables\) => refreshAnchor/);
});

test('V2 generation uses durable operations and shared active-operations drawer without V4 job polling', () => {
  assert.match(workspace, /visualReferencesV2Client\.submitGeneration/);
  assert.match(workspace, /visualReferencesV2Client\.regenerateAnchor/);
  assert.match(workspace, /visualReferencesV2Client\.retryFailed/);
  assert.match(workspace, /ActiveOperationsDrawer/);
  assert.match(workspace, /visualReferencesV2Client\.cancelOperation/);
  assert.doesNotMatch(workspace, /getVisualReferenceJobV4|visual-reference-job-v4|operation\.media_progress/);
});
