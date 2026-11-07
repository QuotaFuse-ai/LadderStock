// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/react'
import { BarChart3, Bot, CalendarRange, Gauge, Layers, ListChecks, Newspaper, NotebookPen } from 'lucide-react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import { SectionNavPills } from '../src/components/PageShell'
import { NavPills } from '../src/components/ui'

/**
 * 横向胶囊页签条（QUOT-390）。
 *
 * 三条验收口径都来自老板那句「我要这样的胶囊」的反面：
 * 选中态必须**只有一颗**整颗填色（不是整组高亮）、单页组不许出现空条、
 * `/market?tab=` 三条同路径入口不许同时不高亮或全高亮。
 */

const REVIEW_ITEMS = [
  { to: '/review', label: '每日复盘', icon: NotebookPen },
  { to: '/review/ai', label: 'AI复盘', icon: Bot },
  { to: '/weekly', label: 'AI周报', icon: CalendarRange },
  { to: '/pool', label: '候选池', icon: ListChecks },
]

const MARKET_ITEMS = [
  { to: '/market?tab=sentiment', label: '情绪温度', icon: Gauge },
  { to: '/market?tab=ladder', label: '连板梯队', icon: BarChart3 },
  { to: '/market?tab=theme', label: '题材轮动', icon: Layers },
  { to: '/news', label: '资讯雷达', icon: Newspaper },
]

function renderAt(entry: string, ui = <SectionNavPills />) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="*" element={ui} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(cleanup)

describe('NavPills · 原语', () => {
  it('选中态只有一颗：aria-current="page" 命中当前路径那一条', () => {
    renderAt(
      '/review/ai',
      <NavPills items={REVIEW_ITEMS} basePath="/review/ai" ariaLabel="复盘导航" />,
    )
    const nav = screen.getByRole('navigation', { name: '复盘导航' })
    const current = within(nav).getAllByRole('link', { current: 'page' })
    expect(current).toHaveLength(1)
    expect(current[0].textContent).toContain('AI复盘')
    expect(current[0].className).toContain('bg-accent-soft')
    expect(current[0].className).toContain('text-accent')
    expect(within(nav).getAllByRole('link')).toHaveLength(4)
  })

  it('一颗一颗独立胶囊：每颗自带完整圆角边框，颗间距走 gap-2（8px），不是连体 segmented', () => {
    renderAt('/review', <NavPills items={REVIEW_ITEMS} ariaLabel="复盘导航" />)
    const nav = screen.getByRole('navigation', { name: '复盘导航' })
    expect(nav.className).toContain('gap-2')
    for (const link of within(nav).getAllByRole('link')) {
      expect(link.className).toContain('rounded-pill')
      expect(link.className).toMatch(/border-\S+|border\b/)
    }
  })

  it('sm 档：紧凑高度与说明文字号，给版本切换这类二级用法', () => {
    renderAt('/review', <NavPills items={REVIEW_ITEMS} size="sm" ariaLabel="版本切换" />)
    const nav = screen.getByRole('navigation', { name: '版本切换' })
    const [first] = within(nav).getAllByRole('link')
    expect(first.className).toContain('h-6')
    expect(first.className).toContain('text-caption')
    expect(first.className).not.toContain('h-control')
  })

  it('窄屏不折行：一行放不下就横向滚动，滚动条裁掉', () => {
    renderAt('/review', <NavPills items={REVIEW_ITEMS} ariaLabel="复盘导航" />)
    const nav = screen.getByRole('navigation', { name: '复盘导航' })
    expect(nav.className).toContain('overflow-x-auto')
    expect(nav.className).not.toContain('flex-wrap')
    expect(nav.className).toContain('ldst-no-scrollbar')
  })
})

describe('NavPills · ?tab= 归组', () => {
  it('URL 带 tab 时只亮对应那颗，`/news` 那颗不受影响', () => {
    renderAt('/market?tab=ladder', <NavPills items={MARKET_ITEMS} basePath="/market" ariaLabel="市场导航" />)
    const nav = screen.getByRole('navigation', { name: '市场导航' })
    const current = within(nav).getAllByRole('link', { current: 'page' })
    expect(current).toHaveLength(1)
    expect(current[0].textContent).toContain('连板梯队')
  })

  it('URL 不带 tab 时归给该路径的第一颗（`/market` = 情绪温度），不会全不高亮', () => {
    renderAt('/market', <NavPills items={MARKET_ITEMS} basePath="/market" ariaLabel="市场导航" />)
    const nav = screen.getByRole('navigation', { name: '市场导航' })
    const current = within(nav).getAllByRole('link', { current: 'page' })
    expect(current).toHaveLength(1)
    expect(current[0].textContent).toContain('情绪温度')
  })

  it('同路径的其它颗在不属于自己的路由上不高亮（`/news` 只亮资讯雷达）', () => {
    renderAt('/news', <NavPills items={MARKET_ITEMS} basePath="/market" ariaLabel="市场导航" />)
    const nav = screen.getByRole('navigation', { name: '市场导航' })
    const current = within(nav).getAllByRole('link', { current: 'page' })
    expect(current.map((link) => link.textContent)).toEqual(['资讯雷达'])
  })
})

describe('SectionNavPills · 挂在页头下方的导航用法', () => {
  it('复盘组：取 NAV_SECTIONS 的复盘四项，顺序与文案跟侧栏同源', () => {
    renderAt('/review/ai')
    const nav = screen.getByRole('navigation', { name: '页内导航' })
    expect(within(nav).getAllByRole('link').map((link) => link.textContent)).toEqual([
      '每日复盘',
      'AI复盘',
      'AI周报',
      '候选池',
    ])
    expect(within(nav).getAllByRole('link', { current: 'page' })).toHaveLength(1)
  })

  it('单页组整条不渲染：首页 / 日志 / 设置都不出现空条', () => {
    // QUOT-474 / DEC-065：`/research` 现在与 `/skills` 同属「研究工具」双页组，
    // 不再是单页组，故从这条用例里移除；单页组只剩 首页 / 日志
    for (const entry of ['/', '/journal', '/settings']) {
      const { unmount } = renderAt(entry)
      expect(screen.queryByRole('navigation', { name: '页内导航' })).toBeNull()
      unmount()
    }
  })

  it('市场段落在胶囊条上是四颗，`?tab=` 只亮一颗', () => {
    renderAt('/market?tab=theme')
    const nav = screen.getByRole('navigation', { name: '页内导航' })
    expect(within(nav).getAllByRole('link')).toHaveLength(4)
    expect(within(nav).getAllByRole('link', { current: 'page' })[0].textContent).toContain('题材轮动')
  })

  it('详情页（`/industry/:name`）不属于任何导航组，不渲染页签条', () => {
    renderAt('/industry/半导体')
    expect(screen.queryByRole('navigation', { name: '页内导航' })).toBeNull()
  })
})
