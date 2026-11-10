// @vitest-environment jsdom
/**
 * QUOT-521：首页卡头常显 ⟳（产品回退 QUOT-457/458 的「只留顶部一条刷新」）。
 *
 * 验收拆成四组不变量：
 * - A/C/D/E/F 五张取数卡的卡头都有 ⟳，且无障碍名说清刷的是哪一块；
 * - 点 ⟳ 只重取该块自己的端点（不顺手把五路都刷一遍）；
 * - 休市停 15s 轮询，但手动 ⟳ 照常生效并让读数前进；
 * - 盘中节拍文案与真实 `usePolling` 同源（同一个 `INDICES_POLL_SECONDS`），
 *   手动插一拍不改变自动节拍，且静默重取不把已上屏的数据换回骨架。
 */

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import HomeScreen from '../src/screens/HomeScreen'
import { INDICES_POLL_SECONDS } from '../src/screens/home/useHomeSource'
import { setDemoPref } from '../src/lib/pageState'

function shanghai(iso: string): Date {
  return new Date(`${iso}+08:00`)
}

/** 五条端点的路径判据：只数各自那一块，别把别的块算进来。 */
const PATHS = {
  indices: '/api/indices',
  watchlist: '/api/watchlist',
  positions: '/api/positions',
  watch: '/api/watch/snapshot',
  validation: '/api/validation',
} as const

function indicesPayload(asOf: string, price = 3813.79) {
  return {
    domain: 'sentiment',
    topic: 'indices',
    data_as_of: asOf,
    fetched_at: asOf,
    payload: {
      available: true,
      reason: null,
      footnote: '快照，不是逐笔实时',
      price_source: 'index_quote',
      datasets: ['index_quote'],
      data_source_status: 'ok',
      cells: [
        {
          code: '000001.SH',
          market: 'SH',
          name: '上证指数',
          available: true,
          price,
          change: 1.2,
          change_pct: 0.03,
          prev_close: price - 1.2,
          as_of: asOf,
          reason: null,
        },
      ],
    },
  }
}

function watchlistPayload() {
  return {
    local_only: true,
    store: 'local_sqlite',
    available: true,
    reason: null,
    watch_count: 1,
    data_source_status: 'ok',
    price_source: 'spot_quote',
    price_as_of: '2026-11-06T15:00:00+08:00',
    source_detail: null,
    items: [
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
        price_as_of: '2026-11-06T15:00:00+08:00',
        price_status: 'ok',
        price_reason: null,
      },
    ],
  }
}

function positionsPayload() {
  return {
    local_only: true,
    store: 'local_sqlite',
    data_source_status: 'ok',
    as_of: '2026-11-06',
    price_source: 'daily_k',
    position_count: 1,
    valuation: {
      total_cost: 1000,
      market_value: 1100,
      priced_cost: 1000,
      unpriced_cost: 0,
      coverage_pct: 100,
      unrealized_pnl: 100,
      unrealized_pnl_pct: 10,
      daily_pnl: 20,
      daily_pnl_status: 'ok',
      currency: 'CNY',
      daily_pnl_missing: [],
    },
    items: [
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
        daily_pnl: 20,
        name: '浦发银行',
      },
    ],
    concentration: {
      available: true,
      reason: null,
      basis: 'cost',
      top1_pct: 100,
      top3_pct: 100,
      hhi: 1,
      holdings: [{ code: '600000', weight_pct: 100, cost: 1000 }],
    },
    sector_distribution: {
      available: true,
      reason: null,
      basis: 'cost',
      labeled_count: 1,
      unlabeled_count: 0,
      industries: [{ industry: '银行', weight_pct: 100, cost: 1000 }],
    },
  }
}

function envelope<T>(topic: string, payload: T) {
  return {
    domain: 'market',
    topic,
    event_id: null,
    data_as_of: '2026-11-06T15:00:00+08:00',
    payload,
    source: 'ladderstock',
    source_detail: '本机快照',
    fetched_at: '2026-11-06T15:00:00+08:00',
    freshness: 'archive',
    confidence: null,
    source_status: 'ok',
  }
}

