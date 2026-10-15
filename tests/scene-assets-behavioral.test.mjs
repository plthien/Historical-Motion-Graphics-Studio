import assert from 'node:assert/strict';
import { after, before, beforeEach, test } from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';

let server;
let SceneAssetsPanel;
let sceneAssetsV2Client;
const originalClient = {};

const visualTreatment = {
  visual_concept: 'A clear visual concept.',
  subject_action: 'The subject performs one readable action.',
  environment_emphasis: 'Keep the approved environment readable.',
  framing: 'medium',
  camera_angle: 'eye_level',
  camera_movement: 'static',
  composition: 'Readable composition.',
  lighting_direction: 'soft',
  color_emphasis: 'approved palette',
  motion_guidance: 'Hold.',
  continuity_with_previous: 'new_setup',
  continuity_notes: 'Maintain identity.',
  production_notes: 'Render one still.',
  negative_requirements: [],
  visible_text: [],
};

function candidate(id, overrides = {}) {
  return {
    candidate_id: id,
    artifact_id: overrides.artifact_id ?? `artifact_${id}`,
    source_kind: overrides.source_kind ?? 'generated',
    mime_type: 'image/png',
    sha256: 'a'.repeat(64),
    width: 1280,
    height: 720,
    is_current_draft: overrides.is_current_draft ?? false,
    is_approved: overrides.is_approved ?? false,
    is_rejected: overrides.is_rejected ?? false,
    generated_from_current_prompt: overrides.generated_from_current_prompt ?? true,
  };
}

function beat(id, overrides = {}) {
  const candidates = overrides.candidates ?? [];
  return {
    scene_id: overrides.scene_id ?? 'scene_001',
    beat_id: id,
    beat_order: overrides.beat_order ?? 1,
    local_beat_order: overrides.local_beat_order ?? 1,
    computed_duration_seconds: 3,
    resolution_status: overrides.resolution_status ?? (candidates.length ? 'candidate_ready' : 'not_generated'),
    review_status: overrides.review_status ?? 'unreviewed',
    beat_state_version: overrides.beat_state_version ?? 1,
    active_operation_beat_id: overrides.active_operation_beat_id ?? null,
    current_draft_candidate_id: overrides.current_draft_candidate_id ?? null,
    selected_candidate_id: overrides.selected_candidate_id ?? null,
    prompt_revision_id: overrides.prompt_revision_id ?? `prompt_revision_${id}`,
    prompt_item_id: `prompt_${id}`,
    prompt_hash: `sha256:${id.padEnd(64, '0').slice(0, 64)}`,
    prompt_origin: overrides.prompt_origin ?? 'compiled',
    image_prompt: overrides.image_prompt ?? `Image prompt for ${id}`,
    negative_prompt: overrides.negative_prompt ?? 'No artifacts.',
    reference_bindings: [],
    treatment: visualTreatment,
    script_quote: overrides.script_quote ?? `Narration for ${id}.`,
    candidates,
    continuity: overrides.continuity ?? { required: false, source_beat_id: null, source_ready: true, source_candidate_id: null, block_reason: null },
    approval_change_impact_count: overrides.approval_change_impact_count ?? 0,
    can_generate: overrides.can_generate ?? candidates.length === 0,
    can_retry: overrides.can_retry ?? false,
    can_regenerate: overrides.can_regenerate ?? candidates.length > 0,
    can_upload: overrides.can_upload ?? true,
    can_review: overrides.can_review ?? candidates.length > 0,
    can_approve: overrides.can_approve ?? candidates.length > 0,
    error: overrides.error ?? null,
  };
}

function scene(id, beats, overrides = {}) {
  return {
    scene_id: id,
    scene_order: overrides.scene_order ?? 1,
    section_id: overrides.section_id ?? 'sec_001',
    section_order: overrides.section_order ?? 1,
    section_title: overrides.section_title ?? 'Section One',
    beats: beats.map((entry) => ({ ...entry, scene_id: id })),
  };
}

function workspace(scenes, actionOverrides = {}, latestOperation = null, workspaceOverrides = {}) {
  return {
    project_id: 'project_a',
    readiness: { ready: true, blockers: [] },
    current_prompt_bundle_artifact_id: 'artifact_prompt_bundle',
    published_manifest_artifact_id: null,
    published_manifest_status: null,
    manifest_state: 'unpublished',
    ...workspaceOverrides,
    latest_operation: latestOperation,
    scenes,
    actions: {
      can_generate_all: true,
      can_generate_missing: true,
      can_retry_failed: false,
      can_approve_manifest: false,
      can_publish_manifest: false,
      eligible_generate_count: 1,
      failed_count: 0,
      approved_count: 0,
      total_count: 1,
      ...actionOverrides,
    },
  };
}

