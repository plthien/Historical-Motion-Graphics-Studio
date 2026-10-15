import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Sparkles } from 'lucide-react';
import { useMemo } from 'react';
import type { ProjectStyleBibleArtifactV4 } from '../../api/client';
import {
  visualReferencesV2Client,
  visualReferenceV2Keys,
  VisualReferenceV2ApiError,
} from '../../api/visualReferencesV2';
import { StatusBadge } from '../../components/ui/StatusBadge';
import { buildInheritedChannelDnaSummary } from './styleBibleReferenceViewModel';
import { StyleBibleVisualReferencesWorkspaceV2 } from './StyleBibleVisualReferencesWorkspaceV2';

function publicationError(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof VisualReferenceV2ApiError) return `${error.errorCode}: ${error.message}`;
  return error instanceof Error ? error.message : 'Unable to publish Visual References.';
}

/**
 * Canonical Style Bible Visual References workspace.
 *
 * The stable entrypoint keeps inherited Channel DNA context and the explicit
 * publication boundary while all live generation/review behavior is delegated
 * to the durable V2 workspace. There is intentionally no V4 job-polling path.
 */
export function StyleBibleVisualReferencesWorkspace({
  projectId,
  styleBibleArtifactId,
  artifact,
  enabled,
}: {
  projectId: string;
  styleBibleArtifactId: string | null;
  artifact: ProjectStyleBibleArtifactV4;
  enabled: boolean;
}) {
  const queryClient = useQueryClient();
  const inherited = useMemo(
    () => buildInheritedChannelDnaSummary(artifact.visual_style_snapshot),
    [artifact.visual_style_snapshot],
  );
  const workspaceKey = styleBibleArtifactId
    ? visualReferenceV2Keys.workspace(projectId, styleBibleArtifactId)
    : ['visual-references-v2', projectId, 'none'] as const;
  const workspace = useQuery({
    queryKey: workspaceKey,
    queryFn: ({ signal }) => visualReferencesV2Client.getWorkspace(projectId, signal),
    enabled: enabled && Boolean(styleBibleArtifactId),
    retry: false,
  });
  const publish = useMutation({
    mutationFn: () => visualReferencesV2Client.publish(projectId, {
      expected_visual_reference_set_artifact_id:
        workspace.data?.published_visual_reference_set_artifact_id ?? null,
      expected_approval_set_artifact_id:
        workspace.data?.published_approval_set_artifact_id ?? null,
    }),
    onSuccess: async () => {
      await queryClient.refetchQueries({ queryKey: workspaceKey, type: 'active' });
    },
  });
  const canPublish = Boolean(
    workspace.data?.readiness.ready
    && workspace.data.active_operation_count === 0
    && !publish.isPending,
  );

  return (
    <div className="space-y-4" data-testid="style-bible-visual-reference-workspace">
      {enabled ? (
        <section
          className="rounded-2xl border border-violet-400/20 bg-violet-500/[0.055] p-4"
          data-testid="style-bible-inherited-channel-dna"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
              <Sparkles className="h-4 w-4 text-violet-300" />
              Channel DNA <span className="font-normal text-slate-500">(Inherited)</span>
            </div>
            <StatusBadge status="locked" label="Locked" />
          </div>
          <div className="mt-3 grid gap-3 text-xs leading-5 md:grid-cols-2">
            <p className="text-slate-400">
              <span className="font-semibold text-slate-300">Style:</span> {inherited.style}
            </p>
            <p className="text-slate-400">
              <span className="font-semibold text-slate-300">Negative:</span> {inherited.negative}
            </p>
          </div>
        </section>
      ) : null}

      {enabled && styleBibleArtifactId ? (
        <section
          className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3"
          data-testid="visual-reference-publication-boundary"
        >
          <div className="text-xs leading-5 text-slate-400">
            Publication freezes the currently approved anchor set for downstream Scene Assets.
          </div>
          <button
            type="button"
            disabled={!canPublish}
            onClick={() => publish.mutate()}
            className="rounded-lg border border-emerald-400/35 bg-emerald-500/10 px-3 py-2 text-sm font-semibold text-emerald-100 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {publish.isPending ? 'Publishing…' : 'Publish References'}
          </button>
          {publicationError(publish.error) ? (
            <p role="alert" className="w-full text-xs text-rose-300">{publicationError(publish.error)}</p>
          ) : null}
        </section>
      ) : null}

      <StyleBibleVisualReferencesWorkspaceV2
        projectId={projectId}
        styleBibleArtifactId={styleBibleArtifactId}
        enabled={enabled}
      />
    </div>
  );
}