function watchSnapshotPayload() {
  return envelope('watch', {
    trade_date: '2026-11-06',
    rows: [{ code: '600000', name: '浦发银行', limit_up_count: 0, change_pct: 2.5, status: 'ok' }],
  })
}

/** 一条待验证条件：E 块只在有待办时渲染，这样 ⟳ 才能被验到。 */
function validationPayload() {
  return envelope('validation', {
    trade_date: '2026-11-06',
    conditions: [{ code: '600000', trigger: '收盘价站上 10 元' }],
    reconcile: null,
    data_source_status: 'ok',
  })
}

function routeResponse(url: string, asOf = '2026-11-06T15:00:00+08:00', price = 3813.79): Response {
  const body = url.includes(PATHS.indices)
    ? indicesPayload(asOf, price)
    : url.includes(PATHS.watchlist)
      ? watchlistPayload()
      : url.includes(PATHS.positions)
        ? positionsPayload()
        : url.includes(PATHS.watch)
          ? watchSnapshotPayload()
          : url.includes(PATHS.validation)
            ? validationPayload()
            : null
  if (body === null) throw new Error(`unexpected fetch: ${url}`)
  return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
}

function stubApi(asOf = '2026-11-06T15:00:00+08:00') {
  const fetchMock = vi.fn(async (input: unknown) => routeResponse(String(input), asOf))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function calls(mock: ReturnType<typeof vi.fn>, path: string): number {
  return mock.mock.calls.filter(([url]) => String(url).includes(path)).length
}

function renderHome(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <HomeScreen />
    </MemoryRouter>,
  )
}

