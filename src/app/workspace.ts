import type { Channel, ChannelDnaV3, Project, ScriptTopicIdea, TopicIdea, TopicIdeaFocus } from '../api/client';

export const SELECTED_CHANNEL_STORAGE_KEY = 'selectedChannelId';
export const SELECTED_PROJECT_STORAGE_KEY = 'selectedProjectId';

export const emptyChannelDna: ChannelDnaV3 = {
  schema_version: '3.0',
  channel: { name: '', language: 'English', niche: '', audience: '' },
  narration_wpm: { min: 120, max: 180 },
  pacing_profiles: {
    fast: { high: { preferred_hold_seconds: { min: 0, max: 0 } }, medium: { preferred_hold_seconds: { min: 0, max: 0 } }, low: { preferred_hold_seconds: { min: 0, max: 0 } } },
    medium: { high: { preferred_hold_seconds: { min: 0, max: 0 } }, medium: { preferred_hold_seconds: { min: 0, max: 0 } }, low: { preferred_hold_seconds: { min: 0, max: 0 } } },
    slow: { high: { preferred_hold_seconds: { min: 0, max: 0 } }, medium: { preferred_hold_seconds: { min: 0, max: 0 } }, low: { preferred_hold_seconds: { min: 0, max: 0 } } },
  },
  narration_identity: { voice: [], delivery: [], explanation_style: [], tone_boundaries: [], avoid: [] },
  visual_identity: { medium: [], linework: [], rendering: [], palette: [], shape_language: [], texture: [], editorial_language: [] },
  entity_styles: {
    character: { appearance: [], avoid: [] },
    location: { appearance: [], avoid: [] },
    prop: { appearance: [], avoid: [] },
    recurring_visual_subject: { appearance: [], avoid: [] },
  },
  global_negative_constraints: [],
};

export const topicIdeaFocusOptions: Array<{ value: TopicIdeaFocus; label: string }> = [
  { value: 'highest_ctr', label: 'Highest CTR' },
  { value: 'most_evergreen', label: 'Most evergreen' },
  { value: 'easiest_to_make', label: 'Easiest to make' },
  { value: 'balanced', label: 'Balanced' },
  { value: 'most_unique', label: 'Most unique' },
];

export type AppView = 'settings' | 'channels' | 'projects' | 'studio' | 'jobs' | 'artifacts';

export type WorkspaceContext = {
  channels: Channel[];
  projects: Project[];
  selectedChannelId: string;
  selectedProjectId: string;
  selectedChannel: Channel | null;
  selectedProject: Project | null;
  setSelectedChannelId: (channelId: string) => void;
  setSelectedProjectId: (projectId: string) => void;
};

export function readStoredSelection(key: string): string {
  if (typeof window === 'undefined') return '';
  return window.localStorage.getItem(key) ?? '';
}

export function writeStoredSelection(key: string, value: string): void {
  if (typeof window === 'undefined') return;
  if (!value) {
    window.localStorage.removeItem(key);
    return;
  }
  window.localStorage.setItem(key, value);
}

export function csvToList(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

export function mergeDnaChannelMetadata(dna: ChannelDnaV3, channel: Pick<Channel, 'name' | 'language' | 'niche' | 'audience'>): ChannelDnaV3 {
  return {
    ...dna,
    channel: {
      ...dna.channel,
      name: channel.name,
      language: channel.language,
      niche: channel.niche,
      audience: channel.audience,
    },
  };
}

export function topicIdeaForScript(idea: TopicIdea): ScriptTopicIdea {
  return {
    idea_id: idea.idea_id,
    title: idea.title,
    core_question: idea.core_question,
    viewer_hook: idea.viewer_hook,
    topic_angle: idea.topic_angle,
    tone: idea.tone,
    content_tags: idea.content_tags,
    risk_notes: idea.risk_notes,
    pov_style: idea.pov_style,
    avoid: idea.avoid,
    recommended_pacing_mode: idea.recommended_pacing_mode,
    pacing_mode_reason: idea.pacing_mode_reason,
  };
}

export function projectMatchesChannel(project: Project, channelId: string): boolean {
  return project.channel_id === channelId;
}