function submission(operationId = 'operation_new', beatIds = ['beat_001']) {
  return {
    project_id: 'project_a',
    operation_id: operationId,
    operation_type: 'generate_missing',
    status: 'queued',
    total_beat_count: beatIds.length,
    scheduled_beat_ids: beatIds,
    blocked_beat_ids: [],
    next_reconcile_at: new Date().toISOString(),
  };
}

function operation() {
  return {
    project_id: 'project_a', operation_id: 'operation_001', operation_type: 'generate_missing', status: 'running',
    total_beat_count: 1, completed_count: 0, failed_count: 0, cancelled_count: 0,
    next_reconcile_at: null, cancel_requested_at: null, completed_at: null, error_code: null, error_message: null,
    beats: [{ operation_beat_id: 'operation_beat_001', beat_id: 'beat_001', status: 'running', candidate_count: 1, tasks: [], error_code: null, error_message: null }],
  };
}

function textOf(node) {
  if (node == null) return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textOf).join(' ');
  return textOf(node.children ?? []);
}

const beatArticle = (renderer, beatId) => renderer.root.find((node) => node.type === 'article' && node.props['data-beat-id'] === beatId);
const actionButton = (renderer, action) => renderer.root.find((node) => node.type === 'button' && node.props['data-action'] === action);
const actionButtonIn = (node, action) => node.find((child) => child.type === 'button' && child.props['data-action'] === action);
const textButton = (renderer, label) => renderer.root.find((node) => node.type === 'button' && textOf(node).trim() === label);
const textButtonIn = (node, label) => node.find((child) => child.type === 'button' && textOf(child).trim() === label);

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function renderPanel(workspaceValue, executionValue = { project_id: 'project_a', active: false, active_operation_count: 0, operations: [] }) {
  sceneAssetsV2Client.getWorkspace = async () => workspaceValue;
  sceneAssetsV2Client.getExecutionStatus = async () => executionValue;
  sceneAssetsV2Client.getCandidateContentUrl = (_projectId, artifactId) => `https://example.test/${artifactId}`;
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  queryClient.setQueryData(['scene-assets-v2-workspace', 'project_a', 'artifact_prompt_bundle'], workspaceValue);
  queryClient.setQueryData(['scene-assets-v2-execution', 'project_a', 'artifact_prompt_bundle'], executionValue);
  let renderer;
  await act(async () => { renderer = create(React.createElement(QueryClientProvider, { client: queryClient }, React.createElement(SceneAssetsPanel, { projectId: 'project_a', promptBundleArtifactId: 'artifact_prompt_bundle', enabled: true }))); });
  await flush();
  return { renderer, queryClient, cleanup() { renderer.unmount(); queryClient.clear(); } };
}

before(async () => {
  server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), logLevel: 'error', server: { middlewareMode: true }, appType: 'custom' });
  ({ sceneAssetsV2Client } = await server.ssrLoadModule('/src/api/sceneAssetsV2.ts'));
  ({ SceneAssetsPanel } = await server.ssrLoadModule('/src/features/studio/SceneAssetsPanel.tsx'));
  for (const key of ['getWorkspace', 'getBeat', 'getExecutionStatus', 'getCandidateContentUrl', 'submitBulk', 'generateBeat', 'retryBeat', 'regenerateBeat', 'cancelOperation', 'upload', 'decide', 'publishManifest', 'editPrompt', 'restorePrompt']) originalClient[key] = sceneAssetsV2Client[key];
});

beforeEach(() => { for (const [key, value] of Object.entries(originalClient)) sceneAssetsV2Client[key] = value; });
after(async () => { if (server) await server.close(); });

