import type { ReactNode } from 'react'
import { WifiOff } from 'lucide-react'

import { cn } from '../lib/cn'
import type { PageState } from '../lib/pageState'
import { Banner, Skeleton } from './ui'

/**
 * 页面骨架件：标题栏 / 演示态标记 / 离线提示 / 整屏骨架。
 *
 * 五态演示统一走 `?state=`（§8），真实数据态与演示态共用同一套 DOM，
 * 保证截图和真实渲染不会两套结构。
 */

export interface PageHeaderProps {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
  badge?: ReactNode
  meta?: ReactNode
  className?: string
}

export function PageHeader({ title, subtitle, actions, badge, meta, className }: PageHeaderProps) {
  return (
    <header className={cn('flex min-h-topbar flex-wrap items-center justify-between gap-3', className)}>
      <div className="flex min-w-0 flex-wrap items-center gap-3">
        <h1 className="text-page font-semibold leading-tight text-text">{title}</h1>
        {badge}
        {subtitle && <p className="text-caption text-text-secondary">{subtitle}</p>}
        {meta}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </header>
  )
}

export const DEMO_STATE_LABEL: Record<Exclude<PageState, 'normal'>, string> = {
  empty: '空态',
  loading: '加载中',
  error: '错误',
  partial: '部分失败',
  offline: '离线',
}

/** 演示态水印：让人一眼看出这是 `?state=` 强制态，不是真实数据。 */
export function DemoStateBadge({ state }: { state: PageState | null }) {
  if (!state || state === 'normal') return null
  return (
    <span className="inline-flex h-5 items-center rounded-xs border border-border-strong px-2 text-caption text-text-secondary">
      演示态：{DEMO_STATE_LABEL[state]}（?state={state}）
    </span>
  )
}

export function OfflineBanner({ onRetry }: { onRetry?: () => void }) {
  return (
    <Banner
      tone="warn"
      title="本机服务未连接"
      action={
        onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="text-caption font-medium text-text underline underline-offset-2"
          >
            重试
          </button>
        ) : undefined
      }
    >
      先运行 <code className="ldst-num">ldst run</code> 启动本机只读 API（默认
      127.0.0.1:5950）。本机草稿仍可编辑，恢复自 IndexedDB 的内容不会丢。
    </Banner>
  )
}

/** 整屏骨架：标题 + 指标卡 + 表格，保持和图谱一致的节奏（C-18）。 */
export function ScreenSkeleton({ metrics = 5, rows = 6 }: { metrics?: number; rows?: number }) {
  return (
    <div className="flex flex-col gap-5" aria-busy="true" aria-live="polite">
      <span className="sr-only">正在加载…</span>
      <div className="grid grid-cols-2 gap-4 desk:grid-cols-5">
        {Array.from({ length: metrics }).map((_, index) => (
          <div key={index} className="flex h-metric flex-col justify-between rounded border border-border bg-surface p-4">
            <Skeleton className="w-1/2" />
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="w-1/3" />
          </div>
        ))}
      </div>
      <div className="rounded border border-border bg-surface">
        {Array.from({ length: rows + 1 }).map((_, index) => (
          <div key={index} className="flex h-row items-center gap-4 border-b border-border px-3 last:border-b-0">
            <Skeleton className="w-16" />
            <Skeleton className="w-24" />
            <Skeleton className="w-12" />
            <Skeleton className="w-20" />
            <Skeleton className="w-10" />
          </div>
        ))}
      </div>
    </div>
  )
}

export function OfflineIcon() {
  return <WifiOff className="size-4" aria-hidden="true" />
}
