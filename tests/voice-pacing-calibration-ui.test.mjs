import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const review = readFileSync(resolve(process.cwd(), 'src/features/studio/voice/VoiceReviewComponents.tsx'), 'utf8');
const setup = readFileSync(resolve(process.cwd(), 'src/features/studio/voice/ChannelVoiceProfileSetup.tsx'), 'utf8');
const elevenLabsApi = readFileSync(resolve(process.cwd(), 'src/api/elevenLabsCredentials.ts'), 'utf8');

test('Voice pacing remediation surfaces backend terminal provider limit reasons', () => {
  assert.match(review, /pacingRecommendationMessage/);
  assert.match(review, /provider_pacing_limit_reached/);
  assert.match(review, /Provider speed limit reached/);
  assert.match(review, /voice-section-terminal-reason-/);
  assert.doesNotMatch(review, /providerMaxReached/);
  assert.doesNotMatch(review, /recommended_provider_pacing_value >= 1\.2 - 0\.000001/);
});

test('Channel voice selection exposes persisted Native WPM editing for the active preset', () => {
  assert.match(setup, /voice-native-wpm-editor/);
  assert.match(setup, /voice-native-wpm-input/);
  assert.match(setup, /save-voice-native-wpm/);
  assert.match(setup, /Native WPM · \{presetLabel\(voicePreset\)\}/);
  assert.match(setup, /Registry baseline/);
  assert.match(setup, /Saved override/);
  assert.match(setup, /Calibration required/);
  assert.match(setup, /existing remediation lineage will be treated as stale/);
  assert.match(elevenLabsApi, /getVoicePacingCalibration/);
  assert.match(elevenLabsApi, /saveVoicePacingCalibration/);
  assert.match(elevenLabsApi, /native_wpm_at_rate_1/);
});
