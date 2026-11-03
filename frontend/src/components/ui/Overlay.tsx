import { useCallback, useEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

import { cn } from '../../lib/cn'
import { Button, IconButton } from './Button'
import { Checkbox } from './Field'
import { useEnter } from './useEnter'

/**
 * C-15 Drawer / C-16 ConfirmDialog（DESIGN-SPEC §5）。
 *
 * 共同行为：`role="dialog" aria-modal`、Esc 关闭、焦点环内循环、滚动锁定、
 * 关闭后焦点回到触发元素。ConfirmDialog 取代 `window.confirm`（C-16 明令禁止原生弹窗）。
 */

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'

function useModalChrome(
  open: boolean,
  onRequestClose: () => void,
  ref: RefObject<HTMLElement>,
): void {
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    const focusables = (): HTMLElement[] => {
      const node = ref.current
      if (!node) return []
      return Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (element) => element.offsetParent !== null || element === document.activeElement,
      )
    }

    const timer = window.setTimeout(() => {
      const first = focusables()[0]
      first?.focus()
    }, 0)

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onRequestClose()
        return
      }
      if (event.key !== 'Tab') return
      const items = focusables()
      if (items.length === 0) return
      const first = items[0]
      const last = items[items.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      window.clearTimeout(timer)
      document.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = previousOverflow
      previous?.focus?.()
    }
  }, [open, onRequestClose, ref])
}

export interface DrawerProps {
  open: boolean
  onClose: () => void
  /** 有未保存修改时由调用方决定是否真的关闭（返回 false 取消关闭） */
  confirmClose?: () => boolean | Promise<boolean>
  title: ReactNode
  subtitle?: ReactNode
  footer?: ReactNode
  width?: 'md' | 'lg'
  children: ReactNode
}

export function Drawer({
  open,
  onClose,
  confirmClose,
  title,
  subtitle,
  footer,
  width = 'md',
  children,
}: DrawerProps) {
  const panelRef = useRef<HTMLDivElement>(null)

  const requestClose = useCallback(() => {
    void (async () => {
      if (confirmClose) {
        const allowed = await confirmClose()
        if (!allowed) return
      }
      onClose()
    })()
  }, [confirmClose, onClose])

  useModalChrome(open, requestClose, panelRef)
  /** 抽屉是 `if (!open) return null` 的即时挂载，入场动画必须由 useEnter 提供起止两帧 */
  const entered = useEnter(open)

  if (!open) return null

  return createPortal(
    <div className="fixed inset-0 z-overlay">
      <div
        className={cn('absolute inset-0 bg-scrim transition-opacity duration-base ease-ldst', entered ? 'opacity-100' : 'opacity-0')}
        aria-hidden="true"
        onClick={requestClose}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ldst-drawer-title"
        className={cn(
          'absolute inset-y-0 right-0 flex flex-col border-l border-border bg-surface shadow-overlay',
          'transition-all duration-base ease-ldst',
          entered ? 'translate-x-0' : 'translate-x-full',
          width === 'lg' ? 'w-drawer-wide' : 'w-drawer',
        )}
      >
        <div className="flex min-h-topbar items-start justify-between gap-3 border-b border-border px-4 py-2">
          <div className="min-w-0">
            <h2 id="ldst-drawer-title" className="truncate text-subtitle font-semibold text-text">
              {title}
            </h2>
            {subtitle && <p className="text-caption text-text-secondary">{subtitle}</p>}
          </div>
          <IconButton label="关闭" icon={<X className="size-4" aria-hidden="true" />} onClick={requestClose} />
        </div>
        <div className="min-h-0 flex-1 overflow-auto px-4 py-4">{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-2">{footer}</div>
        )}
      </div>
    </div>,
    document.body,
  )
}

export interface ConfirmDialogProps {
  open: boolean
  title: string
  description?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  tone?: 'primary' | 'danger'
  loading?: boolean
  onConfirm: () => void
  onCancel: () => void
  /** 强制阅读项：勾选后才能确认（如「本次不会真的安装」） */
  requireCheckbox?: { label: ReactNode }
  children?: ReactNode
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = '确认',
  cancelLabel = '取消',
  tone = 'primary',
  loading = false,
  onConfirm,
  onCancel,
  requireCheckbox,
  children,
}: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const [checked, setChecked] = useState(false)

  useEffect(() => {
    if (open) setChecked(false)
  }, [open])

  const cancel = useCallback(() => {
    if (loading) return
    onCancel()
  }, [loading, onCancel])

  useModalChrome(open, cancel, dialogRef)
  const entered = useEnter(open)

  if (!open) return null

  return createPortal(
    <div className="fixed inset-0 z-dialog flex items-center justify-center p-5">
      <div
        className={cn('absolute inset-0 bg-scrim transition-opacity duration-base ease-ldst', entered ? 'opacity-100' : 'opacity-0')}
        aria-hidden="true"
        onClick={cancel}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ldst-confirm-title"
        aria-describedby={description ? 'ldst-confirm-desc' : undefined}
        className={cn(
          'relative flex w-dialog max-w-full flex-col gap-3 rounded border border-border bg-surface p-4 shadow-dialog',
          'transition-all duration-base ease-ldst',
          entered ? 'scale-100 opacity-100' : 'scale-95 opacity-0',
        )}
      >
        <h2 id="ldst-confirm-title" className="text-subtitle font-semibold text-text">
          {title}
        </h2>
        {description && (
          <div id="ldst-confirm-desc" className="text-body leading-body text-text-secondary">
            {description}
          </div>
        )}
        {children}
        {requireCheckbox && (
          <div className="rounded border border-border bg-surface-2 p-2">
            <Checkbox checked={checked} onChange={setChecked}>
              {requireCheckbox.label}
            </Checkbox>
          </div>
        )}
        <div className="mt-1 flex justify-end gap-2">
          <Button variant="ghost" onClick={cancel} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button
            variant={tone === 'danger' ? 'danger' : 'primary'}
            onClick={onConfirm}
            loading={loading}
            disabled={requireCheckbox ? !checked : false}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