async function tick(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

function refreshButtons(): Record<string, HTMLButtonElement> {
  return {
    indices: screen.getByRole('button', { name: '刷新大盘与全球指数' }),
    watchlist: screen.getByRole('button', { name: '刷新我的自选' }),
    positions: screen.getByRole('button', { name: '刷新我的持仓' }),
    todo: screen.getByRole('button', { name: '刷新待办' }),
    movers: screen.getByRole('button', { name: '刷新最近异动' }),
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  setDemoPref(false)
  window.localStorage.clear()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  setDemoPref(false)
  window.localStorage.clear()
})

describe('卡头常显 ⟳', () => {
  it('A/C/D/E/F 五张取数卡各有一颗，无障碍名指向自己的块', async () => {
    vi.setSystemTime(shanghai('2026-11-06T10:00:00'))
    stubApi()

    renderHome()
    await tick(0)
    expect(screen.getByText('上证指数')).toBeTruthy()

    const buttons = refreshButtons()
    for (const button of Object.values(buttons)) {
      expect(button.disabled).toBe(false)
      expect(button.getAttribute('title')).toBeTruthy()
    }
  })

  it('点 A 的 ⟳ 只重取指数块，其余四路不跟着刷', async () => {
    vi.setSystemTime(shanghai('2026-11-06T10:00:00'))
    const fetchMock = stubApi()

    renderHome()
    await tick(0)
    // 首屏五路各一次
    expect(calls(fetchMock, PATHS.indices)).toBe(1)
    expect(calls(fetchMock, PATHS.watchlist)).toBe(1)
    expect(calls(fetchMock, PATHS.positions)).toBe(1)
    expect(calls(fetchMock, PATHS.watch)).toBe(1)
    expect(calls(fetchMock, PATHS.validation)).toBe(1)

    fireEvent.click(refreshButtons().indices)
    await tick(0)

    expect(calls(fetchMock, PATHS.indices)).toBe(2)
    expect(calls(fetchMock, PATHS.watchlist)).toBe(1)
    expect(calls(fetchMock, PATHS.positions)).toBe(1)
    expect(calls(fetchMock, PATHS.watch)).toBe(1)
    expect(calls(fetchMock, PATHS.validation)).toBe(1)
  })

  it('自选 / 持仓 / 待办 / 最近异动的 ⟳ 各刷自己的端点', async () => {
    vi.setSystemTime(shanghai('2026-11-06T10:00:00'))
    const fetchMock = stubApi()

    renderHome()
    await tick(0)

    const buttons = refreshButtons()
    fireEvent.click(buttons.watchlist)
    await tick(0)
    expect(calls(fetchMock, PATHS.watchlist)).toBe(2)
    expect(calls(fetchMock, PATHS.positions)).toBe(1)

    fireEvent.click(buttons.positions)
    await tick(0)
    expect(calls(fetchMock, PATHS.positions)).toBe(2)
    expect(calls(fetchMock, PATHS.watch)).toBe(1)

    fireEvent.click(buttons.todo)
    await tick(0)
    expect(calls(fetchMock, PATHS.validation)).toBe(2)
    expect(calls(fetchMock, PATHS.watch)).toBe(1)

    fireEvent.click(buttons.movers)
    await tick(0)
    expect(calls(fetchMock, PATHS.watch)).toBe(2)
  })
})

describe('休市手动刷', () => {
  it('休市停 15s 轮询，但 ⟳ 照常取数且读数前进', async () => {
    // 周五 16:00，已收盘
    vi.setSystemTime(shanghai('2026-11-06T16:00:00'))
    let beat = 0
    const fetchMock = vi.fn(async (input: unknown) => {
      const url = String(input)
      if (url.includes(PATHS.indices)) {
        const asOf = `2026-11-06T15:0${Math.min(beat, 9)}:00+08:00`
        beat += 1
        return routeResponse(url, asOf, 3813.79 + beat)
      }
      return routeResponse(url)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderHome()
    await tick(0)
    expect(calls(fetchMock, PATHS.indices)).toBe(1)
    expect(screen.getByText(/休市 · 显示最近交易日读数/)).toBeTruthy()

    await tick(120_000)
    // 休市：自动轮询一拍都不发
    expect(calls(fetchMock, PATHS.indices)).toBe(1)

    // ⟳ 不受时段门控
    fireEvent.click(refreshButtons().indices)
    await tick(0)
    expect(calls(fetchMock, PATHS.indices)).toBe(2)
    // 读数来自第二拍（价格 3813.79 + 2）
    expect(screen.getByText('3,815.79')).toBeTruthy()
  })
})

describe('盘中节拍与手动 ⟳ 同源', () => {
  it('节拍文案读 INDICES_POLL_SECONDS，手动插一拍不改变 15s 自动节拍', async () => {
    vi.setSystemTime(shanghai('2026-11-06T10:00:00'))
    const fetchMock = stubApi()

    renderHome()
    await tick(0)
    expect(screen.getByText(`交易时段 · 每 ${INDICES_POLL_SECONDS} 秒刷新`)).toBeTruthy()
    expect(INDICES_POLL_SECONDS).toBe(15)

    // 手动先插一拍：2 次
    fireEvent.click(refreshButtons().indices)
    await tick(0)
    expect(calls(fetchMock, PATHS.indices)).toBe(2)

    // 自动节拍仍在：再过 15s 又有一拍
    await tick(INDICES_POLL_SECONDS * 1000)
    expect(calls(fetchMock, PATHS.indices)).toBe(3)
  })

  it('静默重取不把已上屏的数据换回骨架', async () => {
    vi.setSystemTime(shanghai('2026-11-06T10:00:00'))
    let release: (() => void) | null = null
    let beat = 0
    const fetchMock = vi.fn(async (input: unknown) => {
      const url = String(input)
      if (!url.includes(PATHS.indices)) return routeResponse(url)
      beat += 1
      if (beat === 2) {
        // 第二拍（手动 ⟳）挂在半空，观察期间页面不该回到骨架
        await new Promise<void>((resolve) => {
          release = resolve
        })
      }
      return routeResponse(url, '2026-11-06T10:00:00+08:00', 3813.79 + beat)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderHome()
    await tick(0)
    expect(screen.getByText('上证指数')).toBeTruthy()

    fireEvent.click(refreshButtons().indices)
    await tick(0)
    expect(screen.getByText('上证指数')).toBeTruthy()

    await act(async () => {
      release?.()
    })
    expect(screen.getByText('上证指数')).toBeTruthy()
  })
})
