import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Plug2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { apiClient, ApiError, type MediaProviderSettings } from '../api/client';
import { ErrorBanner } from '../components/ui/ErrorBanner';
import { IconButton } from '../components/ui/IconButton';
import { StatusBadge } from '../components/ui/StatusBadge';
import { ElevenLabsCredentialSettings } from '../features/settings/ElevenLabsCredentialSettings';
import { MediaProviderSettingsForm } from '../features/settings/MediaProviderSettingsForm';

function message(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) return `${error.errorCode}: ${error.message}`;
  return error instanceof Error ? error.message : 'Unknown error';
}

export function SettingsPage() {
  const queryClient = useQueryClient();
  const health = useQuery({ queryKey: ['health'], queryFn: () => apiClient.getHealth(), refetchInterval: 30000 });
  const media = useQuery({ queryKey: ['media-settings'], queryFn: () => apiClient.getMediaSettings() });
  const [settings, setSettings] = useState<MediaProviderSettings | null>(null);
  const [apiKey, setApiKey] = useState('');
  useEffect(() => { if (media.data?.settings) setSettings(media.data.settings); }, [media.data]);

  const save = useMutation({ mutationFn: () => { if (!settings) throw new Error('Media settings have not loaded.'); return apiClient.updateMediaSettings({ settings: { ...settings, api_key: apiKey.trim() || undefined } }); }, onSuccess: (response) => { setSettings(response.settings); setApiKey(''); void queryClient.invalidateQueries({ queryKey: ['media-settings'] }); } });
  const test = useMutation({ mutationFn: () => apiClient.testMediaConnection() });
  const error = [media.error, save.error, test.error].find(Boolean);
  const readiness = media.data?.readiness.status ?? 'loading';

  return <div className="space-y-5">
    {error ? <ErrorBanner message={message(error) ?? 'Settings request failed.'} /> : null}
    <section className="rounded-[1.5rem] border border-white/10 bg-cockpit-900/85 p-5 shadow-panel">
      <div className="flex flex-wrap items-start justify-between gap-4"><div><div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Production Configuration</div><h2 className="mt-2 text-2xl font-semibold text-slate-50">Media Provider</h2><p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">Authoring uses external ChatGPT prompts, so the tool no longer stores LLM provider credentials or stage model settings.</p></div><div className="flex gap-2"><StatusBadge status={health.data?.status === 'ok' ? 'approved' : 'pending'} label={health.data?.status === 'ok' ? 'Backend Ready' : 'Checking Backend'} /><StatusBadge status={readiness === 'ready' ? 'approved' : 'pending'} label={readiness === 'ready' ? 'Media Ready' : 'Media Needs Setup'} /></div></div>
    </section>
    <section className="rounded-[1.5rem] border border-white/10 bg-cockpit-900/85 p-5 shadow-panel">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3"><div><h3 className="text-lg font-semibold text-slate-100">Reference and Scene Image Generation</h3><p className="mt-1 text-sm text-slate-400">Configure the existing G-Labs webhook used by Visual References and generated-still Scene Assets.</p></div><div className="flex gap-2"><IconButton icon={Plug2} label={test.isPending ? 'Testing...' : 'Test Connection'} onClick={() => test.mutate()} disabled={test.isPending} /><IconButton icon={CheckCircle2} label={save.isPending ? 'Saving...' : 'Save Config'} onClick={() => save.mutate()} disabled={!settings || save.isPending} variant="primary" /></div></div>
      {settings ? <MediaProviderSettingsForm mediaSettings={settings} setMediaSettings={setSettings} mediaApiKey={apiKey} setMediaApiKey={setApiKey} readiness={readiness} /> : <div className="text-sm text-slate-400">Loading media settings...</div>}
      {test.data ? <div className="mt-4 rounded-2xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-100">{test.data.message}</div> : null}
    </section>
    <ElevenLabsCredentialSettings />
  </div>;
}
