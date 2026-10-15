import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';

let server;
let StyleBiblePanel;
let DEFINITION_CATEGORIES;
let visualReferencesV2Client;

function textOf(node) {
  if (node == null) return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textOf).join(' ');
  return textOf(node.children ?? []);
}

function normalizedText(node) {
  return textOf(node).replace(/\s+/g, ' ').replace(/\(\s+/g, '(').replace(/\s+\)/g, ')').trim();
}

const snapshot = {
  visual_identity: {
    medium: ['CHANNEL_DNA_MEDIUM_SHOULD_RENDER'],
    linework: ['clean'],
    rendering: ['flat'],
    palette: ['muted blue'],
    shape_language: ['rounded'],
    texture: ['paper'],
    editorial_language: ['switch representation mode only when it better communicates the locked meaning'],
  },
  entity_styles: {
    character: { appearance: ['simple face'], avoid: ['photorealism'] },
    location: { appearance: ['wide view'], avoid: [] },
    prop: { appearance: ['clear silhouette'], avoid: [] },
    recurring_visual_subject: { appearance: ['consistent icon'], avoid: [] },
  },
  global_negative_constraints: ['CHANNEL_DNA_NEGATIVE_SHOULD_RENDER'],
};

function styleOccurrence(quote) {
  return { quote, context: `Context for ${quote}`, resolved_script_position: 12, section_id: 'sec_001' };
}

function styleArtifact() {
  return {
    artifact_type: 'project_style_bible',
    schema_version: '2.0',
    project_id: 'project_a',
    style_bible_revision_id: 'artifact_style',
    parent_style_bible_revision_id: null,
    revision_operation: 'draft_generation',
    status: 'text_approved',
    lineage: {
      script_analysis_artifact_id: 'LINEAGE_SHOULD_NOT_RENDER',
      source_script_artifact_id: 'SOURCE_SCRIPT_SHOULD_NOT_RENDER',
      channel_dna_snapshot_sha256: `sha256:${'a'.repeat(64)}`,
    },
    visual_style_snapshot: snapshot,
    characters: [{
      char_id: 'char_001',
      label: 'Guide',
      description: 'A calm guide prepared for image generation.',
      explicit_visual_traits: ['round glasses'],
      identity_markers: ['blue scarf'],
      continuity_notes: ['same scarf'],
      first_mention_quote: 'SOURCE_QUOTE_SHOULD_NOT_RENDER',
      resolved_first_mention_position: 12,
      occurrence_quotes: [styleOccurrence('SOURCE_OCCURRENCE_SHOULD_NOT_RENDER')],
      entity_kind: 'human_character',
      narrative_role: 'guide',
      approval_status: 'text_approved',
    }],
    locations: [{
      loc_id: 'loc_001',
      label: 'Archive Hall',
      description: 'A high-ceiling archive interior.',
      explicit_visual_traits: ['arched shelves'],
      identity_markers: ['brass clock'],
      continuity_notes: ['symmetrical aisles'],
      first_mention_quote: 'archive source',
      resolved_first_mention_position: 30,
      occurrence_quotes: [styleOccurrence('archive source')],
      location_kind: 'interior archive',
      approval_status: 'text_approved',
    }],
    recurring_props: [{
      prop_id: 'prop_001',
      label: 'Signal Key',
      description: 'A compact mechanical key.',
      explicit_visual_traits: ['triangular bow'],
      identity_markers: ['red enamel mark'],
      continuity_notes: ['fixed proportion'],
      first_mention_quote: 'key source',
      resolved_first_mention_position: 45,
      occurrence_quotes: [styleOccurrence('key source'), styleOccurrence('key again')],
      prop_kind: 'key',
      significance: 'Unlocks the signal chamber.',
      approval_status: 'text_approved',
    }],
    recurring_visual_subjects: [{
      subject_id: 'subject_001',
      label: 'Pulse Diagram',
      description: 'Three rings used as a recurring visual subject.',
      visual_type: 'diagram',
      explicit_visual_traits: ['three cyan rings'],
      continuity_notes: ['clockwise expansion'],
      first_mention_quote: 'pulse source',
      resolved_first_mention_position: 60,
      occurrence_quotes: [styleOccurrence('pulse source'), styleOccurrence('pulse again')],
      approval_status: 'text_approved',
    }],
    motifs: [{
      motif_id: 'motif_001',
      label: 'Broken Circle',
      description: 'Incomplete knowledge represented by an open ring.',
      occurrences: [{ quote: 'motif source', resolved_script_position: 75 }, { quote: 'motif again', resolved_script_position: 90 }],
      approval_status: 'text_approved',
    }],
  };
}

