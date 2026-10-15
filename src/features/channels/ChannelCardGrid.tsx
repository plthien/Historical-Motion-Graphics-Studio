import { FolderKanban } from 'lucide-react';
import type { Channel } from '../../api/client';
import { StatusBadge } from '../../components/ui/StatusBadge';
import { cx } from '../../lib/cx';

function formatTimestamp(value: string): string {
  return new Date(value).toLocaleString();
}

function channelStatus(channel: Channel, invalidChannelIds: string[]) {
  if (invalidChannelIds.includes(channel.channel_id)) {
    return { status: 'failed', label: 'Invalid' };
  }
  if (channel.has_channel_dna) {
    return { status: 'approved', label: 'Valid' };
  }
  return { status: 'draft', label: 'Missing' };
}

export function ChannelCardGrid({
  channels,
  selectedChannelId,
  invalidChannelIds,
  projectCounts,
  onSelect,
  onViewProjects,
}: {
  channels: Channel[];
  selectedChannelId: string;
  invalidChannelIds: string[];
  projectCounts: Record<string, number>;
  onSelect: (channelId: string) => void;
  onViewProjects: (channelId: string) => void;
}) {
  return (
    <section className="panel-surface overflow-hidden">
      <div className="flex flex-col gap-2 border-b border-white/10 px-5 py-4">
        <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Channel Grid</div>
        <h2 className="text-lg font-semibold text-slate-100">Channels</h2>
        <p className="max-w-3xl text-sm leading-6 text-slate-400">
          Select a channel card to load its real saved Channel DNA into the editor on the right.
        </p>
      </div>

      <div data-testid="channel-grid-scroll" className="max-h-[calc(100vh-15rem)] overflow-y-auto p-5">
        {channels.length === 0 ? (
          <div className="rounded-[1.25rem] border border-dashed border-white/10 bg-white/[0.02] p-6 text-sm leading-6 text-slate-400">
            No channels yet. Create one to begin the V3 channel and project flow.
          </div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
            {channels.map((channel) => {
              const isSelected = channel.channel_id === selectedChannelId;
              const status = channelStatus(channel, invalidChannelIds);
              return (
                <article
                  key={channel.channel_id}
                  data-testid={isSelected ? 'channel-card-selected' : 'channel-card'}
                  className={cx(
                    'rounded-[1.25rem] border p-4 shadow-soft transition',
                    isSelected
                      ? 'border-cyan-400/30 bg-cyan-500/[0.09]'
                      : 'border-white/10 bg-white/[0.03] hover:bg-white/[0.05]',
                  )}
                >
                  <button type="button" onClick={() => onSelect(channel.channel_id)} className="w-full text-left">
                    <div className="flex items-start gap-4">
                      <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-white/10 bg-cockpit-800/70 text-lg font-semibold text-slate-100">
                        {channel.name.slice(0, 1).toUpperCase()}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <div className="text-base font-semibold text-slate-50">{channel.name}</div>
                          <StatusBadge status={status.status} label={status.label} />
                        </div>
                        <div className="mt-1 font-mono text-xs text-slate-500">{channel.channel_id}</div>
                        <div className="mt-3 max-h-11 overflow-hidden text-sm leading-5 text-slate-300">{channel.niche}</div>
                        <div className="mt-2 max-h-11 overflow-hidden text-sm leading-5 text-slate-400">{channel.audience}</div>
                      </div>
                    </div>
                  </button>

                  <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-white/10 pt-4">
                    <div className="space-y-1 text-xs text-slate-500">
                      <div>Projects: {projectCounts[channel.channel_id] ?? 0}</div>
                      <div>Updated: {formatTimestamp(channel.updated_at)}</div>
                    </div>
                    <button
                      type="button"
                      data-testid="view-projects-button"
                      onClick={() => onViewProjects(channel.channel_id)}
                      className="inline-flex items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.03] px-3 py-2 text-sm font-medium text-slate-100 transition hover:bg-white/[0.06]"
                    >
                      <FolderKanban className="h-4 w-4" />
                      View Projects
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}
