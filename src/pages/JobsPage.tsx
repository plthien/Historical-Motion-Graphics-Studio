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

export function JobsPage() {
  const jobsQuery = useQuery({ queryKey: ['jobs'], queryFn: () => apiClient.listJobs() });
  const jobs = jobsQuery.data?.items ?? [];

  return (
    <SectionCard title="Jobs" description="Inspect generic backend jobs and queue progress without leaving the cockpit shell.">
      <div className="space-y-4">
        {errorMessage(jobsQuery.error) ? <ErrorBanner message={errorMessage(jobsQuery.error)!} /> : null}
        {jobs.length === 0 ? (
          <div className="rounded-[1.25rem] border border-dashed border-white/10 bg-white/[0.02] p-6 text-sm leading-6 text-slate-400">
            No jobs found. Long-running backend work will appear here as the later V3 stages come online.
          </div>
        ) : (
          jobs.map((job) => (
            <article key={job.job_id} className="rounded-[1.25rem] border border-white/10 bg-white/[0.03] p-4">
              <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                <div className="space-y-2">
                  <div className="text-sm font-semibold text-slate-50">{job.job_type}</div>
                  <div className="font-mono text-xs text-slate-500">{job.job_id}</div>
                  {job.error_message ? <div className="text-sm text-rose-300">{job.error_code}: {job.error_message}</div> : null}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge status={job.status} />
                  <StatusBadge status="ready" label={`${job.progress}%`} />
                </div>
              </div>
            </article>
          ))
        )}
      </div>
    </SectionCard>
  );
}
