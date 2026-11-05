// @vitest-environment jsdom
import type { ReactElement } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import CalendarScreen from '../src/screens/radar/CalendarScreen'
import PositionsScreen from '../src/screens/radar/PositionsScreen'
import MyStocksScreen from '../src/screens/my-stocks/MyStocksScreen'

/**
 * QUOT-441 / DEC-061：只读页进入即取数。
 *
 * 三屏（`/positions` `/my-stocks?tab=watch` `/calendar`）挂载后必须立刻调用各自只读 GET，
 * 不允许出现「先跑命令才出数」的路径；空态只写缺什么 + 为什么 + 页内重试，
 * 也不再出现「先运行 ldst …」式终端指令。
 */

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function stubApi(routes: Record<string, unknown>) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      calls.push(url)
      const prefix = Object.keys(routes).find((key) => url.startsWith(key))
      if (!prefix) throw new Error(`unexpected fetch: ${url}`)
      return new Response(JSON.stringify(routes[prefix]), { status: 200 })
    }),
  )
  return calls
}

function renderAt(path: string, element: ReactElement) {
  // 查询串只进 initialEntries，`Route path` 只认 pathname（`/my-stocks?tab=watch`）
  const pathname = path.split('?')[0]
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={pathname} element={element} />
      </Routes>
    </MemoryRouter>,
  )
}

function positionsPayload(withItems: boolean) {
  return {
    local_only: true,
    store: 'local_sqlite',
    data_source_status: withItems ? 'ok' : 'missing',
    as_of: '2026-11-05',
    price_source: 'daily_k',
    position_count: withItems ? 1 : 0,
    valuation: {
      total_cost: withItems ? 1000 : 0,
      market_value: withItems ? 1100 : null,
      priced_cost: withItems ? 1000 : 0,
      unpriced_cost: 0,
      coverage_pct: withItems ? 100 : null,
      unrealized_pnl: withItems ? 100 : null,
      unrealized_pnl_pct: withItems ? 10 : null,
      currency: 'CNY',
    },
    items: withItems
      ? [
          {
            code: '600000',
            cost: 10,
            qty: 100,
            entry_date: '2026-11-01',
            industry: '银行',
            note: '',
            latest_price: 11,
            market_value: 1100,
            unrealized_pnl: 100,
            unrealized_pnl_pct: 10,
            name: '浦发银行',
          },
        ]
      : [],
    concentration: {
      available: withItems,
      reason: withItems ? null : '还没有持仓',
      basis: 'cost',
      top1_pct: withItems ? 100 : null,
      top3_pct: withItems ? 100 : null,
      hhi: withItems ? 1 : null,
      holdings: withItems ? [{ code: '600000', weight_pct: 100, cost: 1000 }] : [],
    },
    sector_distribution: {
      available: withItems,
      reason: withItems ? null : '还没有持仓',
      basis: 'cost',
      labeled_count: withItems ? 1 : 0,
      unlabeled_count: 0,
      industries: withItems ? [{ industry: '银行', weight_pct: 100, cost: 1000 }] : [],
    },
  }
}

function watchlistPayload(withItems: boolean) {
  return {
    local_only: true,
    store: 'local_sqlite',
    available: withItems,
    reason: withItems ? null : '本机还没有自选',
    watch_count: withItems ? 1 : 0,
    data_source_status: withItems ? 'ok' : 'missing',
    price_source: 'spot_quote',
    price_as_of: withItems ? '2026-11-05T15:00:00+08:00' : null,
    source_detail: null,
    items: withItems
      ? [
          {
            code: '600000',
            added_at: '2026-11-01T09:00:00+08:00',
            available: true,
            name: '浦发银行',
            market: 'sh',
            latest_price: 10,
            change: 0.1,
            change_pct: 1.01,
            prev_close: 9.9,
            price_as_of: '2026-11-05T15:00:00+08:00',
            price_status: 'ok',
            price_reason: null,
          },
        ]
      : [],
  }
}

