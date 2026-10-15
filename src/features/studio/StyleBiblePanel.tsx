import { CheckCircle2, Save, Sparkles, Undo2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import type {
  ProjectStyleBibleArtifactV4,
  StyleBibleArtifactResponseV4,
  StyleBibleCharacter,
  StyleBibleEditableCharacter,
  StyleBibleEditableContentV4,
  StyleBibleEditableLocation,
  StyleBibleEditableMotif,
  StyleBibleEditableRecurringProp,
  StyleBibleEditableRecurringVisualSubject,
  StyleBibleLocation,
  StyleBibleMotifV4,
  StyleBibleRecurringProp,
  StyleBibleRecurringVisualSubject,
  StyleBibleTextUpdatePayloadV4,
} from '../../api/client';
import { IconButton } from '../../components/ui/IconButton';
import { SectionCard } from '../../components/ui/SectionCard';
import { StatusBadge } from '../../components/ui/StatusBadge';
import {
  CategoryEmptyState,
  DefinitionCard,
  DefinitionCardGrid,
  DefinitionFieldList,
  DefinitionTabs,
} from './DefinitionWorkspace';
import {
  STYLE_BIBLE_VISIBLE_CATEGORIES,
  definitionCategory,
  type DefinitionCategoryKey,
} from './definitionCategories';
import { createStyleBibleEditableContent, isStyleBibleEditableContentChanged, isStyleBibleEditableContentValid } from './styleBibleState';
import { StyleBibleVisualReferencesWorkspace } from './StyleBibleVisualReferencesWorkspace';

type StyleBibleItem =
  | StyleBibleCharacter
  | StyleBibleLocation
  | StyleBibleRecurringProp
  | StyleBibleRecurringVisualSubject
  | StyleBibleMotifV4;

type StyleBibleEditableItem =
  | StyleBibleEditableCharacter
  | StyleBibleEditableLocation
  | StyleBibleEditableRecurringProp
  | StyleBibleEditableRecurringVisualSubject
  | StyleBibleEditableMotif;

interface EditableField {
  key: string;
  label: string;
  kind: 'text' | 'multiline' | 'list';
}

const EDITABLE_FIELDS: Record<DefinitionCategoryKey, readonly EditableField[]> = {
  characters: [
    { key: 'description', label: 'Description', kind: 'multiline' },
    { key: 'entity_kind', label: 'Entity type', kind: 'text' },
    { key: 'narrative_role', label: 'Narrative role', kind: 'text' },
    { key: 'explicit_visual_traits', label: 'Explicit visual traits', kind: 'list' },
    { key: 'identity_markers', label: 'Identity markers', kind: 'list' },
    { key: 'continuity_notes', label: 'Continuity notes', kind: 'list' },
    { key: 'physical_constraints', label: 'Physical constraints', kind: 'list' },
  ],
  locations: [
    { key: 'description', label: 'Description', kind: 'multiline' },
    { key: 'location_kind', label: 'Environment type', kind: 'text' },
    { key: 'explicit_visual_traits', label: 'Explicit visual traits', kind: 'list' },
    { key: 'identity_markers', label: 'Recurring landmarks / identity markers', kind: 'list' },
    { key: 'continuity_notes', label: 'Continuity notes', kind: 'list' },
    { key: 'physical_constraints', label: 'Physical constraints', kind: 'list' },
  ],
  recurring_props: [
    { key: 'description', label: 'Description', kind: 'multiline' },
    { key: 'prop_kind', label: 'Object type', kind: 'text' },
    { key: 'explicit_visual_traits', label: 'Explicit visual traits', kind: 'list' },
    { key: 'identity_markers', label: 'Distinctive identity markers', kind: 'list' },
    { key: 'continuity_notes', label: 'Continuity notes', kind: 'list' },
    { key: 'physical_constraints', label: 'Physical constraints', kind: 'list' },
  ],
  recurring_visual_subjects: [
    { key: 'description', label: 'Description', kind: 'multiline' },
    { key: 'visual_type', label: 'Subject type', kind: 'text' },
    { key: 'explicit_visual_traits', label: 'Explicit visual traits', kind: 'list' },
    { key: 'continuity_notes', label: 'Continuity notes', kind: 'list' },
    { key: 'physical_constraints', label: 'Physical constraints', kind: 'list' },
  ],
  visual_motifs: [
    { key: 'description', label: 'Visual meaning and recurrence guidance', kind: 'multiline' },
  ],
};

function styleBibleCounts(artifact: ProjectStyleBibleArtifactV4): Record<DefinitionCategoryKey, number> {
  return {
    characters: artifact.characters.length,
    locations: artifact.locations.length,
    recurring_props: artifact.recurring_props.length,
    recurring_visual_subjects: artifact.recurring_visual_subjects.length,
    visual_motifs: artifact.motifs.length,
  };
}

function styleBibleItems(artifact: ProjectStyleBibleArtifactV4, category: DefinitionCategoryKey): StyleBibleItem[] {
  return artifact[definitionCategory(category).styleBibleKey];
}

function editableItems(content: StyleBibleEditableContentV4, category: DefinitionCategoryKey): StyleBibleEditableItem[] {
  return content[definitionCategory(category).styleBibleKey];
}

function itemId(item: StyleBibleItem | StyleBibleEditableItem): string {
  if ('char_id' in item) return item.char_id;
  if ('loc_id' in item) return item.loc_id;
  if ('prop_id' in item) return item.prop_id;
  if ('subject_id' in item) return item.subject_id;
  return item.motif_id;
}

function TextInput({
  value,
  onChange,
  multiline = false,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  multiline?: boolean;
  label: string;
}) {
  const className = 'w-full rounded-lg border border-white/10 bg-cockpit-950/70 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400/40';
  return (
    <label className="block space-y-1.5 text-xs text-slate-400">
      <span>{label}</span>
      {multiline
        ? <textarea aria-label={label} className={`${className} min-h-24 resize-y`} value={value} onChange={(event) => onChange(event.target.value)} />
        : <input aria-label={label} className={className} value={value} onChange={(event) => onChange(event.target.value)} />}
    </label>
  );
}

function ListInput({ values, onChange, label }: { values: string[]; onChange: (values: string[]) => void; label: string }) {
  return (
    <label className="block space-y-1.5 text-xs text-slate-400">
      <span>{label}</span>
      <textarea
        aria-label={label}
        className="min-h-20 w-full resize-y rounded-lg border border-white/10 bg-cockpit-950/70 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400/40"
        value={values.join('\n')}
        onChange={(event) => onChange(event.target.value.split('\n').map((value) => value.trim()).filter(Boolean))}
      />
    </label>
  );
}

function ReadOnlyDefinitionFields({ item }: { item: StyleBibleItem }) {
  const metadata: Array<{ label: string; value: string }> = [];
  if ('entity_kind' in item) metadata.push({ label: 'Entity type', value: item.entity_kind }, { label: 'Narrative role', value: item.narrative_role });
  if ('location_kind' in item) metadata.push({ label: 'Environment type', value: item.location_kind });
  if ('prop_kind' in item) metadata.push({ label: 'Object type', value: item.prop_kind });
  if ('visual_type' in item) metadata.push({ label: 'Subject type', value: item.visual_type });
  const traits = 'explicit_visual_traits' in item ? item.explicit_visual_traits : [];
  const identityMarkers = 'identity_markers' in item ? item.identity_markers : [];
  const notes = 'continuity_notes' in item ? item.continuity_notes : [];

  return (
    <>
      <p className="break-words text-sm leading-6 text-slate-300">{item.description}</p>
      {metadata.length ? (
        <dl className="grid gap-2 text-xs sm:grid-cols-2">
          {metadata.map(({ label, value }) => <div key={label}><dt className="text-slate-500">{label}</dt><dd className="mt-0.5 break-words text-slate-300">{value}</dd></div>)}
        </dl>
      ) : null}
      {'significance' in item ? <div><div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">Significance</div><p className="mt-1.5 break-words text-sm leading-5 text-slate-300">{item.significance}</p></div> : null}
      <DefinitionFieldList label="Explicit visual traits" values={traits} />
      <DefinitionFieldList label="Identity markers" values={identityMarkers} />
      <DefinitionFieldList label="Continuity notes" values={notes} />
    </>
  );
}

function EditableDefinitionFields({
  category,
  item,
  sourceItem,
  onChange,
}: {
  category: DefinitionCategoryKey;
  item: StyleBibleEditableItem;
  sourceItem: StyleBibleItem;
  onChange: (field: string, value: string | string[]) => void;
}) {
  const editableRecord = item as unknown as Record<string, string | string[]>;
  return (
    <>
      <TextInput label="Display name" value={item.label} onChange={(value) => onChange('label', value)} />
      {EDITABLE_FIELDS[category].map((field) => field.kind === 'list'
        ? <ListInput key={field.key} label={field.label} values={editableRecord[field.key] as string[]} onChange={(value) => onChange(field.key, value)} />
        : <TextInput key={field.key} label={field.label} multiline={field.kind === 'multiline'} value={editableRecord[field.key] as string} onChange={(value) => onChange(field.key, value)} />)}
      {'significance' in sourceItem ? <div><div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">Significance (read-only)</div><p className="mt-1.5 text-sm leading-5 text-slate-300">{sourceItem.significance}</p></div> : null}
    </>
  );
}

function StyleBibleDefinitionCard({
  category,
  sourceItem,
  editableItem,
  onChange,
}: {
  category: DefinitionCategoryKey;
  sourceItem: StyleBibleItem;
  editableItem: StyleBibleEditableItem | null;
  onChange: (field: string, value: string | string[]) => void;
}) {
  const id = itemId(sourceItem);
  return (
    <DefinitionCard
      title={editableItem?.label ?? sourceItem.label}
      technicalId={id}
      testId={`style-bible-card-${category}-${id}`}
      badge={<StatusBadge status={sourceItem.approval_status} />}
    >
      {editableItem
        ? <EditableDefinitionFields category={category} item={editableItem} sourceItem={sourceItem} onChange={onChange} />
        : <ReadOnlyDefinitionFields item={sourceItem} />}
    </DefinitionCard>
  );
}

export function StyleBiblePanel({
  projectId,
  styleBibleResult,
  onGenerate,
  onSaveDraft,
  onApproveText,
  isGenerating,
  isSaving,
  isApproving,
  isHydrating,
  hydrationError,
  mutationError,
  canGenerate,
  generationBlockedReason,
  scriptAnalysisArtifactId,
}: {
  projectId: string;
  styleBibleResult: StyleBibleArtifactResponseV4 | null;
  onGenerate: () => void;
  onSaveDraft: (payload: StyleBibleTextUpdatePayloadV4) => void;
  onApproveText: () => void;
  isGenerating: boolean;
  isSaving: boolean;
  isApproving: boolean;
  isHydrating: boolean;
  hydrationError: string | null;
  mutationError: string | null;
  canGenerate: boolean;
  generationBlockedReason: string | null;
  scriptAnalysisArtifactId: string | null;
}) {
  const artifact = styleBibleResult?.artifact ?? null;
  const [activeCategory, setActiveCategory] = useState<DefinitionCategoryKey>('characters');
  const [editable, setEditable] = useState<StyleBibleEditableContentV4 | null>(null);
  useEffect(() => setEditable(null), [projectId, styleBibleResult?.artifact_id]);
  const valid = useMemo(() => isStyleBibleEditableContentValid(editable), [editable]);
  const changed = useMemo(() => isStyleBibleEditableContentChanged(artifact, editable), [artifact, editable]);
  const pending = isSaving || isApproving;
  const counts = artifact ? styleBibleCounts(artifact) : null;
  const selectedItems = artifact ? styleBibleItems(artifact, activeCategory) : [];
  const selectedEditableItems = editable ? editableItems(editable, activeCategory) : [];
  const selectedCategory = definitionCategory(activeCategory);

  const updateEditableItem = (index: number, field: string, value: string | string[]) => {
    setEditable((current) => {
      if (!current) return current;
      const collection = selectedCategory.styleBibleKey;
      const items = [...current[collection]] as unknown as Array<Record<string, unknown>>;
      items[index] = { ...items[index], [field]: value };
      return { ...current, [collection]: items } as StyleBibleEditableContentV4;
    });
  };

  const actions = artifact ? (
    <>
      {editable
        ? <IconButton icon={Save} label="Save Changes" onClick={() => onSaveDraft({ expected_style_bible_revision_id: artifact.style_bible_revision_id, editable_content: editable })} disabled={!valid || !changed || pending} variant="primary" />
        : <IconButton icon={Sparkles} label="Edit Definitions" onClick={() => setEditable(createStyleBibleEditableContent(artifact))} disabled={pending} />}
      {editable ? <IconButton icon={Undo2} label="Cancel" onClick={() => setEditable(null)} disabled={pending} /> : null}
      {artifact.status === 'draft' && !editable ? <IconButton icon={CheckCircle2} label="Approve Text" onClick={onApproveText} disabled={pending} variant="primary" /> : null}
    </>
  ) : <IconButton icon={Sparkles} label="Build Style Bible" onClick={onGenerate} disabled={!canGenerate || isGenerating} variant="primary" />;

  return (
    <div className="space-y-4">
      {artifact?.status === 'text_approved' ? (
        <>
          {[hydrationError, mutationError].filter(Boolean).map((message) => <div key={message} className="rounded-2xl border border-amber-400/30 bg-amber-500/[0.08] p-4 text-sm text-amber-100">{message}</div>)}
          <StyleBibleVisualReferencesWorkspace
            projectId={projectId}
            styleBibleArtifactId={styleBibleResult!.artifact_id}
            artifact={artifact}
            enabled
          />
        </>
      ) : (
        <SectionCard title="Style Bible" description="Prepare and approve the canonical image-generation definitions." actions={actions}>
          <div className="space-y-6">
            {[hydrationError, mutationError].filter(Boolean).map((message) => <div key={message} className="rounded-2xl border border-amber-400/30 bg-amber-500/[0.08] p-4 text-sm text-amber-100">{message}</div>)}
            {isHydrating && !artifact ? <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-8 text-sm text-slate-300">Loading current Style Bible...</div> : null}
            {artifact && counts ? (
              <>
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                  <div className="flex items-center gap-3">
                    <StatusBadge status={artifact.status} />
                    <span className="text-sm text-slate-300">{STYLE_BIBLE_VISIBLE_CATEGORIES.reduce((total, category) => total + counts[category.key], 0)} visual definitions</span>
                  </div>
                  {editable ? <span className="text-xs text-cyan-200">Editing image-generation fields</span> : null}
                </div>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4" data-testid="style-bible-category-counts">
                  {STYLE_BIBLE_VISIBLE_CATEGORIES.map((category) => (
                    <div key={category.key} className="rounded-xl border border-white/10 bg-white/[0.02] p-3">
                      <div className="text-xs text-slate-500">{category.label}</div>
                      <div className="mt-1 text-lg font-semibold text-slate-100">{counts[category.key]}</div>
                    </div>
                  ))}
                </div>
                {!valid && editable ? <div className="rounded-xl border border-rose-400/30 bg-rose-500/10 p-3 text-sm text-rose-100">Every required editable value must be non-empty.</div> : null}
                <section className="space-y-4" data-testid="style-bible-definition-workspace">
                  <DefinitionTabs selected={activeCategory} counts={counts} onSelect={setActiveCategory} testId="style-bible-definition-tabs" categories={STYLE_BIBLE_VISIBLE_CATEGORIES} />
                  <div role="tabpanel" data-active-category={activeCategory} data-edit-mode={Boolean(editable)}>
                    {selectedItems.length ? (
                      <DefinitionCardGrid>
                        {selectedItems.map((item, index) => (
                          <StyleBibleDefinitionCard
                            key={itemId(item)}
                            category={activeCategory}
                            sourceItem={item}
                            editableItem={selectedEditableItems[index] ?? null}
                            onChange={(field, value) => updateEditableItem(index, field, value)}
                          />
                        ))}
                      </DefinitionCardGrid>
                    ) : <CategoryEmptyState message={selectedCategory.styleBibleEmptyMessage} />}
                  </div>
                </section>
              </>
            ) : !isHydrating ? <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.02] p-8 text-sm text-slate-400">{generationBlockedReason ?? (scriptAnalysisArtifactId ? 'Build the Style Bible from the current Script Analysis.' : 'Import Script & Analysis first.')}</div> : null}
          </div>
        </SectionCard>
      )}
    </div>
  );
}
