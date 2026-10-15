export type DefinitionCategoryKey =
  | 'characters'
  | 'locations'
  | 'recurring_props'
  | 'recurring_visual_subjects'
  | 'visual_motifs';

export type StyleBibleCollectionKey =
  | 'characters'
  | 'locations'
  | 'recurring_props'
  | 'recurring_visual_subjects'
  | 'motifs';

export interface DefinitionCategoryDescriptor {
  key: DefinitionCategoryKey;
  styleBibleKey: StyleBibleCollectionKey;
  label: string;
  analysisEmptyMessage: string;
  styleBibleEmptyMessage: string;
}

export const DEFINITION_CATEGORIES: readonly DefinitionCategoryDescriptor[] = [
  {
    key: 'characters',
    styleBibleKey: 'characters',
    label: 'Characters',
    analysisEmptyMessage: 'No characters were identified in this Script Analysis.',
    styleBibleEmptyMessage: 'No character definitions are present in this Style Bible.',
  },
  {
    key: 'locations',
    styleBibleKey: 'locations',
    label: 'Locations',
    analysisEmptyMessage: 'No locations were identified in this Script Analysis.',
    styleBibleEmptyMessage: 'No location definitions are present in this Style Bible.',
  },
  {
    key: 'recurring_props',
    styleBibleKey: 'recurring_props',
    label: 'Recurring Props',
    analysisEmptyMessage: 'No recurring props were identified in this Script Analysis.',
    styleBibleEmptyMessage: 'No recurring prop definitions are present in this Style Bible.',
  },
  {
    key: 'recurring_visual_subjects',
    styleBibleKey: 'recurring_visual_subjects',
    label: 'Recurring Visual Subjects',
    analysisEmptyMessage: 'No recurring visual subjects were identified in this Script Analysis.',
    styleBibleEmptyMessage: 'No recurring visual subject definitions are present in this Style Bible.',
  },
  {
    key: 'visual_motifs',
    styleBibleKey: 'motifs',
    label: 'Visual Motifs',
    analysisEmptyMessage: 'No visual motifs were identified in this Script Analysis.',
    styleBibleEmptyMessage: 'No visual motif definitions are present in this Style Bible.',
  },
] as const;

export function definitionCategory(key: DefinitionCategoryKey): DefinitionCategoryDescriptor {
  const descriptor = DEFINITION_CATEGORIES.find((item) => item.key === key);
  if (!descriptor) throw new Error(`Unknown definition category: ${key}`);
  return descriptor;
}

export const STYLE_BIBLE_VISIBLE_CATEGORIES = DEFINITION_CATEGORIES.filter(
  (category) => category.key !== 'visual_motifs',
);
