// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { setDemoPref } from '../src/lib/pageState'
import MarketScreen from '../src/screens/MarketScreen'

/**
 * 市场屏演示源（QUOT-335 / UI-SPEC §5.6）三条红线：
 * 1. 演示入口（`?demo=1` 与「设置 → 虚拟数据」开关）任一条开启，三处图表都拿到数据点；
 * 2. 关闭开关后走真实通道，请求发往 `/api/*`；
 * 3. 真实请求失败（断连 / 404）时，页面上**不允许出现任何演示数据**。
 *
 * uPlot 画在 `<canvas>` 上，jsdom 没有 2D 上下文（截图取证才走真实浏览器），
 * 所以这里把两个 canvas 组件换成记录入参的替身，直接断言「图表收到了几个点」。
 * `HBarList` 是 DOM 组件，保留真实实现。
 */

interface ChartStubProps {
  points?: Array<{ label: string; value: number }>
  ariaLabel?: string
}

vi.mock('../src/charts/Chart', () => ({
  LineChart: ({ points, ariaLabel }: ChartStubProps) => (
    <div data-testid="line-chart" data-points={points?.length ?? 0} aria-label={ariaLabel} />
  ),
  BarChart: ({ points, ariaLabel }: ChartStubProps) => (
    <div data-testid="bar-chart" data-points={points?.length ?? 0} aria-label={ariaLabel} />
  ),
}))

afterEach(() => {
  cleanup()
  setDemoPref(false)
  window.localStorage.clear()
  vi.unstubAllGlobals()
})

function renderMarket(entry = '/market') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/market" element={<MarketScreen />} />
      </Routes>
    </MemoryRouter>,
  )
}

function stubOffline() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new TypeError('offline')
    }),
  )
}

/** 真实通道的最小可用响应：数值与演示基准日都不同，用来证明「页面出的是真实数据」。 */
const REAL_SENTIMENT = {
  date: '2026-06-05',
  available: true,
  reason: null,
  temperature: 41.2,
  promotion_rate: 30.5,
  seal_rate: 62.1,
  limit_up_count: 27,
  broken_count: 9,
  yesterday_limit_up_count: 40,
  promoted_count: 12,
  max_board: 3,
  gap: false,
  gap_levels: [],
  missing: [],
}

const REAL_LADDER = {
  date: '2026-06-05',
  available: true,
  reason: null,
  source_detail: 'limit_pool: duckdb（27 条）',
  height: 3,
  count: 27,
  ladder: [
    { boards: 3, count: 1, stocks: [{ code: '600001', name: '真实股份', boards: 3, first_seal_time: '093000', seal_time: '093000' }] },
    { boards: 1, count: 26, stocks: [{ code: '600002', name: '大盘股', boards: 1, first_seal_time: null, seal_time: null }] },
  ],
  gap: false,
  gap_levels: [],
  leader_candidates: [],
  unknown_board: 0,
}

function realEnvelope<T>(key: string, payload: T) {
  return {
    domain: 'sentiment',
    topic: key,
    event_id: null,
    data_as_of: '2026-06-05',
    payload: { ...payload, data_source_status: 'ok' },
    source: 'ladderstock',
    source_detail: `real: ${key}`,
    fetched_at: '2026-06-05T15:00:01+08:00',
    freshness: 'realtime',
    confidence: 'high',
    source_status: 'ok',
  }
}

const REAL_RESPONSES: Record<string, unknown> = {
  '/api/sentiment': realEnvelope('shortline_sentiment', { sentiment: REAL_SENTIMENT }),
  '/api/ladder': realEnvelope('limit_ladder', { ladder: REAL_LADDER }),
  '/api/market/history': realEnvelope('shortline_sentiment_history', {
    date: '2026-06-05',
    available: true,
    reason: null,
    window: 20,
    points: [
      { date: '2026-06-04', temperature: 38.4 },
      { date: '2026-06-05', temperature: 41.2 },
    ],
  }),
  '/api/market/theme': realEnvelope('theme_rotation', {
    date: '2026-06-05',
    available: true,
    reason: null,
    themes: [{ theme: '真实题材', strength: 66.6, limit_up_count: 3, leader: '真实股份', delta: 1.1 }],
  }),
}

