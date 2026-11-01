// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, matchRoutes } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

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
 * 所以这里只比路径。
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

afterEach(() => {
  cleanup()
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

describe('侧栏顺序与渲染（docs/UI-SPEC.md）', () => {
  it('既有分组顺序保留，QUOT-304 的「我的股票」插在「日志」之后', () => {
    expect(NAV_SECTIONS.map((section) => section.key)).toEqual([
      'home',
      'market',
      'watch',
      'review',
      'journal',
      'my-stocks',
      'validate',
      'industry',
      'deep-dive',
    ])
  })

  it('「我的股票」只聚合已有路由：持仓 + 事件日历（DEC-044）', () => {
    const group = NAV_SECTIONS.find((section) => section.key === 'my-stocks')
    expect(group?.pages.map((page) => page.to)).toEqual(['/positions', '/calendar'])
    // 聚合是移动不是复制：原分组里不再重复出现
    const other = NAV_SECTIONS.filter((section) => section.key !== 'my-stocks')
    const moved = other.flatMap((section) => section.pages.map((page) => page.to))
    expect(moved).not.toContain('/positions')
    expect(moved).not.toContain('/calendar')
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
      '/positions',
      '/calendar',
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

  it('提醒 / 自选没有已交付页面：导航里不许出现点进去是空页的占位入口（DEC-044 §2.4）', () => {
    expect(NAV_PATHS.filter((path) => /inbox|remind|watchlist/.test(path))).toEqual([])
    const labels = NAV_SECTIONS.flatMap((section) => [section.label, ...section.pages.map((page) => page.label)])
    expect(labels.filter((label) => /提醒|自选/.test(label))).toEqual([])
  })

  it('单页组不挂二级（`pages.length > 1` 才展开）：持仓移出后日志只剩交易日志', () => {
    const single = NAV_SECTIONS.filter((section) => section.pages.length === 1).map((section) => section.key)
    expect(single).toEqual(['home', 'journal', 'deep-dive'])
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
