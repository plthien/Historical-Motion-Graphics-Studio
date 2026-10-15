export type VisualReferenceV2AnchorType = 'character' | 'location' | 'prop' | 'recurring_visual_subject';
export type VisualReferenceV2ResolutionStatus = 'not_generated' | 'candidate_ready' | 'approved' | 'failed';
export type VisualReferenceV2ReviewStatus = 'unreviewed' | 'draft_selected' | 'approved' | 'rejected';
export type VisualReferenceV2OperationStatus = 'queued' | 'running' | 'finalizing' | 'completed' | 'partial' | 'failed' | 'cancelled' | 'superseded';
export type VisualReferenceV2TaskStatus = 'queued' | 'submitting' | 'submit_outcome_unknown' | 'waiting_provider' | 'downloading' | 'materializing' | 'completed' | 'failed' | 'cancelled';
export type VisualReferenceV2AspectRatio = '1:1' | '3:4' | '4:3' | '9:16' | '16:9';

export interface VisualReferenceV2Candidate {
  candidate_id: string;
  source_prompt_revision_id: string;
  candidate_index: number;
  media_artifact_id: string;
  media_task_id: string | null;
  mime_type: string;
  media_sha256: string;
  file_url: string;
  source_kind: string;
  compatible_with_current_definition: boolean;
  created_at: string;
}

export interface VisualReferenceV2Anchor {
  anchor_type: VisualReferenceV2AnchorType;
  anchor_id: string;
  label: string;
  source_definition_hash: string;
  version: number;
  initialized: boolean;
  upstream_stale: boolean;
  resolution_status: VisualReferenceV2ResolutionStatus;
  review_status: VisualReferenceV2ReviewStatus;
  prompt_revision_id: string | null;
  prompt: string;
  negative_prompt: string;
  candidates: VisualReferenceV2Candidate[];
  selected_candidate_id: string | null;
  approved_candidate_id: string | null;
  active_operation_anchor_id: string | null;
  last_error: Record<string, unknown> | null;
  last_progress_at: string | null;
}

export interface VisualReferenceV2Readiness {
  required_count: number;
  approved_count: number;
  ready: boolean;
  missing_anchor_keys: string[];
  stale_anchor_keys: string[];
}

export interface VisualReferenceV2Workspace {
  project_id: string;
  style_bible_artifact_id: string | null;
  published_visual_reference_set_artifact_id: string | null;
  published_approval_set_artifact_id: string | null;
  generated: boolean;
  anchors: VisualReferenceV2Anchor[];
  readiness: VisualReferenceV2Readiness;
  active_operation_count: number;
}

export interface VisualReferenceV2ExecutionTask {
  operation_task_id: string;
  candidate_index: number;
  status: VisualReferenceV2TaskStatus;
  media_task_id: string | null;
  next_reconcile_at: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface VisualReferenceV2ExecutionAnchor {
  operation_anchor_id: string;
  anchor_type: VisualReferenceV2AnchorType;
  anchor_id: string;
  status: 'queued' | 'running' | 'candidate_ready' | 'failed' | 'cancelled';
  candidate_count: number;
  tasks: VisualReferenceV2ExecutionTask[];
}

export interface VisualReferenceV2Execution {
  operation_id: string;
  operation_type: string;
  status: VisualReferenceV2OperationStatus;
  total_anchor_count: number;
  completed_count: number;
  failed_count: number;
  cancelled_count: number;
  next_reconcile_at: string | null;
  created_at: string;
  updated_at: string;
  error_code: string | null;
  error_message: string | null;
  anchors: VisualReferenceV2ExecutionAnchor[];
}

export interface VisualReferenceV2Executions {
  project_id: string;
  executions: VisualReferenceV2Execution[];
}

export interface VisualReferenceV2GenerateRequest {
  style_bible_artifact_id: string;
  expected_visual_reference_set_artifact_id: string | null;
  candidate_count: number;
  aspect_ratio?: VisualReferenceV2AspectRatio;
  aspect_ratios_by_anchor_type?: Partial<Record<VisualReferenceV2AnchorType, VisualReferenceV2AspectRatio>>;
}

export interface VisualReferenceV2OperationSubmission {
  project_id: string;
  operation_id: string;
  operation_type: string;
  status: 'queued';
  total_anchor_count: number;
  scheduled_anchors: Array<{ anchor_type: string; anchor_id: string }>;
  blocked_anchors: Array<{ anchor_type: string; anchor_id: string }>;
  next_reconcile_at: string;
}

export interface VisualReferenceV2MutationResult {
  project_id: string;
  anchor_type: VisualReferenceV2AnchorType;
  anchor_id: string;
  version: number;
  prompt_revision_id?: string | null;
  prompt?: string | null;
  negative_prompt?: string | null;
  candidate_id?: string | null;
  media_artifact_id?: string | null;
  media_sha256?: string | null;
  mime_type?: string | null;
  resolution_status?: string | null;
  review_status?: string | null;
  selected_candidate_id?: string | null;
  approved_candidate_id?: string | null;
  published_visual_reference_set_artifact_id?: string | null;
  published_approval_set_artifact_id?: string | null;
}

export interface VisualReferenceV2PublicationRequest {
  expected_visual_reference_set_artifact_id: string | null;
  expected_approval_set_artifact_id: string | null;
}

export interface VisualReferenceV2PublicationResult {
  project_id: string;
  visual_reference_set_artifact_id: string;
  approval_set_artifact_id: string;
  published_anchor_count: number;
  ready: true;
}

interface ApiErrorPayload {
  error_code: string;
  message: string;
  user_message?: string;
  retryable?: boolean;
  details?: Record<string, unknown>;
}

export class VisualReferenceV2ApiError extends Error {
  readonly errorCode: string;
  readonly retryable: boolean;
  readonly details?: Record<string, unknown>;

