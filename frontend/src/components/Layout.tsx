import { useState } from 'react'
import { Link, NavLink, Outlet, useLocation, type Location } from 'react-router-dom'
import {
  BarChart3,
  Building2,
  CalendarDays,
  CalendarRange,
  CandlestickChart,
  ChevronDown,
  ClipboardCheck,
  Flame,
  Gauge,
  Home,
  Layers,
  ListChecks,
  Microscope,
  Newspaper,
  NotebookPen,
  Radio,
  Radar,
  ScrollText,
  Settings,
  Timer,
  TrendingUp,
  Wallet,
  type LucideIcon,
} from 'lucide-react'

import PersistenceBanner from './PersistenceBanner'
import { Segmented } from './ui'
import { cn } from '../lib/cn'
import { setTheme, useThemeChoice } from '../lib/prefs'
import type { ThemeChoice } from '../lib/theme'

/**
 * 应用外壳（DESIGN-SPEC §3.1 / §4.1）。
 *
 * 一级导航 8 组 + 底部固定的「设置」：
 * 首页 → 市场 → 盯盘 → 复盘 → 日志 → 验证 → 产业研究 → 个股深挖 ─┬─ 设置（弱化、沉底）。
 * 前 6 组顺序是既有的，QUOT-300 只往后追加，不打乱 `docs/UI-SPEC.md` 的四屏主顺序。
 *
 * **二级导航只在「真的有两页以上」时展开**（QUOT-198 就绪表）：
 * - 市场 5 段（情绪温度 / 连板梯队 / 题材轮动 / 资讯雷达 / 事件日历）都已实现 → 展开为二级；
 * - 复盘 2 段（情绪复盘 / 候选池）、日志 2 段（交易日志 / 我的持仓）同样展开；
 * - 盯盘 2 段（盘中信号 / 竞价与盘中）都已实现 → 展开为二级；
 *   「竞价与盘中」由 QUOT-302 补上，前置是 QUOT-286（6 时点快照落盘）+ QUOT-301（读接口）。
 * 判定是数据驱动的（`pages.length > 1`），后端口径补上后只加数据、不改组件。
 *
 * - 1280 起：232px 完整侧栏（图标 + 文字 + 二级缩进）。
 * - 1024–1279：收窄为 56px 图标栏，二级展平为图标行（不做嵌套，避免挤压）。
 * - 展开状态写 localStorage（`ldst.nav.expanded`），刷新后原样恢复。
 */

export interface NavPage {
  to: string
  label: string
  icon: LucideIcon
  /** 同路由多入口时用于判高亮（`/market?tab=…`） */
  tab?: string
}

export interface NavSection {
  key: string
  label: string
  icon: LucideIcon
  pages: NavPage[]
}

/**
 * 导出给导航一致性测试（`tests/nav-routes.test.ts`，QUOT-300）：
 * 每条 `to` 必须在 `App.tsx` 的 `APP_ROUTES` 里命中，反之每条已交付路由也要有入口。
 * 「做了页面忘接导航」已经发生过一次（产业研究 / 个股深挖四页），断言比人肉检查可靠。
 */
export const NAV_SECTIONS: NavSection[] = [
  {
    key: 'home',
    label: '首页',
    icon: Home,
    pages: [{ to: '/', label: '首页', icon: Home }],
  },
  {
    key: 'market',
    label: '市场',
    icon: CandlestickChart,
    pages: [
      { to: '/market?tab=sentiment', label: '情绪温度', icon: Gauge, tab: 'sentiment' },
      { to: '/market?tab=ladder', label: '连板梯队', icon: BarChart3, tab: 'ladder' },
      { to: '/market?tab=theme', label: '题材轮动', icon: Layers, tab: 'theme' },
      { to: '/news', label: '资讯雷达', icon: Newspaper },
      { to: '/calendar', label: '事件日历', icon: CalendarDays },
    ],
  },
  {
    key: 'watch',
    label: '盯盘',
    icon: Radar,
    // 实时动态（阈值信号）+ 竞价与盘中（当日 6 时点快照，QUOT-302 / DEC-035 §5）
    pages: [
      { to: '/watch', label: '盘中信号', icon: Radar },
      { to: '/intraday', label: '竞价与盘中', icon: Timer },
    ],
  },
  {
    key: 'review',
    label: '复盘',
    icon: NotebookPen,
    // 「每日复盘」是 P1 留位（🟡），本轮不建入口；候选池是复盘的产出
    pages: [
      { to: '/review', label: '情绪复盘', icon: NotebookPen },
      // R11 周度复盘（QUOT-149）：事实包聚合 + 五段 AI 叙述，`?week=YYYY-Www`
      { to: '/weekly', label: '周度复盘', icon: CalendarRange },
      { to: '/pool', label: '候选池', icon: ListChecks },
    ],
  },
  {
    key: 'journal',
    label: '日志',
    icon: ScrollText,
    // 交易日志与我的持仓同属本机私有数据
    pages: [
      { to: '/journal', label: '交易日志', icon: ScrollText },
      { to: '/positions', label: '我的持仓', icon: Wallet },
    ],
  },
  {
    key: 'validate',
    label: '验证',
    icon: ClipboardCheck,
    pages: [
      { to: '/validate', label: '验证', icon: ClipboardCheck },
      { to: '/limit-up', label: '涨停样本', icon: Flame },
    ],
  },
  // QUOT-300：R9 产业研究两页已交付却进不去，补一级入口（新组排在验证之后、设置之前）
  {
    key: 'industry',
    label: '产业研究',
    icon: Building2,
    pages: [
      { to: '/industry', label: '产业研究', icon: Building2 },
      { to: '/signals', label: '产业信号', icon: Radio },
    ],
  },
  {
    key: 'deep-dive',
    label: '个股深挖',
    icon: Microscope,
    // 落地页收 6 位代码后才跳 `/research/:code`：侧栏本身没有代码可带
    pages: [{ to: '/research', label: '个股深挖', icon: Microscope }],
  },
]

