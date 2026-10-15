import type { ChannelDnaV3, ChannelContext, PacingDensity, VideoPacingMode } from '../../api/client';
import { normalizeLineArrayInput, parseChannelDnaObject } from './channelDnaCore.js';

export { normalizeLineArrayInput };

export const narrationIdentityFields = [
  ['voice', 'Voice'],
  ['delivery', 'Delivery'],
  ['explanation_style', 'Explanation Style'],
  ['tone_boundaries', 'Tone Boundaries'],
  ['avoid', 'Avoid'],
] as const;

export const visualIdentityFields = [
  ['medium', 'Medium'],
  ['linework', 'Linework'],
  ['rendering', 'Rendering'],
  ['palette', 'Palette'],
  ['shape_language', 'Shape Language'],
  ['texture', 'Texture'],
  ['editorial_language', 'Editorial Language'],
] as const;

export const entityStyleGroups = [
  ['character', 'Character'],
  ['location', 'Location'],
  ['prop', 'Recurring Props'],
  ['recurring_visual_subject', 'Recurring Visual Subject'],
] as const;

export type NarrationIdentityKey = keyof ChannelDnaV3['narration_identity'];
export type VisualIdentityKey = keyof ChannelDnaV3['visual_identity'];
export type EntityStyleKey = keyof ChannelDnaV3['entity_styles'];
export type EntityStyleField = 'appearance' | 'avoid';
export type ChannelDnaArrayPath =
  | { kind: 'narration_identity'; field: NarrationIdentityKey }
  | { kind: 'visual_identity'; field: VisualIdentityKey }
  | { kind: 'entity_style'; entity: EntityStyleKey; field: EntityStyleField }
  | { kind: 'global_negative_constraints' };

export function createEmptyPacingProfiles(): ChannelDnaV3['pacing_profiles'] {
  const emptyDensity = () => ({ preferred_hold_seconds: { min: 0, max: 0 } });
  const emptyMode = () => ({ high: emptyDensity(), medium: emptyDensity(), low: emptyDensity() });
  return { fast: emptyMode(), medium: emptyMode(), slow: emptyMode() };
}

export function createChannelDnaDraft(channel: ChannelContext): ChannelDnaV3 {
  return {
    schema_version: '3.0',
    channel: { ...channel },
    narration_wpm: { min: 0, max: 0 },
    pacing_profiles: createEmptyPacingProfiles(),
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
}

export function syncChannelFieldsIntoDna(dna: ChannelDnaV3, channel: ChannelContext): ChannelDnaV3 {
  return { ...dna, channel: { ...channel } };
}

export function isCompleteChannelDna(dna: ChannelDnaV3): boolean {
  const narrationWpmComplete = Number.isInteger(dna.narration_wpm.min) && Number.isInteger(dna.narration_wpm.max) && dna.narration_wpm.min >= 80 && dna.narration_wpm.max <= 300 && dna.narration_wpm.min < dna.narration_wpm.max;
  const pacingComplete = (['fast', 'medium', 'slow'] as VideoPacingMode[]).every((mode) =>
    (['high', 'medium', 'low'] as PacingDensity[]).every((density) => {
      const hold = dna.pacing_profiles[mode][density].preferred_hold_seconds;
      return Number.isFinite(hold.min) && Number.isFinite(hold.max) && hold.min > 0 && hold.max > hold.min;
    }),
  );
  return Object.values(dna.channel).every((value) => value.trim().length > 0)
    && narrationWpmComplete
    && pacingComplete
    && dna.visual_identity.editorial_language.length > 0;
}

export function updateChannelDnaPacingRange(
  dna: ChannelDnaV3,
  mode: VideoPacingMode,
  density: PacingDensity,
  field: 'min' | 'max',
  value: number,
): ChannelDnaV3 {
  return {
    ...dna,
    pacing_profiles: {
      ...dna.pacing_profiles,
      [mode]: {
        ...dna.pacing_profiles[mode],
        [density]: {
          preferred_hold_seconds: {
            ...dna.pacing_profiles[mode][density].preferred_hold_seconds,
            [field]: value,
          },
        },
      },
    },
  };
}

export function parseChannelDnaJson(text: string): ChannelDnaV3 {
  let parsed: unknown;
  try { parsed = JSON.parse(text); } catch { throw new Error('Channel DNA must be valid JSON.'); }
  return parseChannelDnaObject(parsed);
}

export function updateChannelDnaArray(dna: ChannelDnaV3, path: ChannelDnaArrayPath, values: string[]): ChannelDnaV3 {
  if (path.kind === 'narration_identity') {
    return { ...dna, narration_identity: { ...dna.narration_identity, [path.field]: values } };
  }
  if (path.kind === 'visual_identity') {
    return { ...dna, visual_identity: { ...dna.visual_identity, [path.field]: values } };
  }
  if (path.kind === 'entity_style') {
    return {
      ...dna,
      entity_styles: {
        ...dna.entity_styles,
        [path.entity]: { ...dna.entity_styles[path.entity], [path.field]: values },
      },
    };
  }
  return { ...dna, global_negative_constraints: values };
}
