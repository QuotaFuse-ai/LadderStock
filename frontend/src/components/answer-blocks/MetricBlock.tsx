import { Minus } from 'lucide-react'

import type { MetricBlockPayload, MetricDirection } from './types'
import { cn } from '../../lib/cn'

/**
 * `metric` 块（任务书 §6.2 / §8.2「新建」）：2–4 个并列格，`value` 走
 * `.ldst-num`（等宽数字）+ `font-semibold text-text`，单位是 `text-caption text-text-muted`。
 *
 * 方向色只作用于数字本身（§8.1：up/down 只用于数字与涨跌箭头）：
 * `up` → `text-up`、`down` → `text-down`、`flat` → `text-flat`；没给方向就不上色，
 * 因为「没方向」和「平」是两件事。
 *
 * 375px 下两块一行（`grid-cols-2`），`sm` 起最多 4 列——一屏手机宽度也读得下。
 */

const DIRECTION_CLASS: Record<MetricDirection, string> = {
  up: 'text-up',
  down: 'text-down',
  flat: 'text-flat',
}

const DIRECTION_LABEL: Record<MetricDirection, string> = {
  up: '上涨',
  down: '下跌',
  flat: '持平',
}

const DIRECTION_ARROW: Record<MetricDirection, string> = {
  up: '↑',
  down: '↓',
  flat: '',
}

/** 数值原样展示：只做千分位分组，不换算单位、不补 0 */
export function formatMetricValue(value: number): string {
  // `toLocaleString` 保留原始小数位（38.62 → "38.62"，-4.3 → "-4.3"）
  return value.toLocaleString('en-US')
}

export function MetricBlock({ payload }: { payload: MetricBlockPayload }) {
  const count = payload.items.length

  return (
    <ul
      data-answer-block="metric"
      className="grid grid-cols-2 gap-2 rounded border border-border bg-surface p-3 shadow-card sm:grid-cols-4"
    >
      {payload.items.map((item) => {
        const direction = item.direction
        const ratio = count === 2 ? 'sm:col-span-2' : 'sm:col-span-1'
        return (
          <li key={`${item.label}-${item.as_of ?? ''}`} className={cn('min-w-0 rounded-xs bg-surface-2 p-3', ratio)}>
            <p className="truncate text-caption text-text-secondary">{item.label}</p>
            <p className="mt-1 flex flex-wrap items-baseline gap-x-1">
              <span className={cn('ldst-num text-page font-semibold', direction ? DIRECTION_CLASS[direction] : 'text-text')}>
                {formatMetricValue(item.value)}
              </span>
              {item.unit && <span className="text-caption text-text-muted">{item.unit}</span>}
              {direction && direction !== 'flat' && (
                <span className="text-caption text-text-muted">
                  <span aria-hidden="true">{DIRECTION_ARROW[direction]}</span>
                  <span className="sr-only">{DIRECTION_LABEL[direction]}</span>
                </span>
              )}
            </p>
            {item.as_of && (
              <p className="mt-0.5 flex items-center gap-1 truncate text-caption text-text-muted">
                <Minus aria-hidden="true" className="size-3 shrink-0" />
                <span className="ldst-num">{item.as_of}</span>
              </p>
            )}
          </li>
        )
      })}
    </ul>
  )
}
