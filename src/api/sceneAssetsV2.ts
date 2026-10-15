export type SceneAssetV2ResolutionStatus = 'not_generated' | 'queued' | 'generating' | 'candidate_ready' | 'approved' | 'failed';
export type SceneAssetV2ReviewStatus = 'unreviewed' | 'current_draft' | 'approved' | 'rejected';
export type SceneAssetV2OperationStatus = 'queued' | 'running' | 'finalizing' | 'completed' | 'partial' | 'failed' | 'cancelled' | 'superseded';
export type SceneAssetV2OperationType = 'generate_all' | 'generate_section' | 'generate_beat' | 'generate_missing' | 'retry_failed' | 'retry_beat' | 'regenerate_beat' | 'regenerate_section';
export type SceneAssetV2ExecutionHealth = 'healthy' | 'stalled' | 'terminal';
export type SceneAssetV2ManifestState = 'unpublished' | 'current' | 'stale';

export interface SceneAssetV2Error {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

export interface SceneAssetV2ReferenceBinding {
  anchor_type: 'character' | 'location' | 'prop' | 'recurring_visual_subject';
  anchor_id: string;
  reference_artifact_id: string;
  media_artifact_id: string;
  approval_set_artifact_id: string;
  source_definition_hash: string;
  media_sha256: string;
  mime_type: string;
  image_artifact_id: string;
}

export interface SceneAssetV2Treatment {
  visual_concept: string;
  subject_action: string;
  environment_emphasis: string;
  framing: string;
  camera_angle: string;
  camera_movement: string;
  composition: string;
  lighting_direction: string;
  color_emphasis: string;
  motion_guidance: string;
  continuity_with_previous: string;
  continuity_notes: string;
  production_notes: string;
  negative_requirements: string[];
  visible_text: string[];
}

export interface SceneAssetV2Candidate {
  candidate_id: string;
  artifact_id: string;
  source_kind: 'generated' | 'uploaded';
  mime_type: string;
  sha256: string;
  width: number | null;
  height: number | null;
  is_current_draft: boolean;
  is_approved: boolean;
  is_rejected: boolean;
  generated_from_current_prompt: boolean;
}

export interface SceneAssetV2Continuity {
  required: boolean;
  source_beat_id: string | null;
  source_ready: boolean;
  source_candidate_id: string | null;
  block_reason: string | null;
}

export interface SceneAssetV2Beat {
  scene_id: string;
  beat_id: string;
  beat_order: number;
  local_beat_order: number;
  computed_duration_seconds: number;
  resolution_status: SceneAssetV2ResolutionStatus;
  review_status: SceneAssetV2ReviewStatus;
  beat_state_version: number;
  active_operation_beat_id: string | null;
  current_draft_candidate_id: string | null;
  selected_candidate_id: string | null;
  prompt_revision_id: string;
  prompt_item_id: string;
  prompt_hash: string;
  prompt_origin: 'compiled' | 'manual_edit';
  image_prompt: string;
  negative_prompt: string;
  reference_bindings: SceneAssetV2ReferenceBinding[];
  treatment: SceneAssetV2Treatment;
  script_quote: string | null;
  candidates: SceneAssetV2Candidate[];
  continuity: SceneAssetV2Continuity;
  approval_change_impact_count: number;
  can_generate: boolean;
  can_retry: boolean;
  can_regenerate: boolean;
  can_upload: boolean;
  can_review: boolean;
  can_approve: boolean;
  error: SceneAssetV2Error | null;
}

export interface SceneAssetV2Scene {
  scene_id: string;
  scene_order: number;
  section_id: string | null;
  section_order: number | null;
  section_title: string | null;
  beats: SceneAssetV2Beat[];
}

export interface SceneAssetV2WorkspaceActions {
  can_generate_all: boolean;
  can_generate_missing: boolean;
  can_retry_failed: boolean;
  can_approve_manifest: boolean;
  can_publish_manifest: boolean;
  eligible_generate_count: number;
  failed_count: number;
  approved_count: number;
  total_count: number;
}

export interface SceneAssetV2OperationSummary {
  operation_id: string;
  operation_type: string;
  status: SceneAssetV2OperationStatus;
  progress: number;
  error_code: string | null;
  error_message: string | null;
}

export interface SceneAssetV2WorkspaceResponse {
  project_id: string;
  readiness: { ready: boolean; blockers: string[] };
  current_prompt_bundle_artifact_id: string | null;
  published_manifest_artifact_id: string | null;
  published_manifest_status: string | null;
  manifest_state: SceneAssetV2ManifestState;
  latest_operation: SceneAssetV2OperationSummary | null;
  scenes: SceneAssetV2Scene[];
  actions: SceneAssetV2WorkspaceActions;
}

export interface SceneAssetV2BeatResponse {
  project_id: string;
  published_manifest_artifact_id: string | null;
  published_manifest_status: string | null;
  manifest_state: SceneAssetV2ManifestState;
  beat: SceneAssetV2Beat;
  actions: SceneAssetV2WorkspaceActions;
}

export interface SceneAssetV2MutationResponse {
  project_id: string;
  published_manifest_artifact_id: string | null;
  published_manifest_status: string | null;
  manifest_state: SceneAssetV2ManifestState;
  beat: SceneAssetV2Beat;
  invalidated_beat_ids: string[];
  actions: SceneAssetV2WorkspaceActions;
}

export interface SceneAssetV2SectionApprovalItem {
  beat_id: string;
  candidate_id: string;
  expected_beat_version: number;
}

export interface SceneAssetV2SectionApprovalResponse {
  project_id: string;
  section_id: string;
  published_manifest_artifact_id: string | null;
  published_manifest_status: string | null;
  manifest_state: SceneAssetV2ManifestState;
  approved_beat_ids: string[];
  skipped_beat_ids: string[];
  invalidated_beat_ids: string[];
  actions: SceneAssetV2WorkspaceActions;
}

export interface SceneAssetV2OperationSubmissionResponse {
  project_id: string;
  operation_id: string;
  operation_type: SceneAssetV2OperationType;
  status: 'queued';
  total_beat_count: number;
  scheduled_beat_ids: string[];
  blocked_beat_ids: string[];
  next_reconcile_at: string;
}

export interface SceneAssetV2TaskStatus {
  operation_task_id: string;
  candidate_index: number;
  status: 'queued' | 'submitting' | 'submit_outcome_unknown' | 'waiting_provider' | 'downloading' | 'materializing' | 'completed' | 'failed' | 'cancelled';
  media_task_id: string | null;
  next_reconcile_at: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface SceneAssetV2BeatOperationStatus {
  operation_beat_id: string;
  beat_id: string;
  status: 'queued' | 'running' | 'candidate_ready' | 'failed' | 'cancelled';
  health: SceneAssetV2ExecutionHealth;
  stalled: boolean;
  stalled_reason: string | null;
  last_progress_at: string | null;
  candidate_count: number;
  tasks: SceneAssetV2TaskStatus[];
  error_code: string | null;
  error_message: string | null;
}

export interface SceneAssetV2OperationStatusResponse {
  project_id: string;
  operation_id: string;
  operation_type: SceneAssetV2OperationType;
  status: SceneAssetV2OperationStatus;
  health: SceneAssetV2ExecutionHealth;
  stalled: boolean;
  stalled_reason: string | null;
  last_progress_at: string;
  total_beat_count: number;
  completed_count: number;
  failed_count: number;
  cancelled_count: number;
  next_reconcile_at: string | null;
  cancel_requested_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
  beats: SceneAssetV2BeatOperationStatus[];
}

export interface SceneAssetV2ExecutionStatusResponse {
  project_id: string;
  active: boolean;
  active_operation_count: number;
  stalled_operation_count: number;
  operations: SceneAssetV2OperationStatusResponse[];
}

export interface SceneAssetV2CancelResponse {
  project_id: string;
  operation_id: string;
  status: 'cancelled';
  already_cancelled: boolean;
  cancelled_task_count: number;
  cancelled_beat_count: number;
  completed_count: number;
  failed_count: number;
  cancelled_count: number;
}

export interface SceneAssetV2PublishResponse {
  project_id: string;
  artifact_id: string;
  status: 'approved';
  beat_count: number;
  already_current: boolean;
}

interface ApiErrorPayload {
  error_code: string;
  message: string;
  user_message?: string;
  retryable?: boolean;
  details?: Record<string, unknown>;
}

export class SceneAssetV2ApiError extends Error {
  readonly errorCode: string;
  readonly retryable: boolean;
  readonly details?: Record<string, unknown>;