function panelProps() {
  return {
    projectId: 'project_a',
    styleBibleResult: { artifact_id: 'artifact_style', artifact: styleArtifact() },
    onGenerate() {},
    onSaveDraft() {},
    onApproveText() {},
    isGenerating: false,
    isSaving: false,
    isApproving: false,
    isHydrating: false,
    hydrationError: null,
    mutationError: null,
    canGenerate: true,
    scriptAnalysisArtifactId: 'artifact_analysis',
  };
}

function candidate(id, index, fileUrl, sourceKind = 'generated') {
  return {
    candidate_id: id,
    source_prompt_revision_id: 'prompt_revision_1',
    candidate_index: index,
    media_artifact_id: `media_${id}`,
    media_task_id: `task_${id}`,
    mime_type: 'image/png',
    media_sha256: `sha256:${id}`,
    file_url: fileUrl,
    source_kind: sourceKind,
    compatible_with_current_definition: true,
    created_at: '2026-08-18T00:00:00Z',
  };
}

function anchor(anchorType, anchorId, label, overrides = {}) {
  const candidates = overrides.candidates ?? [];
  const approvedCandidateId = overrides.approved_candidate_id ?? null;
  return {
    anchor_type: anchorType,
    anchor_id: anchorId,
    label,
    source_definition_hash: `hash_${anchorId}`,
    version: overrides.version ?? 1,
    initialized: overrides.initialized ?? true,
    upstream_stale: overrides.upstream_stale ?? false,
    resolution_status: overrides.resolution_status ?? (approvedCandidateId ? 'approved' : candidates.length ? 'candidate_ready' : 'not_generated'),
    review_status: overrides.review_status ?? (approvedCandidateId ? 'approved' : candidates.length ? 'draft_selected' : 'unreviewed'),
    prompt_revision_id: overrides.prompt_revision_id ?? 'prompt_revision_1',
    prompt: overrides.prompt ?? `Prompt for ${label}`,
    negative_prompt: overrides.negative_prompt ?? 'no text',
    candidates,
    selected_candidate_id: overrides.selected_candidate_id ?? candidates[0]?.candidate_id ?? null,
    approved_candidate_id: approvedCandidateId,
    active_operation_anchor_id: overrides.active_operation_anchor_id ?? null,
    last_error: null,
    last_progress_at: null,
  };
}

function baseAnchors() {
  return [
    anchor('character', 'char_001', 'Guide'),
    anchor('location', 'loc_001', 'Archive Hall'),
    anchor('prop', 'prop_001', 'Signal Key'),
    anchor('recurring_visual_subject', 'subject_001', 'Pulse Diagram'),
  ];
}

