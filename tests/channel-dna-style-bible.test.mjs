import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { normalizeLineArrayInput, parseChannelDnaObject } from '../src/features/channels/channelDnaCore.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const channelEditor = read('../src/features/channels/ChannelDnaEditor.tsx');
const createModal = read('../src/features/channels/CreateChannelModal.tsx');
const channelsPage = read('../src/pages/ChannelsPage.tsx');
const narrationFields = read('../src/features/channels/ChannelDnaNarrationFields.tsx');
const visualFields = read('../src/features/channels/ChannelDnaVisualFields.tsx');
const pacingFields = read('../src/features/channels/ChannelDnaPacingFields.tsx');
const channelDnaHelpers = read('../src/features/channels/channelDna.ts');
const lineEditor = read('../src/features/channels/LineArrayEditor.tsx');
const stylePanel = read('../src/features/studio/StyleBiblePanel.tsx');
const summary = read('../src/features/studio/StyleBibleVisualReferencesWorkspace.tsx');
const v2Workspace = read('../src/features/studio/StyleBibleVisualReferencesWorkspaceV2.tsx');
const v2Client = read('../src/api/visualReferencesV2.ts');
const viewModel = read('../src/features/studio/styleBibleReferenceViewModel.ts');
const apiClient = read('../src/api/client.ts');

function dna() {
  return {
    schema_version: '3.0',
    channel: { name: 'Zenn', language: 'English', niche: 'Psychology', audience: 'Curious viewers' },
    narration_wpm: { min: 145, max: 180 },
    pacing_profiles: {
      fast: { high: { preferred_hold_seconds: { min: 2.5, max: 4 } }, medium: { preferred_hold_seconds: { min: 4, max: 6 } }, low: { preferred_hold_seconds: { min: 6, max: 8 } } },
      medium: { high: { preferred_hold_seconds: { min: 3.5, max: 5.5 } }, medium: { preferred_hold_seconds: { min: 5.5, max: 8 } }, low: { preferred_hold_seconds: { min: 8, max: 11 } } },
      slow: { high: { preferred_hold_seconds: { min: 5, max: 7 } }, medium: { preferred_hold_seconds: { min: 7, max: 10 } }, low: { preferred_hold_seconds: { min: 10, max: 14 } } },
    },
    narration_identity: {
      voice: ['clear'], delivery: ['adaptive'], explanation_style: ['concrete'], tone_boundaries: ['evidence-safe'], avoid: ['style drift'],
    },
    visual_identity: {
      medium: ['ink'], linework: ['rough'], rendering: ['flat'], palette: ['muted'], shape_language: ['rounded'], texture: ['paper'], editorial_language: ['switch representation mode when useful'],
    },
    entity_styles: {
      character: { appearance: ['simple'], avoid: ['realistic'] },
      location: { appearance: ['minimal'], avoid: ['clutter'] },
      prop: { appearance: ['recognizable'], avoid: ['logos'] },
      recurring_visual_subject: { appearance: ['consistent'], avoid: ['story action'] },
    },
    global_negative_constraints: ['watermark', 'text'],
  };
}

test('Channel DNA 3.0 parser preserves deterministic array order and rejects old or duplicate shapes', () => {
  const input = dna();
  assert.deepEqual(parseChannelDnaObject(input), input);
  assert.deepEqual(normalizeLineArrayInput(' first \n\n second\n'), { values: ['first', 'second'], duplicates: [] });
  assert.deepEqual(normalizeLineArrayInput('same\nsame\nother'), { values: ['same', 'other'], duplicates: ['same'] });
  assert.throws(() => parseChannelDnaObject({ schema_version: '1.0', channel: input.channel, style_config: {} }), /exactly/);
  const missingWpm = dna();
  delete missingWpm.narration_wpm;
  assert.throws(() => parseChannelDnaObject(missingWpm), /exactly/);
  const invalidWpm = dna();
  invalidWpm.narration_wpm = { min: 180, max: 145 };
  assert.throws(() => parseChannelDnaObject(invalidWpm), /80 <= min < max <= 300/);
  const missingPacing = dna();
  delete missingPacing.pacing_profiles;
  assert.throws(() => parseChannelDnaObject(missingPacing), /exactly/);
  const invalidPacing = dna();
  invalidPacing.pacing_profiles.fast.high.preferred_hold_seconds = { min: 5, max: 4 };
  assert.throws(() => parseChannelDnaObject(invalidPacing), /0 < min < max/);
  const duplicate = dna();
  duplicate.visual_identity.medium = ['ink', 'ink'];
  assert.throws(() => parseChannelDnaObject(duplicate), /duplicate value/);
  const missingEditorialLanguage = dna();
  delete missingEditorialLanguage.visual_identity.editorial_language;
  assert.throws(() => parseChannelDnaObject(missingEditorialLanguage), /exactly/);
  const emptyEditorialLanguage = dna();
  emptyEditorialLanguage.visual_identity.editorial_language = [];
  assert.throws(() => parseChannelDnaObject(emptyEditorialLanguage), /at least one value/);
});

