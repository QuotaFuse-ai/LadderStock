import type { CSSProperties, ReactNode } from 'react'

import { cn } from '../../lib/cn'

/** C-13 Badge / C-12 StatusDot（DESIGN-SPEC §5）。 */

export type BadgeTone = 'ok' | 'warn' | 'danger' | 'info' | 'neutral' | 'up' | 'down' | 'flat'

const TONES: Record<BadgeTone, string> = {
  ok: 'bg-ok-soft text-ok',
  warn: 'bg-warn-soft text-warn',
  danger: 'bg-danger-soft text-danger',
  info: 'bg-info-soft text-info',
  neutral: 'bg-flat-soft text-flat',
  up: 'bg-up-soft text-up',
  down: 'bg-down-soft text-down',
  flat: 'border border-border text-text-secondary',
}

export interface BadgeProps {
  tone?: BadgeTone
  icon?: ReactNode
  className?: string
  title?: string
  children: ReactNode
}

export function Badge({ tone = 'neutral', icon, className, title, children }: BadgeProps) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex h-5 max-w-full items-center gap-1 truncate rounded-xs px-2 text-caption font-medium',
        TONES[tone],
        className,
      )}
    >
      {icon}
      <span className="ldst-num">{children}</span>
    </span>
  )
}

export type DotTone = 'ok' | 'warn' | 'danger' | 'flat' | 'idle' | 'accent'

const DOTS: Record<DotTone, string> = {
  ok: 'bg-ok',
  warn: 'bg-warn',
  danger: 'bg-danger',
  flat: 'bg-flat',
  accent: 'bg-accent',
  idle: 'border border-border-strong bg-transparent',
}

/** 状态点必须与文字同现（§9 状态不靠色）。 */
export function StatusDot({
  tone = 'flat',
  pulse = false,
  children,
  className,
}: {
  tone?: DotTone
  pulse?: boolean
  children: ReactNode
  className?: string
}) {
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-caption text-text-secondary', className)}>
      <span
        aria-hidden="true"
        className={cn('inline-block size-2 shrink-0 rounded-pill', DOTS[tone], pulse && 'ldst-pulse')}
      />
      <span>{children}</span>
    </span>
  )
}

/** 涨跌数字：颜色只作用于数字与箭头（§7.1）。 */
export function SignedNumber({
  text,
  tone,
  arrow,
  className,
  style,
}: {
  text: string
  tone: 'up' | 'down' | 'flat'
  arrow: string
  className?: string
  style?: CSSProperties
}) {
  const color = tone === 'up' ? 'text-up' : tone === 'down' ? 'text-down' : 'text-text-secondary'
  return (
    <span className={cn('ldst-num', color, className)} style={style}>
      {text}
      {arrow && <span aria-hidden="true"> {arrow}</span>}
    </span>
  )
}
