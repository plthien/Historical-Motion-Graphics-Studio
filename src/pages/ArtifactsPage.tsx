import { useQuery } from '@tanstack/react-query';
import { apiClient, ApiError } from '../api/client';
import { ErrorBanner } from '../components/ui/ErrorBanner';
import { SectionCard } from '../components/ui/SectionCard';
import { StatusBadge } from '../components/ui/StatusBadge';

function errorMessage(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) return `${error.errorCode}: ${error.message}`;
  if (error instanceof Error) return error.message;
  return 'Unknown error';
}

export function ArtifactsPage() {
  const artifactsQuery = useQuery({ queryKey: ['artifacts'], queryFn: () => apiClient.listArtifacts() });
  const artifacts = artifactsQuery.data?.items ?? [];

  return (
    <SectionCard title="Artifacts" description="Browse stored artifacts and keep lineage visible without turning the screen into a raw storage dump.">
      <div className="space-y-4">
        {errorMessage(artifactsQuery.error) ? <ErrorBanner message={errorMessage(artifactsQuery.error)!} /> : null}
        {artifacts.length === 0 ? (
          <div className="rounded-[1.25rem] border border-dashed border-white/10 bg-white/[0.02] p-6 text-sm leading-6 text-slate-400">
            No artifacts found yet. Topic ideas, scripts, story context, and future post-script artifacts will appear here as the pipeline progresses.
          </div>
        ) : (
          artifacts.map((artifact) => (
            <article key={artifact.artifact_id} className="rounded-[1.25rem] border border-white/10 bg-white/[0.03] p-4">
              <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
                <div className="space-y-2">
                  <div className="text-sm font-semibold text-slate-50">{artifact.logical_id}</div>
                  <div className="font-mono text-xs text-slate-500">{artifact.artifact_id}</div>
                  <div className="text-sm text-slate-400">{artifact.path ?? 'No file path'}</div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge status="ready" label={artifact.artifact_type} />
                  <StatusBadge status={artifact.status} />
                </div>
              </div>
            </article>
          ))
        )}
      </div>
    </SectionCard>
  );
}
