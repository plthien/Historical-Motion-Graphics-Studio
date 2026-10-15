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
    review_status: overrides.review_status ?? (candidates.length ? 'current_draft' : 'unreviewed'),
    beat_state_version: overrides.beat_state_version ?? 1,
    active_operation_beat_id: overrides.active_operation_beat_id ?? null,
    current_draft_candidate_id: overrides.current_draft_candidate_id ?? null,
    selected_candidate_id: overrides.selected_candidate_id ?? null,
    prompt_revision_id: overrides.prompt_revision_id ?? `prompt_revision_${id}`,
    prompt_item_id: `prompt_${id}`,
    prompt_hash: `sha256:${id.padEnd(64, '0').slice(0, 64)}`,
    prompt_origin: 'compiled',
    image_prompt: `Image prompt for ${id}`,
    negative_prompt: 'No artifacts.',
    reference_bindings: [],
    treatment: visualTreatment,
    script_quote: `Narration for ${id}.`,
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

function workspace(scenes, actionOverrides = {}, latestOperation = null) {
  const allBeats = scenes.flatMap((entry) => entry.beats);
  return {
    project_id: 'project_a',
    readiness: { ready: true, blockers: [] },
    current_prompt_bundle_artifact_id: 'artifact_prompt_bundle',
    published_manifest_artifact_id: null,
    published_manifest_status: null,
    latest_operation: latestOperation,
    scenes,
    actions: {
      can_generate_all: false,
      can_generate_missing: false,
      can_retry_failed: false,
      can_approve_manifest: false,
      can_publish_manifest: false,
      eligible_generate_count: 0,
      failed_count: 0,
      approved_count: allBeats.filter((entry) => entry.resolution_status === 'approved').length,
      total_count: allBeats.length,
      ...actionOverrides,
    },
  };
}

function operation() {
  return {
    project_id: 'project_a',
    operation_id: 'operation_001',
    operation_type: 'generate_section',
    status: 'running',
    health: 'healthy',
    stalled: false,
    stalled_reason: null,
    last_progress_at: new Date().toISOString(),
    total_beat_count: 2,
    completed_count: 1,
    failed_count: 0,
    cancelled_count: 0,
    next_reconcile_at: null,
    cancel_requested_at: null,
    completed_at: null,
    error_code: null,
    error_message: null,
    beats: [
      { operation_beat_id: 'operation_beat_001', beat_id: 'beat_001', status: 'candidate_ready', health: 'terminal', stalled: false, stalled_reason: null, last_progress_at: null, candidate_count: 1, tasks: [], error_code: null, error_message: null },
      { operation_beat_id: 'operation_beat_002', beat_id: 'beat_002', status: 'running', health: 'healthy', stalled: false, stalled_reason: null, last_progress_at: null, candidate_count: 1, tasks: [], error_code: null, error_message: null },
    ],
  };
}

const beatArticle = (renderer, beatId) => renderer.root.find((node) => node.type === 'article' && node.props['data-beat-id'] === beatId);
const actionButton = (renderer, action) => renderer.root.find((node) => node.type === 'button' && node.props['data-action'] === action);
const actionButtonIn = (node, action) => node.find((child) => child.type === 'button' && child.props['data-action'] === action);

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function renderPanel(workspaceValue, executionValue = { project_id: 'project_a', active: false, active_operation_count: 0, stalled_operation_count: 0, operations: [] }) {
  sceneAssetsV2Client.getWorkspace = async () => workspaceValue;
  sceneAssetsV2Client.getExecutionStatus = async () => executionValue;
  sceneAssetsV2Client.getCandidateContentUrl = (_projectId, artifactId) => `https://example.test/${artifactId}`;
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  queryClient.setQueryData(['scene-assets-v2-workspace', 'project_a', 'artifact_prompt_bundle'], workspaceValue);
  queryClient.setQueryData(['scene-assets-v2-execution', 'project_a', 'artifact_prompt_bundle'], executionValue);
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(
      QueryClientProvider,
      { client: queryClient },
      React.createElement(SceneAssetsPanel, { projectId: 'project_a', promptBundleArtifactId: 'artifact_prompt_bundle', enabled: true }),
    ));
  });
  await flush();
  return {
    renderer,
    queryClient,
    cleanup() {
      renderer.unmount();
      queryClient.clear();
    },
  };
}

before(async () => {
  server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    logLevel: 'error',
    server: { middlewareMode: true },
    appType: 'custom',
  });
  ({ sceneAssetsV2Client } = await server.ssrLoadModule('/src/api/sceneAssetsV2.ts'));
  ({ SceneAssetsPanel } = await server.ssrLoadModule('/src/features/studio/SceneAssetsPanel.tsx'));
  for (const key of [
    'getWorkspace',
    'getBeat',
    'getExecutionStatus',
    'getCandidateContentUrl',
    'submitBulk',
    'generateBeat',
    'retryBeat',
    'regenerateBeat',
    'cancelOperation',
    'upload',
    'decide',
    'approveSectionReady',
    'publishManifest',
    'editPrompt',
    'restorePrompt',
  ]) originalClient[key] = sceneAssetsV2Client[key];
});