  constructor(payload: ApiErrorPayload) {
    super(payload.user_message ?? payload.message);
    this.name = 'SceneAssetV2ApiError';
    this.errorCode = payload.error_code;
    this.retryable = Boolean(payload.retryable);
    this.details = payload.details;
  }
}

class SceneAssetsV2Client {
  private readonly baseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000';

  private url(path: string): string {
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
      throw new SceneAssetV2ApiError({
        error_code: 'NETWORK_ERROR',
        message: error instanceof Error ? error.message : 'Network request failed.',
        retryable: true,
      });
    }
    const contentType = response.headers.get('content-type') ?? '';
    const body = contentType.includes('application/json') ? await response.json() : null;
    if (!response.ok) {
      throw new SceneAssetV2ApiError((body ?? {
        error_code: 'HTTP_ERROR',
        message: `Request failed with status ${response.status}.`,
        retryable: response.status >= 500,
      }) as ApiErrorPayload);
    }
    return body as T;
  }

  private async resolveBeatVersion(projectId: string, beatId: string, expectedBeatVersion?: number): Promise<number> {
    if (expectedBeatVersion !== undefined) return expectedBeatVersion;
    return (await this.getBeat(projectId, beatId)).beat.beat_state_version;
  }

  getWorkspace(projectId: string, signal?: AbortSignal): Promise<SceneAssetV2WorkspaceResponse> {
    return this.request(`/api/v4/projects/${projectId}/scene-assets`, { signal });
  }

