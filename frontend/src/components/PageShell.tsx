import { useContext, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Loader2, RefreshCw, WifiOff } from 'lucide-react'

import { cn } from '../lib/cn'
import { PageToolsSlotContext } from '../lib/pageToolsSlot'
import { useDemoEnabled, useDemoUrlEnabled, type PageState } from '../lib/pageState'
import { PageSubNav } from './PageSubNav'
import { Badge, Banner, IconButton, Skeleton } from './ui'

/**
 * 页面骨架件：内容区状态行 + 演示态标记 / 离线提示 / 整屏骨架。
 *
 * 五态演示统一走 `?state=`（§8），真实数据态与演示态共用同一套 DOM，
 * 保证截图和真实渲染不会两套结构。
 *
 * **顶栏收敛（QUOT-457 / DEC-063）**：页面不再渲染自己的标题条。
 * 标题只留一颗对读屏可见的 `h1.sr-only`（页面必须有 h1，顶栏的 `<strong>` 是导航不是标题），
 * 主操作「刷新」经 `PageToolsSlotContext` 送进顶栏工具区；没有外壳时（组件单测 / Storybook）
 * 退回内容区就地渲染，屏幕本身不区分环境。
 *
 * **栏目内子导航搬到顶栏（2026-11-16）**：内容区不再有第二排同组胶囊
 * （原 `SectionNavPills`），改由 `Layout` 顶栏的 `PageSubNav` 渲染。
 */

/**
 * 当前路由所属导航组 → 一组横向胶囊（QUOT-390）。
 *
 * ⚠️ **已迁出内容区（2026-11-16）**：`PageHeader` 不再调用它，胶囊改由顶栏的
 * `PageSubNav` 渲染（与面包屑同一行）。函数保留给 `tests/nav-pills.test.tsx`
 * 的归组口径单测（选中态、同路径多入口），实现直接转调 `PageSubNav`，
 * 两边只有一处真源。
 */
export function SectionNavPills({ className }: { className?: string }) {
  return <PageSubNav className={className} />
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
      {/* QUOT-397 返工：页头下方的横向胶囊页签已撤掉。
          它与侧栏二级导航是同一份 `NAV_SECTIONS` 的两处渲染：组内 2 页时页面出现
          两条同组入口、组内 1 页时一条都没有，全站看起来「有的页面两个导航、有的一个」。
          导航只留侧栏一处，页头不再重复。 */}
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