function stubRealChannel() {
  const fetchMock = vi.fn(async (input: unknown) => {
    const url = String(input)
    const match = Object.keys(REAL_RESPONSES).find((path) => url.includes(path))
    if (!match) return { ok: false, status: 404, json: async () => ({ detail: 'Not Found' }) }
    return { ok: true, status: 200, json: async () => REAL_RESPONSES[match] }
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('市场屏演示源', () => {
  it('设置开关开启：三处图表都有数据点，日期落在演示基准日', async () => {
    stubOffline()
    setDemoPref(true)
    renderMarket()

    expect(await screen.findByRole('heading', { name: '市场', level: 1 })).toBeTruthy()
    expect(screen.getByText('演示数据 · 设置开关')).toBeTruthy()
    expect((screen.getByLabelText('交易日') as HTMLInputElement).value).toBe('2026-10-30')

    // CH-01：折线拿到 20 个演示交易日点
    expect(screen.getByTestId('line-chart').getAttribute('data-points')).toBe('20')
    // 明细表 20 行 + 表头
    expect(screen.getAllByRole('row').length).toBeGreaterThanOrEqual(13)

    // CH-02：连板梯队柱状图拿到 5 个板位
    fireEvent.click(screen.getByRole('radio', { name: '连板梯队' }))
    expect(screen.getByTestId('bar-chart').getAttribute('data-points')).toBe('5')

    // CH-03：题材强度横向条（真实 DOM 组件）画前 8 条，其余只进明细表
    fireEvent.click(screen.getByRole('radio', { name: '题材轮动' }))
    expect(screen.getAllByText('人工智能')).toHaveLength(2)
    expect(screen.getAllByText('光伏')).toHaveLength(2)
    expect(screen.getAllByText('地产')).toHaveLength(1)
    expect(screen.getByText('稀土')).toBeTruthy()
  })

  it('?demo=1 入口同样出演示数据，徽标标注来源是 URL', async () => {
    stubOffline()
    renderMarket('/market?demo=1')

    expect(await screen.findByText('演示数据 · ?demo=1')).toBeTruthy()
    expect(screen.getByTestId('line-chart').getAttribute('data-points')).toBe('20')
  })

  it('关闭开关：请求发往 /api/*，页面出的是真实通道数据而不是演示数据', async () => {
    const fetchMock = stubRealChannel()
    renderMarket()

    expect(await screen.findByRole('heading', { name: '市场', level: 1 })).toBeTruthy()
    expect(screen.queryByText(/演示数据/)).toBeNull()
    expect(fetchMock.mock.calls.map((call) => String(call[0]))).toEqual(
      expect.arrayContaining([
        expect.stringContaining('/api/sentiment'),
        expect.stringContaining('/api/ladder'),
        expect.stringContaining('/api/market/history'),
        expect.stringContaining('/api/market/theme'),
      ]),
    )

    // 真实序列只有 2 个点；演示源是 20 个点
    expect(screen.getByTestId('line-chart').getAttribute('data-points')).toBe('2')
    expect(screen.getByText('2026-06-04')).toBeTruthy()
    expect(screen.queryByText('比亚迪')).toBeNull()

    fireEvent.click(screen.getByRole('radio', { name: '题材轮动' }))
    expect(screen.getAllByText('真实题材').length).toBeGreaterThan(0)
    expect(screen.queryByText('人工智能')).toBeNull()
  })

  it('真实请求断连时进离线态，不回落任何演示数据', async () => {
    stubOffline()
    renderMarket()

    expect(await screen.findByText('本机服务未连接')).toBeTruthy()
    expect(screen.queryByTestId('line-chart')).toBeNull()
    expect(screen.queryByText('比亚迪')).toBeNull()
    expect(screen.queryByText('人工智能')).toBeNull()
    expect(screen.queryByText(/演示数据/)).toBeNull()
  })

  it('真实请求 404 时只出降级说明，不画演示图表', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 404, json: async () => ({ detail: 'Not Found' }) })),
    )
    renderMarket()

    expect(await screen.findByText('部分数据源不可用')).toBeTruthy()
    expect(screen.getByText('序列接口未接入（404）')).toBeTruthy()
    expect(screen.queryByTestId('line-chart')).toBeNull()
    expect(screen.queryByText('比亚迪')).toBeNull()
    expect(screen.queryByText(/演示数据/)).toBeNull()
  })
})
