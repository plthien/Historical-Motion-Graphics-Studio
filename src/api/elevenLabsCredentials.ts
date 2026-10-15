import { ApiError, type ErrorResponse } from './client';

export interface ElevenLabsCredentialStatus {
  provider: 'elevenlabs';
  api_key_configured: boolean;
  source: 'stored' | 'environment' | 'none';
}

export interface ElevenLabsConnectionTest {
  provider: 'elevenlabs';
  status: 'ready';
  model_id: 'eleven_multilingual_v2';
  authentication_accepted: boolean;
  catalog_reachable: boolean;
  voice_count: number;
}

export type ElevenLabsVoicePreset = 'economy' | 'quality_cloned_voice';

export interface ElevenLabsProfessionalVoice {
  voice_id: string;
  display_name: string;
  language: string;
  ready_presets: ElevenLabsVoicePreset[];
}

export interface ElevenLabsProfessionalVoiceCatalog {
  provider: 'elevenlabs';
  voices: ElevenLabsProfessionalVoice[];
}

export interface ElevenLabsVoicePacingCalibrationEntry {
  model_id: 'eleven_flash_v2_5' | 'eleven_multilingual_v2';
  native_wpm_at_rate_1: number | null;
  source: 'override' | 'registry' | 'missing';
  default_native_wpm_at_rate_1: number | null;
}

export interface ElevenLabsVoicePacingCalibration {
  provider: 'elevenlabs';
  voice_id: string;
  calibrations: Record<ElevenLabsVoicePreset, ElevenLabsVoicePacingCalibrationEntry>;
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000';

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init?.headers ?? {}),
    },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const errorPayload: ErrorResponse =
      payload && typeof payload === 'object'
        ? (payload as ErrorResponse)
        : {
            error_code: 'HTTP_ERROR',
            message: `Request failed with status ${response.status}.`,
            retryable: false,
            details: {},
          };
    throw new ApiError(errorPayload);
  }
  return payload as T;
}

export const elevenLabsCredentialsApi = {
  getCredential(signal?: AbortSignal): Promise<ElevenLabsCredentialStatus> {
    return request<ElevenLabsCredentialStatus>(
      '/api/settings/tts/providers/elevenlabs/credential',
      { signal },
    );
  },

  saveCredential(
    apiKey: string,
    signal?: AbortSignal,
  ): Promise<ElevenLabsCredentialStatus> {
    return request<ElevenLabsCredentialStatus>(
      '/api/settings/tts/providers/elevenlabs/credential',
      {
        method: 'PUT',
        signal,
        body: JSON.stringify({ api_key: apiKey }),
      },
    );
  },

  clearCredential(
    signal?: AbortSignal,
  ): Promise<ElevenLabsCredentialStatus> {
    return request<ElevenLabsCredentialStatus>(
      '/api/settings/tts/providers/elevenlabs/credential',
      { method: 'DELETE', signal },
    );
  },

  listProfessionalVoices(
    signal?: AbortSignal,
  ): Promise<ElevenLabsProfessionalVoiceCatalog> {
    return request<ElevenLabsProfessionalVoiceCatalog>(
      '/api/settings/tts/providers/elevenlabs/professional-voices',
      { signal },
    );
  },

  getVoicePacingCalibration(
    voiceId: string,
    signal?: AbortSignal,
  ): Promise<ElevenLabsVoicePacingCalibration> {
    return request<ElevenLabsVoicePacingCalibration>(
      `/api/settings/tts/providers/elevenlabs/voices/${encodeURIComponent(voiceId)}/pacing-calibration`,
      { signal },
    );
  },

  saveVoicePacingCalibration(
    voiceId: string,
    voicePreset: ElevenLabsVoicePreset,
    nativeWpmAtRate1: number,
    signal?: AbortSignal,
  ): Promise<ElevenLabsVoicePacingCalibration> {
    return request<ElevenLabsVoicePacingCalibration>(
      `/api/settings/tts/providers/elevenlabs/voices/${encodeURIComponent(voiceId)}/pacing-calibration`,
      {
        method: 'PUT',
        signal,
        body: JSON.stringify({
          voice_preset: voicePreset,
          native_wpm_at_rate_1: nativeWpmAtRate1,
        }),
      },
    );
  },

  testConnection(
    signal?: AbortSignal,
  ): Promise<ElevenLabsConnectionTest> {
    return request<ElevenLabsConnectionTest>(
      '/api/settings/tts/providers/elevenlabs/test-connection',
      { method: 'POST', signal },
    );
  },
};
