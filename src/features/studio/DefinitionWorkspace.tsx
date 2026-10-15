import type { ReactNode } from 'react';
import { CardGrid } from '../../components/ui/CardGrid';
import type { DefinitionCategoryKey } from './definitionCategories';
import { DEFINITION_CATEGORIES, type DefinitionCategoryDescriptor } from './definitionCategories';

export function DefinitionTabs({
  selected,
  counts,
  onSelect,
  testId,
  variant = 'pill',
  categories = DEFINITION_CATEGORIES,
}: {
  selected: DefinitionCategoryKey;
  counts: Record<DefinitionCategoryKey, number>;
  onSelect: (key: DefinitionCategoryKey) => void;
  testId: string;
  variant?: 'pill' | 'underline';
  categories?: readonly DefinitionCategoryDescriptor[];
}) {
  const underline = variant === 'underline';
  return (
    <div className={underline ? 'overflow-x-auto border-b border-white/10' : 'overflow-x-auto pb-1'} data-testid={testId}>
      <div className={underline ? 'flex min-w-max gap-7' : 'flex min-w-max gap-2'} role="tablist" aria-label="Definition categories">
        {categories.map((category) => (
          <button
            key={category.key}
            type="button"
            role="tab"
            aria-selected={selected === category.key}
            data-category-key={category.key}
            onClick={() => onSelect(category.key)}
            className={underline
              ? `whitespace-nowrap border-b-2 px-0 pb-3 pt-1 text-sm font-medium transition ${
                selected === category.key
                  ? 'border-cyan-300 text-white'
                  : 'border-transparent text-slate-500 hover:text-slate-300'
              }`
              : `whitespace-nowrap rounded-xl border px-4 py-2.5 text-sm transition ${
                selected === category.key
                  ? 'border-cyan-400/40 bg-cyan-500/15 text-cyan-50'
                  : 'border-white/10 bg-white/[0.025] text-slate-300 hover:border-white/20'
              }`}
          >
            {category.label} ({counts[category.key]})
          </button>
        ))}
      </div>
    </div>
  );
}

export function DefinitionCardGrid({ children }: { children: ReactNode }) {
  return <CardGrid>{children}</CardGrid>;
}

export function DefinitionCard({
  title,
  technicalId,
  badge,
  children,
  testId,
}: {
  title: ReactNode;
  technicalId: string;
  badge?: ReactNode;
  children: ReactNode;
  testId: string;
}) {
  return (
    <article className="min-w-0 rounded-2xl border border-white/10 bg-white/[0.025] p-5" data-testid={testId}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="break-words text-base font-semibold text-slate-100">{title}</h4>
          <div className="mt-1 break-all font-mono text-[11px] text-slate-600">{technicalId}</div>
        </div>
        {badge}
      </div>
      <div className="mt-4 space-y-4">{children}</div>
    </article>
  );
}

export function DefinitionFieldList({ label, values }: { label: string; values: string[] }) {
  if (!values.length) return null;
  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">{label}</div>
      <ul className="mt-1.5 space-y-1 text-sm leading-5 text-slate-300">
        {values.map((value, index) => <li key={`${value}:${index}`} className="break-words">• {value}</li>)}
      </ul>
    </div>
  );
}

export function CategoryEmptyState({ message }: { message: string }) {
  return <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.015] p-8 text-center text-sm text-slate-500">{message}</div>;
}

export function OccurrenceDisclosure({
  count,
  children,
}: {
  count: number;
  children: ReactNode;
}) {
  return (
    <details className="rounded-xl border border-white/10 bg-black/10 p-3" data-testid="occurrence-disclosure">
      <summary className="cursor-pointer text-xs font-semibold text-slate-300">Occurrences ({count})</summary>
      <div className="mt-3">{children}</div>
    </details>
  );
}