beforeEach(() => {
  for (const [key, value] of Object.entries(originalClient)) sceneAssetsV2Client[key] = value;
});

after(async () => {
  if (server) await server.close();
});

test('Active operations live in a right drawer that is hidden by default and can be opened', async () => {
  const op = operation();
  const state = workspace(
    [scene('scene_001', [
      beat('beat_001', { resolution_status: 'candidate_ready', can_generate: false }),
      beat('beat_002', { beat_order: 2, local_beat_order: 2, resolution_status: 'generating', active_operation_beat_id: 'operation_beat_002', can_generate: false }),
    ])],
    { total_count: 2 },
    { operation_id: op.operation_id, operation_type: op.operation_type, status: 'running', progress: 50, error_code: null, error_message: null },
  );
  const execution = { project_id: 'project_a', active: true, active_operation_count: 1, stalled_operation_count: 0, operations: [op] };
  const { renderer, cleanup } = await renderPanel(state, execution);

  let drawer = renderer.root.find((node) => node.props['data-testid'] === 'scene-assets-operation-drawer');
  assert.equal(drawer.props['aria-hidden'], true);
  assert.match(drawer.props.className, /\binvisible\b/);

  await act(async () => actionButton(renderer, 'toggle-operations-drawer').props.onClick());
  await flush();

  drawer = renderer.root.find((node) => node.props['data-testid'] === 'scene-assets-operation-drawer');
  assert.equal(drawer.props['aria-hidden'], false);
  assert.doesNotMatch(drawer.props.className, /\binvisible\b/);
  assert.match(drawer.props.className, /right-0/);
  assert.ok(drawer.findAll((node) => node.type === 'button' && node.props['data-action'] === 'close-operations-drawer').length === 1);
  cleanup();
});

test('approving a source beat refreshes its direct continuity dependent without a full workspace refresh', async () => {
  const sourceDraft = candidate('candidate_source', { is_current_draft: true });
  const historical = candidate('candidate_dependent_old');
  const source = beat('beat_source', {
    candidates: [sourceDraft],
    current_draft_candidate_id: sourceDraft.candidate_id,
    can_generate: false,
    can_regenerate: true,
    can_approve: true,
  });
  const dependentBlocked = beat('beat_dependent', {
    beat_order: 2,
    local_beat_order: 2,
    candidates: [historical],
    resolution_status: 'not_generated',
    review_status: 'unreviewed',
    current_draft_candidate_id: null,
    can_generate: false,
    can_regenerate: false,
    can_upload: false,
    can_review: false,
    can_approve: false,
    continuity: { required: true, source_beat_id: 'beat_source', source_ready: false, source_candidate_id: null, block_reason: 'source_not_approved' },
    error: { code: 'SCENE_ASSET_CONTINUITY_SOURCE_CHANGED', message: 'Approved continuity source changed; regenerate this beat.', details: {} },
  });
  const initial = workspace([scene('scene_001', [source, dependentBlocked])], { total_count: 2 });
  const sourceApprovedCandidate = candidate('candidate_source', { is_current_draft: true, is_approved: true });
  const sourceApproved = beat('beat_source', {
    candidates: [sourceApprovedCandidate],
    current_draft_candidate_id: sourceApprovedCandidate.candidate_id,
    selected_candidate_id: sourceApprovedCandidate.candidate_id,
    resolution_status: 'approved',
    review_status: 'approved',
    can_generate: false,
    can_retry: false,
    can_regenerate: false,
    can_upload: false,
    can_review: false,
    can_approve: false,
  });
  const dependentReady = beat('beat_dependent', {
    beat_order: 2,
    local_beat_order: 2,
    candidates: [historical],
    resolution_status: 'not_generated',
    review_status: 'unreviewed',
    current_draft_candidate_id: null,
    can_generate: false,
    can_regenerate: true,
    can_upload: true,
    can_review: true,
    can_approve: false,
    continuity: { required: true, source_beat_id: 'beat_source', source_ready: true, source_candidate_id: sourceApprovedCandidate.candidate_id, block_reason: null },
    error: { code: 'SCENE_ASSET_CONTINUITY_SOURCE_CHANGED', message: 'Approved continuity source changed; regenerate this beat.', details: {} },
  });

  sceneAssetsV2Client.decide = async () => ({
    project_id: 'project_a',
    beat: sourceApproved,
    invalidated_beat_ids: [],
    actions: { ...initial.actions, approved_count: 1 },
  });

  const { renderer, cleanup } = await renderPanel(initial);
  let workspaceCalls = 0;
  const beatCalls = [];
  sceneAssetsV2Client.getWorkspace = async () => {
    workspaceCalls += 1;
    return initial;
  };
  sceneAssetsV2Client.getBeat = async (projectId, beatId) => {
    beatCalls.push([projectId, beatId]);
    assert.equal(beatId, 'beat_dependent');
    return { project_id: projectId, beat: dependentReady, actions: { ...initial.actions, approved_count: 1 } };
  };

  await act(async () => actionButtonIn(beatArticle(renderer, 'beat_source'), 'approve-beat-image').props.onClick());
  await flush();
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
  await flush();

  assert.equal(workspaceCalls, 0);
  assert.deepEqual(beatCalls, [['project_a', 'beat_dependent']]);
  const dependentArticle = beatArticle(renderer, 'beat_dependent');
  assert.equal(actionButtonIn(dependentArticle, 'regenerate-image').props.disabled, false);
  cleanup();
});

