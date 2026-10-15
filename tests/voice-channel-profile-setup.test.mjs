import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';

let server; let ChannelVoiceProfileSetup; let apiClient; let elevenLabsCredentialsApi;
const originals = {};
const byTestId = (renderer, id) => renderer.root.find((node) => node.props['data-testid'] === id);
const textOf = (node) => node == null ? '' : typeof node === 'string' || typeof node === 'number' ? String(node) : Array.isArray(node) ? node.map(textOf).join(' ') : textOf(node.children ?? []);
async function flush() { await act(async () => { for (let index = 0; index < 5; index += 1) { await Promise.resolve(); await new Promise((resolve) => setTimeout(resolve, 0)); } }); }

before(async () => {
  server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), logLevel: 'error', server: { middlewareMode: true }, appType: 'custom' });
  ({ apiClient } = await server.ssrLoadModule('/src/api/client.ts'));
  ({ elevenLabsCredentialsApi } = await server.ssrLoadModule('/src/api/elevenLabsCredentials.ts'));
  ({ ChannelVoiceProfileSetup } = await server.ssrLoadModule('/src/features/studio/voice/ChannelVoiceProfileSetup.tsx'));
  for (const key of ['getProject', 'getChannelVoiceProfile', 'updateChannelVoiceProfile']) originals[`api:${key}`] = apiClient[key];
  for (const key of ['getCredential', 'listProfessionalVoices', 'getVoicePacingCalibration', 'saveVoicePacingCalibration']) originals[`credential:${key}`] = elevenLabsCredentialsApi[key];
});

after(async () => {
  for (const [key, value] of Object.entries(originals)) {
    const [owner, method] = key.split(':');
    (owner === 'api' ? apiClient : elevenLabsCredentialsApi)[method] = value;
  }
  if (server) await server.close();
});

function calibration(voiceId, economy, quality) {
  return {
    provider: 'elevenlabs',
    voice_id: voiceId,
    calibrations: {
      economy,
      quality_cloned_voice: quality,
    },
  };
}

const registryEconomy = {
  model_id: 'eleven_flash_v2_5',
  native_wpm_at_rate_1: 150,
  source: 'registry',
  default_native_wpm_at_rate_1: 150,
};
const registryQuality = {
  model_id: 'eleven_multilingual_v2',
  native_wpm_at_rate_1: 150,
  source: 'registry',
  default_native_wpm_at_rate_1: 150,
};

test('missing channel Voice Profile can be saved from a Professional Voice Clone and refreshes workspace readiness', async () => {
  let storedProfile = null;
  let savedProfile = null;
  apiClient.getProject = async () => ({ project_id: 'project_a', channel_id: 'channel_a' });
  apiClient.getChannelVoiceProfile = async () => ({ channel_id: 'channel_a', voice_profile: storedProfile, updated_at: null });
  apiClient.updateChannelVoiceProfile = async (channelId, profile) => {
    assert.equal(channelId, 'channel_a');
    savedProfile = profile;
    storedProfile = profile;
    return { channel_id: channelId, voice_profile: profile, updated_at: 'now' };
  };
  elevenLabsCredentialsApi.getCredential = async () => ({ provider: 'elevenlabs', api_key_configured: true, source: 'stored' });
  elevenLabsCredentialsApi.listProfessionalVoices = async () => ({
    provider: 'elevenlabs',
    voices: [{ voice_id: 'pvc_ready', display_name: 'Ready PVC', language: 'en', ready_presets: ['economy', 'quality_cloned_voice'] }],
  });
  elevenLabsCredentialsApi.getVoicePacingCalibration = async (voiceId) => calibration(voiceId, registryEconomy, registryQuality);

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  let renderer;
  await act(async () => { renderer = create(React.createElement(QueryClientProvider, { client: queryClient }, React.createElement(ChannelVoiceProfileSetup, { projectId: 'project_a', voicePreset: 'economy', disabled: false }))); });
  await flush();

  assert.match(textOf(byTestId(renderer, 'channel-voice-profile-status')), /Not configured/);
  assert.equal(byTestId(renderer, 'channel-voice-profile-select').props.value, 'pvc_ready');
  assert.equal(byTestId(renderer, 'voice-native-wpm-input').props.value, '150');
  assert.match(textOf(byTestId(renderer, 'voice-native-wpm-source')), /Registry baseline/);
  assert.equal(byTestId(renderer, 'save-channel-voice-profile').props.disabled, false);

  await act(async () => { byTestId(renderer, 'save-channel-voice-profile').props.onClick(); });
  await flush();

  assert.equal(savedProfile.voice_id, 'pvc_ready');
  assert.equal(savedProfile.voice_kind, 'professional');
  assert.equal(savedProfile.provider, 'elevenlabs');
  assert.equal(savedProfile.default_preset, 'economy');
  assert.deepEqual(savedProfile.calibration.economy, { stability: 0.5, similarity_boost: 0.75, use_speaker_boost: true });
  assert.deepEqual(savedProfile.calibration.quality_cloned_voice, { stability: 0.5, similarity_boost: 0.75, use_speaker_boost: true });
  assert.match(textOf(byTestId(renderer, 'channel-voice-profile-status')), /Configured/);
  renderer.unmount();
  queryClient.clear();
});

