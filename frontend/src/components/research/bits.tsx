import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { cn } from '../../lib/cn'

/**
 * 编号方块（QUOT-313 精致度 · 维度 1）。
 *
 * 正文角标 `[n]`、来源抽屉里的 `[n]`、风险清单序号原本是三套尺寸（h-4 / h-5 / h-5 w-5），
 * 同一页上「方括号里的数字」有三种视觉重量，读起来像三种东西。这里定一档：
 * 16px 高、4px 圆角、等宽数字、内容居中，色调由调用方给（info=角标 / warn=未核实 / surface=编号）。
 *
 * 不给 `min-w`：`[1]` 与 `[11]` 的宽度本来就该随位数走，强行等宽会让两位数撑破行高。
 */
export const NUMBER_CHIP =
  'inline-flex h-4 shrink-0 items-center justify-center whitespace-nowrap rounded-xs px-1 text-caption leading-none font-medium'

/**
 * 页面内跳转保持与 Button 同形（真锚点，保留右键/新窗口）。
 *
 * 类名逐条对齐 `Button` 的 BASE / VARIANTS / SIZES：`transition-all`（不是 `transition-colors`，
 * 因为 `active:translate-y-px` 的位移不在 colors 的过渡范围内）、`ease-ldst`、按压位移。
 * `Button` 本身没有 `asChild`（在 `components/ui/**`，本轨不改），所以锚点版只能在这里同形；
 * 等 ui 侧补上 `asChild` 后本函数应直接删掉。
 */
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
        'inline-flex h-control select-none items-center justify-center gap-1 rounded px-3 text-body font-medium transition-all duration-fast ease-ldst active:translate-y-px',
        variant === 'primary'
          ? 'bg-accent-solid text-accent-contrast hover:bg-accent-solid-hover active:bg-accent-solid-hover'
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
          className="absolute inset-y-0 w-half -translate-x-1/2 rounded-pill bg-text"
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
