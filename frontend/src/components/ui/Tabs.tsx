import { useRef, type KeyboardEvent, type ReactNode } from 'react'

import { cn } from '../../lib/cn'

/**
 * C-08 Tabs（DESIGN-SPEC §5）：2px 强调色下划线，键盘左右切换。
 * 与 Segmented 的分工：Tabs 切换「同一实体的不同视角」（设置四栏），Segmented 切换互斥模式。
 */

export interface TabOption<T extends string> {
  value: T
  label: string
  icon?: ReactNode
  badge?: ReactNode
}

export interface TabsProps<T extends string> {
  tabs: readonly TabOption<T>[]
  value: T
  onChange: (next: T) => void
  ariaLabel: string
  className?: string
}

export function Tabs<T extends string>({ tabs, value, onChange, ariaLabel, className }: TabsProps<T>) {
  const refs = useRef<Array<HTMLButtonElement | null>>([])

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft' && event.key !== 'Home' && event.key !== 'End') {
      return
    }
    event.preventDefault()
    let next = index
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length
    if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length
    if (event.key === 'Home') next = 0
    if (event.key === 'End') next = tabs.length - 1
    onChange(tabs[next].value)
    refs.current[next]?.focus()
  }

  return (
    <div role="tablist" aria-label={ariaLabel} className={cn('flex items-center gap-1 border-b border-border', className)}>
      {tabs.map((tab, index) => {
        const selected = tab.value === value
        return (
          <button
            key={tab.value}
            ref={(node) => {
              refs.current[index] = node
            }}
            type="button"
            role="tab"
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.value)}
            onKeyDown={(event) => handleKeyDown(event, index)}
            className={cn(
              '-mb-px inline-flex h-control items-center gap-1.5 border-b-2 px-3 text-body transition-colors duration-fast ease-ldst',
              selected
                ? 'border-accent font-medium text-text'
                : 'border-transparent text-text-secondary hover:text-text',
            )}
          >
            {tab.icon}
            <span>{tab.label}</span>
            {tab.badge}
          </button>
        )
      })}
    </div>
  )
}
