import type { QueryClient } from '@tanstack/react-query';
import type { SceneBreakdownReadResponseV4, SceneBreakdownRevisionResponseV4 } from '../../api/client';

export const sceneAssetsWorkspaceQueryKey = (projectId: string, promptBundleArtifactId: string | null) => (
  ['scene-assets-v2-workspace', projectId, promptBundleArtifactId] as const
);

export const sceneAssetsExecutionQueryKey = (projectId: string, promptBundleArtifactId: string | null) => (
  ['scene-assets-v2-execution', projectId, promptBundleArtifactId] as const
);

export function removeSceneAssetsProjectQueries(queryClient: QueryClient, projectId: string): void {
  queryClient.removeQueries({ queryKey: ['scene-assets-v2-workspace', projectId] });
  queryClient.removeQueries({ queryKey: ['scene-assets-v2-execution', projectId] });
}

export function cachePublishedScenePair(
  queryClient: QueryClient,
  projectId: string,
  response: SceneBreakdownRevisionResponseV4,
): void {
  queryClient.setQueryData<SceneBreakdownReadResponseV4>(
    ['studio-scene-breakdown-v4', projectId, response.scene_plan_artifact_id, response.visual_plan_artifact_id],
    {
      project_id: projectId,
      generated: true,
      scene_plan_artifact_id: response.scene_plan_artifact_id,
      scene_plan: response.scene_plan,
      visual_plan_artifact_id: response.visual_plan_artifact_id,
      visual_plan: response.visual_plan,
    },
  );
  removeSceneAssetsProjectQueries(queryClient, projectId);
}
