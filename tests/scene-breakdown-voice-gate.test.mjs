import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const read = (path) => readFileSync(resolve(process.cwd(), path), 'utf8');
const studio = read('src/pages/StudioPage.tsx');
const service = read('app/modules/v4/scene_breakdown_service.py');

test('Scene Breakdown stays locked until approved Voice and visual-reference authorities exist', () => {
  assert.match(studio, /const hasApprovedVoicePair = Boolean\(project\?\.narration_audio_artifact_id && project\?\.narration_word_timing_map_artifact_id\);/);
  assert.match(studio, /styleBibleResult\?\.artifact\.status === 'text_approved' && hasApprovedVoicePair && hasApprovedVisualReferences \? 'ready' : 'locked'/);
  assert.match(studio, /canImport=\{Boolean\(project\.script_artifact_id && project\.script_analysis_artifact_id && hasApprovedVoicePair && hasApprovedVisualReferences && styleBibleResult\?\.artifact\.status === 'text_approved'\)\}/);
});

test('backend remains authoritative when Scene Breakdown is called without approved Voice', () => {
  assert.match(service, /SCENE_BREAKDOWN_VOICE_NOT_APPROVED/);
  assert.match(service, /Scene Breakdown requires an approved Narration Audio \+ NarrationWordTimingMap pair\./);
  assert.match(service, /self\.approved_voice_timing\(project\)/);
});
