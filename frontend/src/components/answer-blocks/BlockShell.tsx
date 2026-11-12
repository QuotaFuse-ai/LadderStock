import type { ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'

import { cn } from '../../lib/cn'
import { Button } from '../ui'

/** 块间距与流方向（§8.3）：永远纵向，块间距 space-2。答案列和独立块都用这一套。 */
export const ANSWER_FLOW = 'flex w-full flex-col gap-2'

/**
 * 块容器（任务书 §8.2）：`bg-surface` + 1px `border-border` + 圆角 8 + `shadow-card`，
 * 内距 space-3（12px），块间距由父级 `flex flex-col gap-2` 给。
 *
 * 不套 `ui/Card`：那是带 44px 标题栏的页面卡，块里不需要标题栏——
 * 块标题是辅助文字（`text-caption text-text-muted`），不是 h2。
 */
/** `notice` / `action` 不走 BlockShell，共用同一套描边配方 */
export const OUTER_MARK = 'rounded border border-border bg-surface p-3 shadow-card'

export function BlockShell({
  type,
  title,
  meta,
  children,
  className,
}: {
  /** 块类型（`DataTable` 之外的块都会打 `data-answer-block`） */
  type: string
  title?: ReactNode
  /** 标题行右侧：as_of / 单位 / 点数这类辅助信息，与标题同一层级 */
  meta?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section data-answer-block={type} className={cn(OUTER_MARK, className)}>
      {(title || meta) && (
        <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          {title && <span className="text-caption font-medium text-text-secondary">{title}</span>}
          {meta && <span className="text-caption text-text-muted">{meta}</span>}
        </div>
      )}
      {children}
    </section>
  )
}

/** 载荷不合法 / 未识别类型：块照样占位，明确说明没画出来，不静默消失。 */
export function InvalidBlockPanel({ reason }: { reason: string }) {
  return (
    <section
      role="status"
      data-answer-block="invalid"
      className="flex items-start gap-2 rounded border border-dashed border-border-strong bg-surface p-3 text-caption text-text-muted"
    >
      <AlertTriangle aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-warn" />
      <span>这一块没有画出来：{reason}。</span>
    </section>
  )
}

/**
 * 动作块的目标在前端导航表里不存在（§7 红线 7「不得编造路由」）。
 * 不摆一个点了没反应的按钮，而是说明「这一步在这里走不到」，并把原文目标给出来。
 */
export function UnreachableActionPanel({ labels, targets }: { labels: string[]; targets: string[] }) {
  return (
    <section
      role="status"
      data-answer-block="action"
      className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded border border-dashed border-border-strong bg-surface p-3 text-caption text-text-muted"
    >
      <AlertTriangle aria-hidden="true" className="size-3.5 shrink-0 text-warn" />
      <span>这些跳转目标不在本应用的页面表里，没有摆成按钮：</span>
      <span className="flex flex-wrap items-center gap-1">
        {labels.map((label, index) => (
          <span key={`${label}-${index}`} className="rounded-xs bg-surface-2 px-1.5 py-0.5">
            {label} → <code className="ldst-num break-all">{targets[index]}</code>
          </span>
        ))}
      </span>
    </section>
  )
}

/** 答案列没有可渲染的块时：不假装「回答为空」，给一口气说明 + 一个可选动作。 */
export function EmptyAnswerPanel({ onRetry }: { onRetry?: () => void }) {
  return (
    <section className={cn(OUTER_MARK, 'flex flex-col items-start gap-2')} data-answer-block="empty">
      <p className="text-body text-text">这一轮没有可展示的答案块。</p>
      <p className="text-caption text-text-muted">页面不会替后端编一段结论；可以再问一次，或换一种问法。</p>
      {onRetry && (
        <Button variant="secondary" onClick={onRetry}>
          重新提问
        </Button>
      )}
    </section>
  )
}
