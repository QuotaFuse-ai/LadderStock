import { useRef, type KeyboardEvent } from 'react'

import { cn } from '../../lib/cn'

/**
 * C-07 Segmented（DESIGN-SPEC §5）。
 *
 * 语义是「一组互斥选项」而不是 tab：`radiogroup` + `radio`，方向键切换。
 * 用于主题（系统/浅色/深色）、验证屏「明日条件/收盘对账」切换。
 */

export interface SegmentedOption<T extends string> {
  value: T
  label: string
  disabled?: boolean
}

export interface SegmentedProps<T extends string> {
  options: readonly SegmentedOption<T>[]
  value: T
  onChange: (next: T) => void
  ariaLabel: string
  size?: 'sm' | 'md'
  className?: string
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  size = 'md',
  className,
}: SegmentedProps<T>) {
  const refs = useRef<Array<HTMLButtonElement | null>>([])

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft' && event.key !== 'Home' && event.key !== 'End') {
      return
    }
    event.preventDefault()
    const enabled = options
      .map((option, position) => ({ option, position }))
      .filter((entry) => !entry.option.disabled)
    if (enabled.length === 0) return
    const current = enabled.findIndex((entry) => entry.position === index)
    let next = current
    if (event.key === 'ArrowRight') next = (current + 1) % enabled.length
    if (event.key === 'ArrowLeft') next = (current - 1 + enabled.length) % enabled.length
    if (event.key === 'Home') next = 0
    if (event.key === 'End') next = enabled.length - 1
    const target = enabled[next]
    onChange(target.option.value)
    refs.current[target.position]?.focus()
  }

  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn(
        'inline-flex items-center gap-0 rounded-pill border border-border-strong bg-sunken p-half',
        className,
      )}
    >
      {options.map((option, index) => {
        const selected = option.value === value
        return (
          <button
            key={option.value}
            ref={(node) => {
              refs.current[index] = node
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            disabled={option.disabled}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) => handleKeyDown(event, index)}
            className={cn(
              'rounded-pill font-medium transition-colors duration-fast ease-ldst disabled:cursor-not-allowed disabled:opacity-50',
              size === 'sm' ? 'h-6 px-2 text-caption' : 'h-7 px-3 text-caption',
              selected
                ? 'bg-surface text-text shadow-card'
                : 'text-text-secondary hover:text-text',
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
