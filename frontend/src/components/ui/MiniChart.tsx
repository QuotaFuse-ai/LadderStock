import { cn } from '../../lib/cn'

/**
 * CH-03 题材强度横向条 + C-20 Sparkline（DESIGN-SPEC §6）。
 *
 * 横向条用 DOM 而不是 canvas：条数少、需要逐条文字标签与读屏信息，
 * 用 DOM 更省事也更可访问；canvas 只留给需要坐标轴的折线/柱图。
 */

export interface HBarItem {
  label: string
  value: number
  hint?: string
}

export interface HBarListProps {
  items: HBarItem[]
  unit?: string
  className?: string
  /** 第一名用实色，其余降为 60% 不透明度（§6.3） */
  emphasizeFirst?: boolean
  format?: (value: number) => string
}

export function HBarList({ items, unit = '', className, emphasizeFirst = true, format }: HBarListProps) {
  const max = Math.max(...items.map((item) => item.value), 1)
  return (
    <ol className={cn('flex flex-col gap-2', className)}>
      {items.map((item, index) => {
        const width = Math.max(2, Math.round((item.value / max) * 100))
        const strong = !emphasizeFirst || index === 0
        return (
          <li key={`${item.label}-${index}`} className="flex items-center gap-2">
            <span className="w-24 shrink-0 truncate text-body text-text" title={item.label}>
              {item.label}
            </span>
            <span className="relative h-2 flex-1 overflow-hidden rounded-xs bg-surface-3">
              <span
                className={cn('absolute inset-y-0 left-0 rounded-xs bg-accent', !strong && 'opacity-60')}
                style={{ width: `${width}%` }}
              />
            </span>
            <span className="ldst-num w-16 shrink-0 text-right text-body text-text-secondary">
              {format ? format(item.value) : item.value}
              {unit && <span className="ml-half text-caption text-text-muted">{unit}</span>}
            </span>
          </li>
        )
      })}
    </ol>
  )
}

export interface SparklineProps {
  values: number[]
  tone?: 'up' | 'down' | 'flat'
  ariaLabel: string
  className?: string
  width?: number
  height?: number
}

/** 迷你走势：纯装饰性趋势（具体数值看表格），因此必须给 `role="img"` + 文字描述。 */
export function Sparkline({ values, tone = 'flat', ariaLabel, className, width = 88, height = 24 }: SparklineProps) {
  if (values.length < 2) {
    return (
      <span className={cn('ldst-num text-caption text-text-muted', className)} title="数据点不足，暂不绘制">
        —
      </span>
    )
  }
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const stepX = width / (values.length - 1)
  const points = values
    .map((value, index) => {
      const x = index * stepX
      const y = height - 2 - ((value - min) / span) * (height - 4)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
  const stroke = tone === 'up' ? 'text-up' : tone === 'down' ? 'text-down' : 'text-text-secondary'
  return (
    <svg
      role="img"
      aria-label={ariaLabel}
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className={cn('shrink-0', stroke, className)}
    >
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}