test('voice without registry WPM can save an exact-preset override before channel selection', async () => {
  let storedCalibration = calibration(
    'pvc_new',
    { model_id: 'eleven_flash_v2_5', native_wpm_at_rate_1: null, source: 'missing', default_native_wpm_at_rate_1: null },
    { model_id: 'eleven_multilingual_v2', native_wpm_at_rate_1: null, source: 'missing', default_native_wpm_at_rate_1: null },
  );
  let savedWpm = null;
  apiClient.getProject = async () => ({ project_id: 'project_b', channel_id: 'channel_b' });
  apiClient.getChannelVoiceProfile = async () => ({ channel_id: 'channel_b', voice_profile: null, updated_at: null });
  apiClient.updateChannelVoiceProfile = async (channelId, profile) => ({ channel_id: channelId, voice_profile: profile, updated_at: 'now' });
  elevenLabsCredentialsApi.getCredential = async () => ({ provider: 'elevenlabs', api_key_configured: true, source: 'stored' });
  elevenLabsCredentialsApi.listProfessionalVoices = async () => ({
    provider: 'elevenlabs',
    voices: [{ voice_id: 'pvc_new', display_name: 'New PVC', language: 'en', ready_presets: ['economy'] }],
  });
  elevenLabsCredentialsApi.getVoicePacingCalibration = async () => storedCalibration;
  elevenLabsCredentialsApi.saveVoicePacingCalibration = async (voiceId, voicePreset, nativeWpmAtRate1) => {
    savedWpm = { voiceId, voicePreset, nativeWpmAtRate1 };
    storedCalibration = calibration(
      voiceId,
      { model_id: 'eleven_flash_v2_5', native_wpm_at_rate_1: nativeWpmAtRate1, source: 'override', default_native_wpm_at_rate_1: null },
      storedCalibration.calibrations.quality_cloned_voice,
    );
    return storedCalibration;
  };

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  let renderer;
  await act(async () => { renderer = create(React.createElement(QueryClientProvider, { client: queryClient }, React.createElement(ChannelVoiceProfileSetup, { projectId: 'project_b', voicePreset: 'economy', disabled: false }))); });
  await flush();

  assert.equal(byTestId(renderer, 'save-channel-voice-profile').props.disabled, true);
  assert.match(textOf(byTestId(renderer, 'voice-native-wpm-required')), /Measure it at ElevenLabs speed 1.0/);

  await act(async () => { byTestId(renderer, 'voice-native-wpm-input').props.onChange({ target: { value: '154.2' } }); });
  assert.equal(byTestId(renderer, 'save-voice-native-wpm').props.disabled, false);
  await act(async () => { byTestId(renderer, 'save-voice-native-wpm').props.onClick(); });
  await flush();

  assert.deepEqual(savedWpm, { voiceId: 'pvc_new', voicePreset: 'economy', nativeWpmAtRate1: 154.2 });
  assert.equal(byTestId(renderer, 'voice-native-wpm-input').props.value, '154.2');
  assert.match(textOf(byTestId(renderer, 'voice-native-wpm-source')), /Saved override/);
  assert.equal(byTestId(renderer, 'save-channel-voice-profile').props.disabled, false);
  renderer.unmount();
  queryClient.clear();
});
