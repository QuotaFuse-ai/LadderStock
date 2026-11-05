import {
  BarChart3,
  Bot,
  Building2,
  CalendarDays,
  CalendarRange,
  CandlestickChart,
  ClipboardCheck,
  Eye,
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

/**
 * 侧栏导航的数据源（DEC-044 / QUOT-304）与顶栏面包屑（DEC-062）。
 *
 * 一级导航 9 组 + 底部固定的「设置」：
 * 首页 → 市场 → 盯盘 → 复盘 → 日志 → 我的股票 → 验证 → 产业研究 → 个股深挖 ─┬─ 设置（弱化、沉底）。
 *
 * 这里只放纯数据与纯函数，不含 React：`components/Layout.tsx` 负责渲染，
 * `lib/nav.ts` 的 `navTitleFor()` 给顶栏取一级栏目名。放在独立模块而不是
 * Layout 内部，是为了让纯函数单测不拖进整棵组件树（`tests/topbar.test.tsx`）。
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
 * 导出给导航一致性测试（`tests/nav-routes.test.tsx`，QUOT-300）：
 * 每条 `to` 必须在 `App.tsx` 的 `APP_ROUTES` 里命中，反之每条已交付路由也要有入口。
 * 「做了页面忘接导航」已经发生过一次（产业研究 / 个股深挖四页），断言比人肉检查可靠。
 *
 * ⚠️ 导出名、分组顺序都与 `tests/nav-routes.test.tsx` 绑定，改结构先改测试口径。
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
    // QUOT-344 / DEC-046：复盘拆成三个独立入口，各占一条路由，不再合成一屏
    pages: [
      { to: '/review', label: '每日复盘', icon: NotebookPen },
      { to: '/review/ai', label: 'AI复盘', icon: Bot },
      // R11（QUOT-149）：事实包聚合 + 五段 AI 叙述，`?week=YYYY-Www`
      { to: '/weekly', label: 'AI周报', icon: CalendarRange },
      { to: '/pool', label: '候选池', icon: ListChecks },
    ],
  },
  {
    key: 'journal',
    label: '日志',
    icon: ScrollText,
    // 交易日志留在「日志」；我的持仓按 QUOT-304 移入「我的股票」
    pages: [{ to: '/journal', label: '交易日志', icon: ScrollText }],
  },
  // QUOT-304 / DEC-044：把「我自己相关」的持仓与事件日历聚成一组。
  // QUOT-419：自选有了独立页面（`/watchlist`），补进同一组；提醒仍无已交付页面，不建占位入口。
  {
    key: 'my-stocks',
    label: '我的股票',
    icon: Wallet,
    pages: [
      { to: '/watchlist', label: '我的自选', icon: Eye },
      { to: '/positions', label: '我的持仓', icon: Wallet },
      { to: '/calendar', label: '事件日历', icon: CalendarDays },
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

/** 侧栏品牌图标（顶栏/抽屉复用时取同一枚，避免各写一套）。 */
export const BRAND_ICON = TrendingUp

/** 顶栏面包屑的兜底文案：路径不属于任何一级栏目时显示 */
export const WORKSPACE_TITLE = '工作空间'

/**
 * 当前路径属于哪个一级栏目（DEC-062 §3）。
 *
 * - `?tab=` 这类查询串不参与匹配：`/market?tab=theme` 只看 `/market`；
 * - 子路径归到最长前缀的页面：`/research/600519` → 「个股深挖」，首页只认 `/` 本身；
 * - 匹配不到返回「工作空间」，回退口径写在这里，顶栏不各写一份。
 *
 * 注意：返回的是**一级栏目名**（`section.label`），不是页面 label——
 * 顶栏是「工作空间 / 我的股票」，页内大标题仍由 `PageHeader` 自己给（两层结构）。
 */
export function navTitleFor(pathname: string, sections: NavSection[] = NAV_SECTIONS): string {
  const path = pathname.split('?')[0] || '/'
  let best: { label: string; length: number } | null = null

  for (const section of sections) {
    for (const page of section.pages) {
      const to = page.to.split('?')[0]
      const hit = path === to || (to !== '/' && path.startsWith(`${to}/`))
      if (!hit) continue
      if (!best || to.length > best.length) best = { label: section.label, length: to.length }
    }
  }

  return best?.label ?? WORKSPACE_TITLE
}
