import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { apiClient, ApiError, type Channel, type Project } from './api/client';
import {
  type AppView,
  type WorkspaceContext,
  projectMatchesChannel,
  readStoredSelection,
  SELECTED_CHANNEL_STORAGE_KEY,
  SELECTED_PROJECT_STORAGE_KEY,
  writeStoredSelection,
} from './app/workspace';
import { ErrorBanner } from './components/ui/ErrorBanner';
import { StatusBadge } from './components/ui/StatusBadge';
import { CockpitShell } from './layout/CockpitShell';
import { PageHeader } from './layout/PageHeader';
import { ArtifactsPage } from './pages/ArtifactsPage';
import { ChannelsPage } from './pages/ChannelsPage';
import { JobsPage } from './pages/JobsPage';
import { ProjectsPage } from './pages/ProjectsPage';
import { SettingsPage } from './pages/SettingsPage';
import { StudioPage } from './pages/StudioPage';

type WorkspaceMessage = { severity: 'info' | 'warning'; text: string } | null;

const routes: Array<{ id: AppView; label: string; path: string; description: string }> = [
  { id: 'settings', label: 'Settings', path: '/settings', description: 'Provider setup, system health, and runtime controls.' },
  { id: 'channels', label: 'Channels', path: '/', description: 'Create channels, validate Channel DNA V3, and keep source context tidy.' },
  { id: 'projects', label: 'Projects', path: '/projects', description: 'Create projects under the active channel and preserve selection state.' },
  { id: 'studio', label: 'Studio', path: '/studio', description: 'Run the cockpit pipeline across topic ideas, scripts, story context, placeholders, and media review.' },
  { id: 'jobs', label: 'Jobs', path: '/jobs', description: 'Inspect generic job execution and queue progress.' },
  { id: 'artifacts', label: 'Artifacts', path: '/artifacts', description: 'Browse stored artifacts without leaving the shell.' },
];

function viewFromPathname(pathname: string): AppView {
  const found = routes.find((route) => route.path === pathname);
  return found?.id ?? 'channels';
}

function pathnameForView(view: AppView): string {
  return routes.find((route) => route.id === view)?.path ?? '/';
}

function errorMessage(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) return `${error.errorCode}: ${error.message}`;
  if (error instanceof Error) return error.message;
  return 'Unknown error';
}

function statusLabel(ready: boolean, readyText: string, missingText: string): string {
  return ready ? readyText : missingText;
}

function channelNextStep(channel: Channel | null): string {
  if (!channel) return 'Select or create a channel to start the V3 flow.';
  if (!channel.has_channel_dna) return 'Import, validate, and save Channel DNA V3 for this channel.';
  return 'Channel is ready. Create or open a project in Studio.';
}

function projectNextStep(project: Project | null): string {
  if (!project) return 'Select or create a project to generate ideas.';
  if (!project.topic_ideas_artifact_id) return 'Generate topic ideas for this project.';
  if (!project.script_artifact_id) return 'Choose a topic idea and generate a script.';
  return 'Project has both topic ideas and a script artifact.';
}

function HeaderContextPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3">
      <div className="text-[10px] font-semibold uppercase tracking-[0.24em] text-slate-500">{label}</div>
      <div className="mt-1 text-sm font-medium text-slate-100">{value}</div>
    </div>
  );
}

function isSelectionResetMessage(message: WorkspaceMessage): boolean {
  return Boolean(message && (message.text.includes('previously selected') || message.text.includes('different channel')));
}

function StudioCompactHeader({ channelName, projectName }: { channelName: string; projectName: string }) {
  return (
    <header className="studio-compact-header panel-surface flex min-h-[56px] items-center overflow-hidden px-4 py-3 md:min-h-[60px] md:px-5">
      <div className="flex min-w-0 items-center gap-2 text-sm text-slate-500">
        <h1 className="text-lg font-semibold tracking-tight text-slate-50">Studio</h1>
        <span aria-hidden="true">/</span>
        <span className="truncate font-medium text-slate-200">{channelName}</span>
        <span aria-hidden="true">/</span>
        <span className="truncate font-medium text-slate-200">{projectName}</span>
      </div>
    </header>
  );
}

