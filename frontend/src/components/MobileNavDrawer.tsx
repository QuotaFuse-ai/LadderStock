import { useEffect, useRef, type RefObject } from 'react'
import { Link, useLocation, type Location } from 'react-router-dom'
import { X } from 'lucide-react'

import { cn } from '../lib/cn'
import { BRAND_ICON, NAV_SECTIONS, PINNED_NAV, type NavPage } from '../lib/nav'

/**
 * 移动端导航抽屉（DEC-062 §2，`<768px`）。
 *
 * 参考实现 `components/layout/Layout.tsx:291-295` 的汉堡 + 左侧抽屉；内容不另立一份，
 * 直接渲染 `NAV_SECTIONS` + `PINNED_NAV`，与侧栏同源（`lib/nav.ts`）。
 *
 * 无障碍口径：
 * - 遮罩点击关闭；
 * - `Escape` 关闭并把焦点还给汉堡按钮；
 * - 汉堡按钮持有 `aria-expanded` / `aria-controls="mobile-nav-drawer"`（在 `Layout.tsx`）；
 * - 打开时锁 `body` 滚动，卸载/关闭还原；
 * - 路由变化由 `Layout.tsx` 统一关闭，点抽屉里的链接不会留下半开状态。
 */

export interface MobileNavDrawerProps {
  open: boolean
  onClose: () => void
  /** 关闭后焦点回落的元素（汉堡按钮） */
  returnFocusTo: RefObject<HTMLButtonElement | null>
}

function drawerNavClass(isActive: boolean): string {
  return cn(
    'flex h-row items-center gap-2 rounded-pill px-3 text-body transition-colors duration-fast',
    isActive
      ? 'bg-accent-soft font-medium text-accent'
      : 'text-text-secondary hover:bg-surface-3 hover:text-text',
  )
}

function pageActive(page: NavPage, location: Location): boolean {
  const [path] = page.to.split('?')
  if (location.pathname !== path) return false
  if (!page.tab) return true
  return (new URLSearchParams(location.search).get('tab') ?? 'sentiment') === page.tab
}

export function MobileNavDrawer({ open, onClose, returnFocusTo }: MobileNavDrawerProps) {
  const location = useLocation()
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    closeRef.current?.focus()

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      onClose()
      returnFocusTo.current?.focus()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, onClose, returnFocusTo])

  if (!open) return null

  const Brand = BRAND_ICON

  return (
    <div className="fixed inset-0 z-dialog">
      <div className="absolute inset-0 bg-scrim" aria-hidden="true" onClick={onClose} />

      <div
        id="mobile-nav-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="导航菜单"
        className="absolute inset-y-0 left-0 flex w-sidebar flex-col border-r border-border bg-surface shadow-overlay"
      >
        <div className="flex h-topbar shrink-0 items-center gap-2 border-b border-border px-3">
          <span
            aria-hidden="true"
            className="flex size-7 shrink-0 items-center justify-center rounded bg-accent-soft text-accent"
          >
            <Brand className="size-4" strokeWidth={1.75} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-body font-semibold text-text">天梯股复盘台</p>
            <p className="truncate text-caption text-text-muted">LadderStock · 本地看板</p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="关闭导航"
            title="关闭导航"
            className="rounded p-1.5 text-text-muted transition-colors duration-fast hover:bg-surface-3 hover:text-text"
          >
            <X className="size-4" strokeWidth={1.75} aria-hidden="true" />
          </button>
        </div>

        <nav aria-label="移动端导航" className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-2">
          <ul className="flex flex-col gap-1">
            {NAV_SECTIONS.map((section) => (
              <li key={section.key}>
                {section.pages.length > 1 && (
                  <p className="px-3 pb-1 pt-2 text-caption text-text-muted">{section.label}</p>
                )}
                <ul className="flex flex-col gap-1">
                  {section.pages.map((page) => {
                    const active = pageActive(page, location)
                    return (
                      <li key={page.to}>
                        <Link
                          to={page.to}
                          onClick={onClose}
                          aria-current={active ? 'page' : undefined}
                          className={drawerNavClass(active)}
                        >
                          <page.icon
                            className={cn('size-4 shrink-0', active ? 'text-accent' : 'text-text-muted')}
                            strokeWidth={1.75}
                            aria-hidden="true"
                          />
                          <span className="truncate">
                            {section.pages.length > 1 ? page.label : section.label}
                          </span>
                        </Link>
                      </li>
                    )
                  })}
                </ul>
              </li>
            ))}
          </ul>
        </nav>

        <div className="mt-auto flex flex-col gap-1 border-t border-border p-2">
          {PINNED_NAV.map((page) => (
            <Link
              key={page.to}
              to={page.to}
              onClick={onClose}
              aria-current={location.pathname === page.to ? 'page' : undefined}
              className={drawerNavClass(location.pathname === page.to)}
            >
              <page.icon className="size-4 shrink-0 text-text-muted" strokeWidth={1.75} aria-hidden="true" />
              <span className="truncate">{page.label}</span>
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}

export default MobileNavDrawer
