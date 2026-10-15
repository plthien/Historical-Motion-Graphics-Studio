import { useEffect, useMemo, useState } from 'react';

export function useProgressiveLimit(total: number, resetKey: string, initial: number, step: number) {
  const safeInitial = Math.max(1, initial);
  const safeStep = Math.max(1, step);
  const [limit, setLimit] = useState(safeInitial);

  useEffect(() => {
    setLimit(safeInitial);
  }, [resetKey, safeInitial]);

  return useMemo(() => ({
    visibleCount: Math.min(total, limit),
    hiddenCount: Math.max(0, total - limit),
    hasMore: limit < total,
    showMore: () => setLimit((current) => Math.min(total, current + safeStep)),
    showAll: () => setLimit(total),
  }), [limit, safeStep, total]);
}
