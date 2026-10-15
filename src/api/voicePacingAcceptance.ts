import { ApiError, type ErrorResponse } from './client';

export interface VoicePacingAcceptanceResponse {
  project_id: string;
  operation_id: string;
  job_id: string;
  section_id: string;
  status: 'accepted' | 'resuming';
  accepted_section_ids: string[];
  remaining_regeneration_required_section_ids: string[];
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000';

async function request<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: { Accept: 'application/json', 'Content-Type': 'application/json', ...(init.headers ?? {}) },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const errorPayload: ErrorResponse = payload && typeof payload === 'object'
      ? payload as ErrorResponse
      : { error_code: 'HTTP_ERROR', message: `Request failed with status ${response.status}.`, retryable: false, details: {} };
    throw new ApiError(errorPayload);
  }
  return payload as T;
}

export const voicePacingAcceptanceApi = {
  acceptSection(
    projectId: string,
    sectionId: string,
    sourceRemediationOperationId: string,
    expectedRemediationReportSha256: string,
    expectedRemediationIdentity: string,
  ): Promise<VoicePacingAcceptanceResponse> {
    return request<VoicePacingAcceptanceResponse>(
      `/api/v4/projects/${encodeURIComponent(projectId)}/voice/sections/${encodeURIComponent(sectionId)}/accept-pacing-deviation`,
      {
        method: 'POST',
        body: JSON.stringify({
          source_remediation_operation_id: sourceRemediationOperationId,
          expected_remediation_report_sha256: expectedRemediationReportSha256,
          expected_remediation_identity: expectedRemediationIdentity,
        }),
      },
    );
  },
};