test('Scene Assets V2 renders the V1 review shell with V2 actions and a reviewable candidate', async () => {
  const current = candidate('candidate_001', { is_current_draft: true });
  const { renderer, cleanup } = await renderPanel(workspace([scene('scene_001', [beat('beat_001', { candidates: [current], current_draft_candidate_id: current.candidate_id })])], { eligible_generate_count: 0 }));
  const text = textOf(renderer.toJSON());
  for (const expected of ['Phase 11', 'Scene Assets', 'Generate All Remaining', 'Publish Scene Assets', 'beat_001', 'Current draft', 'Regenerate Image', 'Review details']) assert.match(text, new RegExp(expected));
  cleanup();
});
test('manifest publication CTA reflects unpublished, current, and stale backend state', async () => {
  const approved = candidate('candidate_approved', { is_current_draft: true, is_approved: true });
  const approvedBeat = beat('beat_001', { candidates: [approved], current_draft_candidate_id: approved.candidate_id, selected_candidate_id: approved.candidate_id, resolution_status: 'approved', review_status: 'approved', can_generate: false, can_retry: false, can_regenerate: false, can_upload: false, can_review: false, can_approve: false });

  let rendered = await renderPanel(workspace([scene('scene_001', [approvedBeat])], { eligible_generate_count: 0, approved_count: 1, can_approve_manifest: true, can_publish_manifest: true }));
  assert.match(textOf(rendered.renderer.toJSON()), /Publish Scene Assets/);
  assert.doesNotMatch(textOf(rendered.renderer.toJSON()), /Approve Manifest/);
  rendered.cleanup();

  rendered = await renderPanel(workspace([scene('scene_001', [approvedBeat])], { eligible_generate_count: 0, approved_count: 1, can_approve_manifest: false, can_publish_manifest: false }, null, { manifest_state: 'current', published_manifest_artifact_id: 'artifact_manifest_current_001', published_manifest_status: 'approved' }));
  const published = actionButton(rendered.renderer, 'publish-scene-assets');
  assert.equal(textOf(published).trim(), 'Published');
  assert.equal(published.props.disabled, true);
  assert.match(textOf(rendered.renderer.toJSON()), /Manifest:\s+artifact_manifest/);
  assert.match(textOf(rendered.renderer.toJSON()), /Scene Assets published/);
  rendered.cleanup();

  rendered = await renderPanel(workspace([scene('scene_001', [approvedBeat])], { eligible_generate_count: 0, approved_count: 1, can_approve_manifest: true, can_publish_manifest: true }, null, { manifest_state: 'stale', published_manifest_artifact_id: 'artifact_manifest_stale_001', published_manifest_status: 'approved' }));
  const republish = actionButton(rendered.renderer, 'publish-scene-assets');
  assert.equal(textOf(republish).trim(), 'Republish Scene Assets');
  assert.equal(republish.props.disabled, false);
  assert.match(textOf(rendered.renderer.toJSON()), /Manifest out of date/);
  rendered.cleanup();
});

test('beat mutation updates publication projection without a workspace reload', async () => {
  const approvedCandidate = candidate('candidate_approved', { is_current_draft: true, is_approved: true });
  const currentBeat = beat('beat_001', {
    candidates: [approvedCandidate],
    current_draft_candidate_id: approvedCandidate.candidate_id,
    selected_candidate_id: approvedCandidate.candidate_id,
    resolution_status: 'approved',
    review_status: 'approved',
    can_generate: false,
    can_retry: false,
    can_regenerate: false,
    can_upload: false,
    can_review: false,
    can_approve: false,
  });
  const currentWorkspace = workspace(
    [scene('scene_001', [currentBeat])],
    { eligible_generate_count: 0, approved_count: 1, can_approve_manifest: false, can_publish_manifest: false },
    null,
    { manifest_state: 'current', published_manifest_artifact_id: 'artifact_manifest_current_001', published_manifest_status: 'approved' },
  );
  const clearedBeat = beat('beat_001', {
    candidates: [candidate('candidate_approved', { is_current_draft: true })],
    current_draft_candidate_id: approvedCandidate.candidate_id,
    selected_candidate_id: null,
    resolution_status: 'candidate_ready',
    review_status: 'current_draft',
    can_generate: false,
    can_retry: false,
    can_regenerate: true,
    can_upload: true,
    can_review: true,
    can_approve: true,
  });
  sceneAssetsV2Client.decide = async () => ({
    project_id: 'project_a',
    published_manifest_artifact_id: 'artifact_manifest_current_001',
    published_manifest_status: 'approved',
    manifest_state: 'stale',
    beat: clearedBeat,
    invalidated_beat_ids: [],
    actions: { ...currentWorkspace.actions, approved_count: 0, can_approve_manifest: false, can_publish_manifest: false },
  });

  const { renderer, queryClient, cleanup } = await renderPanel(currentWorkspace);
  let workspaceCalls = 0;
  sceneAssetsV2Client.getWorkspace = async () => {
    workspaceCalls += 1;
    return currentWorkspace;
  };

  assert.equal(textOf(actionButton(renderer, 'publish-scene-assets')).trim(), 'Published');
  await act(async () => textButtonIn(beatArticle(renderer, 'beat_001'), 'Clear Approval').props.onClick());
  await flush();

  assert.equal(workspaceCalls, 0);
  assert.equal(queryClient.getQueryData(['scene-assets-v2-workspace', 'project_a', 'artifact_prompt_bundle']).manifest_state, 'stale');
  assert.equal(textOf(actionButton(renderer, 'publish-scene-assets')).trim(), 'Republish Scene Assets');
  assert.match(textOf(renderer.toJSON()), /Manifest out of date/);
  cleanup();
});
test('Generate All Remaining submits one V2 generate_all operation and starts execution tracking', async () => {
  let submitted = null;
  sceneAssetsV2Client.submitBulk = async (...args) => { submitted = args; return submission(); };
  const { renderer, cleanup } = await renderPanel(workspace([scene('scene_001', [beat('beat_001')])]));
  await act(async () => actionButton(renderer, 'generate-all-remaining').props.onClick());
  await flush();
  assert.deepEqual(submitted, ['project_a', 'generate_all', 1, null]);
  cleanup();
});

