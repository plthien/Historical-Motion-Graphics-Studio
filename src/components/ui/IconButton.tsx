import type { LucideIcon } from 'lucide-react';
import { cx } from '../../lib/cx';

export function IconButton({
  icon: Icon,
  label,
  onClick,
  disabled,
  variant = 'secondary',
  className,
}: {
  icon?: LucideIcon;
  label: string;
  onClick?: () => void;
  disabled?: boolean;
  variant?: 'primary' | 'secondary' | 'ghost';
  className?: string;
}) {
  const styles =
    variant === 'primary'
      ? 'border-cyan-400/30 bg-cyan-500/15 text-cyan-50 hover:bg-cyan-500/25'
      : variant === 'ghost'
        ? 'border-transparent bg-transparent text-slate-300 hover:border-white/10 hover:bg-white/[0.03]'
        : 'border-white/10 bg-white/[0.03] text-slate-200 hover:bg-white/[0.06]';

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cx(
        'inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:border-white/5 disabled:bg-white/[0.02] disabled:text-slate-500',
        styles,
        className,
      )}
    >
      {Icon ? <Icon className="h-4 w-4" /> : null}
      {label}
    </button>
  );
}
