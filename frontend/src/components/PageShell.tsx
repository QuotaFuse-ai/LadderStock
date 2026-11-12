import { useContext, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Loader2, RefreshCw, WifiOff } from 'lucide-react'
import { useLocation } from 'react-router-dom'

import { cn } from '../lib/cn'
import { PageToolsSlotContext } from '../lib/pageToolsSlot'
import { useDemoEnabled, useDemoUrlEnabled, type PageState } from '../lib/pageState'
import { NAV_SECTIONS } from './Layout'
import { Badge, Banner, IconButton, NavPills, Skeleton } from './ui'

/**
 * 页面骨架件：内容区状态行 + 横向胶囊页签 + 演示态标记 / 离线提示 / 整屏骨架。
 *
 * 五态演示统一走 `?state=`（§8），真实数据态与演示态共用同一套 DOM，
 * 保证截图和真实渲染不会两套结构。
 *
 * **顶栏收敛（QUOT-457 / DEC-063）**：页面不再渲染自己的标题条。
 * 标题只留一颗对读屏可见的 `h1.sr-only`（页面必须有 h1，顶栏的 `<strong>` 是导航不是标题），
 * 主操作「刷新」经 `PageToolsSlotContext` 送进顶栏工具区；没有外壳时（组件单测 / Storybook）
 * 退回内容区就地渲染，屏幕本身不区分环境。
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

/** 页面主操作「刷新」：各屏只给行为与状态，按钮形状由骨架件统一。 */
export interface PageRefresh {
  onClick: () => void
  /** 无障碍名与 tooltip；默认「刷新」 */
  label?: string
  loading?: boolean
  disabled?: boolean
}

function RefreshControl({ onClick, label = '刷新', loading = false, disabled = false }: PageRefresh) {
  return (
    <IconButton
      label={label}
      variant="secondary"
      onClick={onClick}
      disabled={disabled || loading}
      icon={
        loading ? (
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
        ) : (
          <RefreshCw className="size-4" aria-hidden="true" />
        )
      }
    />
  )
}

export interface PageHeaderProps {
  /** 页面标题。不占版面：渲染成 `sr-only` 的 `h1`，可见标题由顶栏面包屑承担 */
  title: string
  subtitle?: ReactNode
  /** 刷新之外的次级操作，留在内容区（新增持仓、月份切换、导出等） */
  actions?: ReactNode
  badge?: ReactNode
  meta?: ReactNode
  refresh?: PageRefresh
  className?: string
}

export function PageHeader({ title, subtitle, actions, badge, meta, refresh, className }: PageHeaderProps) {
  const toolsSlot = useContext(PageToolsSlotContext)
  const refreshControl = refresh ? <RefreshControl {...refresh} /> : null

  return (
    <>
      {/* 一行状态 + 次级操作，没有底色、分隔线与 sticky：这是内容，不是第二条横栏。
          `h1` 与副标题仍是同一父节点下的兄弟（`tests/quota-331-envelope.test.tsx` 的
          `subtitleText()` 依赖这个结构），只是标题不再占版面。 */}
      <div className={cn('flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2', className)}>
        <h1 className="sr-only">{title}</h1>
        {badge}
        {subtitle && <p className="ldst-num min-w-0 text-caption text-text-secondary">{subtitle}</p>}
        {meta && <div className="ldst-num">{meta}</div>}
        {(refreshControl || actions) && (
          <div className="ml-auto flex shrink-0 flex-wrap items-center gap-2">
            {/* 有外壳时刷新在顶栏，这里补位只会出现第二颗；无外壳时才就地渲染 */}
            {!toolsSlot && refreshControl}
            {actions}
          </div>
        )}
      </div>
      {toolsSlot && refreshControl && createPortal(refreshControl, toolsSlot)}
      {/* 页签留在内容区第一行：屏幕自己不写导航，位置由骨架件保证一致 */}
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
 * 演示水印的唯一真源（UI-SPEC §5.6 / 整改项 R-18；QUOT-559 去技术参数串）。
 *
 * 两件事分别标识，互不替代：
 * - 演示源 → 演示徽标（§5.6 硬约束，`tone="warn"`）：文案统一「演示数据」，
 *   不再印 `?demo=1` / 「设置开关」等技术参数串；来源细节放 hover title。
 * - `?state=` → 强制态水印 `演示态：…`：这是**开发态视觉回归工具**，
 *   仅 `import.meta.env.DEV` 渲染，生产构建不出现（QUOT-559 隐藏技术开关）。
 *
 * `state` 为 null 且未开演示时才不渲染——旧实现只认 `?state=`，
 * 于是演示假数据整屏零标识（R-18 的 P0 根因）。
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
        <Badge
          tone="warn"
          title={
            urlDemo
              ? '本页数据来自演示样例（网址开关打开时），不是真实行情'
              : '本页数据来自演示样例（设置页「虚拟数据」开关），不是真实行情'
          }
        >
          演示数据
        </Badge>
      )}
      {forced && import.meta.env.DEV && (
        <span
          className="inline-flex h-5 items-center rounded-xs border border-border-strong px-2 text-caption text-text-secondary"
          data-testid="forced-state-watermark"
        >
          演示态：{DEMO_STATE_LABEL[forced]}
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
        看板的本机服务还没有就绪：看一眼顶栏状态灯，点「重启平台」通常就能恢复；
        恢复前本机草稿仍可编辑，内容不会丢。
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