function calendarPayload(withEntries: boolean) {
  return {
    payload: {
      month: '2026-11',
      window: { start: '2026-11-01', end: '2026-11-30' },
      codes: [],
      sections: [
        {
          key: 'earnings_disclosure',
          label: '财报预约披露日',
          status: withEntries ? 'sourced' : 'no_source',
          entries: withEntries
            ? [
                {
                  id: 'evt-1',
                  date: '2026-11-10',
                  code: '600000',
                  section: 'earnings_disclosure',
                  label: '预约披露三季报',
                  detail: null,
                  source: 'tickhub_cn',
                },
              ]
            : [],
          note: withEntries ? null : '上游没有预约披露时间表工具',
          data_source: withEntries ? 'tickhub_cn' : null,
          degraded: false,
          reason: withEntries ? null : '上游没有「财报预约披露时间表」工具',
          ask: null,
        },
      ],
      extracted_events: [],
      data_source_status: withEntries ? 'ok' : 'missing',
      reason: withEntries ? null : '五个小节都没有数据源',
      data_as_of: '2026-11-01',
    },
  }
}

describe('只读页进入即取数（QUOT-441）', () => {
  it('/positions：挂载后立即 GET /api/positions，不需要先跑命令', async () => {
    const calls = stubApi({ '/api/positions': positionsPayload(true) })
    renderAt('/positions', <PositionsScreen />)

    expect(await screen.findByRole('heading', { name: '我的持仓', level: 1 })).toBeTruthy()
    expect(await screen.findByText(/1 只 · 价格来源/)).toBeTruthy()
    expect(calls.filter((url) => url.startsWith('/api/positions')).length).toBeGreaterThanOrEqual(1)
    expect(document.body.textContent).not.toContain('先运行')
  })

  it('/my-stocks?tab=watch：挂载后立即 GET /api/watchlist，不需要先跑命令', async () => {
    const calls = stubApi({ '/api/watchlist': watchlistPayload(true), '/api/portfolio': { items: [] } })
    renderAt('/my-stocks?tab=watch', <MyStocksScreen />)

    expect(await screen.findByRole('heading', { name: '我的自选', level: 1 })).toBeTruthy()
    expect(await screen.findByText('600000')).toBeTruthy()
    expect(calls.filter((url) => url.startsWith('/api/watchlist')).length).toBeGreaterThanOrEqual(1)
    expect(document.body.textContent).not.toContain('先运行')
  })

  it('/calendar：挂载后立即 GET /api/events，不需要先跑命令', async () => {
    const calls = stubApi({ '/api/events': calendarPayload(true) })
    renderAt('/calendar', <CalendarScreen />)

    expect(await screen.findByRole('heading', { name: '事件日历', level: 1 })).toBeTruthy()
    expect((await screen.findAllByText(/财报预约披露日/)).length).toBeGreaterThan(0)
    expect(calls.filter((url) => url.startsWith('/api/events')).length).toBeGreaterThanOrEqual(1)
    expect(document.body.textContent).not.toContain('先运行')
  })
})

describe('只读页空态：缺什么 + 为什么 + 页内重试（QUOT-441）', () => {
  it('/positions 空态不出现终端指令，保留页内刷新', async () => {
    stubApi({ '/api/positions': positionsPayload(false) })
    renderAt('/positions', <PositionsScreen />)

    expect(await screen.findByText('还没有持仓')).toBeTruthy()
    expect(screen.getAllByRole('button', { name: '刷新' }).length).toBeGreaterThan(0)
    expect(document.body.textContent).not.toContain('先运行')
  })

  it('/my-stocks?tab=watch 空态不出现终端指令，保留页内刷新', async () => {
    stubApi({ '/api/watchlist': watchlistPayload(false), '/api/portfolio': { items: [] } })
    renderAt('/my-stocks?tab=watch', <MyStocksScreen />)

    expect(await screen.findByText('还没有自选')).toBeTruthy()
    expect(screen.getAllByRole('button', { name: '刷新' }).length).toBeGreaterThan(0)
    expect(document.body.textContent).not.toContain('先运行')
  })

  it('/calendar 空态不出现终端指令，保留页内刷新', async () => {
    stubApi({ '/api/events': calendarPayload(false) })
    renderAt('/calendar', <CalendarScreen />)

    expect(await screen.findByText('这个月没有可用事件')).toBeTruthy()
    expect(screen.getAllByRole('button', { name: '刷新' }).length).toBeGreaterThan(0)
    expect(document.body.textContent).not.toContain('先运行')
  })
})
