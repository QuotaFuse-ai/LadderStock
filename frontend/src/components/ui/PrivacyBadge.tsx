import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Lock } from 'lucide-react'

import { cn } from '../../lib/cn'
import { PrivacyNotice, privacyNoticeText } from './PrivacyNotice'

/**
 * 卡头「仅本机」隐私徽标（QUOT-552 / SPEC-HOME-V2 v2.0 §5.3）。
 *
 * 首页三张卡（持仓 / 自选 / 研究）不再印整段隐私长文案，收进这颗徽标：
 * - hover：title 给出纯文本完整口径（`privacyNoticeText`，与 `PrivacyNotice` 同一份字符串）；
 * - 点击 / Enter：展开弹层，里面渲染 `PrivacyNotice` 本体——说明文案永远只有一份实现。
 *
 * 弹层走 `createPortal`：`SectionCard` 是 `overflow-hidden`，行内展开会被裁掉。
 * 非模态（不锁滚动、不设焦点陷阱）：Esc / 点外部 / 滚动即收，关闭后焦点回徽标。
 * **隐私承诺不随数据状态消失**：加载中 / 失败 / 空态徽标常驻，调用方不得按状态条件渲染。
 */
export interface PrivacyBadgeProps {
  /** 说明对象（持仓 / 自选）；默认「持仓」，与 PrivacyNotice 同口径。 */
  subject?: string
  /** 非存储类口径（研究卡：上下文白名单）覆盖弹层内容；给了就不再渲染 PrivacyNotice。 */
  detail?: ReactNode
  /** 覆盖 hover 的纯文本口径；默认 privacyNoticeText(subject)。 */
  detailText?: string
  className?: string
}

/** 弹层几何（内联 style，不走 class——check:ui 规则 3 禁方括号任意值）。 */
const POPOVER_WIDTH = 320
const VIEWPORT_GUTTER = 8

export function PrivacyBadge({ subject, detail, detailText, className }: PrivacyBadgeProps) {
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const panelId = useId()

  const title = detailText ?? privacyNoticeText(subject)

  const openPanel = () => {
    const rect = buttonRef.current?.getBoundingClientRect()
    if (!rect) return
    const left = Math.max(VIEWPORT_GUTTER, Math.min(rect.left, window.innerWidth - POPOVER_WIDTH - VIEWPORT_GUTTER))
    // 视口底部放不下时翻到徽标上方（按说明条典型高度 ~180px 预留）
    const above = rect.bottom + 190 > window.innerHeight
    setPosition({ left, top: above ? Math.max(VIEWPORT_GUTTER, rect.top - 190) : rect.bottom + 4 })
    setOpen(true)
  }

  const closePanel = (restoreFocus = true) => {
    setOpen(false)
    if (restoreFocus) buttonRef.current?.focus({ preventScroll: true })
  }

  useEffect(() => {
    if (!open) return
    panelRef.current?.focus({ preventScroll: true })
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (panelRef.current?.contains(target) || buttonRef.current?.contains(target)) return
      closePanel(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        closePanel()
      }
    }
    // 滚动后锚点坐标失效：直接收起，不追位置（与顶栏浮层同一取舍）
    const onScroll = () => closePanel(false)
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onScroll)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onScroll)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        title={title}
        aria-label="隐私说明：仅本机"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => (open ? closePanel() : openPanel())}
        className={cn(
          'inline-flex h-5 shrink-0 items-center gap-1 rounded-xs border border-border px-2 text-caption text-text-secondary transition-colors duration-fast ease-ldst hover:bg-surface-3 hover:text-text',
          className,
        )}
      >
        <Lock className="size-3 shrink-0 text-accent" aria-hidden="true" />
        仅本机
      </button>
      {open &&
        position &&
        createPortal(
          <div
            ref={panelRef}
            id={panelId}
            role="dialog"
            aria-label="隐私说明"
            tabIndex={-1}
            style={{ position: 'fixed', left: position.left, top: position.top, width: POPOVER_WIDTH }}
            // bg-surface 必须不透明：PrivacyNotice 的 accent-soft 是 tint，浮在表格上会透字
            className="z-overlay rounded bg-surface shadow-dialog outline-none"
          >
            {detail ? (
              <div
                role="note"
                aria-label="隐私说明"
                className="rounded border border-accent bg-accent-soft px-3 py-2 text-body leading-body text-text-secondary"
              >
                {detail}
              </div>
            ) : (
              <PrivacyNotice subject={subject} />
            )}
          </div>,
          document.body,
        )}
    </>
  )
}