test('Approve All Ready sends one frozen section approval request and refreshes returned beats locally', async () => {
  const firstCandidate = candidate('candidate_001', { is_current_draft: true });
  const secondCandidate = candidate('candidate_002', { is_current_draft: true });
  const first = beat('beat_001', {
    candidates: [firstCandidate],
    current_draft_candidate_id: firstCandidate.candidate_id,
    beat_state_version: 4,
    can_generate: false,
    can_regenerate: true,
    can_approve: true,
  });
  const second = beat('beat_002', {
    beat_order: 2,
    local_beat_order: 2,
    candidates: [secondCandidate],
    current_draft_candidate_id: secondCandidate.candidate_id,
    beat_state_version: 7,
    can_generate: false,
    can_regenerate: true,
    can_approve: true,
  });
  const initial = workspace([scene('scene_001', [first, second])], { total_count: 2 });
  const calls = [];
  let perBeatDecisionCalls = 0;
  sceneAssetsV2Client.approveSectionReady = async (...args) => {
    calls.push(args);
    return {
      project_id: 'project_a',
      section_id: 'sec_001',
      approved_beat_ids: ['beat_001', 'beat_002'],
      skipped_beat_ids: [],
      invalidated_beat_ids: [],
      actions: { ...initial.actions, approved_count: 2, can_approve_manifest: true, can_publish_manifest: true },
    };
  };
  sceneAssetsV2Client.decide = async () => {
    perBeatDecisionCalls += 1;
    throw new Error('per-beat decide must not be called');
  };

  const { renderer, cleanup } = await renderPanel(initial);
  const refreshed = {
    beat_001: beat('beat_001', {
      candidates: [candidate('candidate_001', { is_current_draft: true, is_approved: true })],
      current_draft_candidate_id: 'candidate_001',
      selected_candidate_id: 'candidate_001',
      resolution_status: 'approved',
      review_status: 'approved',
      can_generate: false,
      can_retry: false,
      can_regenerate: false,
      can_upload: false,
      can_review: false,
      can_approve: false,
    }),
    beat_002: beat('beat_002', {
      beat_order: 2,
      local_beat_order: 2,
      candidates: [candidate('candidate_002', { is_current_draft: true, is_approved: true })],
      current_draft_candidate_id: 'candidate_002',
      selected_candidate_id: 'candidate_002',
      resolution_status: 'approved',
      review_status: 'approved',
      can_generate: false,
      can_retry: false,
      can_regenerate: false,
      can_upload: false,
      can_review: false,
      can_approve: false,
    }),
  };
  const beatCalls = [];
  sceneAssetsV2Client.getBeat = async (projectId, beatId) => {
    beatCalls.push(beatId);
    return { project_id: projectId, beat: refreshed[beatId], actions: { ...initial.actions, approved_count: 2, can_approve_manifest: true, can_publish_manifest: true } };
  };

  await act(async () => actionButton(renderer, 'approve-section-ready').props.onClick());
  await flush();
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
  await flush();

  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], [
    'project_a',
    'sec_001',
    [
      { beat_id: 'beat_001', candidate_id: 'candidate_001', expected_beat_version: 4 },
      { beat_id: 'beat_002', candidate_id: 'candidate_002', expected_beat_version: 7 },
    ],
  ]);
  assert.equal(perBeatDecisionCalls, 0);
  assert.deepEqual(new Set(beatCalls), new Set(['beat_001', 'beat_002']));
  assert.match(beatArticle(renderer, 'beat_001').props['data-beat-id'], /beat_001/);
  assert.match(beatArticle(renderer, 'beat_002').props['data-beat-id'], /beat_002/);
  cleanup();
});