/** 侧栏底部固定项：不进 `NAV_SECTIONS`（不参与展开分组），但仍是一等入口，一致性测试要覆盖。 */
export const PINNED_NAV: NavPage[] = [{ to: '/settings', label: '设置', icon: Settings }]

const THEME_OPTIONS: ReadonlyArray<{ value: ThemeChoice; label: string }> = [
  { value: 'system', label: '系统' },
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
]

const EXPANDED_KEY = 'ldst.nav.expanded'
const GROUP_KEYS = NAV_SECTIONS.filter((section) => section.pages.length > 1).map((section) => section.key)

function readExpanded(): string[] {
  try {
    const raw = window.localStorage.getItem(EXPANDED_KEY)
    if (!raw) return GROUP_KEYS
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return GROUP_KEYS
    return parsed.filter((value): value is string => typeof value === 'string')
  } catch {
    return GROUP_KEYS
  }
}

function writeExpanded(keys: string[]): void {
  try {
    window.localStorage.setItem(EXPANDED_KEY, JSON.stringify(keys))
  } catch {
    // 隐私模式写入失败：只影响「下次是否记得展开」，不影响导航本身
  }
}

/** 同路径多入口（`/market?tab=`）时，高亮要跟着 `?tab=` 走，而不是整组都亮。 */
function pageActive(page: NavPage, location: Location): boolean {
  const [path] = page.to.split('?')
  if (location.pathname !== path) return false
  if (!page.tab) return true
  const current = new URLSearchParams(location.search).get('tab') ?? 'sentiment'
  return current === page.tab
}

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
  const location = useLocation()
  const [expanded, setExpanded] = useState<string[]>(readExpanded)

  const toggleGroup = (key: string) => {
    setExpanded((current) => {
      const next = current.includes(key) ? current.filter((item) => item !== key) : [...current, key]
      writeExpanded(next)
      return next
    })
  }

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
            {NAV_SECTIONS.map((section) => {
              const isGroup = section.pages.length > 1
              const isOpen = expanded.includes(section.key)
              const childActive = section.pages.some((page) => pageActive(page, location))

              if (!isGroup) {
                const [page] = section.pages
                return (
                  <li key={section.key}>
                    <NavLink
                      to={page.to}
                      end={page.to === '/'}
                      title={page.label}
                      className={({ isActive }) => navClass(isActive)}
                    >
                      {({ isActive }) => (
                        <>
                          <page.icon
                            className={cn('size-4 shrink-0', isActive ? 'text-accent' : 'text-text-muted')}
                            strokeWidth={1.75}
                            aria-hidden="true"
                          />
                          <span className="truncate rail:hidden">{section.label}</span>
                        </>
                      )}
                    </NavLink>
                  </li>
                )
              }

              return (
                <li key={section.key}>
                  <button
                    type="button"
                    aria-expanded={isOpen}
                    aria-controls={`nav-group-${section.key}`}
                    title={isOpen ? `收起${section.label}` : `展开${section.label}`}
                    onClick={() => toggleGroup(section.key)}
                    className={cn(
                      'flex h-row w-full items-center gap-2 rounded px-2 text-body transition-colors duration-fast hover:bg-surface-3 hover:text-text rail:hidden',
                      childActive ? 'font-medium text-text' : 'text-text-secondary',
                    )}
                  >
                    <section.icon
                      className={cn('size-4 shrink-0', childActive ? 'text-accent' : 'text-text-muted')}
                      strokeWidth={1.75}
                      aria-hidden="true"
                    />
                    <span className="min-w-0 flex-1 truncate text-left">{section.label}</span>
                    <ChevronDown
                      className={cn(
                        'size-3.5 shrink-0 text-text-muted transition-transform duration-fast',
                        !isOpen && '-rotate-90',
                      )}
                      aria-hidden="true"
                    />
                  </button>

                  {/* 窄侧栏（rail）下二级始终展平显示：一级标题隐藏，子项变成一排图标 */}
                  <ul
                    id={`nav-group-${section.key}`}
                    className={cn(
                      'mt-1 flex flex-col gap-1 border-l border-border pl-2 rail:mt-0 rail:border-l-0 rail:pl-0',
                      !isOpen && 'hidden rail:flex',
                    )}
                  >
                    {section.pages.map((page) => {
                      const active = pageActive(page, location)
                      return (
                        <li key={page.to}>
                          {/* 用 Link 而不是 NavLink：NavLink 的 aria-current 只看 pathname，
                              三段同路径会同时被标成「当前页」；这里以 `?tab=` 为准。 */}
                          <Link
                            to={page.to}
                            title={page.label}
                            aria-current={active ? 'page' : undefined}
                            className={navClass(active, true)}
                          >
                            <page.icon
                              className={cn('size-4 shrink-0', active ? 'text-accent' : 'text-text-muted')}
                              strokeWidth={1.75}
                              aria-hidden="true"
                            />
                            <span className="truncate rail:hidden">{page.label}</span>
                          </Link>
                        </li>
                      )
                    })}
                  </ul>
                </li>
              )
            })}
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
          {PINNED_NAV.map((page) => (
            <NavLink key={page.to} to={page.to} title={page.label} className={({ isActive }) => navClass(isActive, true)}>
              <page.icon className="size-4 shrink-0 text-text-muted" strokeWidth={1.75} aria-hidden="true" />
              <span className="truncate rail:hidden">{page.label}</span>
            </NavLink>
          ))}
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