  constructor(payload: ApiErrorPayload) {
    super(payload.user_message ?? payload.message);
    this.name = 'VisualReferenceV2ApiError';
    this.errorCode = payload.error_code;
    this.retryable = Boolean(payload.retryable);
    this.details = payload.details;
  }
}

export const visualReferenceV2Keys = {
  workspace: (projectId: string, styleBibleArtifactId: string) => ['visual-references-v2', projectId, styleBibleArtifactId] as const,
  anchor: (projectId: string, anchorType: VisualReferenceV2AnchorType, anchorId: string) => ['visual-reference-anchor-v2', projectId, anchorType, anchorId] as const,
  executions: (projectId: string) => ['visual-reference-executions-v2', projectId] as const,
};

export function patchVisualReferenceAnchor(
  workspace: VisualReferenceV2Workspace,
  anchor: VisualReferenceV2Anchor,
): VisualReferenceV2Workspace {
  let found = false;
  const anchors = workspace.anchors.map((current) => {
    if (current.anchor_type !== anchor.anchor_type || current.anchor_id !== anchor.anchor_id) return current;
    found = true;
    return anchor;
  });
  if (!found) return workspace;
  const approved = anchors.filter((item) => !item.upstream_stale && item.resolution_status === 'approved' && item.approved_candidate_id).length;
  const missing = anchors
    .filter((item) => item.upstream_stale || item.resolution_status !== 'approved' || !item.approved_candidate_id)
    .map((item) => `${item.anchor_type}:${item.anchor_id}`);
  return {
    ...workspace,
    generated: anchors.some((item) => item.candidates.length > 0),
    anchors,
    readiness: {
      required_count: anchors.length,
      approved_count: approved,
      ready: anchors.length > 0 && approved === anchors.length,
      missing_anchor_keys: missing,
      stale_anchor_keys: anchors.filter((item) => item.upstream_stale).map((item) => `${item.anchor_type}:${item.anchor_id}`),
    },
  };
}

class VisualReferencesV2Client {
  private readonly baseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000';

  resolveUrl(path: string): string {
    return new URL(path, this.baseUrl).toString();
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const headers = new Headers(init?.headers);
    const isFormData = typeof FormData !== 'undefined' && init?.body instanceof FormData;
    if (!isFormData && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, { ...init, headers });
    } catch (error) {
      if (init?.signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error;
      throw new VisualReferenceV2ApiError({
        error_code: 'NETWORK_ERROR',
        message: error instanceof Error ? error.message : 'Network request failed.',
        retryable: true,
      });
    }
    const contentType = response.headers.get('content-type') ?? '';
    const body = contentType.includes('application/json') ? await response.json() : null;
    if (!response.ok) {
      throw new VisualReferenceV2ApiError((body ?? {
        error_code: 'HTTP_ERROR',
        message: `Request failed with status ${response.status}.`,
        retryable: response.status >= 500,
      }) as ApiErrorPayload);
    }
    return body as T;
  }

