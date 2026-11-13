import { useContext, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ClipboardList } from 'lucide-react'

import { cn } from '../../lib/cn'
import { PageToolsSlotContext } from '../../lib/pageToolsSlot'
import type { WatchSnapshotRow } from '../../api'
import type { LadderEnvelope, ValidationPayload, WatchSnapshotPayload } from '../../api'
import { HomeTodoMoversCard } from './HomeTodoMoversCard'
import type { HomeTodo } from './HomeTodoMoversCard'
import type { BlockView } from './useHomeSource'

/**
 * 「待办与异动」顶栏入口（QUOT-587）。
 *
 * 卡片本体从内容区左区撤出（老板返工：不占内容区一块），顶栏只留一颗紧凑入口：
 * `待办 N · 异动 N`，点击在按钮下方展开完整表——表就是既有的 `HomeTodoMoversCard`
 * （COLUMNS / DataTable / 口径都不动，QUOT-552 / QUOT-521 的用例继续成立）。
 * 顶栏槽位缺失（组件单测 / 独立渲染）时退回内容区就地渲染，屏幕不区分环境。
 */

const POPOVER_WIDTH = 460
const POPOVER_MAX_HEIGHT = 360
const VIEWPORT_GUTTER = 8

export interface HomeTodoMoversBarProps {
  todos: HomeTodo[]
  movers: WatchSnapshotRow[]
  validation: BlockView<LadderEnvelope<ValidationPayload>>
  watch: BlockView<LadderEnvelope<WatchSnapshotPayload>>
  className?: string
}

export function HomeTodoMoversBar({ todos, movers, validation, watch, className }: HomeTodoMoversBarProps) {
  const toolsSlot = useContext(PageToolsSlotContext)
  const [open, setOpen] = useState(false)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null)
  const panelId = useId()

  const openPanel = () => {
    const rect = buttonRef.current?.getBoundingClientRect()
    if (!rect) return
    const left = Math.max(VIEWPORT_GUTTER, Math.min(rect.left, window.innerWidth - POPOVER_WIDTH - VIEWPORT_GUTTER))
    setPosition({ left, top: rect.bottom + 4 })
    setOpen(true)
  }

  const closePanel = () => {
    setOpen(false)
    buttonRef.current?.focus({ preventScroll: true })
  }

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (panelRef.current?.contains(target) || buttonRef.current?.contains(target)) return
      setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        closePanel()
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  // 顶栏挂着时弹层锚定按钮；无槽位（单测）时入口就地渲染、无弹层
  if (!toolsSlot) {
    return (
      <HomeTodoMoversCard
        todos={todos}
        movers={movers}
        validation={validation}
        watch={watch}
        className={cn('shrink-0', className)}
      />
    )
  }

  const entry = (
    <>
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => (open ? setOpen(false) : openPanel())}
        className={cn(
          'inline-flex h-control items-center gap-1.5 rounded-pill border border-border px-3 text-caption text-text-secondary transition-colors duration-fast ease-ldst hover:bg-surface-3 hover:text-text',
          open && 'border-border-strong bg-surface-3 text-text',
          className,
        )}
      >
        <ClipboardList className="size-3.5 shrink-0 text-accent" aria-hidden="true" />
        <span>
          待办 {todos.length} · 异动 {movers.length}
        </span>
        {todos.some((todo) => todo.tone === 'danger') && (
          <span aria-hidden="true" className="size-1.5 rounded-full bg-danger" />
        )}
      </button>
      {open &&
        position &&
        createPortal(
          <div
            ref={panelRef}
            id={panelId}
            role="dialog"
            aria-label="待办与异动"
            tabIndex={-1}
            style={{
              position: 'fixed',
              left: position.left,
              top: position.top,
              width: POPOVER_WIDTH,
              maxHeight: POPOVER_MAX_HEIGHT,
            }}
            className="z-overlay flex flex-col overflow-hidden rounded border border-border bg-surface shadow-dialog outline-none"
          >
            <HomeTodoMoversCard todos={todos} movers={movers} validation={validation} watch={watch} />
          </div>,
          document.body,
        )}
    </>
  )

  return createPortal(entry, toolsSlot)
}
