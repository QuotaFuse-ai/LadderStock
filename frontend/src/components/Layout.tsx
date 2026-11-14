import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, type Location } from 'react-router-dom'
import { ChevronDown, Menu, Sparkles } from 'lucide-react'

import AiConnectDialog from './AiConnectDialog'
import MobileNavDrawer from './MobileNavDrawer'
import PageCommandBar from './PageCommandBar'
import PersistenceBanner from './PersistenceBanner'
import TopbarTools from './TopbarTools'
import PeerHealthAlert from './supervisor/PeerHealthDialog'
import { AssistantDockDrawer } from './research/sections/AiDock'
import { cn } from '../lib/cn'
import { AssistantObjectContext, AssistantShellContext, type AssistantDockState, type AssistantOpenInput } from '../lib/assistant/shell'
import { useIsMobile } from '../lib/mediaQuery'
import { PageToolsSlotContext } from '../lib/pageToolsSlot'
import { HomeModeSlotContext } from '../lib/homeModeSlot'
import { HomeAskFocusContext } from '../lib/homeAskFocus'
import { buildDockView, routeObject, type AssistantObject } from '../lib/skills/dock'
import {
  BRAND_ICON,
  NAV_SECTIONS,
  PINNED_NAV,
  navTitleFor,
  type NavPage,
} from '../lib/nav'

// 导航数据搬到 `lib/nav.ts`（DEC-062 §3）：顶栏面包屑与侧栏必须同一个真源。
// 导出名与顺序保持不变——`tests/nav-routes.test.tsx` 直接从这里 import。
export { NAV_SECTIONS, PINNED_NAV, type NavPage, type NavSection } from '../lib/nav'

/**
 * 应用外壳（UI-SPEC §2「全局外壳」/ §7.3 结构尺寸）。
 *
 * 一级导航 9 组 + 底部固定的「设置」：
 * 首页 → 市场 → 盯盘 → 复盘 → 日志 → 我的股票 → 验证 → 产业研究 → 个股深挖 ─┬─ 设置（弱化、沉底）。
 * 「我的股票」由 QUOT-304 从「市场 / 日志」抽出（DEC-044）：持仓与事件日历同属"我自己相关"，
 * 聚成一组但不改路由；QUOT-435 把提醒（`/inbox`）补成组内第三项。
 *
 * **二级导航只在「真的有两页以上」时展开**（QUOT-198 就绪表）：
 * - 市场 4 段（情绪温度 / 连板梯队 / 题材轮动 / 资讯雷达）已实现 → 展开为二级；
 * - 复盘 4 段（每日复盘 / AI复盘 / AI周报 / 候选池）、我的股票 3 段（我的股票 / 事件日历 / 提醒）
 *   同样展开；「AI复盘」由 QUOT-344（DEC-046）从复盘组拆出：三屏各自一条路由，高亮按 pathname 精确匹配，
 *   `/review` 亮时 `/review/ai` 不跟着亮；
 * - 盯盘 2 段（盘中信号 / 竞价与盘中）都已实现 → 展开为二级；
 *   判定是数据驱动的（`pages.length > 1`），后端口径补上后只加数据、不改组件。
 *
 * - 1280 起：232px 完整侧栏（图标 + 文字 + 二级缩进 16px）。
 * - 1024–1279：收窄为 56px 图标栏，二级展平为图标行（不做嵌套，避免挤压）；
 *   展平后 20 行图标同形同距，分组靠组间 1px 分隔线保留可读性（QUOT-316）。
 * - 展开状态写 localStorage（`ldst.nav.expanded`），刷新后原样恢复。
 *
 * **顶栏**（QUOT-442 / DEC-062，QUOT-457 / DEC-063 收敛）：侧栏右侧纵向列 = `header.workspace-topbar` + 主内容列。
 * 顶栏左侧是面包屑（`工作空间 /` + 一级栏目名）；右侧依次是页面工具槽（`PageHeader` 经 portal 送进来的「刷新」）、
 * 本页命令（`PageCommandBar`，图标 + 弹层）、全局工具区（`TopbarTools`）。
 * 全平台只有这一条横栏：页面自己不再渲染标题条，命令也不再占内容区一行。
 * `<768px` 侧栏收起为抽屉（`MobileNavDrawer`），由汉堡按钮打开。
 * 主内容列仍是唯一滚动容器，`PersistenceBanner` 挂在顶栏之下、`main` 之上。
 *
 * **胶囊样式**（QUOT-200）：导航仍保持竖排侧栏，只是每个条目本身是横置胶囊（`rounded-pill`）——
 * 一级组、二级页、底部设置/AI 全部同一形状；当前页整颗填色，不再靠左侧色条或方角区分。
 * 不改竖排结构、不改展开逻辑，窄屏 rail 下胶囊收成图标胶囊。
 */

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

