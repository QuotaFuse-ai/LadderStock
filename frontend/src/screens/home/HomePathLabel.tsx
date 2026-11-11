import type { ReactNode } from 'react'

import { cn } from '../../lib/cn'

/**
 * 首页 B 块三条研究路径的统一序号标签（QUOT-551）。
 *
 * 同一个「序号徽标 + 白话动作」体系把三条可选路径一眼摆明：
 * 1 问 AI（自由提问）/ 2 开现成工具（点一下直达）/ 3 直接搜（不经过 AI）。
 * 徽标恒为 accent-soft 底，使三者读作「同一类路径标记」；主次靠 `tone` 与
 * 各自标题字号（h2 subtitle > h3 13px > caption）区分，不靠序号本身。纯呈现，无行为。
 */
export interface HomePathLabelProps {
  step: '1' | '2' | '3'
  tone?: 'primary' | 'secondary' | 'tertiary'
  className?: string
  children: ReactNode
}

const LABEL_TONE: Record<NonNullable<HomePathLabelProps['tone']>, string> = {
  primary: 'text-accent',
  secondary: 'text-text-secondary',
  tertiary: 'text-text-muted',
}

export function HomePathLabel({ step, tone = 'primary', className, children }: HomePathLabelProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 text-11px font-medium',
        LABEL_TONE[tone],
        className,
      )}
    >
      <span
        aria-hidden="true"
        className="inline-flex size-4 shrink-0 items-center justify-center rounded-full bg-accent-soft text-11px leading-none text-accent"
      >
        {step}
      </span>
      <span>{children}</span>
    </span>
  )
}