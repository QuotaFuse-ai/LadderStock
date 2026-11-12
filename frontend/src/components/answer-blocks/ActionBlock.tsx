import { ArrowRight } from 'lucide-react'

import type { ActionBlockPayload } from './types'
import { cn } from '../../lib/cn'

/**
 * `action` 块（任务书 §6.2 / §5.5「新建」）：按钮行，点了就 `navigate(target)`。
 *
 * §7 红线 7「不得编造路由」：`target` 由 `BlockRenderer` 的导航口校验，非法目标
 * 不会到这里（渲染器会退化成一条说明）。这里只负责形状 —— 一行按钮，
 * 从第二个起用次按钮，一屏不出现两个主操作。
 */
export function ActionBlock({
  payload,
  onNavigate,
}: {
  payload: ActionBlockPayload
  onNavigate: (target: string) => void
}) {
  return (
    <nav aria-label="下一步" data-answer-block="action" className="flex flex-wrap gap-2">
      {payload.items.map((item, index) => (
        <button
          key={`${item.label}-${item.target}`}
          type="button"
          onClick={() => onNavigate(item.target)}
          className={cn(
            'inline-flex h-control select-none items-center gap-1 rounded px-3 text-body font-medium',
            'transition-all duration-fast ease-ldst active:translate-y-px',
            index === 0
              ? 'bg-accent-solid text-accent-contrast hover:bg-accent-solid-hover'
              : 'border border-border-strong bg-surface text-text hover:bg-surface-3',
          )}
        >
          {item.label}
          <ArrowRight aria-hidden="true" className="size-3.5" />
        </button>
      ))}
    </nav>
  )
}
