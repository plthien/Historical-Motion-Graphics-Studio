import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { apiClient, ApiError, type ChannelVoiceProfile, type VoicePreset } from '../../../api/client';
import { elevenLabsCredentialsApi } from '../../../api/elevenLabsCredentials';

const DEFAULT_CALIBRATION = {
  stability: 0.5,
  similarity_boost: 0.75,
  use_speaker_boost: true,
} as const;

function message(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) return `${error.errorCode}: ${error.message}`;
  return error instanceof Error ? error.message : String(error);
}

function presetLabel(preset: VoicePreset): string {
  return preset === 'economy' ? 'Economy' : 'Quality cloned voice';
}

export function ChannelVoiceProfileSetup({ projectId, voicePreset, disabled }: { projectId: string; voicePreset: VoicePreset; disabled: boolean }) {
  const queryClient = useQueryClient();
  const [voiceId, setVoiceId] = useState('');
  const [nativeWpmInput, setNativeWpmInput] = useState('');

  const project = useQuery({
    queryKey: ['voice-project-channel', projectId],
    queryFn: () => apiClient.getProject(projectId),
  });
  const channelId = project.data?.channel_id ?? null;
  const profile = useQuery({
    queryKey: ['channel-voice-profile', channelId],
    queryFn: () => apiClient.getChannelVoiceProfile(channelId!),
    enabled: Boolean(channelId),
    retry: false,
  });
  const credential = useQuery({
    queryKey: ['elevenlabs-credential'],
    queryFn: ({ signal }) => elevenLabsCredentialsApi.getCredential(signal),
    retry: false,
  });
  const catalog = useQuery({
    queryKey: ['elevenlabs-professional-voices'],
    queryFn: ({ signal }) => elevenLabsCredentialsApi.listProfessionalVoices(signal),
    enabled: credential.data?.api_key_configured === true,
    retry: false,
  });

  const voices = catalog.data?.voices ?? [];
  useEffect(() => {
    const storedVoiceId = profile.data?.voice_profile?.voice_id;
    if (storedVoiceId) {
      setVoiceId(storedVoiceId);
      return;
    }
    setVoiceId((current) => {
      if (current && voices.some((voice) => voice.voice_id === current)) return current;
      return voices.find((voice) => voice.ready_presets.includes(voicePreset))?.voice_id ?? voices[0]?.voice_id ?? '';
    });
  }, [profile.data?.voice_profile?.voice_id, voicePreset, voices]);

  const selectedVoice = useMemo(() => voices.find((voice) => voice.voice_id === voiceId) ?? null, [voiceId, voices]);
  const selectedPresetReady = selectedVoice?.ready_presets.includes(voicePreset) === true;
  const currentProfile = profile.data?.voice_profile ?? null;
  const pacingCalibration = useQuery({
    queryKey: ['elevenlabs-voice-pacing-calibration', voiceId],
    queryFn: ({ signal }) => elevenLabsCredentialsApi.getVoicePacingCalibration(voiceId, signal),
    enabled: Boolean(voiceId && credential.data?.api_key_configured),
    retry: false,
  });
  const selectedPacingCalibration = pacingCalibration.data?.calibrations[voicePreset] ?? null;

  useEffect(() => {
    const nativeWpm = selectedPacingCalibration?.native_wpm_at_rate_1;
    setNativeWpmInput(nativeWpm == null ? '' : String(nativeWpm));
  }, [voiceId, voicePreset, selectedPacingCalibration?.native_wpm_at_rate_1]);

  const saveNativeWpm = useMutation({
    mutationFn: () => {
      const nativeWpm = Number(nativeWpmInput);
      if (!voiceId || !Number.isFinite(nativeWpm) || nativeWpm < 60 || nativeWpm > 300) {
        throw new Error('VOICE_PACING_CALIBRATION_WPM_INVALID: Native WPM must be between 60 and 300.');
      }
      return elevenLabsCredentialsApi.saveVoicePacingCalibration(voiceId, voicePreset, nativeWpm);
    },
    onSuccess: async (response) => {
      queryClient.setQueryData(['elevenlabs-voice-pacing-calibration', voiceId], response);
      await queryClient.invalidateQueries({ queryKey: ['voice-workspace', projectId] });
    },
  });

  const save = useMutation({
    mutationFn: () => {
      if (!channelId || !voiceId) throw new Error('CHANNEL_VOICE_PROFILE_REQUIRED: Select a Professional Voice Clone first.');
      if (!selectedPresetReady) throw new Error(`VOICE_PVC_NOT_READY: The selected voice is not ready for ${presetLabel(voicePreset)}.`);
      if (selectedPacingCalibration?.native_wpm_at_rate_1 == null) throw new Error('VOICE_PACING_CALIBRATION_REQUIRED: Save a Native WPM for the selected voice and preset first.');
      const next: ChannelVoiceProfile = {
        schema_version: 1,
        provider: 'elevenlabs',
        voice_id: voiceId,
        voice_kind: 'professional',
        default_preset: voicePreset,
        calibration: {
          economy: { ...DEFAULT_CALIBRATION },
          quality_cloned_voice: { ...DEFAULT_CALIBRATION },
        },
      };
      return apiClient.updateChannelVoiceProfile(channelId, next);
    },
    onSuccess: async (response) => {
      queryClient.setQueryData(['channel-voice-profile', channelId], response);
      await queryClient.invalidateQueries({ queryKey: ['voice-workspace', projectId] });
    },
  });

  const error = [project.error, profile.error, credential.error, catalog.error, pacingCalibration.error, saveNativeWpm.error, save.error].find(Boolean);
  const configuredName = currentProfile
    ? voices.find((voice) => voice.voice_id === currentProfile.voice_id)?.display_name ?? currentProfile.voice_id
    : null;
  const parsedNativeWpm = Number(nativeWpmInput);
  const nativeWpmValid = nativeWpmInput.trim() !== '' && Number.isFinite(parsedNativeWpm) && parsedNativeWpm >= 60 && parsedNativeWpm <= 300;
  const calibrationReady = selectedPacingCalibration?.native_wpm_at_rate_1 != null;
  const canSaveNativeWpm = Boolean(selectedVoice && nativeWpmValid && !disabled && !saveNativeWpm.isPending);
  const canSave = Boolean(channelId && credential.data?.api_key_configured && selectedVoice && selectedPresetReady && calibrationReady && !disabled && !save.isPending);

  return <section className="rounded-2xl border border-white/10 bg-white/[0.025] p-4" data-testid="channel-voice-profile-setup">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <strong className="text-sm text-white">Channel Professional Voice Clone</strong>
        <p className="mt-1 text-xs text-slate-400">Persistent channel-level voice identity. Generation still exposes only the Economy / Quality preset below.</p>
      </div>
      <span data-testid="channel-voice-profile-status" className={`text-xs ${currentProfile ? 'text-emerald-300' : 'text-amber-300'}`}>
        {currentProfile ? `Configured · ${configuredName}` : 'Not configured'}
      </span>
    </div>

    {credential.data && !credential.data.api_key_configured ? <p className="mt-3 rounded border border-amber-400/30 bg-amber-400/5 p-3 text-xs text-amber-200" data-testid="channel-voice-profile-credential-warning">Configure the ElevenLabs credential in Settings before selecting a channel voice.</p> : null}
    {credential.data?.api_key_configured && catalog.isPending ? <p className="mt-3 text-xs text-slate-400">Loading account Professional Voice Clones…</p> : null}
    {credential.data?.api_key_configured && !catalog.isPending && voices.length === 0 && !catalog.error ? <p className="mt-3 rounded border border-amber-400/30 bg-amber-400/5 p-3 text-xs text-amber-200">No Professional Voice Clones were reported by the account.</p> : null}

    {voices.length ? <label className="mt-3 block text-xs text-slate-300">Professional Voice Clone
      <select data-testid="channel-voice-profile-select" disabled={disabled || save.isPending || saveNativeWpm.isPending} value={voiceId} onChange={(event) => { setVoiceId(event.target.value); save.reset(); saveNativeWpm.reset(); }} className="mt-1 w-full rounded border border-white/10 bg-slate-950 px-3 py-2 text-sm text-white disabled:opacity-50">
        {voices.map((voice) => <option key={voice.voice_id} value={voice.voice_id}>{voice.display_name} · {voice.ready_presets.length ? voice.ready_presets.map(presetLabel).join(' + ') : 'not ready for active presets'}</option>)}
      </select>
    </label> : null}

    {selectedVoice ? <div className="mt-3 rounded-xl border border-white/10 bg-slate-950/40 p-3" data-testid="voice-native-wpm-editor">
      <div className="flex flex-wrap items-end gap-3">
        <label className="min-w-[180px] flex-1 text-xs text-slate-300">Native WPM · {presetLabel(voicePreset)}
          <input
            data-testid="voice-native-wpm-input"
            type="number"
            min="60"
            max="300"
            step="0.1"
            disabled={disabled || saveNativeWpm.isPending || pacingCalibration.isPending}
            value={nativeWpmInput}
            onChange={(event) => { setNativeWpmInput(event.target.value); saveNativeWpm.reset(); }}
            placeholder="Enter measured WPM at speed 1.0"
            className="mt-1 w-full rounded border border-white/10 bg-slate-950 px-3 py-2 text-sm text-white disabled:opacity-50"
          />
        </label>
        <button type="button" data-testid="save-voice-native-wpm" disabled={!canSaveNativeWpm} onClick={() => saveNativeWpm.mutate()} className="rounded border border-violet-400/40 bg-violet-400/10 px-3 py-2 text-xs text-violet-100 disabled:opacity-40">{saveNativeWpm.isPending ? 'Saving WPM…' : 'Save WPM'}</button>
      </div>
      {pacingCalibration.isPending ? <p className="mt-2 text-[11px] text-slate-500">Loading pacing calibration…</p> : null}
      {selectedPacingCalibration ? <p className="mt-2 text-[11px] leading-5 text-slate-500" data-testid="voice-native-wpm-source">
        Model {selectedPacingCalibration.model_id} · {selectedPacingCalibration.source === 'override' ? 'Saved override' : selectedPacingCalibration.source === 'registry' ? 'Registry baseline' : 'Calibration required'}{selectedPacingCalibration.default_native_wpm_at_rate_1 != null && selectedPacingCalibration.source === 'override' ? ` · registry default ${selectedPacingCalibration.default_native_wpm_at_rate_1} WPM` : ''}.
      </p> : null}
      {selectedPacingCalibration?.source === 'missing' ? <p className="mt-2 text-xs text-amber-300" data-testid="voice-native-wpm-required">This voice has no Native WPM baseline for {presetLabel(voicePreset)}. Measure it at ElevenLabs speed 1.0 and save it before using this preset.</p> : null}
      {saveNativeWpm.isSuccess ? <p className="mt-2 text-xs text-emerald-300">Native WPM saved. New Voice generations will use this calibration; existing remediation lineage will be treated as stale.</p> : null}
    </div> : null}

    {selectedVoice && !selectedPresetReady ? <p className="mt-2 text-xs text-amber-300" data-testid="channel-voice-profile-preset-warning">This voice is not currently reported fine-tuned for {presetLabel(voicePreset)}. Choose another voice or switch the generation preset.</p> : null}
    <p className="mt-2 text-[11px] leading-5 text-slate-500">Initial channel calibration: stability 0.50, similarity 0.75, speaker boost on for both presets.</p>
    <div className="mt-3 flex flex-wrap items-center gap-3">
      <button type="button" data-testid="save-channel-voice-profile" disabled={!canSave} onClick={() => save.mutate()} className="rounded border border-cyan-400/40 bg-cyan-400/10 px-3 py-2 text-xs text-cyan-100 disabled:opacity-40">{save.isPending ? 'Saving…' : currentProfile ? 'Update Channel Voice' : 'Save Channel Voice'}</button>
      {save.isSuccess ? <span className="text-xs text-emerald-300">Channel voice saved. Voice readiness refreshed.</span> : null}
    </div>
    {error ? <p role="alert" className="mt-3 rounded border border-rose-400/30 p-3 text-xs text-rose-300">{message(error)}</p> : null}
  </section>;
}
