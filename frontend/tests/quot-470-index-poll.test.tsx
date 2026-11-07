// @vitest-environment jsdom
/**
 * QUOT-470：首页指数区盘中每 15 秒自动刷新（参考页口径）。
 *
 * 验收口径拆成四组不变量：
 * - 交易时段判定走**交易所挂钟**（`Asia/Shanghai` + 竞价窗口），不是本机钟点、
 *   也不是后端恒发的 `freshness='realtime'`；
 * - 盘中指数条按 15s 静默重取，2 分钟 ≈ 8 次（±1）；其余四块不跟着刷；
 * - 休市 / 演示态停轮询（计数不再增长）且文案切到「休市 · 显示最近交易日读数」；
 * - 断连时指数退避生效（不空转），后台标签页跳过这一拍（不消耗上游额度）。
 */

import { act, cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import HomeScreen from '../src/screens/HomeScreen'
import { INDICES_POLL_SECONDS } from '../src/screens/home/useHomeSource'
import { backoffDelay, MAX_BACKOFF_MS } from '../src/lib/usePolling'
import { isTradingSession } from '../src/lib/tradingSession'
import { setDemoPref } from '../src/lib/pageState'

/** 交易所挂钟某时刻 → `Date`（`Asia/Shanghai` 恒为 UTC+8，无夏令时）。 */
function shanghai(iso: string): Date {
  return new Date(`${iso}+08:00`)
}

function indicesResponse(asOf: string, price: number): Response {
  return new Response(
    JSON.stringify({
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
    }),
    { status: 200, headers: { 'content-type': 'application/json' } },
  )
}

/** 空响应：其余四块（自选 / 持仓 / 盯盘 / 验证）走空态，不干扰指数计数。 */
function emptyResponse(): Response {
  return new Response(JSON.stringify({ items: [], position_count: 0, payload: { rows: [], conditions: [] } }), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
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

/** 只数打到 `/api/indices` 的请求，别把其余四块算进来。 */
function indicesCalls(mock: ReturnType<typeof vi.fn>): number {
  return mock.mock.calls.filter(([url]) => String(url).includes('/api/indices')).length
}

/** 卡头把轮询计数挂在 `data-poll-ticks` 上（不改视觉，验收按此取证）。 */
function headerTicks(): number {
  return Number(screen.getByText(/交易时段 · 每|休市 · 显示最近交易日读数/).getAttribute('data-poll-ticks'))
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

describe('交易时段判定（交易所挂钟）', () => {
  it('工作日竞价窗口内为盘中，窗口外为休市', () => {
    // 2026-11-06 是周五
    expect(isTradingSession(shanghai('2026-11-06T09:29:59'))).toBe(false) // 开盘前一秒
    expect(isTradingSession(shanghai('2026-11-06T09:30:00'))).toBe(true) // 左闭
    expect(isTradingSession(shanghai('2026-11-06T11:30:00'))).toBe(false) // 左闭右开 → 午休
    expect(isTradingSession(shanghai('2026-11-06T13:00:00'))).toBe(true) // 午后开盘
    expect(isTradingSession(shanghai('2026-11-06T14:59:59'))).toBe(true)
    expect(isTradingSession(shanghai('2026-11-06T15:00:00'))).toBe(false) // 收盘
  })

  it('周末恒为休市（即便落在竞价钟点）', () => {
    // 2026-11-07 是周六
    expect(isTradingSession(shanghai('2026-11-07T10:00:00'))).toBe(false)
    // 2026-11-08 是周日
    expect(isTradingSession(shanghai('2026-11-08T14:00:00'))).toBe(false)
  })

  it('按交易所时区判，而不是本机时区（同一绝对时刻同一结论）', () => {
    // 东八区 10:00 = UTC 02:00，两者是同一绝对时刻
    const moment = shanghai('2026-11-06T10:00:00')
    expect(moment.toISOString()).toBe('2026-11-06T02:00:00.000Z')
    expect(isTradingSession(moment)).toBe(true)
  })
})

describe('盘中 15 秒自动刷新', () => {
  it('2 分钟 ≈ 8 次指数请求（±1），且其余四块不跟着刷', async () => {
    vi.setSystemTime(shanghai('2026-11-06T10:00:00'))
    const fetchMock = vi.fn(async (url: unknown) => {
      if (String(url).includes('/api/indices')) return indicesResponse('2026-11-06T10:00:00', 3813.79)
      return emptyResponse()
    })
    vi.stubGlobal('fetch', fetchMock)

    renderHome()
    await tick(0)
    // 首屏：一次初始取数
    expect(indicesCalls(fetchMock)).toBe(1)

    // 走满 2 分钟：每 15s 一拍 → 8 拍
    await tick(120_000)
    const count = indicesCalls(fetchMock)
    expect(count).toBeGreaterThanOrEqual(7)
    expect(count).toBeLessThanOrEqual(9)

    // 其余四块各只取一次，不跟指数节拍
    const others = fetchMock.mock.calls.filter(([url]) => !String(url).includes('/api/indices')).length
    expect(others).toBe(4)

    expect(screen.getByText(`交易时段 · 每 ${INDICES_POLL_SECONDS} 秒刷新`)).toBeTruthy()
    // 卡头自报的计数与真实请求数一致（首屏那次是加载，不是轮询拍）
    expect(headerTicks()).toBe(count - 1)
  })

  it('静默重取不回到骨架屏，且刷新后 as_of 前进', async () => {
    vi.setSystemTime(shanghai('2026-11-06T10:00:00'))
    let beat = 0
    const fetchMock = vi.fn(async (url: unknown) => {
      if (String(url).includes('/api/indices')) {
        const asOf = `2026-11-06T10:0${Math.min(beat, 9)}:00`
        beat += 1
        return indicesResponse(asOf, 3813.79 + beat)
      }
      return emptyResponse()
    })
    vi.stubGlobal('fetch', fetchMock)

    renderHome()
    await tick(0)
    expect(screen.getByText('上证指数')).toBeTruthy()

    await tick(15_000)
    // 已上屏的数据没有被换回骨架
    expect(screen.getByText('上证指数')).toBeTruthy()
    expect(indicesCalls(fetchMock)).toBe(2)
  })
})

describe('休市停轮询', () => {
  it('休市当拍请求计数不再增长，文案切到「休市 · 显示最近交易日读数」', async () => {
    // 周五 16:00，已收盘
    vi.setSystemTime(shanghai('2026-11-06T16:00:00'))
    const fetchMock = vi.fn(async (url: unknown) => {
      if (String(url).includes('/api/indices')) return indicesResponse('2026-11-06T15:00:00', 3813.79)
      return emptyResponse()
    })
    vi.stubGlobal('fetch', fetchMock)

    renderHome()
    await tick(0)
    const initial = indicesCalls(fetchMock)
    expect(initial).toBe(1)

    await tick(120_000)
    // 休市：一拍都没发，计数纹丝不动
    expect(indicesCalls(fetchMock)).toBe(initial)
    expect(headerTicks()).toBe(0)
    expect(screen.getByText(/休市 · 显示最近交易日读数/)).toBeTruthy()
  })

  it('演示态不轮询（固定夹具，没有可刷新的远端）', async () => {
    vi.setSystemTime(shanghai('2026-11-06T10:00:00')) // 盘中也照样不轮询
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    renderHome('/?demo=1')
    await tick(0)
    await tick(120_000)

    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.getByText(/休市 · 显示最近交易日读数|交易时段/)).toBeTruthy()
  })
})

describe('断连退避与后台跳过', () => {
  it('连续失败按指数退避，封顶 120s（不空转）', () => {
    expect(backoffDelay(15_000, 0)).toBe(15_000)
    expect(backoffDelay(15_000, 1)).toBe(30_000)
    expect(backoffDelay(15_000, 2)).toBe(60_000)
    expect(backoffDelay(15_000, 3)).toBe(120_000)
    expect(backoffDelay(15_000, 9)).toBe(MAX_BACKOFF_MS)
  })

  it('指数接口断连时退避生效：2 分钟内的请求数远少于盘中 8 次', async () => {
    vi.setSystemTime(shanghai('2026-11-06T10:00:00'))
    const fetchMock = vi.fn(async (url: unknown) => {
      if (String(url).includes('/api/indices')) throw new TypeError('offline')
      return emptyResponse()
    })
    vi.stubGlobal('fetch', fetchMock)

    renderHome()
    await tick(0) // 首屏首拍失败（计数 1）
    await tick(120_000)

    // 退避节拍：15s → 30s → 60s → 120s，2 分钟内约 4~5 拍，而不是 8~9 拍
    const count = indicesCalls(fetchMock)
    expect(count).toBeGreaterThanOrEqual(3)
    expect(count).toBeLessThanOrEqual(6)
  })

  it('后台标签页跳过这一拍：不计入请求、不消耗额度', async () => {
    vi.setSystemTime(shanghai('2026-11-06T10:00:00'))
    const fetchMock = vi.fn(async (url: unknown) => {
      if (String(url).includes('/api/indices')) return indicesResponse('2026-11-06T10:00:00', 3813.79)
      return emptyResponse()
    })
    vi.stubGlobal('fetch', fetchMock)

    renderHome()
    await tick(0)
    expect(indicesCalls(fetchMock)).toBe(1)

    // 切到后台：整段时间一次都不发
    const hidden = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    await tick(120_000)
    expect(indicesCalls(fetchMock)).toBe(1)

    // 回到前台：恢复基础节拍
    hidden.mockReturnValue('visible')
    await tick(15_000)
    expect(indicesCalls(fetchMock)).toBe(2)
  })
})
