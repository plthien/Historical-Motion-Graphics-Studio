import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const review = readFileSync(resolve(process.cwd(), 'src/features/studio/voice/VoiceReviewComponents.tsx'), 'utf8');
const panel = readFileSync(resolve(process.cwd(), 'src/features/studio/VoicePanel.tsx'), 'utf8');
const api = readFileSync(resolve(process.cwd(), 'src/api/voicePacingAcceptance.ts'), 'utf8');
const finalizer = readFileSync(resolve(process.cwd(), 'app/modules/v4/voice_durable_finalizer.py'), 'utf8');

test('Voice UI offers explicit per-section pacing acceptance without removing regeneration', () => {
  assert.match(review, /Accept deviation/);
  assert.match(review, /accept-voice-pacing-deviation-/);
  assert.match(review, /section\.can_accept_pacing_deviation/);
  assert.match(review, /15% final-pacing safety fence/);
  assert.match(review, /Regenerate Section/);
  assert.match(panel, /acceptPacingDeviation/);
  assert.match(api, /accept-pacing-deviation/);
  assert.doesNotMatch(review, /manualAcceptanceSafe = warning && section\.correction_relative/);
});

test('manual pacing acceptance is consumed before final pacing rather than bypassing finalization', () => {
  assert.match(finalizer, /apply_manual_pacing_acceptances/);
  assert.match(finalizer, /apply_section_pacing/);
});