test('exact generation busy state is beat-local and leaves another ready beat actionable', async () => {
  let release;
  sceneAssetsV2Client.generateBeat = async () => new Promise((resolve) => { release = () => resolve(submission('operation_exact', ['beat_001'])); });
  const { renderer, cleanup } = await renderPanel(workspace([scene('scene_001', [beat('beat_001'), beat('beat_002', { beat_order: 2, local_beat_order: 2 })])], { eligible_generate_count: 2, total_count: 2 }));
  act(() => actionButtonIn(beatArticle(renderer, 'beat_001'), 'generate-image').props.onClick());
  await flush();
  assert.equal(actionButtonIn(beatArticle(renderer, 'beat_001'), 'generate-image').props.disabled, true);
  assert.equal(actionButtonIn(beatArticle(renderer, 'beat_002'), 'generate-image').props.disabled, false);
  await act(async () => release());
  await flush();
  cleanup();
});

test('active Operation status is rendered with beat progress and supports operation-local cancel', async () => {
  const op = operation();
  let cancelled = null;
  sceneAssetsV2Client.cancelOperation = async (projectId, operationId) => {
    cancelled = [projectId, operationId];
    return { project_id: projectId, operation_id: operationId, status: 'cancelled', already_cancelled: false, cancelled_task_count: 1, cancelled_beat_count: 1, completed_count: 0, failed_count: 0, cancelled_count: 1 };
  };
  const latest = { operation_id: op.operation_id, operation_type: op.operation_type, status: 'running', progress: 0, error_code: null, error_message: null };
  const state = workspace([scene('scene_001', [beat('beat_001', { active_operation_beat_id: 'operation_beat_001', resolution_status: 'generating', can_generate: false })])], {}, latest);
  const { renderer, cleanup } = await renderPanel(state, { project_id: 'project_a', active: true, active_operation_count: 1, operations: [op] });
  assert.match(textOf(renderer.toJSON()), /Active operations/);
  await act(async () => textButton(renderer, 'Cancel').props.onClick());
  await flush();
  assert.deepEqual(cancelled, ['project_a', 'operation_001']);
  cleanup();
});

