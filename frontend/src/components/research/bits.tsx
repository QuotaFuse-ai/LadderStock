import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { cn } from '../../lib/cn'

/** 页面内跳转保持与 Button 同形（真锚点，保留右键/新窗口）。 */
export function LinkButton({
  to,
  children,
  variant = 'secondary',
  leadingIcon,
  className,
}: {
  to: string
  children: ReactNode
  variant?: 'primary' | 'secondary'
  leadingIcon?: ReactNode
  className?: string
}) {
  return (
    <Link
      to={to}
      className={cn(
        'inline-flex h-control select-none items-center justify-center gap-1 rounded px-3 text-body font-medium transition-colors duration-fast',
        variant === 'primary'
          ? 'bg-accent-solid text-accent-contrast hover:bg-accent-solid-hover'
          : 'border border-border-strong bg-surface text-text hover:bg-surface-3',
        className,
      )}
    >
      {leadingIcon}
      <span>{children}</span>
    </Link>
  )
}

/** 估值分位条：0 = 历史低位，100 = 历史高位；数值由 facts 提供，页面不计算。 */
export function PercentileBar({
  label,
  value,
  note,
}: {
  label: string
  value: number
  note?: string
}) {
  const pct = Math.min(100, Math.max(0, value))
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-body text-text">{label}</span>
        <span className="ldst-num text-subtitle font-semibold text-text">{pct.toFixed(0)}%</span>
      </div>
      <div
        role="img"
        aria-label={`${label} ${pct.toFixed(0)}% 分位；0 为历史低位，100 为历史高位`}
        className="relative h-2 w-full rounded-pill bg-surface-3"
      >
        <span
          aria-hidden="true"
          className="absolute inset-y-0 left-0 rounded-pill bg-accent opacity-40"
          style={{ width: `${pct}%` }}
        />
        <span
          aria-hidden="true"
          className="absolute inset-y-0 w-0.5 -translate-x-1/2 rounded-pill bg-text"
          style={{ left: `${pct}%` }}
        />
      </div>
      <div className="flex items-baseline justify-between text-caption text-text-muted">
        <span>0 低位</span>
        {note ? <span>{note}</span> : null}
        <span>100 高位</span>
      </div>
    </div>
  )
}
