import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { apiClient, ApiError, type ChannelDnaV3, type PacingDensity, type VideoPacingMode } from '../api/client';
import { type AppView, type WorkspaceContext } from '../app/workspace';
import { ErrorBanner } from '../components/ui/ErrorBanner';
import { ChannelCardGrid } from '../features/channels/ChannelCardGrid';
import { ChannelDnaEditor } from '../features/channels/ChannelDnaEditor';
import { CreateChannelModal } from '../features/channels/CreateChannelModal';
import { createChannelDnaDraft, isCompleteChannelDna, parseChannelDnaJson, syncChannelFieldsIntoDna, updateChannelDnaArray, updateChannelDnaPacingRange, type ChannelDnaArrayPath } from '../features/channels/channelDna';

function errorMessage(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) return `${error.errorCode}: ${error.message}`;
  if (error instanceof Error) return error.message;
  return 'Unknown error';
}

type ChannelsPageProps = {
  workspace: WorkspaceContext;
  onNavigate: (view: AppView) => void;
};

function initialCreateState() {
  const form = {
    name: '',
    language: 'English',
    niche: '',
    audience: '',
  };
  return {
    form,
    dna: createChannelDnaDraft(form),
  };
}

type CreateSubmitPhase = 'idle' | 'creating' | 'saving' | 'refreshing';

type PendingCreateFlow = {
  channelId: string;
  channelName: string;
  shouldSaveDna: boolean;
  dnaSaved: boolean;
};

