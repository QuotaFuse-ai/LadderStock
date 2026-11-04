import type { LucideIcon } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'

import { cn } from '../../lib/cn'

/**
 * C-21 NavPills（QUOT-390）：内容区标题下方那一行**横向**胶囊页签。
 *
 * 与同族组件的分工，三者形状不同、不可互相冒充：
 * - `Segmented`：连体分段控件（`radiogroup`），切互斥模式，如主题切换；
 * - `Tabs`：下划线页签（`tablist`），切同一实体的不同视角，如设置四栏；
 * - `NavPills`：一颗一颗**独立**胶囊（`rounded-pill` + 各自边框 + 8px 间距），语义是
 *   **页面之间的跳转**，所以是 `<nav>` + `Link` + `aria-current="page"`，不是 `role="tab"`。
 *
 * 用 `Link` 而不是 `NavLink`：`NavLink` 的 `aria-current` 只看 pathname，
 * `/market?tab=sentiment|ladder|theme` 三条同路径入口会同时被标成当前页（与 `Layout.tsx` 同一口径）。
 * 归组规则写在 `activeFor` 里：URL 没带 `?tab=` 时，高亮该路径下的第一颗。
 */

export interface NavPillItem {
  to: string
  label: string
  icon: LucideIcon
}

export interface NavPillsProps {
  items: readonly NavPillItem[]
  /** `?tab=` 归组键（如 `/market`）：当前路径落在它上面且 URL 未带 `?tab=` 时，高亮该组第一颗 */
  basePath?: string
  /** `md` = 页签条（32px）；`sm` = 同一原语的紧凑档（24px，如版本切换） */
  size?: 'md' | 'sm'
  ariaLabel?: string
  className?: string
}

/** `/market?tab=ladder` → `{ path: '/market', tab: 'ladder' }`；无查询串时 `tab` 为 null。 */
function splitTarget(to: string): { path: string; tab: string | null } {
  const cut = to.indexOf('?')
  if (cut < 0) return { path: to, tab: null }
  return { path: to.slice(0, cut), tab: new URLSearchParams(to.slice(cut + 1)).get('tab') }
}

/** 同一 path 下第一个带 `tab` 的条目 = 该 path 的缺省页（`/market` 不带参数时就是情绪温度）。 */
function defaultTabByPath(items: readonly NavPillItem[]): Map<string, string> {
  const defaults = new Map<string, string>()
  for (const item of items) {
    const { path, tab } = splitTarget(item.to)
    if (tab && !defaults.has(path)) defaults.set(path, tab)
  }
  return defaults
}

export function NavPills({
  items,
  basePath,
  size = 'md',
  ariaLabel = '页内导航',
  className,
}: NavPillsProps) {
  const location = useLocation()
  const defaults = defaultTabByPath(items)
  const urlTab = new URLSearchParams(location.search).get('tab')

  const activeFor = (item: NavPillItem): boolean => {
    const { path, tab } = splitTarget(item.to)
    if (location.pathname !== path) return false
    if (!tab) return true
    return (urlTab ?? defaults.get(basePath ?? path)) === tab
  }

  return (
    // 只有一行：窄屏不折行，横向滚动且不显示滚动条（`.ldst-no-scrollbar` 在 styles/index.css）。
    <nav
      aria-label={ariaLabel}
      className={cn('ldst-no-scrollbar flex min-w-0 items-center gap-2 overflow-x-auto', className)}
    >
      {items.map((item) => {
        const active = activeFor(item)
        return (
          <Link
            key={item.to}
            to={item.to}
            title={item.label}
            aria-current={active ? 'page' : undefined}
            className={cn(
              'inline-flex shrink-0 items-center rounded-pill border transition-colors duration-fast ease-ldst',
              // 字号挂在尺寸档上而不是基础类：Tailwind 按 token 顺序生成 CSS，基础类里的
              // `text-body` 会盖掉 sm 档的 `text-caption`，紧凑档就白设了。
              size === 'sm' ? 'h-6 gap-1 px-2 text-caption' : 'h-control gap-2 px-3 text-body',
              active
                ? 'border-accent bg-accent-soft font-medium text-accent'
                : 'border-border-strong text-text-secondary hover:border-accent hover:text-text',
            )}
          >
            <item.icon
              className={size === 'sm' ? 'size-3.5 shrink-0' : 'size-4 shrink-0'}
              strokeWidth={1.75}
              aria-hidden="true"
            />
            <span className="whitespace-nowrap">{item.label}</span>
          </Link>
        )
      })}
    </nav>
  )
}