test('create and edit use the same nested Channel DNA 3.0 visual editor', () => {
  for (const source of [channelEditor, createModal]) { assert.match(source, /ChannelDnaNarrationFields/); assert.match(source, /ChannelDnaVisualFields/); }
  assert.match(channelsPage, /updateChannelDnaArray/);
  assert.match(channelsPage, /setEditorDraft\(\(current\) => current \? updateChannelDnaArray/);
  assert.match(channelsPage, /setCreateDnaDraft\(\(current\) => updateChannelDnaArray/);
  assert.match(channelsPage, /saveChannelDnaV3/);
  assert.match(channelsPage, /CHANNEL_DNA_STORED_INVALID/);
  assert.match(channelsPage, /setEditorDraft\(\(current\) => current \?\? createChannelDnaDraft/);
  assert.match(channelEditor, /loadState === 'ready' \|\| loadState === 'missing' \|\| loadState === 'invalid'/);
  for (const label of ['Narration Identity', 'Voice', 'Delivery', 'Explanation Style', 'Tone Boundaries', 'Visual Identity', 'Medium', 'Linework', 'Rendering', 'Palette', 'Shape Language', 'Texture', 'Editorial Language', 'Entity Styles', 'Character', 'Location', 'Recurring Props', 'Recurring Visual Subject', 'Appearance', 'Avoid', 'Global Negative Constraints']) {
    assert.match(`${narrationFields}\n${visualFields}\n${channelDnaHelpers}`, new RegExp(label));
  }
  assert.match(narrationFields, /Default narration WPM range/);
  assert.match(narrationFields, /dna\.narration_wpm/);
  assert.match(lineEditor, /normalizeLineArrayInput/);
  assert.match(lineEditor, /Duplicate values:/);
  assert.match(lineEditor, /role="alert"/);
  assert.doesNotMatch(`${channelEditor}\n${createModal}\n${channelsPage}\n${visualFields}`, /style_config|global_mood|character_style|scene_style|background_style|thumbnail_style|negative_style/);
});

test('Style Bible preserves inherited Channel DNA context while Visual References use durable V2', () => {
  assert.doesNotMatch(stylePanel, /VisualStyleSnapshotReadOnly|Channel DNA visual snapshot|channel_dna_snapshot_sha256|visual_style_snapshot/);
  assert.match(summary, /Channel DNA/);
  assert.match(summary, /\(Inherited\)/);
  assert.match(summary, /label="Locked"/);
  assert.match(summary, /artifact\.visual_style_snapshot/);
  assert.match(viewModel, /VISUAL_IDENTITY_ORDER/);
  assert.match(viewModel, /global_negative_constraints/);
  assert.doesNotMatch(`${stylePanel}\n${summary}\n${viewModel}`, /JSON\.stringify/);
  assert.doesNotMatch(summary, /channel_dna_snapshot_sha256|source_definition_hash/);

  assert.match(v2Workspace, /const POLL_INTERVAL_MS = 2000/);
  assert.match(v2Workspace, /visualReferencesV2Client\.getExecutions/);
  assert.match(v2Workspace, /Generate Reference Set/);
  assert.match(v2Workspace, /Approve/);
  assert.match(v2Workspace, /candidate_count: 1/);
  assert.doesNotMatch(v2Workspace, /getVisualReferenceJobV4|effective_poll_interval_seconds/);
  assert.match(summary, /Publish References/);
  assert.match(summary, /visualReferencesV2Client\.publish/);
  assert.match(v2Client, /expected_approval_set_artifact_id/);
  assert.doesNotMatch(summary, /Global Style|Prompt Modules/);
});

test('frontend API types expose structured Channel DNA and nullable provider metadata', () => {
  for (const token of ['NarrationIdentity', 'VisualIdentity', 'EntityStyles', 'VisualStyleSnapshot', "schema_version: '3.0'", "VisualReferenceAnchorTypeV4 = 'character' | 'location' | 'prop' | 'recurring_visual_subject'"]) {
    assert.match(apiClient, new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  }
  assert.doesNotMatch(apiClient, /style_config|global_mood|character_style|scene_style|background_style|thumbnail_style|negative_style/);
});