test('active terminal beat refreshes only the exact beat while the operation remains active', async () => {
  const ready = candidate('candidate_ready_001', { is_current_draft: true });
  const staleWorkspace = workspace([scene('scene_001', [
    beat('beat_001', { active_operation_beat_id: 'operation_beat_001', resolution_status: 'generating', can_generate: false, can_upload: false }),
    beat('beat_002', { beat_order: 2, local_beat_order: 2, active_operation_beat_id: 'operation_beat_002', resolution_status: 'generating', can_generate: false, can_upload: false }),
  ])], { eligible_generate_count: 0, total_count: 2 }, { operation_id: 'operation_001', operation_type: 'generate_missing', status: 'running', progress: 0, error_code: null, error_message: null });
  const refreshedBeat = beat('beat_001', {
    candidates: [ready],
    current_draft_candidate_id: ready.candidate_id,
    resolution_status: 'candidate_ready',
    review_status: 'current_draft',
    can_generate: false,
    can_upload: true,
  });
  const runningExecution = {
    project_id: 'project_a', active: true, active_operation_count: 1, operations: [{
      ...operation(), total_beat_count: 2,
      beats: [
        { operation_beat_id: 'operation_beat_001', beat_id: 'beat_001', status: 'running', candidate_count: 1, tasks: [], error_code: null, error_message: null },
        { operation_beat_id: 'operation_beat_002', beat_id: 'beat_002', status: 'running', candidate_count: 1, tasks: [], error_code: null, error_message: null },
      ],
    }],
  };
  const terminalExecution = {
    project_id: 'project_a', active: true, active_operation_count: 1, operations: [{
      ...runningExecution.operations[0], completed_count: 1,
      beats: [
        { ...runningExecution.operations[0].beats[0], status: 'candidate_ready' },
        runningExecution.operations[0].beats[1],
      ],
    }],
  };
  let workspaceCalls = 0;
  const beatCalls = [];
  const { renderer, queryClient, cleanup } = await renderPanel(staleWorkspace, runningExecution);
  sceneAssetsV2Client.getWorkspace = async () => { workspaceCalls += 1; return staleWorkspace; };
  sceneAssetsV2Client.getBeat = async (projectId, beatId) => {
    beatCalls.push([projectId, beatId]);
    return { project_id: projectId, beat: refreshedBeat, actions: staleWorkspace.actions };
  };

  await act(async () => {
    queryClient.setQueryData(['scene-assets-v2-execution', 'project_a', 'artifact_prompt_bundle'], terminalExecution);
  });
  await flush();
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 25)); });
  await flush();

  assert.equal(workspaceCalls, 0);
  assert.deepEqual(beatCalls, [['project_a', 'beat_001']]);
  const text = textOf(renderer.toJSON());
  assert.match(text, /Ready to review/);
  assert.match(text, /candidate ready/);
  assert.match(text, /Current draft/);
  cleanup();
});
test('candidate decisions use candidate identity and remain local to the selected beat', async () => {
  const calls = [];
  sceneAssetsV2Client.decide = async (...args) => { calls.push(args); return {}; };
  const state = workspace([scene('scene_001', [beat('beat_001', { candidates: [candidate('candidate_001')] }), beat('beat_002', { beat_order: 2, local_beat_order: 2, candidates: [candidate('candidate_002')] })])], { eligible_generate_count: 0, total_count: 2 });
  const { renderer, cleanup } = await renderPanel(state);
  await act(async () => actionButtonIn(beatArticle(renderer, 'beat_001'), 'approve-beat-image').props.onClick());
  await flush();
  assert.deepEqual(calls[0], ['project_a', 'beat_001', 'candidate_001', 'approve']);
  assert.match(textOf(beatArticle(renderer, 'beat_002')), /beat_002/);
  cleanup();
});

