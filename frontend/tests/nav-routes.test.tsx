// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Navigate, Route, Routes, matchRoutes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import Layout, { NAV_SECTIONS, PINNED_NAV } from '../src/components/Layout'
import { NON_PAGE_ROUTES, ROUTE_PATHS } from '../src/routes'

/**
 * 导航 ↔ 路由一致性（QUOT-300 的核心产出）。
 *
 * 产业研究 / 产业信号 / 个股深挖三页做完就进了路由表，侧栏却没接，用户只能手打 URL ——
 * 这类「做了页面忘接导航」靠人肉 review 抓不住，靠两个方向的断言才守得住：
 * - 正向：侧栏每一项都能被真实路由表命中（防死链）；
 * - 反向：路由表里每条已交付路由都在侧栏有入口，或由有入口的页面链入（防孤儿页）。
 *
 * 路由表本身少写组件编译不过（`App.tsx` 的 `Record<RoutePath, ReactElement>`），
 * 所以路径比对不碰组件；只有 DEC-046 那一条要区分「两条路径挂同一个屏」，
 * 才动态 import 一次组件表（代价见 `stubMatchMedia`）。
 */

/** 侧栏项里的 `?tab=` 只是高亮用的，路由匹配只看 path。 */
function pathOf(to: string): string {
  return to.split('?')[0]
}

const NAV_TO_KEYS = [
  ...NAV_SECTIONS.flatMap((section) => section.pages.map((page) => page.to)),
  ...PINNED_NAV.map((page) => page.to),
]
const NAV_PATHS = NAV_TO_KEYS.map(pathOf)

/** `/research/600000/report` 这类带参路由的静态前缀，用来判断它是否挂在某个有入口的页面下面。 */
function staticPrefixOf(path: string): string {
  return path.slice(0, path.indexOf('/:'))
}

function matchedRoute(path: string): string | null {
  const matches = matchRoutes(
    ROUTE_PATHS.map((routePath) => ({ path: routePath })),
    path,
  )
  return matches && matches.length > 0 ? (matches[matches.length - 1].route.path ?? null) : null
}

/** uPlot 模块期就要 `matchMedia`，jsdom 不提供——动态 import `App.tsx` 之前必须先补这个桩。 */
function stubMatchMedia(): void {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }))
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('侧栏入口 → 路由表', () => {
  it.each(NAV_PATHS)('`%s` 命中真实路由，不落进 404', (path) => {
    expect(matchedRoute(path)).not.toBe('*')
  })

  it('每条 `NAV_SECTIONS` / `PINNED_NAV` 的 `to` 都是路由表里写着的页', () => {
    expect(NAV_PATHS.filter((path) => !ROUTE_PATHS.includes(path as (typeof ROUTE_PATHS)[number]))).toEqual([])
  })

  it('分组 key 与侧栏链接都不重复（展开态、aria-controls、React key 都靠它们）', () => {
    const keys = NAV_SECTIONS.map((section) => section.key)
    expect(new Set(keys).size).toBe(keys.length)
    expect(new Set(NAV_TO_KEYS).size).toBe(NAV_TO_KEYS.length)
  })
})

describe('路由表 → 侧栏入口', () => {
  it('每条已交付路由要么直接有入口，要么由有入口的页面链入', () => {
    const orphan = ROUTE_PATHS.filter(
      (path) =>
        !NON_PAGE_ROUTES.includes(path) &&
        (path.includes(':') ? !NAV_PATHS.includes(staticPrefixOf(path)) : !NAV_PATHS.includes(path)),
    )
    expect(orphan).toEqual([])
  })

  it('QUOT-146 / QUOT-147 交付的 5 条路由都在侧栏可达链上', () => {
    for (const path of ['/industry', '/signals', '/research']) {
      expect(NAV_PATHS).toContain(path)
    }
    // 两条二级深链由已有入口的页面内部链入，不重复挂侧栏
    expect(NAV_PATHS).toContain(staticPrefixOf('/industry/:name'))
    expect(NAV_PATHS).toContain(staticPrefixOf('/research/:code/report'))
  })
})

/**
 * 复盘三屏（QUOT-344 / DEC-046）：每日复盘 / AI复盘 / AI周报 各自一条路由、一个入口。
 *
 * 老板拍板不合并成单屏，那就得防住「拆了导航没拆屏」这条退路——只比路径的断言看不出
 * 两条路径挂同一个组件，所以这里额外读一次 `App.tsx` 的组件表。
 */