function Shell() {
  const [activeView, setActiveView] = useState<AppView>(() => viewFromPathname(window.location.pathname));
  const [selectedChannelId, setSelectedChannelIdState] = useState(() => readStoredSelection(SELECTED_CHANNEL_STORAGE_KEY));
  const [selectedProjectId, setSelectedProjectIdState] = useState(() => readStoredSelection(SELECTED_PROJECT_STORAGE_KEY));
  const [workspaceMessage, setWorkspaceMessage] = useState<WorkspaceMessage>(null);

  const healthQuery = useQuery({ queryKey: ['health'], queryFn: () => apiClient.getHealth(), refetchInterval: 30000 });
  const channelsQuery = useQuery({ queryKey: ['v3-channels'], queryFn: () => apiClient.listChannels() });
  const projectsQuery = useQuery({ queryKey: ['v3-projects'], queryFn: () => apiClient.listProjects() });

  const channels = channelsQuery.data?.items ?? [];
  const projects = projectsQuery.data?.items ?? [];

  const selectedChannel = useMemo(
    () => channels.find((channel) => channel.channel_id === selectedChannelId) ?? null,
    [channels, selectedChannelId],
  );
  const selectedProject = useMemo(
    () => projects.find((project) => project.project_id === selectedProjectId) ?? null,
    [projects, selectedProjectId],
  );

  function setSelectedChannelId(channelId: string) {
    setSelectedChannelIdState(channelId);
    writeStoredSelection(SELECTED_CHANNEL_STORAGE_KEY, channelId);
  }

  function setSelectedProjectId(projectId: string) {
    setSelectedProjectIdState(projectId);
    writeStoredSelection(SELECTED_PROJECT_STORAGE_KEY, projectId);
  }

  useEffect(() => {
    const nextPath = pathnameForView(activeView);
    if (window.location.pathname !== nextPath) {
      window.history.pushState({}, '', nextPath);
    }
  }, [activeView]);

  useEffect(() => {
    const handlePopState = () => setActiveView(viewFromPathname(window.location.pathname));
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  useEffect(() => {
    if (!channelsQuery.isSuccess) return;
    if (!selectedChannelId) return;
    const exists = channels.some((channel) => channel.channel_id === selectedChannelId);
    if (!exists) {
      setSelectedChannelId('');
      setWorkspaceMessage({ severity: 'warning', text: 'The previously selected channel no longer exists, so the selection was cleared.' });
    }
  }, [channels, channelsQuery.isSuccess, selectedChannelId]);

  useEffect(() => {
    if (!projectsQuery.isSuccess) return;
    if (!selectedProjectId) return;
    const project = projects.find((item) => item.project_id === selectedProjectId);
    if (!project) {
      setSelectedProjectId('');
      setWorkspaceMessage({ severity: 'warning', text: 'The previously selected project no longer exists, so the selection was cleared.' });
      return;
    }
    if (selectedChannelId && !projectMatchesChannel(project, selectedChannelId)) {
      setSelectedProjectId('');
      setWorkspaceMessage({ severity: 'info', text: 'The selected project belonged to a different channel, so the project selection was reset.' });
    }
  }, [projects, projectsQuery.isSuccess, selectedChannelId, selectedProjectId]);

  useEffect(() => {
    if (!isSelectionResetMessage(workspaceMessage)) return;
    if (!selectedChannel || !selectedProject) return;
    if (!projectMatchesChannel(selectedProject, selectedChannel.channel_id)) return;
    setWorkspaceMessage(null);
  }, [selectedChannel, selectedProject, workspaceMessage]);

  const activeRoute = useMemo(() => routes.find((route) => route.id === activeView) ?? routes[0], [activeView]);

  const workspace: WorkspaceContext = {
    channels,
    projects,
    selectedChannelId,
    selectedProjectId,
    selectedChannel,
    selectedProject,
    setSelectedChannelId: (channelId) => {
      setSelectedChannelId(channelId);
      if (!channelId) {
        setSelectedProjectId('');
        return;
      }
      const nextProject = projects.find((project) => project.project_id === selectedProjectId);
      if (nextProject && !projectMatchesChannel(nextProject, channelId)) {
        setSelectedProjectId('');
      }
    },
    setSelectedProjectId,
  };

  function renderContent() {
    if (activeView === 'channels') return <ChannelsPage workspace={workspace} onNavigate={setActiveView} />;
    if (activeView === 'projects') return <ProjectsPage workspace={workspace} onNavigate={setActiveView} />;
    if (activeView === 'studio') return <StudioPage workspace={workspace} onNavigate={setActiveView} />;
    if (activeView === 'settings') return <SettingsPage />;
    if (activeView === 'jobs') return <JobsPage />;
    return <ArtifactsPage />;
  }

  return (
    <CockpitShell
      activeView={activeView}
      onNavigate={setActiveView}
      routes={routes}
      channelName={selectedChannel?.name ?? 'No channel selected'}
      projectName={selectedProject?.name ?? 'No project selected'}
      healthLabel={healthQuery.data?.status === 'ok' ? `Backend ready ${healthQuery.data.version}` : 'Checking backend'}
    >
      {activeView === 'studio' ? (
        <StudioCompactHeader
          channelName={selectedChannel?.name ?? 'No channel selected'}
          projectName={selectedProject?.name ?? 'No project selected'}
        />
      ) : (
        <PageHeader
          eyebrow="V3 Workspace Context"
          title={activeRoute.label}
          description={`${activeRoute.description} Channel: ${channelNextStep(selectedChannel)} Project: ${projectNextStep(selectedProject)}`}
          context={
            <>
              <HeaderContextPill label="Channel" value={selectedChannel?.name ?? 'No channel selected'} />
              <HeaderContextPill label="Project" value={selectedProject?.name ?? 'No project selected'} />
              <HeaderContextPill label="Channel DNA" value={statusLabel(Boolean(selectedChannel?.has_channel_dna), 'Valid', 'Missing')} />
              <HeaderContextPill label="Topic Ideas" value={statusLabel(Boolean(selectedProject?.topic_ideas_artifact_id), 'Generated', 'Missing')} />
              <HeaderContextPill label="Script" value={statusLabel(Boolean(selectedProject?.script_artifact_id), 'Generated', 'Missing')} />
            </>
          }
          actions={
            <>
              <StatusBadge status={healthQuery.data?.status === 'ok' ? 'approved' : 'pending'} label={healthQuery.data?.status === 'ok' ? 'Backend Ready' : 'Checking backend'} />
              <button
                type="button"
                onClick={() => setActiveView('channels')}
                className="rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-2 text-sm font-medium text-slate-100 transition hover:bg-white/[0.05]"
              >
                Change Channel
              </button>
              <button
                type="button"
                onClick={() => setActiveView('projects')}
                className="rounded-2xl border border-cyan-400/30 bg-cyan-500/10 px-4 py-2 text-sm font-medium text-cyan-50 transition hover:bg-cyan-500/20"
              >
                Change Project
              </button>
            </>
          }
        />
      )}

      <div className="space-y-4">
        {workspaceMessage ? (
          <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
            {workspaceMessage.text}
          </div>
        ) : null}
        {[channelsQuery.error, projectsQuery.error].map((error, index) =>
          errorMessage(error) ? <ErrorBanner key={index} message={errorMessage(error)!} /> : null,
        )}
        {renderContent()}
      </div>
    </CockpitShell>
  );
}

const queryClient = new QueryClient();

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Shell />
    </QueryClientProvider>
  );
}
