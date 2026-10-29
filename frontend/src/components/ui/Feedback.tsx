import { useCallback, useState, type ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, Info, RotateCcw, X, XCircle } from 'lucide-react'

import { cn } from '../../lib/cn'
import { Button, IconButton } from './Button'

/**
 * C-14 Toast / C-17 EmptyState / C-18 Skeleton / C-19 ErrorBlock + Banner（DESIGN-SPEC §5）。
 */

export type BannerTone = 'info' | 'ok' | 'warn' | 'danger'

const BANNER_TONE: Record<BannerTone, { box: string; icon: ReactNode }> = {
  info: { box: 'border-info bg-info-soft text-info', icon: <Info className="size-4" aria-hidden="true" /> },
  ok: { box: 'border-ok bg-ok-soft text-ok', icon: <CheckCircle2 className="size-4" aria-hidden="true" /> },
  warn: {
    box: 'border-warn bg-warn-soft text-warn',
    icon: <AlertTriangle className="size-4" aria-hidden="true" />,
  },
  danger: {
    box: 'border-danger bg-danger-soft text-danger',
    icon: <XCircle className="size-4" aria-hidden="true" />,
  },
}

export interface BannerProps {
  tone?: BannerTone
  title?: ReactNode
  children?: ReactNode
  action?: ReactNode
  className?: string
  id?: string
}

/** 横幅提示：结论在前、原因在后；warn/danger 用 `role="alert"`。 */
export function Banner({ tone = 'info', title, children, action, className, id }: BannerProps) {
  const config = BANNER_TONE[tone]
  return (
    <div
      id={id}
      role={tone === 'warn' || tone === 'danger' ? 'alert' : 'status'}
      className={cn(
        'flex items-start gap-2 rounded border px-3 py-2 text-body leading-body',
        config.box,
        className,
      )}
    >
      <span className="mt-0.5 shrink-0">{config.icon}</span>
      <div className="min-w-0 flex-1">
        {title && <p className="font-medium">{title}</p>}
        {children && <div className="text-text-secondary">{children}</div>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  )
}

export interface EmptyStateProps {
  icon?: ReactNode
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  className?: string
  compact?: boolean
}

/** 空态：必须给出下一步动作（§8「空态要有出口」）。 */
export function EmptyState({ icon, title, description, action, className, compact }: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-2 text-center',
        compact ? 'py-4' : 'min-h-empty py-6',
        className,
      )}
    >
      {icon && <div className="text-text-muted [&>svg]:size-12">{icon}</div>}
      <p className="text-subtitle font-medium text-text">{title}</p>
      {description && <p className="max-w-[52ch] text-body leading-body text-text-secondary">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

export interface ErrorBlockProps {
  title: string
  detail?: ReactNode
  code?: string | null
  onRetry?: () => void
  retryLabel?: string
  variant?: 'panel' | 'inline' | 'banner'
  className?: string
}

/** 错误块：一句结论 + 原因 + 代码 + 重试（§8）。 */
export function ErrorBlock({
  title,
  detail,
  code,
  onRetry,
  retryLabel = '重试',
  variant = 'panel',
  className,
}: ErrorBlockProps) {
  const body = (
    <>
      <div className="flex items-start gap-2">
        <XCircle className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden="true" />
        <div className="min-w-0">
          <p className="text-body font-medium text-text">{title}</p>
          {detail && <p className="text-caption leading-body text-text-secondary">{detail}</p>}
          {code && <p className="ldst-num mt-1 text-caption text-text-muted">{code}</p>}
        </div>
      </div>
      {onRetry && (
        <Button
          size="md"
          variant="secondary"
          className="mt-2"
          onClick={onRetry}
          leadingIcon={<RotateCcw className="size-3.5" aria-hidden="true" />}
        >
          {retryLabel}
        </Button>
      )}
    </>
  )

  if (variant === 'banner') {
    return (
      <div role="alert" className={cn('flex items-start justify-between gap-3 rounded border border-danger bg-danger-soft px-3 py-2', className)}>
        {body}
      </div>
    )
  }
  if (variant === 'inline') {
    return (
      <div role="alert" className={cn('flex flex-col', className)}>
        {body}
      </div>
    )
  }
  return (
    <div role="alert" className={cn('rounded border border-border bg-surface p-4', className)}>
      {body}
    </div>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <span aria-hidden="true" className={cn('ldst-skeleton block h-3 w-full', className)} />
}

export function SkeletonText({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-2" aria-hidden="true">
      {Array.from({ length: lines }).map((_, index) => (
        <span
          key={index}
          className="ldst-skeleton block h-3"
          style={{ width: `${100 - (index % 3) * 12}%` }}
        />
      ))}
    </div>
  )
}

export interface ToastItem {
  id: number
  tone: 'ok' | 'danger' | 'info'
  message: string
}

let toastSeed = 0

/** 最多保留 3 条，4s 自动消失（C-14）。 */
export function useToasts() {
  const [items, setItems] = useState<ToastItem[]>([])

  const dismiss = useCallback((id: number) => {
    setItems((current) => current.filter((item) => item.id !== id))
  }, [])

  const push = useCallback((message: string, tone: ToastItem['tone'] = 'info') => {
    const id = ++toastSeed
    setItems((current) => [...current.slice(-2), { id, tone, message }])
    window.setTimeout(() => {
      setItems((current) => current.filter((item) => item.id !== id))
    }, 4000)
  }, [])

  return { items, push, dismiss }
}

const TOAST_TONE: Record<ToastItem['tone'], string> = {
  ok: 'border-ok text-ok',
  danger: 'border-danger text-danger',
  info: 'border-border-strong text-text',
}

export function Toasts({ items, onDismiss }: { items: ToastItem[]; onDismiss: (id: number) => void }) {
  if (items.length === 0) return null
  return (
    <div className="fixed bottom-4 right-4 z-toast flex w-drawer max-w-full flex-col gap-2">
      {items.map((item) => (
        <div
          key={item.id}
          role="status"
          className={cn(
            'flex items-center justify-between gap-2 rounded border bg-surface px-3 py-2 text-body shadow-overlay',
            TOAST_TONE[item.tone],
          )}
        >
          <span>{item.message}</span>
          <IconButton label="关闭提示" icon={<X className="size-4" aria-hidden="true" />} onClick={() => onDismiss(item.id)} />
        </div>
      ))}
    </div>
  )
}
