import { NavLink, Outlet } from 'react-router-dom'
import {
  ClipboardCheck,
  Home,
  NotebookPen,
  Radar,
  ScrollText,
  Settings,
  TrendingUp,
  type LucideIcon,
} from 'lucide-react'

import PersistenceBanner from './PersistenceBanner'
import { Segmented } from './ui'
import { cn } from '../lib/cn'
import { setTheme, useThemeChoice } from '../lib/prefs'
import type { ThemeChoice } from '../lib/theme'

/**
 * 应用外壳（DESIGN-SPEC §3.1 / §4）。
 *
 * - 1280 起：232px 完整侧栏（图标 + 文字），内容区最大 1296。
 * - 1024–1279：收窄为 56px 图标栏，文字靠 `title` 与读屏标签保留。
 * - 「设置」固定在侧栏底部，用更弱的次级文字色，与 5 个业务页区分。
 * - 主题切换有两个入口（此处 + 设置页），共享 `lib/prefs` 的同一状态。
 */

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
}

const PRIMARY_NAV: NavItem[] = [
  { to: '/', label: '首页', icon: Home },
  { to: '/watch', label: '盯盘', icon: Radar },
  { to: '/review', label: '复盘', icon: NotebookPen },
  { to: '/journal', label: '日志', icon: ScrollText },
  { to: '/validate', label: '验证', icon: ClipboardCheck },
]

const THEME_OPTIONS: ReadonlyArray<{ value: ThemeChoice; label: string }> = [
  { value: 'system', label: '系统' },
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
]

function navClass(isActive: boolean, secondary = false): string {
  return cn(
    'group flex h-row items-center gap-2 rounded px-2 text-body transition-colors duration-fast rail:justify-center rail:px-0',
    isActive
      ? 'bg-surface-3 font-medium text-text'
      : secondary
        ? 'text-text-secondary hover:bg-surface-3 hover:text-text'
        : 'text-text-secondary hover:bg-surface-3 hover:text-text',
  )
}

export default function Layout() {
  const theme = useThemeChoice()

  return (
    <div className="flex min-h-screen bg-bg text-text">
      <a
        href="#main"
        className="sr-only z-toast rounded border border-border-strong bg-surface px-3 py-2 text-body focus:not-sr-only focus:fixed focus:left-2 focus:top-2"
      >
        跳到主内容
      </a>

      <aside className="sticky top-0 flex h-screen w-sidebar shrink-0 flex-col border-r border-border bg-surface rail:w-sidebar-rail">
        <div className="flex h-topbar items-center gap-2 border-b border-border px-3 rail:justify-center rail:px-0">
          <span
            aria-hidden="true"
            className="flex size-7 shrink-0 items-center justify-center rounded bg-accent-soft text-accent"
          >
            <TrendingUp className="size-4" strokeWidth={1.75} />
          </span>
          <div className="min-w-0 rail:hidden">
            <p className="truncate text-body font-semibold text-text">天梯股复盘台</p>
            <p className="truncate text-caption text-text-muted">LadderStock · 本地看板</p>
          </div>
        </div>

        <nav aria-label="主导航" className="min-h-0 flex-1 overflow-y-auto p-2">
          <ul className="flex flex-col gap-1">
            {PRIMARY_NAV.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.to === '/'}
                  title={item.label}
                  className={({ isActive }) => navClass(isActive)}
                >
                  {({ isActive }) => (
                    <>
                      <item.icon
                        className={cn('size-4 shrink-0', isActive ? 'text-accent' : 'text-text-muted')}
                        strokeWidth={1.75}
                        aria-hidden="true"
                      />
                      <span className="truncate rail:hidden">{item.label}</span>
                    </>
                  )}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        <div className="mt-auto flex flex-col gap-2 border-t border-border p-2">
          <div className="rail:hidden">
            <Segmented
              ariaLabel="主题"
              size="sm"
              options={THEME_OPTIONS}
              value={theme}
              onChange={setTheme}
              className="w-full justify-between"
            />
          </div>
          <NavLink to="/settings" title="设置" className={({ isActive }) => navClass(isActive, true)}>
            <Settings className="size-4 shrink-0 text-text-muted" strokeWidth={1.75} aria-hidden="true" />
            <span className="truncate rail:hidden">设置</span>
          </NavLink>
          <p className="px-2 text-caption leading-body text-text-muted rail:hidden">
            仅监听本机回环 · 草稿只存本机 IndexedDB
          </p>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <PersistenceBanner />
        <main id="main" tabIndex={-1} className="mx-auto w-full max-w-table flex-1 px-5 py-5 focus:outline-none">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