export function ChannelsPage({ workspace, onNavigate }: ChannelsPageProps) {
  const queryClient = useQueryClient();
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [successMessage, setSuccessMessage] = useState('');
  const [editorInputError, setEditorInputError] = useState('');
  const [invalidChannelIds, setInvalidChannelIds] = useState<string[]>([]);
  const [editorDraft, setEditorDraft] = useState<ChannelDnaV3 | null>(null);
  const [editorPasteValue, setEditorPasteValue] = useState('');
  const [createForm, setCreateForm] = useState(initialCreateState().form);
  const [createDnaDraft, setCreateDnaDraft] = useState(initialCreateState().dna);
  const [createPasteValue, setCreatePasteValue] = useState('');
  const [createModalError, setCreateModalError] = useState('');
  const [createSubmitPhase, setCreateSubmitPhase] = useState<CreateSubmitPhase>('idle');
  const [pendingCreateFlow, setPendingCreateFlow] = useState<PendingCreateFlow | null>(null);

  const selectedChannel = workspace.selectedChannel;

  const channelDnaQuery = useQuery({
    queryKey: ['channel-dna', workspace.selectedChannelId],
    queryFn: () => apiClient.getChannelDna(workspace.selectedChannelId),
    enabled: Boolean(workspace.selectedChannelId),
    retry: false,
  });

  const createChannel = useMutation({
    mutationFn: (payload: { name: string; language: string; niche: string; audience: string }) => apiClient.createChannel(payload),
  });

  const saveCreatedChannelDna = useMutation({
    mutationFn: ({ channelId, channelDna }: { channelId: string; channelDna: ChannelDnaV3 }) => apiClient.saveChannelDnaV3(channelId, channelDna),
  });

  const validateSelectedDna = useMutation({
    mutationFn: () => {
      if (!editorDraft) throw new Error('Load or draft Channel DNA before validating.');
      return apiClient.validateChannelDnaV3(editorDraft);
    },
    onSuccess: (response) => {
      setSuccessMessage(`Channel DNA V3 is valid for "${response.channel_context.name}".`);
    },
  });

  const saveSelectedDna = useMutation({
    mutationFn: () => {
      if (!workspace.selectedChannelId) throw new Error('Select a channel before saving Channel DNA.');
      if (!editorDraft) throw new Error('Load or draft Channel DNA before saving.');
      return apiClient.saveChannelDnaV3(workspace.selectedChannelId, editorDraft);
    },
    onSuccess: (channel) => {
      setSuccessMessage(`Saved Channel DNA V3 to "${channel.name}".`);
      void queryClient.invalidateQueries({ queryKey: ['v3-channels'] });
      void queryClient.invalidateQueries({ queryKey: ['channel-dna', channel.channel_id] });
      setInvalidChannelIds((current) => current.filter((item) => item !== channel.channel_id));
    },
  });

  useEffect(() => {
    setEditorDraft(null);
    setEditorPasteValue('');
    setEditorInputError('');
    validateSelectedDna.reset();
    saveSelectedDna.reset();
  }, [workspace.selectedChannelId]);

  useEffect(() => {
    if (!selectedChannel) return;
    if (channelDnaQuery.isPending) return;
    if (channelDnaQuery.isError) {
      const isStoredInvalid = channelDnaQuery.error instanceof ApiError && channelDnaQuery.error.errorCode === 'CHANNEL_DNA_STORED_INVALID';
      if (isStoredInvalid) {
        setInvalidChannelIds((current) => (current.includes(selectedChannel.channel_id) ? current : [...current, selectedChannel.channel_id]));
        setEditorDraft((current) => current ?? createChannelDnaDraft({
          name: selectedChannel.name,
          language: selectedChannel.language,
          niche: selectedChannel.niche,
          audience: selectedChannel.audience,
        }));
      }
      return;
    }
    if (!channelDnaQuery.data) return;
    setInvalidChannelIds((current) => current.filter((item) => item !== selectedChannel.channel_id));
    setEditorDraft(
      channelDnaQuery.data.channel_dna
        ? channelDnaQuery.data.channel_dna
        : createChannelDnaDraft({
            name: selectedChannel.name,
            language: selectedChannel.language,
            niche: selectedChannel.niche,
            audience: selectedChannel.audience,
          }),
    );
  }, [selectedChannel, channelDnaQuery.data, channelDnaQuery.error, channelDnaQuery.isError, channelDnaQuery.isPending]);

  const editorLoadState: 'idle' | 'loading' | 'ready' | 'missing' | 'invalid' | 'error' = !selectedChannel
    ? 'idle'
    : channelDnaQuery.isPending
      ? 'loading'
      : channelDnaQuery.isError
        ? channelDnaQuery.error instanceof ApiError && channelDnaQuery.error.errorCode === 'CHANNEL_DNA_STORED_INVALID'
          ? 'invalid'
          : 'error'
        : channelDnaQuery.data?.has_channel_dna
          ? 'ready'
          : 'missing';

  const projectCounts = useMemo(() => {
    return workspace.projects.reduce<Record<string, number>>((counts, project) => {
      counts[project.channel_id] = (counts[project.channel_id] ?? 0) + 1;
      return counts;
    }, {});
  }, [workspace.projects]);

  function resetCreateModalState() {
    const next = initialCreateState();
    setCreateForm(next.form);
    setCreateDnaDraft(next.dna);
    setCreatePasteValue('');
    setCreateModalError('');
    setCreateSubmitPhase('idle');
    setPendingCreateFlow(null);
    createChannel.reset();
    saveCreatedChannelDna.reset();
  }

  function openCreateModal() {
    resetCreateModalState();
    setIsCreateModalOpen(true);
  }

  function closeCreateModal() {
    setIsCreateModalOpen(false);
    resetCreateModalState();
  }

  function updateEditorChannelField(field: keyof ChannelDnaV3['channel'], value: string) {
    setSuccessMessage('');
    setEditorInputError('');
    validateSelectedDna.reset();
    saveSelectedDna.reset();
    setEditorDraft((current) => current ? { ...current, channel: { ...current.channel, [field]: value } } : current);
  }

  function updateEditorNarrationWpm(field: 'min' | 'max', value: number) {
    setSuccessMessage('');
    setEditorInputError('');
    validateSelectedDna.reset();
    saveSelectedDna.reset();
    setEditorDraft((current) => current ? { ...current, narration_wpm: { ...current.narration_wpm, [field]: value } } : current);
  }

  function updateEditorPacingRange(mode: VideoPacingMode, density: PacingDensity, field: 'min' | 'max', value: number) {
    setSuccessMessage('');
    setEditorInputError('');
    validateSelectedDna.reset();
    saveSelectedDna.reset();
    setEditorDraft((current) => current ? updateChannelDnaPacingRange(current, mode, density, field, value) : current);
  }

  function updateEditorVisualArray(path: ChannelDnaArrayPath, values: string[]) {
    setSuccessMessage('');
    setEditorInputError('');
    validateSelectedDna.reset();
    saveSelectedDna.reset();
    setEditorDraft((current) => current ? updateChannelDnaArray(current, path, values) : current);
  }

  function updateCreateField(field: 'name' | 'language' | 'niche' | 'audience', value: string) {
    setCreateModalError('');
    setCreateForm((current) => {
      const nextForm = { ...current, [field]: value };
      setCreateDnaDraft((draft) => syncChannelFieldsIntoDna(draft, nextForm));
      return nextForm;
    });
  }

  function updateCreateNarrationWpm(field: 'min' | 'max', value: number) {
    setCreateModalError('');
    setCreateDnaDraft((current) => ({ ...current, narration_wpm: { ...current.narration_wpm, [field]: value } }));
  }

  function updateCreatePacingRange(mode: VideoPacingMode, density: PacingDensity, field: 'min' | 'max', value: number) {
    setCreateModalError('');
    setCreateDnaDraft((current) => updateChannelDnaPacingRange(current, mode, density, field, value));
  }

  function updateCreateVisualArray(path: ChannelDnaArrayPath, values: string[]) {
    setCreateModalError('');
    setCreateDnaDraft((current) => updateChannelDnaArray(current, path, values));
  }

  function applyEditorJson(text: string) {
    const parsed = parseChannelDnaJson(text);
    validateSelectedDna.reset();
    saveSelectedDna.reset();
    setEditorInputError('');
    setSuccessMessage('');
    setEditorDraft(parsed);
  }

  async function importEditorFile(file: File | null) {
    if (!file) return;
    try {
      const text = await file.text();
      setEditorPasteValue(text);
      applyEditorJson(text);
    } catch (error) {
      setEditorInputError(errorMessage(error) ?? 'Could not import Channel DNA JSON.');
    }
  }

  function applyEditorPaste() {
    try {
      applyEditorJson(editorPasteValue);
    } catch (error) {
      setEditorInputError(errorMessage(error) ?? 'Could not parse Channel DNA JSON.');
    }
  }

  function resetEditorDraft() {
    if (!selectedChannel) return;
    setEditorInputError('');
    validateSelectedDna.reset();
    saveSelectedDna.reset();
    if (channelDnaQuery.data?.channel_dna) {
      setEditorDraft(channelDnaQuery.data.channel_dna);
      return;
    }
    setEditorDraft(
      createChannelDnaDraft({
        name: selectedChannel.name,
        language: selectedChannel.language,
        niche: selectedChannel.niche,
        audience: selectedChannel.audience,
      }),
    );
  }

  function applyCreateJson(text: string) {
    const parsed = parseChannelDnaJson(text);
    setCreateForm({
      name: parsed.channel.name,
      language: parsed.channel.language,
      niche: parsed.channel.niche,
      audience: parsed.channel.audience,
    });
    setCreateDnaDraft(parsed);
    setCreateModalError('');
  }

  async function importCreateFile(file: File | null) {
    if (!file) return;
    try {
      const text = await file.text();
      setCreatePasteValue(text);
      applyCreateJson(text);
    } catch (error) {
      setCreateModalError(errorMessage(error) ?? 'Could not import Channel DNA JSON.');
    }
  }

  function applyCreatePaste() {
    try {
      applyCreateJson(createPasteValue);
    } catch (error) {
      setCreateModalError(errorMessage(error) ?? 'Could not parse Channel DNA JSON.');
    }
  }

  async function refreshCreatedChannelState(
    channelId: string,
    fallbackChannel: { name: string; language: string; niche: string; audience: string },
  ) {
    const [channelsResponse, channelDnaResponse] = await Promise.all([
      queryClient.fetchQuery({
        queryKey: ['v3-channels'],
        queryFn: () => apiClient.listChannels(),
      }),
      queryClient.fetchQuery({
        queryKey: ['channel-dna', channelId],
        queryFn: () => apiClient.getChannelDna(channelId),
      }),
    ]);

    const refreshedChannel = channelsResponse.items.find((channel) => channel.channel_id === channelId) ?? null;
    workspace.setSelectedChannelId(channelId);
    setInvalidChannelIds((current) => current.filter((item) => item !== channelId));
    setEditorInputError('');
    setEditorPasteValue('');
    setEditorDraft(
      channelDnaResponse.channel_dna ??
        createChannelDnaDraft({
          name: refreshedChannel?.name ?? fallbackChannel.name,
          language: refreshedChannel?.language ?? fallbackChannel.language,
          niche: refreshedChannel?.niche ?? fallbackChannel.niche,
          audience: refreshedChannel?.audience ?? fallbackChannel.audience,
        }),
    );

    return { channel: refreshedChannel, channelDna: channelDnaResponse };
  }

  async function handleCreateChannelSubmit() {
    setCreateModalError('');
    setSuccessMessage('');
    const candidateDna = syncChannelFieldsIntoDna(createDnaDraft, createForm);
    const shouldSaveDna = pendingCreateFlow?.shouldSaveDna ?? isCompleteChannelDna(candidateDna);
    const fallbackChannel = {
      name: candidateDna.channel.name,
      language: candidateDna.channel.language,
      niche: candidateDna.channel.niche,
      audience: candidateDna.channel.audience,
    };
    let flow = pendingCreateFlow;
    try {
      if (!flow) {
        setCreateSubmitPhase('creating');
        const createdChannel = await createChannel.mutateAsync(createForm);
        flow = {
          channelId: createdChannel.channel_id,
          channelName: createdChannel.name,
          shouldSaveDna,
          dnaSaved: false,
        };
        setPendingCreateFlow(flow);
      }

      if (flow.shouldSaveDna && !flow.dnaSaved) {
        setCreateSubmitPhase('saving');
        await saveCreatedChannelDna.mutateAsync({ channelId: flow.channelId, channelDna: candidateDna });
        flow = { ...flow, dnaSaved: true };
        setPendingCreateFlow(flow);
      }

      setCreateSubmitPhase('refreshing');
      await refreshCreatedChannelState(flow.channelId, fallbackChannel);
      setPendingCreateFlow(null);
      setCreateSubmitPhase('idle');
      setSuccessMessage(
        flow.shouldSaveDna
          ? `Created channel "${flow.channelName}" and saved Channel DNA.`
          : `Created channel "${flow.channelName}". Channel DNA has not been saved yet.`,
      );
      closeCreateModal();
    } catch (error) {
      setCreateSubmitPhase('idle');
      if (flow) {
        try {
          await refreshCreatedChannelState(flow.channelId, fallbackChannel);
        } catch {
          // Best effort: preserve created-channel context even when refresh fails.
        }
        if (flow.shouldSaveDna && !flow.dnaSaved) {
          saveCreatedChannelDna.reset();
          setPendingCreateFlow(flow);
          setCreateModalError(
            [`Channel created, but Channel DNA save failed.`, errorMessage(error)]
              .filter(Boolean)
              .join(' '),
          );
          return;
        }
        setPendingCreateFlow(flow);
        setCreateModalError(
          [
            flow.dnaSaved
              ? `Channel and Channel DNA were saved, but the channel view failed to refresh.`
              : `Channel created, but the channel view failed to refresh.`,
            errorMessage(error),
          ]
            .filter(Boolean)
            .join(' '),
        );
        return;
      }
      setCreateModalError(errorMessage(error) ?? 'Could not create channel.');
    }
  }

  const createSubmitLabel = createSubmitPhase === 'creating'
    ? 'Creating channel...'
    : createSubmitPhase === 'saving'
      ? 'Saving Channel DNA...'
      : createSubmitPhase === 'refreshing'
        ? 'Refreshing channel...'
        : pendingCreateFlow
          ? pendingCreateFlow.dnaSaved
            ? 'Retry Refresh'
            : 'Retry Save Channel DNA'
          : 'Create Channel';

  function handleViewProjects(channelId: string) {
    workspace.setSelectedChannelId(channelId);
    onNavigate('projects');
  }

  return (
    <div className="space-y-4">
      {successMessage ? <div className="rounded-2xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-100">{successMessage}</div> : null}
      {[editorInputError, errorMessage(channelDnaQuery.error), errorMessage(validateSelectedDna.error), errorMessage(saveSelectedDna.error)].map((message, index) =>
        message ? <ErrorBanner key={index} message={message} /> : null,
      )}

      <section className="panel-surface overflow-hidden">
        <div className="flex flex-col gap-4 px-5 py-5 md:flex-row md:items-start md:justify-between">
          <div className="space-y-2">
            <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Channel Management</div>
            <h2 className="text-2xl font-semibold tracking-tight text-slate-50">Channels</h2>
            <p className="max-w-3xl text-sm leading-6 text-slate-400">
              Manage your YouTube channels and edit their Channel DNA with a simple left card grid and a real backend-loaded editor.
            </p>
          </div>
          <button
            type="button"
            data-testid="create-channel-button"
            onClick={openCreateModal}
            className="inline-flex items-center gap-2 rounded-2xl border border-cyan-400/30 bg-cyan-500/15 px-4 py-2 text-sm font-medium text-cyan-50 transition hover:bg-cyan-500/25"
          >
            <Plus className="h-4 w-4" />
            Create Channel
          </button>
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_480px]">
        <ChannelCardGrid
          channels={workspace.channels}
          selectedChannelId={workspace.selectedChannelId}
          invalidChannelIds={invalidChannelIds}
          projectCounts={projectCounts}
          onSelect={workspace.setSelectedChannelId}
          onViewProjects={handleViewProjects}
        />

        <ChannelDnaEditor
          selectedChannel={selectedChannel}
          draft={editorDraft}
          loadState={editorLoadState}
          updatedAt={channelDnaQuery.data?.updated_at ?? null}
          loadError={errorMessage(channelDnaQuery.error)}
          pasteValue={editorPasteValue}
          onPasteValueChange={setEditorPasteValue}
          onApplyPastedJson={applyEditorPaste}
          onImportFile={importEditorFile}
          onChangeChannelField={updateEditorChannelField} onChangePacingRange={updateEditorPacingRange} onChangeVisualArray={updateEditorVisualArray} onChangeNarrationWpm={updateEditorNarrationWpm}
          onReset={resetEditorDraft}
          onValidate={() => validateSelectedDna.mutate()}
          onSave={() => saveSelectedDna.mutate()}
          isValidating={validateSelectedDna.isPending}
          isSaving={saveSelectedDna.isPending}
        />
      </div>

      <CreateChannelModal
        isOpen={isCreateModalOpen}
        form={createForm}
        draft={createDnaDraft}
        pasteValue={createPasteValue}
        errorMessage={createModalError}
        submitLabel={createSubmitLabel}
        submitPhase={createSubmitPhase}
        isSubmitting={createSubmitPhase !== 'idle' || createChannel.isPending || saveCreatedChannelDna.isPending}
        onClose={closeCreateModal}
        onUpdateField={updateCreateField} onUpdatePacingRange={updateCreatePacingRange} onUpdateVisualArray={updateCreateVisualArray} onUpdateNarrationWpm={updateCreateNarrationWpm}
        onPasteValueChange={setCreatePasteValue}
        onApplyPastedJson={applyCreatePaste}
        onImportFile={importCreateFile}
        onSubmit={handleCreateChannelSubmit}
      />
    </div>
  );
}
