// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ResearchDetailScreen from '../src/screens/research/ResearchDetailScreen'
import { KLINE_SLOT } from '../src/components/research/sections/TrendSection'

/**
 * QUOT-448 整页编排：
 * 标的搜索 → 行情条 → 走势 K 线 → 财报前瞻 → 事件回看 → 我的论点与判断 →
 * 研究报告 → 报告对比 / 分享卡 → 研究助手。
 *
 * 两个 tab 保留（概览 / 深度研究），页脚块（事件 / 论点 / 报告 / 对比）挂在分页之外，
 * 因此块序不随 tab 变化；E/F/G/H/I 都有独立组件文件与挂载点。
 */

vi.mock('../src/charts/Chart', () => ({
  LineChart: ({ points, ariaLabel }: { points?: unknown[]; ariaLabel?: string }) => (
    <div data-testid="line-chart" data-points={points?.length ?? 0} aria-label={ariaLabel} />
  ),
  BarChart: ({ points, ariaLabel }: { points?: unknown[]; ariaLabel?: string }) => (
    <div data-testid="bar-chart" data-points={points?.length ?? 0} aria-label={ariaLabel} />
  ),
}))

const { symbolsSearch, fetchWatchlist, addWatch } = vi.hoisted(() => ({
  symbolsSearch: vi.fn(),
  fetchWatchlist: vi.fn(),
  addWatch: vi.fn(),
}))

// 只替换搜索入口，保留 `ApiError` / `isApiError`：研究页的错误分类依赖真实类
vi.mock('../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/api')>()
  return { ...actual, api: { ...actual.api, symbolsSearch } }
})
vi.mock('../src/api/radar', () => ({ fetchWatchlist, addWatch }))

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>
}

function renderRoute(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <LocationProbe />
      <Routes>
        <Route path="/research/:code" element={<ResearchDetailScreen />} />
      </Routes>
    </MemoryRouter>,
  )
}

/** 断言一组元素在文档里的先后顺序（a 在 b 之前）。`last` 用于同名标题取卡片内的那一个。 */
function assertOrder(labels: Array<string | { label: string; last?: boolean }>) {
  const nodes = labels.map((entry) => {
    const { label, last } = typeof entry === 'string' ? { label: entry, last: false } : entry
    const all = screen.getAllByRole('heading', { name: label })
    return { label, node: last ? all[all.length - 1] : all[0] }
  })
  for (let index = 1; index < nodes.length; index += 1) {
    const previous = nodes[index - 1]
    const current = nodes[index]
    const relation = previous.node.compareDocumentPosition(current.node)
    expect(
      Boolean(relation & Node.DOCUMENT_POSITION_FOLLOWING),
      `「${current.label}」应排在「${previous.label}」之后`,
    ).toBe(true)
  }
}

beforeEach(() => {
  symbolsSearch.mockReset()
  // 搜索框随页面挂载会按当前代码防抖查询一次；默认给空结果，避免悬空的定时器调用
  symbolsSearch.mockResolvedValue({ payload: { matches: [] } })
  fetchWatchlist.mockReset()
  addWatch.mockReset()
  fetchWatchlist.mockResolvedValue({
    local_only: true,
    store: 'local_sqlite',
    available: false,
    reason: '测试环境不自选',
    watch_count: 0,
    data_source_status: 'unavailable',
    price_source: 'local',
    price_as_of: null,
    source_detail: null,
    items: [],
    freshness: null,
    source_status: null,
    data_as_of: null,
    fetched_at: null,
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('个股深挖整页编排（QUOT-448）', () => {
  it('概览块序：行情条 → 走势 → 财报 → 事件 → 论点 → 研究报告 → 报告对比 → 分享卡', async () => {
    renderRoute('/research/600000?demo=1')

    expect((await screen.findAllByRole('heading', { name: '浦发银行' })).length).toBeGreaterThan(0)
    assertOrder([
      // 页头 H1 也叫「浦发银行」，这里取行情条卡片自己的标题
      { label: '浦发银行', last: true },
      '走势',
      '财报前瞻与复盘',
      '事件回看',
      '我的论点与判断',
      '研究报告',
      '报告对比',
      '分享卡',
    ])

    // 顶部搜索在整页最前（在行情条之前），且 E 的 K 线槽位有独立挂载点
    const bar = screen.getByLabelText('标的搜索')
    const headings = screen.getAllByRole('heading', { name: '浦发银行' })
    const quote = headings[headings.length - 1]
    expect(Boolean(bar.compareDocumentPosition(quote) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true)
    expect(document.querySelector(`[data-slot="${KLINE_SLOT}"]`)).not.toBeNull()
    expect(screen.getByRole('button', { name: /研究助手/ })).toBeTruthy()
  })

  it('深度研究块序不变：六段之后仍是事件 → 论点 → 研究报告 → 报告对比 → 分享卡', async () => {
    renderRoute('/research/600000?demo=1&tab=research')

    expect(await screen.findByRole('heading', { name: '公司画像' })).toBeTruthy()
    assertOrder([
      '公司画像',
      '财务趋势',
      '风险清单',
      '事件回看',
      '我的论点与判断',
      '研究报告',
      '报告对比',
      '分享卡',
    ])
    // 六个区块槽位在两个 tab 里都在：概览不依赖六段，但页脚块共用同一份
    expect(screen.getByRole('heading', { name: '六段状态' })).toBeTruthy()
  })

  it('换股只改代码：demo 等取证参数保留，tab 回到概览（照验收口径只带四个参数）', async () => {
    symbolsSearch.mockResolvedValue({
      payload: {
        query: '600001',
        count: 1,
        matches: [
          { symbol: '600001', name: '邯郸钢铁', market: 'SH', kind: 'stock', board: '主板', pinyin: null },
        ],
      },
    })
    renderRoute('/research/600000?demo=1&tab=research')

    const input = screen.getByRole('combobox', { name: /股票代码/ })
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '600001' } })
    // 页面上还有对比块的两个日期下拉，这里的 role=option 要指名搜到的那条
    const option = await screen.findByRole('option', { name: /600001/ })
    fireEvent.mouseDown(option)

    await waitFor(() =>
      expect(screen.getByTestId('location').textContent).toBe('/research/600001?demo=1'),
    )
  })
})