  getBeat(projectId: string, beatId: string, signal?: AbortSignal): Promise<SceneAssetV2BeatResponse> {
    return this.request(`/api/v4/projects/${projectId}/scene-assets/beats/${beatId}`, { signal });
  }

  getExecutionStatus(projectId: string, signal?: AbortSignal): Promise<SceneAssetV2ExecutionStatusResponse> {
    return this.request(`/api/v4/projects/${projectId}/scene-assets/execution-status`, { signal });
  }

  getCandidateContentUrl(projectId: string, artifactId: string): string {
    return this.url(`/api/v4/projects/${projectId}/scene-assets/${artifactId}/content`);
  }

  submitBulk(
    projectId: string,
    operationType: 'generate_all' | 'generate_section' | 'generate_missing' | 'retry_failed' | 'regenerate_section',
    candidateCount = 1,
    sectionId: string | null = null,
  ): Promise<SceneAssetV2OperationSubmissionResponse> {
    return this.request(`/api/v4/projects/${projectId}/scene-assets/jobs`, {
      method: 'POST',
      body: JSON.stringify({
        operation_type: operationType,
        candidate_count: candidateCount,
        ...(sectionId ? { section_id: sectionId } : {}),
      }),
    });
  }

  generateBeat(projectId: string, beatId: string, candidateCount = 1): Promise<SceneAssetV2OperationSubmissionResponse> {
    return this.request(`/api/v4/projects/${projectId}/scene-assets/beats/${beatId}/generate/jobs`, {
      method: 'POST',
      body: JSON.stringify({ candidate_count: candidateCount }),
    });
  }

  retryBeat(projectId: string, beatId: string, candidateCount = 1): Promise<SceneAssetV2OperationSubmissionResponse> {
    return this.request(`/api/v4/projects/${projectId}/scene-assets/beats/${beatId}/retry/jobs`, {
      method: 'POST',
      body: JSON.stringify({ candidate_count: candidateCount }),
    });
  }