test('approval response updates the beat card before workspace refetch settles', async () => {
  const draft = candidate('candidate_001', { is_current_draft: true });
  const approved = candidate('candidate_001', { is_current_draft: true, is_approved: true });
  const initial = workspace([scene('scene_001', [beat('beat_001', {
    candidates: [draft],
    current_draft_candidate_id: draft.candidate_id,
    resolution_status: 'candidate_ready',
    review_status: 'current_draft',
  })])], { eligible_generate_count: 0 });
  const approvedBeat = beat('beat_001', {
    candidates: [approved],
    current_draft_candidate_id: approved.candidate_id,
    selected_candidate_id: approved.candidate_id,
    resolution_status: 'approved',
    review_status: 'approved',
    can_generate: false,
    can_retry: false,
    can_regenerate: false,
    can_upload: false,
    can_review: false,
    can_approve: false,
  });
  const fresh = workspace([scene('scene_001', [approvedBeat])], { eligible_generate_count: 0, approved_count: 1, can_approve_manifest: true });
  sceneAssetsV2Client.decide = async (...args) => ({
    project_id: args[0],
    beat: approvedBeat,
    invalidated_beat_ids: [],
    actions: fresh.actions,
  });
  const { renderer, cleanup } = await renderPanel(initial);
  sceneAssetsV2Client.getWorkspace = async () => fresh;

  await act(async () => actionButtonIn(beatArticle(renderer, 'beat_001'), 'approve-beat-image').props.onClick());
  await flush();

  const article = beatArticle(renderer, 'beat_001');
  assert.equal(article.findAll((node) => node.type === 'button' && node.props['data-action'] === 'approve-beat-image').length, 0);
  assert.match(textOf(article), /Clear Approval/);
  assert.match(textOf(article), /approved/);
  cleanup();
});
test('approved beat keeps the V1 disabled regenerate control until approval is cleared', async () => {
  const approved = candidate('candidate_approved', { is_current_draft: true, is_approved: true });
  let decision = null;
  sceneAssetsV2Client.decide = async (...args) => { decision = args; return {}; };
  const state = workspace([scene('scene_001', [beat('beat_001', { candidates: [approved], current_draft_candidate_id: approved.candidate_id, selected_candidate_id: approved.candidate_id, resolution_status: 'approved', review_status: 'approved', can_generate: false, can_retry: false, can_regenerate: false })])], { eligible_generate_count: 0, approved_count: 1, can_approve_manifest: true });
  const { renderer, cleanup } = await renderPanel(state);
  const article = beatArticle(renderer, 'beat_001');
  assert.equal(article.findAll((node) => node.type === 'button' && node.props['data-action'] === 'generate-image').length, 0);
  assert.equal(article.findAll((node) => node.type === 'button' && node.props['data-action'] === 'retry-image').length, 0);
  assert.equal(actionButtonIn(article, 'regenerate-image').props.disabled, true);
  await act(async () => textButtonIn(article, 'Clear Approval').props.onClick());
  await flush();
  assert.deepEqual(decision, ['project_a', 'beat_001', 'candidate_approved', 'clear-approval']);
  cleanup();
});

test('continuity-invalidated beat with candidate history shows disabled Regenerate while source is unapproved', async () => {
  const historical = candidate('candidate_continuity_stale');
  const state = workspace([scene('scene_001', [beat('beat_continuous', {
    candidates: [historical],
    current_draft_candidate_id: null,
    selected_candidate_id: null,
    resolution_status: 'not_generated',
    review_status: 'unreviewed',
    can_generate: false,
    can_regenerate: false,
    can_upload: false,
    can_review: false,
    can_approve: false,
    continuity: { required: true, source_beat_id: 'beat_prev', source_ready: false, source_candidate_id: null, block_reason: 'source_not_approved' },
    error: { code: 'SCENE_ASSET_CONTINUITY_SOURCE_CHANGED', message: 'Approved continuity source changed; regenerate this beat.', details: { source_beat_id: 'beat_prev' } },
  })])], { eligible_generate_count: 0 });

  const { renderer, cleanup } = await renderPanel(state);
  const article = beatArticle(renderer, 'beat_continuous');
  assert.equal(article.findAll((node) => node.type === 'button' && node.props['data-action'] === 'generate-image').length, 0);
  assert.equal(actionButtonIn(article, 'regenerate-image').props.disabled, true);
  assert.match(textOf(article), /Waiting for approval of beat_prev/);
  assert.match(textOf(article), /SCENE_ASSET_CONTINUITY_SOURCE_CHANGED/);
  cleanup();
});

test('continuity-invalidated beat enables Regenerate after source is approved again and calls regenerateBeat', async () => {
  const historical = candidate('candidate_continuity_stale');
  const regenerateCalls = [];
  const generateCalls = [];
  sceneAssetsV2Client.regenerateBeat = async (...args) => {
    regenerateCalls.push(args);
    return submission('operation_regenerate_continuity', ['beat_continuous']);
  };
  sceneAssetsV2Client.generateBeat = async (...args) => {
    generateCalls.push(args);
    return submission('operation_generate_wrong', ['beat_continuous']);
  };
  const state = workspace([scene('scene_001', [beat('beat_continuous', {
    candidates: [historical],
    current_draft_candidate_id: null,
    selected_candidate_id: null,
    resolution_status: 'not_generated',
    review_status: 'unreviewed',
    can_generate: false,
    can_regenerate: true,
    can_upload: true,
    can_review: true,
    can_approve: false,
    continuity: { required: true, source_beat_id: 'beat_prev', source_ready: true, source_candidate_id: 'candidate_prev_new', block_reason: null },
    error: { code: 'SCENE_ASSET_CONTINUITY_SOURCE_CHANGED', message: 'Approved continuity source changed; regenerate this beat.', details: { source_beat_id: 'beat_prev' } },
  })])], { eligible_generate_count: 0 });

  const { renderer, cleanup } = await renderPanel(state);
  const article = beatArticle(renderer, 'beat_continuous');
  assert.equal(article.findAll((node) => node.type === 'button' && node.props['data-action'] === 'generate-image').length, 0);
  const regenerate = actionButtonIn(article, 'regenerate-image');
  assert.equal(regenerate.props.disabled, false);
  await act(async () => regenerate.props.onClick());
  await flush();
  assert.deepEqual(regenerateCalls, [['project_a', 'beat_continuous', 1]]);
  assert.deepEqual(generateCalls, []);
  cleanup();
});

