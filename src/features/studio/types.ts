import type { TopicIdea } from '../../api/client';

export type StudioStageId =
  | 'topic'
  | 'script'
  | 'style_bible'
  | 'scene'
  | 'scene_assets'
  | 'voice'
  | 'assembly'
  | 'render'
  | 'media';

export type TopicFormState = {
  seed_topic: string;
  topic_count: number;
  focus_keywords: string;
  focus: 'highest_ctr' | 'most_evergreen' | 'easiest_to_make' | 'balanced' | 'most_unique';
};

export type ScriptFormState = {
  target_duration_minutes: number;
  pov_style: string;
  avoid: string;
  section_count_mode: 'llm_decides' | 'backend_plan';
  pacing_mode: 'fast' | 'medium' | 'slow';
  narration_wpm: { min: number; max: number };
};

export type TopicIdeaResolution =
  | {
      status: 'valid';
      idea: TopicIdea;
    }
  | {
      status:
        | 'none'
        | 'partial_state'
        | 'stale_selection'
        | 'artifact_not_loaded'
        | 'artifact_mismatch';
      idea: null;
    };

export type MediaFormState = {
  entity_type: 'character' | 'location' | 'prop' | 'recurring_visual_subject';
  entity_id: string;
  candidate_count: number;
  prompt: string;
  negative_prompt: string;
  model: 'nano_banana_2' | 'nano_banana_pro';
  aspect_ratio: '1:1' | '3:4' | '4:3' | '9:16' | '16:9';
  upscale: Array<'2K' | '4K'>;
};

export type MediaStatusFilter =
  | 'all'
  | 'queued'
  | 'submitted'
  | 'provider_pending'
  | 'provider_running'
  | 'downloading'
  | 'completed'
  | 'failed'
  | 'canceled'
  | 'download_failed';
