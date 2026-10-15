import type { Dispatch, SetStateAction } from 'react';
import { Image, Plug2, RadioTower } from 'lucide-react';
import type { MediaProviderSettings } from '../../api/client';
import { StatusBadge } from '../../components/ui/StatusBadge';
import { cx } from '../../lib/cx';

function FieldLabel({ children }: { children: React.ReactNode }) {
  return <label className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">{children}</label>;
}

function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={cx(
        'w-full rounded-2xl border border-white/10 bg-cockpit-950/70 px-3 py-2.5 text-sm text-slate-100 outline-none transition placeholder:text-slate-500 focus:border-cyan-400/30 focus:bg-cockpit-950 disabled:cursor-not-allowed disabled:text-slate-500',
        props.className,
      )}
    />
  );
}

function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={cx(
        'w-full rounded-2xl border border-white/10 bg-cockpit-950/70 px-3 py-2.5 text-sm text-slate-100 outline-none transition focus:border-cyan-400/30 focus:bg-cockpit-950 disabled:cursor-not-allowed disabled:text-slate-500',
        props.className,
      )}
    />
  );
}

export function MediaProviderSettingsForm({
  mediaSettings,
  setMediaSettings,
  mediaApiKey,
  setMediaApiKey,
  readiness,
}: {
  mediaSettings: MediaProviderSettings;
  setMediaSettings: Dispatch<SetStateAction<MediaProviderSettings | null>>;
  mediaApiKey: string;
  setMediaApiKey: Dispatch<SetStateAction<string>>;
  readiness: string;
}) {
  function updateMediaSettings(patch: Partial<MediaProviderSettings>) {
    setMediaSettings((current) => (current ? { ...current, ...patch } : current));
  }

  function toggleUpscale(value: '2K' | '4K') {
    const hasValue = mediaSettings.default_upscale.includes(value);
    updateMediaSettings({
      default_upscale: hasValue
        ? mediaSettings.default_upscale.filter((item) => item !== value)
        : [...mediaSettings.default_upscale, value],
    });
  }

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
      <div className="space-y-5">
        <section className="rounded-[1.25rem] border border-white/10 bg-white/[0.03] p-4">
          <div className="flex items-center gap-2 text-slate-100">
            <RadioTower className="h-4 w-4 text-cyan-200" />
            <span className="text-sm font-medium">Provider</span>
          </div>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <div className="grid gap-2">
              <FieldLabel>Provider</FieldLabel>
              <Input value="G-Labs Webhook" disabled />
            </div>
            <div className="grid gap-2">
              <FieldLabel>Enabled Media Kinds</FieldLabel>
              <Input value={mediaSettings.enabled_media_kinds.join(', ')} disabled />
            </div>
            <div className="grid gap-2 md:col-span-2">
              <FieldLabel>Base URL</FieldLabel>
              <Input value={mediaSettings.base_url} onChange={(event) => updateMediaSettings({ base_url: event.target.value })} />
            </div>
            <div className="grid gap-2 md:col-span-2">
              <FieldLabel>API Key</FieldLabel>
              <Input
                type="password"
                value={mediaApiKey}
                placeholder={mediaSettings.api_key_configured ? 'Configured - leave blank to keep current key' : 'Paste API key'}
                onChange={(event) => setMediaApiKey(event.target.value)}
              />
            </div>
          </div>
        </section>

        <section className="rounded-[1.25rem] border border-white/10 bg-white/[0.03] p-4">
          <div className="flex items-center gap-2 text-slate-100">
            <Image className="h-4 w-4 text-cyan-200" />
            <span className="text-sm font-medium">Image Defaults</span>
          </div>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <div className="grid gap-2">
              <FieldLabel>Default Image Model</FieldLabel>
              <Select value={mediaSettings.default_image_model} onChange={(event) => updateMediaSettings({ default_image_model: event.target.value as MediaProviderSettings['default_image_model'] })}>
                <option value="nano_banana_2">nano_banana_2</option>
                <option value="nano_banana_pro">nano_banana_pro</option>
              </Select>
            </div>
            <div className="grid gap-2">
              <FieldLabel>Default Aspect Ratio</FieldLabel>
              <Select value={mediaSettings.default_image_aspect_ratio} onChange={(event) => updateMediaSettings({ default_image_aspect_ratio: event.target.value as MediaProviderSettings['default_image_aspect_ratio'] })}>
                {['1:1', '3:4', '4:3', '9:16', '16:9'].map((ratio) => (
                  <option key={ratio} value={ratio}>
                    {ratio}
                  </option>
                ))}
              </Select>
            </div>
            <div className="grid gap-2">
              <FieldLabel>Poll Interval</FieldLabel>
              <Input type="number" min="1" max="30" value={mediaSettings.poll_interval_seconds} onChange={(event) => updateMediaSettings({ poll_interval_seconds: Number(event.target.value) })} />
            </div>
            <div className="grid gap-2">
              <FieldLabel>Max Parallel Tasks</FieldLabel>
              <Input type="number" min="1" max="10" value={mediaSettings.max_parallel_tasks} onChange={(event) => updateMediaSettings({ max_parallel_tasks: Number(event.target.value) })} />
            </div>
            <div className="grid gap-2">
              <FieldLabel>Provider Enabled</FieldLabel>
              <Select value={mediaSettings.enabled ? 'enabled' : 'disabled'} onChange={(event) => updateMediaSettings({ enabled: event.target.value === 'enabled' })}>
                <option value="enabled">Enabled</option>
                <option value="disabled">Disabled</option>
              </Select>
            </div>
            <div className="grid gap-2 md:col-span-2">
              <FieldLabel>Default Upscale</FieldLabel>
              <div className="flex flex-wrap gap-2">
                {(['2K', '4K'] as const).map((value) => {
                  const active = mediaSettings.default_upscale.includes(value);
                  return (
                    <button
                      key={value}
                      type="button"
                      onClick={() => toggleUpscale(value)}
                      className={cx(
                        'rounded-full border px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.22em] transition',
                        active ? 'border-cyan-400/30 bg-cyan-500/10 text-cyan-100' : 'border-white/10 bg-white/[0.03] text-slate-400',
                      )}
                    >
                      {value}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </section>
      </div>

      <div className="space-y-5">
        <section className="rounded-[1.25rem] border border-white/10 bg-white/[0.03] p-4">
          <div className="flex items-center gap-2 text-slate-100">
            <Plug2 className="h-4 w-4 text-cyan-200" />
            <span className="text-sm font-medium">Provider Readiness</span>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <StatusBadge status={readiness === 'ready' ? 'approved' : 'pending'} label={readiness === 'ready' ? 'CONNECTED' : 'NOT CONFIGURED'} />
            <StatusBadge status={mediaSettings.enabled ? 'approved' : 'draft'} label={mediaSettings.enabled ? 'Image only' : 'Disabled'} />
          </div>
          <div className="mt-4 space-y-3 text-sm text-slate-300">
            <div>Provider: G-Labs Webhook</div>
            <div>Backend key: {mediaSettings.provider}</div>
            <div>Current model: {mediaSettings.default_image_model}</div>
            <div>Aspect ratio: {mediaSettings.default_image_aspect_ratio}</div>
            <div>API key: {mediaSettings.api_key_configured ? 'Configured' : 'Missing'}</div>
          </div>
        </section>
      </div>
    </div>
  );
}