  regenerateBeat(projectId: string, beatId: string, candidateCount = 1): Promise<SceneAssetV2OperationSubmissionResponse> {
    return this.request(`/api/v4/projects/${projectId}/scene-assets/beats/${beatId}/regenerate/jobs`, {
      method: 'POST',
      body: JSON.stringify({ candidate_count: candidateCount }),
    });
  }

  getOperation(projectId: string, operationId: string, signal?: AbortSignal): Promise<SceneAssetV2OperationStatusResponse> {
    return this.request(`/api/v4/projects/${projectId}/scene-assets/jobs/${operationId}`, { signal });
  }

  cancelOperation(projectId: string, operationId: string): Promise<SceneAssetV2CancelResponse> {
    return this.request(`/api/v4/projects/${projectId}/scene-assets/jobs/${operationId}/cancel`, { method: 'POST' });
  }

  async upload(projectId: string, beatId: string, file: File, expectedBeatVersion?: number): Promise<SceneAssetV2MutationResponse> {
    const version = await this.resolveBeatVersion(projectId, beatId, expectedBeatVersion);
    const body = new FormData();
    body.append('expected_beat_version', String(version));
    body.append('file', file);
    return this.request(`/api/v4/projects/${projectId}/scene-assets/beats/${beatId}/upload`, { method: 'POST', body });
  }

  async decide(
    projectId: string,
    beatId: string,
    candidateId: string | null,
    decision: 'set-current-draft' | 'approve' | 'reject' | 'clear-approval',
    expectedBeatVersion?: number,
  ): Promise<SceneAssetV2MutationResponse> {
    const version = await this.resolveBeatVersion(projectId, beatId, expectedBeatVersion);
    return this.request(`/api/v4/projects/${projectId}/scene-assets/beats/${beatId}/candidates/${decision}`, {
      method: 'POST',
      body: JSON.stringify({ expected_beat_version: version, candidate_id: candidateId }),
    });
  }

  approveSectionReady(
    projectId: string,
    sectionId: string,
    beats: SceneAssetV2SectionApprovalItem[],
  ): Promise<SceneAssetV2SectionApprovalResponse> {
    return this.request(`/api/v4/projects/${projectId}/scene-assets/sections/${sectionId}/approve-ready`, {
      method: 'POST',
      body: JSON.stringify({ beats }),
    });
  }

  publishManifest(projectId: string): Promise<SceneAssetV2PublishResponse> {
    return this.request(`/api/v4/projects/${projectId}/scene-assets/approve`, { method: 'POST' });
  }

  async editPrompt(
    projectId: string,
    beatId: string,
    expectedPromptRevisionId: string,
    imagePrompt: string,
    negativePrompt: string,
    expectedBeatVersion?: number,
  ): Promise<unknown> {
    const version = await this.resolveBeatVersion(projectId, beatId, expectedBeatVersion);
    return this.request(`/api/v4/projects/${projectId}/scene-assets/beats/${beatId}/prompt`, {
      method: 'PUT',
      body: JSON.stringify({
        expected_beat_version: version,
        expected_prompt_revision_id: expectedPromptRevisionId,
        image_prompt: imagePrompt,
        negative_prompt: negativePrompt,
      }),
    });
  }

  async restorePrompt(
    projectId: string,
    beatId: string,
    expectedPromptRevisionId: string,
    expectedBeatVersion?: number,
  ): Promise<unknown> {
    const version = await this.resolveBeatVersion(projectId, beatId, expectedBeatVersion);
    return this.request(`/api/v4/projects/${projectId}/scene-assets/beats/${beatId}/prompt/restore-compiled`, {
      method: 'POST',
      body: JSON.stringify({
        expected_beat_version: version,
        expected_prompt_revision_id: expectedPromptRevisionId,
      }),
    });
  }
}

export const sceneAssetsV2Client = new SceneAssetsV2Client();