const REVIEW_PAGES = NAV_SECTIONS.find((section) => section.key === 'review')?.pages ?? []

describe('复盘组三独立入口（QUOT-344 / DEC-046）', () => {
  it('复盘组是 每日复盘 / AI复盘 / AI周报 / 候选池 四项，顺序与文案在册', () => {
    expect(REVIEW_PAGES.map((page) => [page.to, page.label])).toEqual([
      ['/review', '每日复盘'],
      ['/review/ai', 'AI复盘'],
      ['/weekly', 'AI周报'],
      ['/pool', '候选池'],
    ])
  })

  it('三条复盘入口互不相同，且各自深链命中自己那条路由', () => {
    const paths = REVIEW_PAGES.map((page) => pathOf(page.to)).filter((path) => path !== '/pool')
    expect(new Set(paths).size).toBe(paths.length)
    for (const path of paths) {
      expect(matchedRoute(path)).toBe(path)
    }
  })

  // 冷导入整张路由表（含全部屏幕）在慢机上会超过默认 5s，这里只放宽这一条的预算。
  it('`/review` 与 `/review/ai` 挂的不是同一个组件', async () => {
    stubMatchMedia()
    const { ROUTE_ELEMENTS } = await import('../src/App')
    expect(ROUTE_ELEMENTS['/review'].type).not.toBe(ROUTE_ELEMENTS['/review/ai'].type)
    expect(ROUTE_ELEMENTS['/review/ai'].type).toBeTruthy()
  }, 15000)
})

/** QUOT-431：旧收藏夹里的 `/watchlist`、`/positions` 不能 404，统一落到合并后的两标签页。 */
describe('旧深链重定向（QUOT-431）', () => {
  it('`/watchlist` 与 `/positions` 都挂 `Navigate`，落点带对应 `?tab=`', async () => {
    stubMatchMedia()
    const { ROUTE_ELEMENTS } = await import('../src/App')
    for (const [path, tab] of [
      ['/watchlist', 'watch'],
      ['/positions', 'hold'],
    ] as const) {
      const element = ROUTE_ELEMENTS[path]
      expect(element.type).toBe(Navigate)
      expect((element.props as { to?: string }).to).toBe(`/my-stocks?tab=${tab}`)
    }
  }, 15000)
})

/**
 * QUOT-474 / DEC-065：第 9 组由「个股深挖」整组改名为「研究工具」，不是新增第 10 组。
 *
 * 改名而非并排，是为了不让侧栏同时出现「研究工具」和「个股深挖」两条平级项；组内两页
 * 固定为 工具总览（`/skills`）+ 个股深挖（`/research`，路径不变、深链不 404）。
 */
describe('「研究工具」组（QUOT-474 / DEC-065）', () => {
  it('第 9 组 key 是 `research-tools`，不再是 `deep-dive`', () => {
    expect(NAV_SECTIONS.at(-1)?.key).toBe('research-tools')
    expect(NAV_SECTIONS.map((section) => section.key)).not.toContain('deep-dive')
  })

  it('组内两页恰好是 `/skills` 与 `/research`，顺序在册', () => {
    const group = NAV_SECTIONS.find((section) => section.key === 'research-tools')
    expect(group?.label).toBe('研究工具')
    expect(group?.pages.map((page) => page.to)).toEqual(['/skills', '/research'])
  })

  it('两条入口都命中真实路由：`/skills` 与 `/skills/:id` 新增，`/research` 深链保留', () => {
    expect(ROUTE_PATHS).toContain('/skills')
    expect(ROUTE_PATHS).toContain('/skills/:id')
    expect(ROUTE_PATHS).toContain('/skills/pkg/:name')
    expect(matchedRoute('/skills')).toBe('/skills')
    expect(matchedRoute('/skills/1')).toBe('/skills/:id')
    expect(matchedRoute('/skills/pkg/a-share-dossier')).toBe('/skills/pkg/:name')
    // 个股深挖路径不变：概览与报告深链都还在
    expect(NAV_PATHS).toContain('/research')
    expect(matchedRoute('/research')).toBe('/research')
    expect(matchedRoute('/research/600000')).toBe('/research/:code')
    expect(matchedRoute('/research/600000/report')).toBe('/research/:code/report')
  })
})

