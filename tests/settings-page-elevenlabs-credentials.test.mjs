import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';

let server; let SettingsPage; let apiClient; let elevenLabsCredentialsApi; const originals = {}; const credentialOriginals = {};
const byTestId = (renderer, id) => renderer.root.find((node) => node.props['data-testid'] === id);
const textOf = (node) => node == null ? '' : typeof node === 'string' || typeof node === 'number' ? String(node) : Array.isArray(node) ? node.map(textOf).join(' ') : textOf(node.children ?? []);
async function flush() { await act(async () => { await Promise.resolve(); await Promise.resolve(); await new Promise((resolve) => setTimeout(resolve, 0)); }); }

before(async () => {
  server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), logLevel: 'error', server: { middlewareMode: true }, appType: 'custom' });
  ({ apiClient } = await server.ssrLoadModule('/src/api/client.ts'));
  ({ elevenLabsCredentialsApi } = await server.ssrLoadModule('/src/api/elevenLabsCredentials.ts'));
  ({ SettingsPage } = await server.ssrLoadModule('/src/pages/SettingsPage.tsx'));
  for (const key of ['getHealth','getMediaSettings','updateMediaSettings','testMediaConnection']) originals[key] = apiClient[key];
  for (const key of ['getCredential','saveCredential','testConnection','clearCredential']) credentialOriginals[key] = elevenLabsCredentialsApi[key];
});
after(async () => { for (const [key, value] of Object.entries(originals)) apiClient[key] = value; for (const [key, value] of Object.entries(credentialOriginals)) elevenLabsCredentialsApi[key] = value; if (server) await server.close(); });

test('SettingsPage saves, tests, and clears ElevenLabs credential without echoing it', async () => {
  const secret = 'test-secret'; const saveCalls = []; let testCalls = 0; let clearCalls = 0;
  apiClient.getHealth = async () => ({ status: 'ok', version: 'test' });
  apiClient.getMediaSettings = async () => ({ settings: { provider: 'glabs_webhook', enabled: true, base_url: 'http://127.0.0.1:5999', api_key_configured: false, default_image_model: 'nano_banana_2', default_image_aspect_ratio: '16:9', default_upscale: [], poll_interval_seconds: 4, max_parallel_tasks: 3, enabled_media_kinds: ['image'] }, readiness: { status: 'ready', issues: [], provider: 'glabs_webhook', enabled_media_kinds: ['image'] } });
  apiClient.updateMediaSettings = async ({ settings }) => ({ settings, readiness: { status: 'ready', issues: [], provider: 'glabs_webhook', enabled_media_kinds: ['image'] } });
  apiClient.testMediaConnection = async () => ({ status: 'ready', message: 'ready', details: { provider: 'glabs_webhook', health_reachable: true, authentication_accepted: true } });
  elevenLabsCredentialsApi.getCredential = async () => ({ provider: 'elevenlabs', api_key_configured: false, source: 'none' });
  elevenLabsCredentialsApi.saveCredential = async (apiKey) => { saveCalls.push(apiKey); return { provider: 'elevenlabs', api_key_configured: true, source: 'stored' }; };
  elevenLabsCredentialsApi.testConnection = async () => { testCalls += 1; return { provider: 'elevenlabs', status: 'ready', model_id: 'eleven_multilingual_v2', authentication_accepted: true, catalog_reachable: true, voice_count: 2 }; };
  elevenLabsCredentialsApi.clearCredential = async () => { clearCalls += 1; return { provider: 'elevenlabs', api_key_configured: false, source: 'none' }; };

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  let renderer;
  await act(async () => { renderer = create(React.createElement(QueryClientProvider, { client: queryClient }, React.createElement(SettingsPage))); });
  await flush();
  assert.equal(byTestId(renderer, 'elevenlabs-api-key').props.type, 'password');
  assert.equal(byTestId(renderer, 'elevenlabs-api-key').props.value, '');
  assert.equal(byTestId(renderer, 'elevenlabs-save-key').props.disabled, true);
  assert.match(textOf(byTestId(renderer, 'elevenlabs-credential-status')), /Not configured/);
  await act(async () => { byTestId(renderer, 'elevenlabs-api-key').props.onChange({ target: { value: secret } }); });
  await flush();
  await act(async () => { byTestId(renderer, 'elevenlabs-save-key').props.onClick(); });
  await flush();
  assert.deepEqual(saveCalls, [secret]);
  assert.equal(byTestId(renderer, 'elevenlabs-api-key').props.value, '');
  assert.match(textOf(byTestId(renderer, 'elevenlabs-credential-status')), /Configured/);
  assert.equal(textOf(renderer.toJSON()).includes(secret), false);
  assert.equal(JSON.stringify(queryClient.getQueryData(['elevenlabs-credential'])).includes(secret), false);
  await act(async () => { byTestId(renderer, 'elevenlabs-test-connection').props.onClick(); }); await flush();
  assert.equal(testCalls, 1); assert.match(textOf(byTestId(renderer, 'elevenlabs-credential-notice')), /2 account voices/);
  await act(async () => { byTestId(renderer, 'elevenlabs-clear-key').props.onClick(); }); await flush();
  assert.equal(clearCalls, 1); assert.match(textOf(byTestId(renderer, 'elevenlabs-credential-status')), /Not configured/);
  assert.equal(byTestId(renderer, 'elevenlabs-api-key').props.value, '');
  renderer.unmount(); queryClient.clear();
});
