import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { elevenLabsCredentialsApi } from '../../api/elevenLabsCredentials';

export function ElevenLabsCredentialSettings() {
  const queryClient = useQueryClient();
  const status = useQuery({
    queryKey: ['elevenlabs-credential'],
    queryFn: ({ signal }) => elevenLabsCredentialsApi.getCredential(signal),
  });
  const [newKey, setNewKey] = useState('');
  const [busy, setBusy] = useState<'save' | 'test' | 'clear' | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refreshVoice(nextStatus?: Awaited<ReturnType<typeof elevenLabsCredentialsApi.getCredential>>) {
    if (nextStatus) queryClient.setQueryData(['elevenlabs-credential'], nextStatus);
    else await queryClient.invalidateQueries({ queryKey: ['elevenlabs-credential'] });
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['tts-settings'] }),
      queryClient.invalidateQueries({ queryKey: ['tts-readiness'] }),
    ]);
  }

  async function save() {
    const value = newKey.trim();
    if (!value || busy) return;
    setBusy('save'); setError(null); setNotice(null);
    try {
      const next = await elevenLabsCredentialsApi.saveCredential(value);
      setNewKey('');
      setNotice('ElevenLabs credential saved.');
      await refreshVoice(next);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally { setBusy(null); }
  }

  async function testConnection() {
    if (busy) return;
    setBusy('test'); setError(null); setNotice(null);
    try {
      const result = await elevenLabsCredentialsApi.testConnection();
      setNotice(`Connection ready. ${result.voice_count} account voices discovered.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally { setBusy(null); }
  }

  async function clear() {
    if (busy) return;
    setBusy('clear'); setError(null); setNotice(null);
    try {
      const next = await elevenLabsCredentialsApi.clearCredential();
      setNewKey('');
      setNotice(next.source === 'environment' ? 'Stored override cleared; environment fallback is active.' : 'Stored ElevenLabs credential cleared.');
      await refreshVoice(next);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally { setBusy(null); }
  }

  const configured = status.data?.api_key_configured ?? false;
  const label = !configured ? 'Not configured' : status.data?.source === 'environment' ? 'Environment fallback' : 'Configured';

  return <section className="rounded-[1.5rem] border border-white/10 bg-cockpit-900/85 p-5 shadow-panel" data-testid="elevenlabs-credential-settings">
    <h3 className="text-lg font-semibold text-slate-100">Voice Provider · ElevenLabs</h3>
    <p className="mt-1 text-sm text-slate-400">Configure the server-side credential used by ElevenLabs Multilingual v2. The saved value is never returned to the browser.</p>
    <div className="mt-4 text-sm text-slate-300" data-testid="elevenlabs-credential-status">Credential: {label}</div>
    <input data-testid="elevenlabs-api-key" type="password" autoComplete="new-password" value={newKey} onChange={(event) => setNewKey(event.target.value)} placeholder="Enter a new ElevenLabs API key" className="mt-3 w-full rounded-xl border border-white/10 bg-black/20 px-3 py-2 text-sm text-slate-100" />
    <div className="mt-3 flex flex-wrap gap-2">
      <button data-testid="elevenlabs-save-key" type="button" disabled={!newKey.trim() || busy !== null} onClick={() => void save()} className="rounded border border-sky-400/40 px-3 py-2 text-sm disabled:opacity-40">{busy === 'save' ? 'Saving...' : 'Save Key'}</button>
      <button data-testid="elevenlabs-test-connection" type="button" disabled={!configured || busy !== null} onClick={() => void testConnection()} className="rounded border border-white/15 px-3 py-2 text-sm disabled:opacity-40">{busy === 'test' ? 'Testing...' : 'Test Connection'}</button>
      <button data-testid="elevenlabs-clear-key" type="button" disabled={status.data?.source !== 'stored' || busy !== null} onClick={() => void clear()} className="rounded border border-rose-400/30 px-3 py-2 text-sm disabled:opacity-40">{busy === 'clear' ? 'Clearing...' : 'Clear Key'}</button>
    </div>
    {notice ? <p className="mt-3 text-sm text-emerald-300" data-testid="elevenlabs-credential-notice">{notice}</p> : null}
    {error ? <p className="mt-3 text-sm text-rose-300" role="alert">{error}</p> : null}
  </section>;
}
