import type { ReactNode } from 'react';
import { GlobalSidebar } from './GlobalSidebar';
import type { AppView } from '../app/workspace';

export function CockpitShell({
  activeView,
  onNavigate,
  routes,
  channelName,
  projectName,
  healthLabel,
  children,
}: {
  activeView: AppView;
  onNavigate: (view: AppView) => void;
  routes: Array<{ id: AppView; label: string; description: string }>;
  channelName: string;
  projectName: string;
  healthLabel: string;
  children: ReactNode;
}) {
  return (
    <div className="min-h-screen px-4 py-4">
      <div className="mx-auto grid max-w-[1800px] gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
        <GlobalSidebar
          activeView={activeView}
          onNavigate={onNavigate}
          routes={routes}
          channelName={channelName}
          projectName={projectName}
          healthLabel={healthLabel}
        />
        <main className="min-w-0 space-y-4">{children}</main>
      </div>
    </div>
  );
}
