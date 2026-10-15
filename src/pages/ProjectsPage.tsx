import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Clapperboard, FolderPlus, Search } from 'lucide-react';
import { useMemo, useState } from 'react';
import { apiClient, ApiError, type Project, type ProjectListResponse } from '../api/client';
import { type AppView, type WorkspaceContext } from '../app/workspace';
import { EmptyState } from '../components/ui/EmptyState';
import { ErrorBanner } from '../components/ui/ErrorBanner';
import { CreateProjectModal } from '../features/projects/CreateProjectModal';
import { ProjectCardGrid } from '../features/projects/ProjectCardGrid';
import { TextInput } from '../features/channels/forms';

function errorMessage(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) return `${error.errorCode}: ${error.message}`;
  if (error instanceof Error) return error.message;
  return 'Unknown error';
}

type ProjectsPageProps = {
  workspace: WorkspaceContext;
  onNavigate: (view: AppView) => void;
};

function matchesProject(project: Project, searchTerm: string) {
  const query = searchTerm.trim().toLowerCase();
  if (!query) return true;
  return project.name.toLowerCase().includes(query) || project.project_id.toLowerCase().includes(query);
}

export function ProjectsPage({ workspace, onNavigate }: ProjectsPageProps) {
  const queryClient = useQueryClient();
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [modalError, setModalError] = useState('');
  const [createForm, setCreateForm] = useState({ name: '', description: '' });

  const selectedChannel = workspace.selectedChannel;

  const createProject = useMutation({
    mutationFn: () => {
      if (!workspace.selectedChannelId) throw new Error('Select a channel before creating a project.');
      return apiClient.createProject({
        channel_id: workspace.selectedChannelId,
        name: createForm.name.trim(),
        description: createForm.description.trim() || null,
      });
    },
  });

  const projectsForChannel = useMemo(
    () => (workspace.selectedChannelId ? workspace.projects.filter((project) => project.channel_id === workspace.selectedChannelId) : []),
    [workspace.projects, workspace.selectedChannelId],
  );
  const filteredProjects = useMemo(
    () => projectsForChannel.filter((project) => matchesProject(project, searchTerm)),
    [projectsForChannel, searchTerm],
  );
  const canOpenCreateModal = Boolean(workspace.selectedChannelId && selectedChannel?.has_channel_dna);
  const canSubmitCreate = Boolean(createForm.name.trim() && canOpenCreateModal);

  function resetCreateState() {
    setCreateForm({ name: '', description: '' });
    setModalError('');
    createProject.reset();
  }

  function openCreateModal() {
    if (!canOpenCreateModal) return;
    resetCreateState();
    setIsCreateModalOpen(true);
  }

  function closeCreateModal() {
    setIsCreateModalOpen(false);
    resetCreateState();
  }

  async function handleCreateProject() {
    setModalError('');
    if (!canSubmitCreate) {
      setModalError('Project title is required, and the selected channel must already have valid Channel DNA.');
      return;
    }
    try {
      const createdProject = await createProject.mutateAsync();
      queryClient.setQueryData<ProjectListResponse>(['v3-projects'], (current) => {
        if (!current) {
          return {
            items: [createdProject],
            page: 1,
            page_size: 50,
            total: 1,
          };
        }

        const withoutCreated = current.items.filter((project) => project.project_id !== createdProject.project_id);
        return {
          ...current,
          items: [createdProject, ...withoutCreated],
          total: withoutCreated.length === current.items.length ? current.total + 1 : current.total,
        };
      });
      workspace.setSelectedProjectId(createdProject.project_id);
      void queryClient.invalidateQueries({ queryKey: ['v3-projects'] });
      closeCreateModal();
      onNavigate('studio');
    } catch (error) {
      setModalError(errorMessage(error) ?? 'Could not create project.');
    }
  }

  function openProject(projectId: string) {
    workspace.setSelectedProjectId(projectId);
    onNavigate('studio');
  }

  return (
    <div className="space-y-4">
      {workspace.selectedChannelId && !selectedChannel?.has_channel_dna ? <ErrorBanner message="Channel DNA is required before creating a production project." /> : null}

      <section className="panel-surface overflow-hidden">
        <div className="flex flex-col gap-5 px-5 py-5">
          <div className="flex flex-col gap-2">
            <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">
              Channels &gt; {selectedChannel?.name ?? 'No channel selected'}
            </div>
            <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
              <div className="space-y-2">
                <h2 className="text-2xl font-semibold tracking-tight text-slate-50">Projects</h2>
                <p className="max-w-3xl text-sm leading-6 text-slate-400">
                  Open a project card to jump into Studio, or create a new project that is immediately selected for the pipeline.
                </p>
              </div>
              <div className="flex w-full flex-col gap-3 md:flex-row lg:w-auto">
                <label className="relative min-w-0 flex-1 lg:w-[320px]">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                  <TextInput
                    data-testid="project-search-input"
                    value={searchTerm}
                    placeholder="Search projects..."
                    onChange={(event) => setSearchTerm(event.target.value)}
                    className="pl-10"
                  />
                </label>
                <button
                  type="button"
                  data-testid="new-project-button"
                  onClick={openCreateModal}
                  disabled={!canOpenCreateModal}
                  className="inline-flex items-center justify-center gap-2 rounded-2xl border border-cyan-400/30 bg-cyan-500/15 px-4 py-2 text-sm font-medium text-cyan-50 transition hover:bg-cyan-500/25 disabled:cursor-not-allowed disabled:border-white/5 disabled:bg-white/[0.02] disabled:text-slate-500"
                >
                  <FolderPlus className="h-4 w-4" />
                  New Project
                </button>
              </div>
            </div>
          </div>

          {!workspace.selectedChannelId ? (
            <EmptyState
              icon={Clapperboard}
              title="Select or create a channel first."
              body="Projects stay scoped to the active channel. Choose one on the Channels page before creating or opening pipeline work."
              actionLabel="Go to Channels"
              onAction={() => onNavigate('channels')}
            />
          ) : (
            <div className="space-y-4">
              {projectsForChannel.length === 0 ? (
                <div className="rounded-[1.5rem] border border-dashed border-white/10 bg-white/[0.02] p-8 text-sm leading-6 text-slate-400">
                  <div className="text-base font-semibold text-slate-100">No projects yet</div>
                  <div className="mt-2">Create your first project to open the Topic Ideas and Script pipeline in Studio.</div>
                </div>
              ) : searchTerm.trim() && filteredProjects.length === 0 ? (
                <div className="rounded-[1.25rem] border border-dashed border-white/10 bg-white/[0.02] p-6 text-sm leading-6 text-slate-400">
                  No projects match <span className="font-medium text-slate-200">{searchTerm}</span> for the selected channel.
                </div>
              ) : (
                <ProjectCardGrid projects={filteredProjects} selectedProjectId={workspace.selectedProjectId} onOpenProject={openProject} />
              )}
            </div>
          )}
        </div>
      </section>

      <CreateProjectModal
        isOpen={isCreateModalOpen}
        selectedChannelLabel={selectedChannel ? `${selectedChannel.name} - ${selectedChannel.language}` : 'No channel selected'}
        form={createForm}
        errorMessage={modalError}
        isSubmitting={createProject.isPending}
        onClose={closeCreateModal}
        onUpdateField={(field, value) => {
          setModalError('');
          setCreateForm((current) => ({ ...current, [field]: value }));
        }}
        onSubmit={handleCreateProject}
      />
    </div>
  );
}
