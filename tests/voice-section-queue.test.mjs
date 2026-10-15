import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const panel = readFileSync('src/features/studio/VoicePanel.tsx', 'utf8');
const review = readFileSync('src/features/studio/voice/VoiceReviewComponents.tsx', 'utf8');
const client = readFileSync('src/api/client.ts', 'utf8');
const routes = readFileSync('app/api/routes/production_v4.py', 'utf8');

test('Voice section remediation queues only section IDs and refreshes before dequeue', () => {
  assert.match(panel, /useState<string\[]>\(\[\]\)/);
  assert.match(panel, /current\.includes\(section\.section_id\)/);
  assert.match(panel, /\[\.\.\.current, section\.section_id\]/);
  assert.match(panel, /refreshWorkspace\(\)\.then\(\(\) => setQueueReadyToDrain\(true\)\)/);
  assert.match(panel, /getQueryData<VoiceWorkspaceResponse>\(queryKey\)/);
  assert.match(panel, /latestSection\?\.regeneration_supported && latestSection\.can_regenerate/);
  assert.match(panel, /latestSection\.section_id !== latestActiveTargetSectionId/);
  assert.match(panel, /regenerate\.mutate\(latestSection\)/);
});

test('other flagged sections remain queueable while one operation is active', () => {
  assert.match(panel, /if \(!section\.regeneration_supported\) return/);
  assert.match(panel, /if \(section\.section_id === activeTargetSectionId\) return/);
  assert.match(client, /target_section_id: string \| null/);
  assert.match(review, /activeTargetSectionId/);
  assert.match(review, /section\.can_regenerate \|\| \(operationActive && section\.regeneration_supported && section\.section_id !== activeTargetSectionId\)/);
  assert.match(review, /section\.section_id !== activeTargetSectionId/);
  assert.doesNotMatch(review, /warning && hasRemediationLineage/);
  assert.match(review, /Queue Regeneration/);
  assert.match(review, /Queued #/);
  assert.doesNotMatch(review, /disabled=\{pending \|\| !section\.can_regenerate\}/);
});

test('terminal provider pacing reasons are rendered without provider-max threshold inference', () => {
  assert.match(client, /recommendation_reason: string \| null/);
  assert.match(client, /regeneration_supported: boolean/);
  assert.match(client, /remediation_block_reason: string \| null/);
  assert.match(review, /pacingRecommendationMessage/);
  assert.match(review, /remediationBlockMessage/);
  assert.match(review, /provider_pacing_limit_reached/);
  assert.match(review, /Provider speed limit reached/);
  assert.match(review, /provider_pacing_no_progress/);
  assert.match(review, /same pacing rate/);
  assert.match(review, /kokoro_duration_model_invalid/);
  assert.match(review, /frozen duration model/);
  assert.match(review, /source_pacing_contract_stale/);
  assert.match(review, /older pacing contract/);
  assert.match(review, /voice-section-terminal-reason-/);
  assert.doesNotMatch(review, /Provider max reached/);
  assert.doesNotMatch(review, /1\.2 - 0\.000001/);
  assert.doesNotMatch(review, /recommended_provider_pacing_value != null[\s\S]*>= 1\.2/);
});

test('section cards play section audio and display actual WPM deviation', () => {
  assert.match(client, /getVoiceSectionAudioUrl/);
  assert.match(routes, /voice\/sections\/\{section_id\}\/audio/);
  assert.match(review, /voice-section-audio-/);
  assert.match(review, /section\.raw_wpm \/ section\.target_wpm/);
  assert.match(review, /WPM deviation:/);
  assert.match(review, /QC ±5% WPM/);
});

test('section audio invalidates browser media cache after regeneration', () => {
  assert.match(review, /section\.remediation_identity/);
  assert.match(review, /section\.remediation_report_sha256/);
  assert.match(review, /section\.source_remediation_operation_id/);
  assert.match(review, /revision=\$\{encodeURIComponent\(audioRevision\)\}/);
  assert.match(review, /<audio key=\{audioSource\}/);
  assert.match(routes, /'Cache-Control': 'no-store, max-age=0'/);
});


test('section regeneration refresh stays scoped to Voice until approval', () => {
  assert.match(panel, /const refreshWorkspace = useCallback/);
  assert.match(panel, /const refreshProject = useCallback/);
  assert.equal((panel.match(/\['v3-projects'\]/g) ?? []).length, 1);
  assert.match(panel, /onSuccess: async \(\) => \{\s*await Promise\.all\(\[refreshWorkspace\(\), refreshProject\(\)\]\);\s*\}/);
});