function workspaceFixture(overrides = {}) {
  const anchors = overrides.anchors ?? baseAnchors();
  const approvedCount = anchors.filter((item) => item.resolution_status === 'approved' && item.approved_candidate_id && !item.upstream_stale).length;
  return {
    project_id: 'project_a',
    style_bible_artifact_id: 'artifact_style',
    published_visual_reference_set_artifact_id: overrides.published_visual_reference_set_artifact_id ?? null,
    published_approval_set_artifact_id: overrides.published_approval_set_artifact_id ?? null,
    generated: overrides.generated ?? anchors.some((item) => item.candidates.length > 0),
    anchors,
    readiness: overrides.readiness ?? {
      required_count: anchors.length,
      approved_count: approvedCount,
      ready: anchors.length > 0 && approvedCount === anchors.length,
      missing_anchor_keys: anchors.filter((item) => item.resolution_status !== 'approved' || !item.approved_candidate_id || item.upstream_stale).map((item) => `${item.anchor_type}:${item.anchor_id}`),
      stale_anchor_keys: anchors.filter((item) => item.upstream_stale).map((item) => `${item.anchor_type}:${item.anchor_id}`),
    },
    active_operation_count: overrides.active_operation_count ?? 0,
  };
}

function approvedWorkspace() {
  const anchors = baseAnchors().map((item, index) => {
    const approved = candidate(`approved_${item.anchor_id}`, 1, `/media/${item.anchor_id}.png`);
    return anchor(item.anchor_type, item.anchor_id, item.label, {
      version: index + 2,
      candidates: [approved],
      selected_candidate_id: approved.candidate_id,
      approved_candidate_id: approved.candidate_id,
      resolution_status: 'approved',
      review_status: 'approved',
    });
  });
  return workspaceFixture({
    anchors,
    generated: true,
    published_visual_reference_set_artifact_id: 'set_previous',
    published_approval_set_artifact_id: 'approval_previous',
  });
}

async function flushUi() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
}

async function renderStyleBible(workspace = workspaceFixture()) {
  let currentWorkspace = workspace;
  visualReferencesV2Client.getWorkspace = async () => currentWorkspace;
  visualReferencesV2Client.getExecutions = async () => ({ project_id: 'project_a', executions: [] });
  visualReferencesV2Client.getAnchor = async (_projectId, anchorType, anchorId) => {
    const result = currentWorkspace.anchors.find((item) => item.anchor_type === anchorType && item.anchor_id === anchorId);
    if (!result) throw new Error(`missing anchor ${anchorType}:${anchorId}`);
    return result;
  };
  const setWorkspace = (next) => { currentWorkspace = next; };
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(QueryClientProvider, { client: queryClient },
      React.createElement(StyleBiblePanel, panelProps()),
    ));
    await flushUi();
  });
  return { renderer, queryClient, setWorkspace, getWorkspace: () => currentWorkspace };
}

function categoryButtons(renderer) {
  const nav = renderer.root.find((node) => node.type === 'nav' && node.props['aria-label'] === 'Visual Reference category');
  return nav.findAll((node) => node.type === 'button');
}

function findAnchorCard(renderer, anchorKey) {
  return renderer.root.find((node) => node.props?.['data-anchor-key'] === anchorKey);
}

before(async () => {
  server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    logLevel: 'error',
    server: { middlewareMode: true },
    appType: 'custom',
  });
  ({ StyleBiblePanel } = await server.ssrLoadModule('/src/features/studio/StyleBiblePanel.tsx'));
  ({ DEFINITION_CATEGORIES } = await server.ssrLoadModule('/src/features/studio/definitionCategories.ts'));
  ({ visualReferencesV2Client } = await server.ssrLoadModule('/src/api/visualReferencesV2.ts'));
});

after(async () => {
  if (server) await server.close();
});

test('the shared definition authority contains exactly the five stable categories', () => {
  assert.deepEqual(
    DEFINITION_CATEGORIES.map((category) => category.key),
    ['characters', 'locations', 'recurring_props', 'recurring_visual_subjects', 'visual_motifs'],
  );
});