test('manual prompt edit and restore use the current beat PromptRevision, not the aggregate Prompt Bundle', async () => {
  const current = candidate('candidate_001', { is_current_draft: true });
  const state = workspace([scene('scene_001', [beat('beat_001', { candidates: [current], current_draft_candidate_id: current.candidate_id, prompt_revision_id: 'prompt_revision_current_001', prompt_origin: 'manual_edit', image_prompt: 'Edited image prompt.', negative_prompt: 'Edited negatives.' })])], { eligible_generate_count: 0 });
  const edits = [];
  const restores = [];
  sceneAssetsV2Client.editPrompt = async (...args) => { edits.push(args); return {}; };
  sceneAssetsV2Client.restorePrompt = async (...args) => { restores.push(args); return {}; };
  const { renderer, cleanup } = await renderPanel(state);

  await act(async () => actionButtonIn(beatArticle(renderer, 'beat_001'), 'review-details').props.onClick());
  await act(async () => actionButtonIn(beatArticle(renderer, 'beat_001'), 'edit-beat-prompt').props.onClick());
  let article = beatArticle(renderer, 'beat_001');
  assert.equal(article.findAllByType('textarea').length, 2);
  await act(async () => textButtonIn(article, 'Save Prompt').props.onClick());
  await flush();
  assert.deepEqual(edits[0], ['project_a', 'beat_001', 'prompt_revision_current_001', 'Edited image prompt.', 'Edited negatives.']);
  assert.notEqual(edits[0][2], state.current_prompt_bundle_artifact_id);

  article = beatArticle(renderer, 'beat_001');
  await act(async () => actionButtonIn(article, 'restore-compiled-prompt').props.onClick());
  await flush();
  assert.deepEqual(restores[0], ['project_a', 'beat_001', 'prompt_revision_current_001']);
  assert.notEqual(restores[0][2], state.current_prompt_bundle_artifact_id);
  cleanup();
});

test('section generation submits only the selected V1-style section card scope', async () => {
  const state = workspace([
    scene('scene_001', [beat('beat_001')], { section_id: 'sec_001', section_title: 'One' }),
    scene('scene_002', [beat('beat_002')], { scene_order: 2, section_id: 'sec_002', section_order: 2, section_title: 'Two' }),
  ], { eligible_generate_count: 2, total_count: 2 });
  let submitted = null;
  sceneAssetsV2Client.submitBulk = async (...args) => { submitted = args; return submission('operation_section', ['beat_002']); };
  const { renderer, cleanup } = await renderPanel(state);
  const sectionButton = renderer.root.find((node) => node.type === 'button' && node.props['data-section-key'] === 'sec_002');
  await act(async () => sectionButton.props.onClick());
  await flush();
  await act(async () => actionButton(renderer, 'generate-section').props.onClick());
  await flush();
  assert.deepEqual(submitted, ['project_a', 'generate_section', 1, 'sec_002']);
  cleanup();
});

test('candidate images are lazy, bounded in the V1 candidate strip, and fully reviewable in details', async () => {
  const candidates = [candidate('candidate_001'), candidate('candidate_002'), candidate('candidate_003')];
  const { renderer, cleanup } = await renderPanel(workspace([scene('scene_001', [beat('beat_001', { candidates })])], { eligible_generate_count: 0 }));
  const article = beatArticle(renderer, 'beat_001');
  assert.equal(article.findAllByType('img').length, 3);
  for (const image of article.findAllByType('img')) assert.equal(image.props.loading, 'lazy');
  await act(async () => actionButtonIn(article, 'review-details').props.onClick());
  assert.equal(beatArticle(renderer, 'beat_001').findAllByType('img').length, 6);
  cleanup();
});
