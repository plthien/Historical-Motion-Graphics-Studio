import type {
  ExternalImportPayload,
  ExternalImportValidationResponse,
  ExternalPromptDraftReadResponse,
  ExternalPromptPackage,
  SceneBreakdownRevisionResponseV4,
  ScenePlanArtifactV4,
} from './client';

export type ScenePlanCandidateResponseV4 = {
  scene_plan_artifact_id: string;
  scene_plan: ScenePlanArtifactV4;
  visual_prompt_package: ExternalPromptPackage;
};

export class SceneBreakdownAuthoringApiError extends Error {
  constructor(public readonly errorCode: string, message: string, public readonly details?: Record<string, unknown>) {
    super(message);
  }
}

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${baseUrl}${path}`, { ...init, headers });
  const body = await response.json() as T | { error_code?: string; message?: string; details?: Record<string, unknown> };
  if (!response.ok) {
    const error = body as { error_code?: string; message?: string; details?: Record<string, unknown> };
    throw new SceneBreakdownAuthoringApiError(error.error_code ?? 'SCENE_BREAKDOWN_AUTHORING_FAILED', error.message ?? 'Scene Breakdown authoring failed.', error.details);
  }
  return body as T;
}

function refreshActiveStudioQueries(): void {
  // The app's TanStack Query focus manager refetches active project/artifact
  // queries on focus/visibility changes. Step 2 publishes the canonical pair
  // outside StudioPage's historical mutation, so nudge the same standard
  // refetch path instead of maintaining a second cache implementation here.
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new Event('focus'));
  window.dispatchEvent(new Event('visibilitychange'));
}

export const sceneBreakdownTwoStepApi = {
  getScenePrompt(projectId: string): Promise<ExternalPromptDraftReadResponse> {
    return request(`/api/v4/projects/${projectId}/scene-breakdown/scene/prompt`);
  },
  buildScenePrompt(projectId: string): Promise<ExternalPromptPackage> {
    return request(`/api/v4/projects/${projectId}/scene-breakdown/scene/prompt`, { method: 'POST' });
  },
  validateSceneImport(projectId: string, payload: ExternalImportPayload): Promise<ExternalImportValidationResponse> {
    return request(`/api/v4/projects/${projectId}/scene-breakdown/scene/import/validate`, { method: 'POST', body: JSON.stringify(payload) });
  },
  importScene(projectId: string, payload: ExternalImportPayload): Promise<ScenePlanCandidateResponseV4> {
    return request(`/api/v4/projects/${projectId}/scene-breakdown/scene/import`, { method: 'POST', body: JSON.stringify(payload) });
  },
  getVisualPrompt(projectId: string): Promise<ExternalPromptDraftReadResponse> {
    return request(`/api/v4/projects/${projectId}/scene-breakdown/visual/prompt`);
  },
  buildVisualPrompt(projectId: string): Promise<ExternalPromptPackage> {
    return request(`/api/v4/projects/${projectId}/scene-breakdown/visual/prompt`, { method: 'POST' });
  },
  validateVisualImport(projectId: string, payload: ExternalImportPayload): Promise<ExternalImportValidationResponse> {
    return request(`/api/v4/projects/${projectId}/scene-breakdown/visual/import/validate`, { method: 'POST', body: JSON.stringify(payload) });
  },
  async importVisual(projectId: string, payload: ExternalImportPayload): Promise<SceneBreakdownRevisionResponseV4> {
    const result = await request<SceneBreakdownRevisionResponseV4>(`/api/v4/projects/${projectId}/scene-breakdown/visual/import`, { method: 'POST', body: JSON.stringify(payload) });
    refreshActiveStudioQueries();
    return result;
  },
};
