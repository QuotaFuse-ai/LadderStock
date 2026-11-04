import type { ReactNode } from 'react'
import { WifiOff } from 'lucide-react'
import { useLocation } from 'react-router-dom'

import { cn } from '../lib/cn'
import { useDemoEnabled, useDemoUrlEnabled, type PageState } from '../lib/pageState'
import { NAV_SECTIONS } from './Layout'
import { Badge, Banner, NavPills, Skeleton } from './ui'

/**
 * 页面骨架件：标题栏 / 页头下方的横向胶囊页签 / 演示态标记 / 离线提示 / 整屏骨架。
 *
 * 五态演示统一走 `?state=`（§8），真实数据态与演示态共用同一套 DOM，
 * 保证截图和真实渲染不会两套结构。
 */

/** 同一路径挂了几颗（市场三段都是 `/market?tab=`），没有返回 undefined。 */
function sharedPath(pages: readonly { to: string }[]): string | undefined {
  const counts = new Map<string, number>()
  for (const page of pages) {
    const path = page.to.split('?')[0]
    counts.set(path, (counts.get(path) ?? 0) + 1)
  }
  return [...counts].find(([, count]) => count > 1)?.[0]
}

/**
 * 当前路由所属导航组 → 页头下方那一行横向胶囊（QUOT-390）。
 *
 * 数据源就是侧栏的 `NAV_SECTIONS`，不再另立一份页签表：二级页加进侧栏，胶囊条自动跟上，
 * 两边不会走样。**组里只有一页时整条不渲染**（首页 / 日志 / 个股深挖 / 设置），
 * 单颗页签既没有导航价值，又比没有更吵。
 */
export function SectionNavPills({ className }: { className?: string }) {
  const { pathname } = useLocation()
  const section = NAV_SECTIONS.find((entry) => entry.pages.some((page) => page.to.split('?')[0] === pathname))
  if (!section || section.pages.length < 2) return null
  return <NavPills items={section.pages} basePath={sharedPath(section.pages)} className={className} />
}

export interface PageHeaderProps {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
  badge?: ReactNode
  meta?: ReactNode
  className?: string
}

export function PageHeader({ title, subtitle, actions, badge, meta, className }: PageHeaderProps) {
  return (
    <>
      {/* `page-shell-header`（styles/index.css，QUOT-397）：整条页头滚动时钉在主内容区顶部；
          `-mx`/`px` 抵消 `.page-shell` 的横向留白，让底色与分隔线贴到主栏两边，标题仍与正文对齐。 */}
      <header
        className={cn(
          'page-shell-header flex min-h-topbar flex-wrap items-center justify-between gap-3',
          className,
        )}
      >
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="text-page font-semibold leading-tight text-text">{title}</h1>
          {badge}
          {/* 说明与状态行必然带计数 / 时间，挂 `.ldst-num` 与正文数字同一基线（§7.2、§11.0）。
              只加可继承的 `font-variant-numeric`，不动各屏自己的字号与颜色；
              h1 / badge / subtitle / meta 仍是同级兄弟节点，页头 DOM 契约不变。
              纵向 `gap-y-1`：换行时收进 4px，不再用 12px 把标题栏撑成两段。 */}
          {subtitle && <p className="ldst-num text-caption text-text-secondary">{subtitle}</p>}
          {meta && <div className="ldst-num">{meta}</div>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </header>
      {/* 页签条留在页头（sticky 区）之外，滚走的是它、钉住的是品牌 + 标题 + 操作区：
          滚动后钉子只占一行标题的高度，不把一整条胶囊页签常驻在视口里（QUOT-397）。 */}
      {/* 页签条挂在页头之后、正文第一张卡片之前：屏幕自己不写导航，位置由骨架件保证一致 */}
      <SectionNavPills />
    </>
  )
}

export const DEMO_STATE_LABEL: Record<Exclude<PageState, 'normal'>, string> = {
  empty: '空态',
  loading: '加载中',
  error: '错误',
  partial: '部分失败',
  offline: '离线',
}

/**
 * 演示水印的唯一真源（UI-SPEC §5.6 / 整改项 R-18）。
 *
 * 两件事分别标识，互不替代：
 * - 演示源 → 演示徽标（§5.6 硬约束，`tone="warn"`）：URL 开启写「演示数据 · ?demo=1」，
 *   设置页开关开启写「演示数据 · 设置开关」；
 * - `?state=` → 强制态水印 `演示态：…（?state=…）`。
 *
 * `state` 为 null 且无 `?demo=1` 时才不渲染——旧实现只认 `?state=`，
 * 于是 `?demo=1` 的假数据整屏零标识（R-18 的 P0 根因）。
 * 各屏调用点保持 `<DemoStateBadge state={forced} />` 不改签名：演示开关由组件自读。
 */
export function DemoStateBadge({ state }: { state: PageState | null }) {
  const demo = useDemoEnabled()
  const urlDemo = useDemoUrlEnabled()
  const forced = state && state !== 'normal' ? state : null
  if (!demo && !forced) return null
  return (
    <>
      {demo && (
        <Badge tone="warn" title="本页数据来自 demo.ts 演示源，不是真实行情">
          {urlDemo ? '演示数据 · ?demo=1' : '演示数据 · 设置开关'}
        </Badge>
      )}
      {forced && (
        <span className="inline-flex h-5 items-center rounded-xs border border-border-strong px-2 text-caption text-text-secondary">
          演示态：{DEMO_STATE_LABEL[forced]}（?state={forced}）
        </span>
      )}
    </>
  )
}

export function OfflineBanner({ onRetry }: { onRetry?: () => void }) {
  return (
    <Banner
      tone="warn"
      title="本机服务未连接"
      action={
        onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="text-caption font-medium text-text underline underline-offset-2"
          >
            重试
          </button>
        ) : undefined
      }
    >
      <p className="max-w-measure">
        先运行 <code className="ldst-num">ldst run</code> 启动本机只读 API（默认
        127.0.0.1:5950）。本机草稿仍可编辑，恢复自 IndexedDB 的内容不会丢。
      </p>
    </Banner>
  )
}

/** 整屏骨架：标题 + 指标卡 + 表格，保持和图谱一致的节奏（C-18）。 */
export function ScreenSkeleton({ metrics = 5, rows = 6 }: { metrics?: number; rows?: number }) {
  return (
    <div className="flex flex-col gap-5" aria-busy="true" aria-live="polite">
      <span className="sr-only">正在加载…</span>
      <div className="grid grid-cols-2 gap-4 desk:grid-cols-5">
        {Array.from({ length: metrics }).map((_, index) => (
          <div key={index} className="flex h-metric flex-col justify-between rounded border border-border bg-surface p-4">
            <Skeleton className="w-1/2" />
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="w-1/3" />
          </div>
        ))}
      </div>
      <div className="rounded border border-border bg-surface">
        {Array.from({ length: rows + 1 }).map((_, index) => (
          <div key={index} className="flex h-row items-center gap-4 border-b border-border px-3 last:border-b-0">
            <Skeleton className="w-16" />
            <Skeleton className="w-24" />
            <Skeleton className="w-12" />
            <Skeleton className="w-20" />
            <Skeleton className="w-10" />
          </div>
        ))}
      </div>
    </div>
  )
}

export function OfflineIcon() {
  return <WifiOff className="size-4" aria-hidden="true" />
}
