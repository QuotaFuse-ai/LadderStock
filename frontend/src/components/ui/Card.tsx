import type { ReactNode } from 'react'

import { cn } from '../../lib/cn'

/**
 * C-10 容器卡 / C-11 指标卡（DESIGN-SPEC §5）。
 *
 * 卡片只承担「分组」职责：描边用 `border`（装饰），交互边界才用 `border-strong`。
 * 标题栏统一 44px 高，右侧放唯一的主/次操作，避免每张卡各写一套内距。
 */

export interface CardProps {
  children: ReactNode
  className?: string
  as?: 'section' | 'div' | 'article'
}

export function Card({ children, className, as: Tag = 'section' }: CardProps) {
  return (
    <Tag className={cn('rounded border border-border bg-surface shadow-card', className)}>
      {children}
    </Tag>
  )
}

export interface CardHeaderProps {
  title: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  className?: string
  id?: string
}

export function CardHeader({ title, subtitle, actions, className, id }: CardHeaderProps) {
  return (
    <div
      className={cn(
        'flex min-h-topbar items-center justify-between gap-3 border-b border-border px-4 py-2',
        className,
      )}
    >
      <div className="min-w-0">
        <h2 id={id} className="truncate text-subtitle font-semibold text-text">
          {title}
        </h2>
        {subtitle && <p className="truncate text-caption text-text-secondary">{subtitle}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}

export function CardBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('p-4', className)}>{children}</div>
}

export function CardFooter({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('border-t border-border px-4 py-2 text-caption text-text-secondary', className)}>
      {children}
    </div>
  )
}

export type SectionTone = 'ok' | 'warn' | 'danger' | 'info' | 'neutral'

const SECTION_BAR: Record<SectionTone, string> = {
  ok: 'bg-ok',
  warn: 'bg-warn',
  danger: 'bg-danger',
  info: 'bg-info',
  neutral: 'bg-border-strong',
}

export interface SectionCardProps extends CardProps {
  title: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  /** 左侧 3px 状态条（§4.5）：只在有状态含义时给，不装饰 */
  tone?: SectionTone
  bodyClassName?: string
}

/** 带左侧状态条的区块卡：复盘五段、日志纪律卡等复用。 */
export function SectionCard({
  title,
  subtitle,
  actions,
  tone = 'neutral',
  children,
  className,
  bodyClassName,
  as,
}: SectionCardProps) {
  return (
    <Card
      as={as}
      className={cn('relative overflow-hidden', className)}
    >
      <span
        aria-hidden="true"
        className={cn('absolute inset-y-0 left-0 w-status-bar', SECTION_BAR[tone])}
      />
      <CardHeader title={title} subtitle={subtitle} actions={actions} />
      <div className={cn('p-4', bodyClassName)}>{children}</div>
    </Card>
  )
}

export interface MetricCardProps {
  label: string
  value: ReactNode
  unit?: string
  /** 指标解释：缺失时为「为什么缺」的原因，不是 0 */
  note?: ReactNode
  /** 变动值（已格式化的 SignedNumber），颜色只作用于数字 */
  change?: ReactNode
  /** 每屏最多一处 36px hero（§4.5 / §10） */
  hero?: boolean
  icon?: ReactNode
  /** 右上角溯源徽标（§4.2 L2：freshness / source_status），不放操作 */
  badge?: ReactNode
}

/** 首页 / 复盘用的指标卡：96px 固定高，缺失值显示 `—` + 原因（不补 0）。 */
export function MetricCard({
  label,
  value,
  unit,
  note,
  change,
  hero = false,
  icon,
  badge,
}: MetricCardProps) {
  return (
    <div className="flex h-metric flex-col justify-between rounded border border-border bg-surface p-4 shadow-card">
      <div className="flex items-center gap-1.5 text-caption text-text-secondary">
        {icon}
        <span className="truncate">{label}</span>
        {badge && <span className="ml-auto shrink-0">{badge}</span>}
      </div>
      <div className="flex items-baseline gap-1">
        <span
          className={cn(
            'ldst-num font-semibold text-text',
            hero ? 'text-hero' : 'text-page',
          )}
        >
          {value}
        </span>
        {unit && <span className="text-caption text-text-muted">{unit}</span>}
        {change && <span className="ml-2 text-body">{change}</span>}
      </div>
      {note && <p className="truncate text-caption text-text-muted">{note}</p>}
    </div>
  )
}