describe('侧栏顺序与渲染（docs/UI-SPEC.md）', () => {
  it('既有分组顺序保留，QUOT-474 第 9 组为「研究工具」', () => {
    expect(NAV_SECTIONS.map((section) => section.key)).toEqual([
      'home',
      'market',
      'watch',
      'review',
      'journal',
      'my-stocks',
      'validate',
      'industry',
      'research-tools',
    ])
  })

  it('「我的股票」= 一页两标签 + 事件日历 + 提醒（QUOT-431 / QUOT-435）', () => {
    const group = NAV_SECTIONS.find((section) => section.key === 'my-stocks')
    // 自选 / 持仓合成 `/my-stocks?tab=watch|hold`，侧栏只留一颗入口；旧地址只做重定向；
    // 提醒（`/inbox`）是 QUOT-435 补上的组内第三项
    expect(group?.pages.map((page) => page.to)).toEqual(['/my-stocks', '/calendar', '/inbox'])
    const other = NAV_SECTIONS.filter((section) => section.key !== 'my-stocks')
    const moved = other.flatMap((section) => section.pages.map((page) => page.to))
    expect(moved).not.toContain('/watchlist')
    expect(moved).not.toContain('/positions')
    expect(moved).not.toContain('/calendar')
    expect(moved).not.toContain('/inbox')
  })

  it('聚合是移动不是砍功能：DEC-044 之前的 14 条已交付入口一条都不能少', () => {
    const delivered = [
      '/',
      '/market',
      '/news',
      '/watch',
      '/intraday',
      '/review',
      '/weekly',
      '/pool',
      '/journal',
      '/my-stocks',
      '/calendar',
      '/inbox',
      '/validate',
      '/limit-up',
      '/industry',
      '/signals',
      '/research',
      '/settings',
    ]
    expect(new Set(NAV_PATHS.filter((path) => delivered.includes(path)))).toEqual(new Set(delivered))
    for (const to of NAV_TO_KEYS) expect(ROUTE_PATHS).toContain(pathOf(to))
  })

  it('自选 / 持仓合并进 `/my-stocks`，旧地址只做重定向；提醒已交付、入口落在「我的股票」组', () => {
    // QUOT-431：页面在 `/my-stocks?tab=watch|hold`，`/watchlist`、`/positions` 只保留深链兼容
    expect(NAV_PATHS).toContain('/my-stocks')
    expect(NAV_PATHS).not.toContain('/watchlist')
    expect(NAV_PATHS).not.toContain('/positions')
    expect(NON_PAGE_ROUTES).toContain('/watchlist')
    expect(NON_PAGE_ROUTES).toContain('/positions')
    const stocks = NAV_SECTIONS.flatMap((section) => section.pages).find((page) => page.to === '/my-stocks')
    expect(stocks?.label).toBe('我的股票')
    // QUOT-435：提醒不再是「没页面就建占位入口」的例外，入口与路由都要真实存在
    const inbox = NAV_SECTIONS.filter((section) => section.key === 'my-stocks').flatMap(
      (section) => section.pages,
    )
    expect(inbox.map((page) => page.label)).toContain('提醒')
    expect(inbox.find((page) => page.label === '提醒')?.to).toBe('/inbox')
    expect(NAV_PATHS).toContain('/inbox')
    expect(NON_PAGE_ROUTES).not.toContain('/inbox')
  })

  it('单页组不挂二级（`pages.length > 1` 才展开）：持仓移出后日志只剩交易日志', () => {
    const single = NAV_SECTIONS.filter((section) => section.pages.length === 1).map((section) => section.key)
    expect(single).toEqual(['home', 'journal'])
  })

  it('设置沉底、不参与分组展开，其余页面都在分组里', () => {
    expect(NAV_SECTIONS.flatMap((section) => section.pages.map((page) => page.to))).not.toContain('/settings')
    expect(PINNED_NAV.map((page) => page.to)).toEqual(['/settings'])
  })

  it('渲染出来的侧栏默认就带着全部入口（不用先点展开，DOM 顺序即视觉顺序）', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="*" element={<div data-testid="main" />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )
    const nav = screen.getByRole('navigation', { name: '主导航' })
    const hrefs = Array.from(nav.querySelectorAll('a[href]')).map((link) => link.getAttribute('href'))
    // 二级分组默认展开（`ldst.nav.expanded` 的缺省口径），新分组不点展开就可达
    expect(hrefs).toEqual(NAV_SECTIONS.flatMap((section) => section.pages.map((page) => page.to)))
    expect(hrefs).toContain('/research')
  })

  it('底部固定项在导航之外，链接指向设置', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="*" element={<div />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )
    expect(screen.getAllByRole('link', { name: /设置/ })[0].getAttribute('href')).toBe('/settings')
  })
})