  getWorkspace(projectId: string, signal?: AbortSignal): Promise<VisualReferenceV2Workspace> {
    return this.request(`/api/v4/projects/${projectId}/visual-references/v2/workspace`, { signal });
  }

  getAnchor(
    projectId: string,
    anchorType: VisualReferenceV2AnchorType,
    anchorId: string,
    signal?: AbortSignal,
  ): Promise<VisualReferenceV2Anchor> {
    return this.request(`/api/v4/projects/${projectId}/visual-references/v2/anchors/${anchorType}/${anchorId}`, { signal });
  }

  getExecutions(projectId: string, signal?: AbortSignal): Promise<VisualReferenceV2Executions> {
    return this.request(`/api/v4/projects/${projectId}/visual-references/v2/executions`, { signal });
  }

  submitGeneration(projectId: string, request: VisualReferenceV2GenerateRequest): Promise<VisualReferenceV2OperationSubmission> {
    return this.request(`/api/v4/projects/${projectId}/visual-references/v2/operations`, {
      method: 'POST',
      body: JSON.stringify(request),
    });
  }

  regenerateAnchor(
    projectId: string,
    anchorType: VisualReferenceV2AnchorType,
    anchorId: string,
    request: VisualReferenceV2GenerateRequest,
  ): Promise<VisualReferenceV2OperationSubmission> {
    return this.request(`/api/v4/projects/${projectId}/visual-references/v2/anchors/${anchorType}/${anchorId}/regenerate`, {
      method: 'POST',
      body: JSON.stringify(request),
    });
  }

  retryFailed(projectId: string, request: VisualReferenceV2GenerateRequest): Promise<VisualReferenceV2OperationSubmission> {
    return this.request(`/api/v4/projects/${projectId}/visual-references/v2/retry-failed`, {
      method: 'POST',
      body: JSON.stringify(request),
    });
  }

  getOperation(projectId: string, operationId: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return this.request(`/api/v4/projects/${projectId}/visual-references/v2/operations/${operationId}`, { signal });
  }

  cancelOperation(projectId: string, operationId: string): Promise<Record<string, unknown>> {
    return this.request(`/api/v4/projects/${projectId}/visual-references/v2/operations/${operationId}/cancel`, { method: 'POST' });
  }

  updatePrompt(
    projectId: string,
    anchorType: VisualReferenceV2AnchorType,
    anchorId: string,
    expectedVersion: number,
    prompt: string,
    negativePrompt: string,
  ): Promise<VisualReferenceV2MutationResult> {
    return this.request(`/api/v4/projects/${projectId}/visual-references/v2/anchors/${anchorType}/${anchorId}/prompt`, {
      method: 'PUT',
      body: JSON.stringify({ expected_version: expectedVersion, prompt, negative_prompt: negativePrompt }),
    });
  }

  upload(
    projectId: string,
    anchorType: VisualReferenceV2AnchorType,
    anchorId: string,
    expectedVersion: number,
    file: File,
  ): Promise<VisualReferenceV2MutationResult> {
    const body = new FormData();
    body.append('expected_version', String(expectedVersion));
    body.append('file', file);
    return this.request(`/api/v4/projects/${projectId}/visual-references/v2/anchors/${anchorType}/${anchorId}/upload`, {
      method: 'POST',
      body,
    });
  }

  approve(
    projectId: string,
    anchorType: VisualReferenceV2AnchorType,
    anchorId: string,
    expectedVersion: number,
    candidateId: string,
  ): Promise<VisualReferenceV2MutationResult> {
    return this.request(`/api/v4/projects/${projectId}/visual-references/v2/anchors/${anchorType}/${anchorId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ expected_version: expectedVersion, candidate_id: candidateId }),
    });
  }

  clearApproval(
    projectId: string,
    anchorType: VisualReferenceV2AnchorType,
    anchorId: string,
    expectedVersion: number,
  ): Promise<VisualReferenceV2MutationResult> {
    return this.request(`/api/v4/projects/${projectId}/visual-references/v2/anchors/${anchorType}/${anchorId}/clear`, {
      method: 'POST',
      body: JSON.stringify({ expected_version: expectedVersion }),
    });
  }

  publish(projectId: string, request: VisualReferenceV2PublicationRequest): Promise<VisualReferenceV2PublicationResult> {
    return this.request(`/api/v4/projects/${projectId}/visual-references/v2/publish`, {
      method: 'POST',
      body: JSON.stringify(request),
    });
  }
}

export const visualReferencesV2Client = new VisualReferencesV2Client();