test('approved Style Bible keeps inherited Channel DNA and exposes four durable visual-reference categories', async () => {
  const { renderer } = await renderStyleBible();
  const text = textOf(renderer.toJSON());
  for (const expected of ['Channel DNA', 'Inherited', 'Locked', 'CHANNEL_DNA_MEDIUM_SHOULD_RENDER', 'CHANNEL_DNA_NEGATIVE_SHOULD_RENDER', 'Visual References']) {
    assert.match(text, new RegExp(expected));
  }
  for (const hidden of ['LINEAGE_SHOULD_NOT_RENDER', 'SOURCE_SCRIPT_SHOULD_NOT_RENDER', 'SOURCE_QUOTE_SHOULD_NOT_RENDER', 'SOURCE_OCCURRENCE_SHOULD_NOT_RENDER', 'Broken Circle', 'Visual Motifs']) {
    assert.doesNotMatch(text, new RegExp(hidden));
  }
  assert.deepEqual(categoryButtons(renderer).map(normalizedText), [
    'Characters (1)', 'Locations (1)', 'Props (1)', 'Visual subjects (1)',
  ]);
  renderer.unmount();
});

test('full generation submits one durable operation with the frozen Style Bible and default ratios', async () => {
  const calls = [];
  visualReferencesV2Client.submitGeneration = async (...args) => {
    calls.push(args);
    return {
      project_id: 'project_a',
      operation_id: 'vr_operation_generate',
      operation_type: 'generate_all',
      status: 'queued',
      total_anchor_count: 4,
      scheduled_anchors: baseAnchors().map((item) => ({ anchor_type: item.anchor_type, anchor_id: item.anchor_id })),
      blocked_anchors: [],
      next_reconcile_at: '2026-08-18T00:00:01Z',
    };
  };
  const { renderer } = await renderStyleBible();
  const button = renderer.root.find((node) => node.type === 'button' && normalizedText(node) === 'Generate Reference Set');
  await act(async () => {
    button.props.onClick();
    await flushUi();
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], 'project_a');
  assert.deepEqual(calls[0][1], {
    style_bible_artifact_id: 'artifact_style',
    expected_visual_reference_set_artifact_id: null,
    candidate_count: 1,
    aspect_ratios_by_anchor_type: {
      character: '1:1',
      location: '16:9',
      prop: '1:1',
      recurring_visual_subject: '1:1',
    },
  });
  renderer.unmount();
});

test('candidate history selection approves the exact candidate with AnchorState version CAS and exact-anchor refresh', async () => {
  const first = candidate('candidate_char_1', 1, '/media/char_1.png');
  const second = candidate('candidate_char_2', 2, '/media/char_2.png');
  const initial = workspaceFixture({
    anchors: [
      anchor('character', 'char_001', 'Guide', { version: 7, candidates: [first, second], selected_candidate_id: first.candidate_id }),
      ...baseAnchors().slice(1),
    ],
  });
  const approvedAnchor = anchor('character', 'char_001', 'Guide', {
    version: 8,
    candidates: [first, second],
    selected_candidate_id: second.candidate_id,
    approved_candidate_id: second.candidate_id,
    resolution_status: 'approved',
    review_status: 'approved',
  });
  const calls = [];
  const harness = await renderStyleBible(initial);
  visualReferencesV2Client.approve = async (...args) => {
    calls.push(args);
    harness.setWorkspace(workspaceFixture({ anchors: [approvedAnchor, ...baseAnchors().slice(1)] }));
    return { project_id: 'project_a', anchor_type: 'character', anchor_id: 'char_001', version: 8 };
  };
  const card = findAnchorCard(harness.renderer, 'character:char_001');
  const thumbnails = card.findAll((node) => node.type === 'button' && node.props.title === 'generated');
  assert.equal(thumbnails.length, 2);
  await act(async () => thumbnails[1].props.onClick());
  const approveButton = card.find((node) => node.type === 'button' && normalizedText(node) === 'Approve');
  await act(async () => {
    approveButton.props.onClick();
    await flushUi();
  });
  assert.deepEqual(calls[0], ['project_a', 'character', 'char_001', 7, 'candidate_char_2']);
  assert.match(textOf(harness.renderer.toJSON()), /approved/);
  harness.renderer.unmount();
});