/**
 * 图标栏（1024–1279）下的组间分隔：展平后 20 行图标同形同距，只靠这条 1px 线保留分组可读性。
 * `ul` 本身已有 `gap-1`，所以这里只加线、不加内距，避免把溢出推得更长。
 */
const RAIL_GROUP = 'rail:border-t rail:border-border rail:first:border-t-0'

function navClass(isActive: boolean, secondary = false): string {
  return cn(
    'group flex h-row items-center gap-2 rounded-pill px-3 text-body transition-colors duration-fast rail:justify-center rail:px-0',
    isActive
      ? 'bg-accent-soft font-medium text-accent'
      : secondary
        ? 'text-text-secondary hover:bg-surface-3 hover:text-text'
        : 'text-text-secondary hover:bg-surface-3 hover:text-text',
  )
}

export default function Layout() {
  const location = useLocation()
  const isMobile = useIsMobile()
  const [expanded, setExpanded] = useState<string[]>(readExpanded)
  // `?ai=1` 是深链（截图与排障用）；首次渲染读取一次，不跟后续导航反复弹窗
  const [aiOpen, setAiOpen] = useState(() => new URLSearchParams(location.search).get('ai') === '1')
  const [drawerOpen, setDrawerOpen] = useState(false)
  // 顶栏「问 AI」与页内「研究助手」共用这一份抽屉（QUOT-475）：全站只有一个实例
  const [assistant, setAssistant] = useState<AssistantDockState | null>(null)
  // 页内登记的当前对象（如 `/research/:code` 的名称与研究日），顶栏打开时带上
  const [pageObject, setPageObject] = useState<AssistantObject | null>(null)
  // 顶栏右侧给页面主操作（刷新）留的落点，见 `lib/pageToolsSlot.tsx`
  const [toolsSlot, setToolsSlot] = useState<HTMLDivElement | null>(null)
  // 顶栏中间给首页双模式切换留的落点（QUOT-582）：只有 `/` 的 `HomeShell` 会往里送，
  // 其余屏读到 null，这个节点本身是空 div，不占宽度、不撑高
  const [modeSlot, setModeSlot] = useState<HTMLDivElement | null>(null)
  // 首页 ask 聚焦信号（QUOT-590）：home 上「问 AI」不开抽屉，改广播聚焦既有输入框。
  // seq 自增让重复请求也能被 HomeShell 感知（比对前值而不是布尔翻转）。
  const [askFocusSeq, setAskFocusSeq] = useState(0)
  const menuRef = useRef<HTMLButtonElement>(null)

  const openAi = useCallback(() => setAiOpen(true), [])
  const closeDrawer = useCallback(() => setDrawerOpen(false), [])

  /**
   * 打开助手抽屉：页面绑定按 `lib/skills/pageBindings.ts` 推断；
   * 调用方（页内按钮）给了对象就用它的，否则用路由 + 页内登记的对象。
   * 首页（`/`）例外：home 已有 chat composer 与 desk 研究面两颗 ask 面，抽屉里的
   * 研究聊天是同一颗 `HomeResearchChat` 的第二实例（QUOT-590 根因）——这里不开抽屉，
   * 改广播聚焦信号，由 `HomeShell` 聚焦当前模式的既有输入框。只聚焦、不自动发送。
   */
  const openAssistant = useCallback(
    (input: AssistantOpenInput = {}) => {
      if (input.pageKey ? input.pageKey === '/' : location.pathname === '/') {
        setAskFocusSeq((seq) => seq + 1)
        return
      }
      const pageKey = input.pageKey ?? location.pathname
      const base = input.object ? {} : { ...routeObject(pageKey), ...(pageObject ?? {}) }
      const object: AssistantObject = { ...base, ...(input.object ?? {}) }
      setAssistant({
        view: buildDockView({
          pathname: pageKey,
          object,
          boundSkill: input.boundSkill,
          initialDraft: input.initialDraft,
          offlineNotice: input.offlineNotice ?? null,
        }),
        overview: input.overview ?? null,
      })
    },
    [location.pathname, pageObject],
  )

  const assistantShell = useMemo(() => ({ open: openAssistant }), [openAssistant])
  const closeAssistant = useCallback(() => setAssistant(null), [])

  // 换页就收起：抽屉里的上下文属于上一页，留着会拼错绑定
  useEffect(() => {
    setAssistant(null)
  }, [location.pathname])

  // 主内容区是滚动容器（QUOT-397），浏览器自带的窗口滚动复原管不到它：换页时手动归顶，
  // 否则从长页跳到短页会停在空白处。页内 `#anchor` 跳转交给浏览器，这里不拦。
  const scroller = useRef<HTMLDivElement>(null)
  // 只滚新页面：`?tab=` / `?date=` 这类同路径的同页查询串变化不应把人拽回顶部
  const pageKey = location.pathname
  useEffect(() => {
    if (location.hash || !scroller.current) return
    // 用 scrollTop 而不是 `scrollTo`：jsdom 没实现后者（与 SearchPanel 取 scrollIntoView 同一原因）
    scroller.current.scrollTop = 0
  }, [pageKey, location.hash])

  // 抽屉是「临时覆盖层」：换页（含点抽屉里的链接）与回到桌面宽度都自动收起
  useEffect(() => {
    setDrawerOpen(false)
  }, [location.pathname, isMobile])

  const toggleGroup = (key: string) => {
    setExpanded((current) => {
      const next = current.includes(key) ? current.filter((item) => item !== key) : [...current, key]
      writeExpanded(next)
      return next
    })
  }

  const Brand = BRAND_ICON

  return (
    <div className="flex min-h-screen bg-bg text-text">
      <a
        href="#main"
        className="sr-only z-toast rounded border border-border-strong bg-surface px-3 py-2 text-body focus:not-sr-only focus:fixed focus:left-2 focus:top-2"
      >
        跳到主内容
      </a>

      {/* `<768px` 侧栏让位给抽屉（DEC-062 §2）：两套导航同屏既挤又重复。
          断点由 JS 判断而不是 `hidden md:flex`——本仓 `screens` 只登记了 sm/lg/rail/desk。 */}
      <aside
        className={cn(
          'sticky top-0 h-screen w-sidebar shrink-0 flex-col border-r border-border bg-surface rail:w-sidebar-rail',
          isMobile ? 'hidden' : 'flex',
        )}
      >
        <div className="flex h-topbar items-center gap-2 border-b border-border px-3 rail:justify-center rail:px-0">
          <span
            aria-hidden="true"
            className="flex size-7 shrink-0 items-center justify-center rounded bg-accent-soft text-accent"
          >
            <Brand className="size-4" strokeWidth={1.75} />
          </span>
          <div className="min-w-0 rail:hidden">
            <p className="truncate text-body font-semibold text-text">天梯股复盘台</p>
            <p className="truncate text-caption text-text-muted">LadderStock · 本地看板</p>
          </div>
        </div>

        <nav aria-label="主导航" className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-2">
          <ul className="flex flex-col gap-1">
            {NAV_SECTIONS.map((section) => {
              const isGroup = section.pages.length > 1
              const isOpen = expanded.includes(section.key)
              const childActive = section.pages.some((page) => pageActive(page, location))

              if (!isGroup) {
                const [page] = section.pages
                return (
                  <li key={section.key} className={RAIL_GROUP}>
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
                <li key={section.key} className={RAIL_GROUP}>
                  <button
                    type="button"
                    aria-expanded={isOpen}
                    aria-controls={`nav-group-${section.key}`}
                    title={isOpen ? `收起${section.label}` : `展开${section.label}`}
                    onClick={() => toggleGroup(section.key)}
                    className={cn(
                      'flex h-row w-full items-center gap-2 rounded-pill px-3 text-body transition-colors duration-fast hover:bg-surface-3 hover:text-text rail:hidden',
                      childActive ? 'bg-accent-soft font-medium text-accent' : 'text-text-secondary',
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

                  {/* 窄侧栏（rail）下二级始终展平显示：一级标题隐藏，子项变成一排图标。
                      完整侧栏下二级胶囊再缩进 12px，靠缩进而不是导引线表达层级
                      （胶囊是整条填色，左侧竖线会切进胶囊圆角里）。 */}
                  <ul
                    id={`nav-group-${section.key}`}
                    className={cn(
                      'mt-1 ml-2 flex flex-col gap-1 pl-2 rail:ml-0 rail:mt-0 rail:pl-0',
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
          {/* 主题入口已上移到顶栏（DEC-062 §2 / QUOT-442），侧栏不再放第二份 Segmented，
              避免两个入口同屏；设置页的入口照旧。 */}
          {/* 接入 AI 是动作而不是页面：不占路由、不参与导航一致性断言，放在设置上方。 */}
          <button
            type="button"
            onClick={openAi}
            title="接入 AI"
            aria-haspopup="dialog"
            className={navClass(false, true)}
          >
            <Sparkles className="size-4 shrink-0 text-accent" strokeWidth={1.75} aria-hidden="true" />
            <span className="truncate rail:hidden">接入 AI</span>
          </button>
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

      {/* 侧栏右侧纵向列（DEC-062 §1）：顶栏固定，主内容列是唯一滚动容器。
          窗口本身不滚，所以页头 `sticky top-0` 才钉得住（QUOT-397）。 */}
      <div className="flex h-screen min-w-0 flex-1 flex-col overflow-hidden">
        <header className="workspace-topbar flex h-topbar shrink-0 items-center justify-between gap-3 border-b border-border bg-bg px-4">
          <div className="flex min-w-0 items-center gap-2 text-body">
            {isMobile && (
              <button
                ref={menuRef}
                type="button"
                onClick={() => setDrawerOpen(true)}
                aria-label="打开导航"
                aria-expanded={drawerOpen}
                aria-controls="mobile-nav-drawer"
                className="-ml-1 rounded p-1.5 text-text-muted transition-colors duration-fast hover:bg-surface-3 hover:text-text"
              >
                <Menu className="size-4" strokeWidth={1.75} aria-hidden="true" />
              </button>
            )}
            {/* `sm` 以下只留栏目名：面包屑在手机上把标题挤没了（DEC-062 §2 断点表） */}
            <span className="hidden text-text-muted sm:inline">工作空间 /</span>
            <strong className="truncate font-semibold">{navTitleFor(location.pathname)}</strong>
          </div>

          {/* 首页「对话 / 研究台」切换落点（QUOT-582）：无页挂载时是空节点 */}
          <div ref={setModeSlot} className="flex min-w-0 items-center" />

          <div className="flex min-w-0 items-center gap-2">
            {/* 页面主操作落点：`PageHeader` 把「刷新」portal 到这里，页面本身不再出标题条 */}
            <div ref={setToolsSlot} className="flex items-center gap-2" />
            {/* QUOT-457：「运行本页命令」收进顶栏（图标 + 弹层）；无命令或控制面未开启时整颗不渲染 */}
            <PageCommandBar />
            <TopbarTools onOpenAi={openAi} onAskAi={() => openAssistant()} homeAskFocus={location.pathname === '/'} />
          </div>
        </header>

        <div ref={scroller} className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto overscroll-contain">
          <PersistenceBanner />
          <main id="main" tabIndex={-1} className="page-shell w-full flex-1 focus:outline-none">
            <PageToolsSlotContext.Provider value={toolsSlot}>
              <HomeModeSlotContext.Provider value={modeSlot}>
                {/* QUOT-590：home 上「问 AI」经这份信号聚焦既有 ask 面，不另开抽屉 */}
                <HomeAskFocusContext.Provider value={{ seq: askFocusSeq }}>
                  {/* QUOT-475：页内「研究助手」通过 shell 打开同一份抽屉；页面可把当前标的上报给顶栏 */}
                  <AssistantShellContext.Provider value={assistantShell}>
                    <AssistantObjectContext.Provider value={setPageObject}>
                      <Outlet />
                    </AssistantObjectContext.Provider>
                  </AssistantShellContext.Provider>
                </HomeAskFocusContext.Provider>
              </HomeModeSlotContext.Provider>
            </PageToolsSlotContext.Provider>
          </main>
        </div>
      </div>

      <MobileNavDrawer
        open={drawerOpen}
        onClose={closeDrawer}
        returnFocusTo={menuRef}
        onAskAi={() => openAssistant()}
      />
      <AiConnectDialog open={aiOpen} onClose={() => setAiOpen(false)} />
      <AssistantDockDrawer
        view={assistant?.view ?? null}
        overview={assistant?.overview ?? null}
        onClose={closeAssistant}
      />
      {/* QUOT-558：子模块健康事件全局弹窗——任何页面 crash/掉线/端口冲突都主动打扰 */}
      <PeerHealthAlert />
    </div>
  )
}