test('upload is anchor-local and uses the current AnchorState version before exact refresh', async () => {
  const calls = [];
  const harness = await renderStyleBible();
  visualReferencesV2Client.upload = async (...args) => {
    calls.push(args);
    return { project_id: 'project_a', anchor_type: 'character', anchor_id: 'char_001', version: 2 };
  };
  const card = findAnchorCard(harness.renderer, 'character:char_001');
  const input = card.find((node) => node.type === 'input' && node.props.type === 'file');
  const file = { name: 'guide.png', type: 'image/png' };
  await act(async () => {
    input.props.onChange({ target: { files: [file], value: 'guide.png' } });
    await flushUi();
  });
  assert.deepEqual(calls[0], ['project_a', 'character', 'char_001', 1, file]);
  harness.renderer.unmount();
});

test('prompt edit uses the current AnchorState version and refreshes only the edited anchor', async () => {
  const calls = [];
  const harness = await renderStyleBible();
  visualReferencesV2Client.updatePrompt = async (...args) => {
    calls.push(args);
    const updated = anchor('character', 'char_001', 'Guide', { version: 2, prompt: 'Edited prompt', negative_prompt: 'Edited negative' });
    harness.setWorkspace(workspaceFixture({ anchors: [updated, ...baseAnchors().slice(1)] }));
    return { project_id: 'project_a', anchor_type: 'character', anchor_id: 'char_001', version: 2 };
  };
  const card = findAnchorCard(harness.renderer, 'character:char_001');
  const edit = card.find((node) => node.type === 'button' && normalizedText(node) === 'Edit');
  await act(async () => edit.props.onClick());
  const textareas = card.findAll((node) => node.type === 'textarea');
  assert.equal(textareas.length, 2);
  await act(async () => {
    textareas[0].props.onChange({ target: { value: 'Edited prompt' } });
    textareas[1].props.onChange({ target: { value: 'Edited negative' } });
  });
  const save = card.find((node) => node.type === 'button' && normalizedText(node) === 'Save Prompt');
  await act(async () => {
    save.props.onClick();
    await flushUi();
  });
  assert.deepEqual(calls[0], ['project_a', 'character', 'char_001', 1, 'Edited prompt', 'Edited negative']);
  harness.renderer.unmount();
});

test('publication is a separate explicit boundary and uses both published pointer CAS values', async () => {
  const workspace = approvedWorkspace();
  const calls = [];
  visualReferencesV2Client.publish = async (...args) => {
    calls.push(args);
    return {
      project_id: 'project_a',
      visual_reference_set_artifact_id: 'set_next',
      approval_set_artifact_id: 'approval_next',
      published_anchor_count: 4,
      ready: true,
    };
  };
  const { renderer } = await renderStyleBible(workspace);
  const publishButton = renderer.root.find((node) => node.type === 'button' && normalizedText(node) === 'Publish References');
  assert.equal(publishButton.props.disabled, false);
  await act(async () => {
    publishButton.props.onClick();
    await flushUi();
  });
  assert.deepEqual(calls[0], ['project_a', {
    expected_visual_reference_set_artifact_id: 'set_previous',
    expected_approval_set_artifact_id: 'approval_previous',
  }]);
  renderer.unmount();
});

test('publication stays blocked while any required anchor is unapproved or durable work is active', async () => {
  const incomplete = workspaceFixture();
  const first = await renderStyleBible(incomplete);
  let button = first.renderer.root.find((node) => node.type === 'button' && normalizedText(node) === 'Publish References');
  assert.equal(button.props.disabled, true);
  first.renderer.unmount();

  const active = approvedWorkspace();
  active.active_operation_count = 1;
  const second = await renderStyleBible(active);
  button = second.renderer.root.find((node) => node.type === 'button' && normalizedText(node) === 'Publish References');
  assert.equal(button.props.disabled, true);
  second.renderer.unmount();
});
