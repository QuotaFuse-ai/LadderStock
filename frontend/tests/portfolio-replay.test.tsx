// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MASK, setPrivacyMode } from '../src/lib/privacy'
import { PortfolioReplayTab } from '../src/screens/my-stocks/PortfolioReplayTab'

/**
 * QUOT-467 情景回放子页：默认不请求 / 一次请求 / 4 张卡与指标 / 缺数据只给原因 /
 * 路径不足 2 点不画图 / 30 分钟缓存 / 隐私只遮金额。
 */

vi.mock('../src/charts/EChart', () => ({
  EChart: ({ ariaLabel }: { ariaLabel?: string }) => <div data-testid="echart">{ariaLabel ?? 'chart'}</div>,
}))

function stubReplay(body: unknown): string[] {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input))
      return new Response(JSON.stringify(body), { status: 200 })
    }),
  )
  return calls
}

interface ScenarioSeed {
  id: string
  label: string
  unavailable?: string
  ret?: number | null
  drawdown?: number | null
  peak?: string | null
  trough?: string | null
  worstDay?: number | null
  worstDayDate?: string | null
  coverage?: number | null
  included?: number
  holdings?: unknown[]
  gaps?: unknown[]
  path?: { dates: string[]; portfolio: number[] } | null
}

function holding(over: Record<string, unknown> = {}) {
  return {
    symbol: '600000',
    name: '浦发银行',
    weight: 0.62,
    return: -0.31,
    contribution: -0.1922,
    first_date: '2015-06-12',
    last_date: '2015-08-26',
    halted_days: 0,
    ...over,
  }
}

function scenario(seed: ScenarioSeed) {
  const unavailable = seed.unavailable ?? null
  return {
    id: seed.id,
    label: seed.label,
    start: '2015-06-12',
    end: '2015-08-26',
    unavailable,
    benchmark: null,
    benchmark_error: '本机没有历史指数序列',
    portfolio: unavailable
      ? null
      : {
          return: seed.ret === undefined ? -0.3521 : seed.ret,
          max_drawdown: seed.drawdown === undefined ? -0.4312 : seed.drawdown,
          max_drawdown_peak: seed.peak === undefined ? '2015-06-12' : seed.peak,
          max_drawdown_trough: seed.trough === undefined ? '2015-08-26' : seed.trough,
          worst_day: seed.worstDay === undefined ? -0.0873 : seed.worstDay,
          worst_day_date: seed.worstDayDate === undefined ? '2015-07-27' : seed.worstDayDate,
          days: 3,
          included_value: 100000,
          value_change: -35210,
        },
    coverage: seed.coverage === undefined ? 0.62 : seed.coverage,
    included: seed.included ?? 1,
    holdings: seed.holdings ?? [holding()],
    gaps: seed.gaps ?? [],
    path:
      seed.path === undefined
        ? { dates: ['2015-06-12', '2015-06-15', '2015-08-26'], portfolio: [1, 0.98, 0.6479] }
        : seed.path,
  }
}

function payload(over: {
  scenarios?: unknown[]
  input?: number
  totalValue?: number | null
  groups?: unknown[]
} = {}) {
  return {
    scenario: 'all',
    as_of: '2026-11-05',
    groups:
      over.groups ??
      [
        {
          market: 'a_share',
          currency: 'CNY',
          label: 'A 股 · 人民币',
          total_value: over.totalValue === undefined ? 200000 : over.totalValue,
          input: over.input ?? 2,
          scenarios:
            over.scenarios ??
            [
              scenario({ id: '2015-crash', label: '2015 股灾' }),
              scenario({ id: '2018-bear', label: '2018 熊市', ret: -0.3145, drawdown: -0.3555, worstDay: -0.0711 }),
              scenario({ id: '2020-covid', label: '2020 疫情', ret: -0.1208, drawdown: -0.1602, worstDay: -0.0512 }),
              scenario({ id: '2024-liquidity', label: '2024 流动性', ret: -0.0905, drawdown: -0.1188, worstDay: -0.0411 }),
            ],
        },
      ],
    notes: ['只回放历史，不预测、不给买卖建议'],
    sources: ['daily_k'],
  }
}

/** 结果卡是按 `h3` 标题定位的 `<section>`；筛选条上也有同名文字，所以不能直接 getByText。 */
function cardOf(label: string): HTMLElement {
  const heading = screen.getAllByRole('heading', { level: 3 }).find((node) => node.textContent === label)
  if (!heading) throw new Error(`没有找到情景卡：${label}`)
  const card = heading.closest('section')
  if (!card) throw new Error(`情景卡没有 section 容器：${label}`)
  return card as HTMLElement
}

function renderTab(signature: string, holdingCount = 2) {
  return render(<PortfolioReplayTab signature={signature} holdingCount={holdingCount} />)
}

beforeEach(() => {
  window.localStorage.clear()
  setPrivacyMode(false)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.useRealTimers()
  setPrivacyMode(false)
  window.localStorage.clear()
})

describe('情景回放：什么时候请求（QUOT-467）', () => {
  it('进标签页不发请求，点「开始回放」才请求一次 /api/portfolio/replay?scenario=all', async () => {
    const calls = stubReplay(payload())
    renderTab('sig-first')

    expect(calls).toHaveLength(0)
    expect(screen.getByText(/不会自动跑/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '开始回放' }))

    await screen.findByRole('heading', { level: 3, name: '2015 股灾' })
    expect(calls).toEqual(['/api/portfolio/replay?scenario=all'])
  })

  it('loading 时说明「只读本机日线，不联网」', async () => {
    let resolve: (value: Response) => void = () => undefined
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise<Response>((done) => {
            resolve = done
          }),
      ),
    )
    renderTab('sig-loading')
    fireEvent.click(screen.getByRole('button', { name: '开始回放' }))

    expect(screen.getByText(/只读本机日线，不联网/)).toBeTruthy()

    resolve(new Response(JSON.stringify(payload()), { status: 200 }))
    await screen.findByRole('heading', { level: 3, name: '2015 股灾' })
    await waitFor(() => expect(screen.queryByText(/只读本机日线，不联网/)).toBeNull())
  })
})

describe('情景回放：结果渲染（QUOT-467）', () => {
  it('4 张情景卡，收益 / 回撤 / 最差单日 / 覆盖率与未纳入原因都在', async () => {
    const body = payload({
      scenarios: [
        scenario({
          id: '2015-crash',
          label: '2015 股灾',
          holdings: [
            holding({
              first_date: '2015-06-15',
              last_date: '2015-08-24',
              halted_days: 3,
            }),
          ],
          gaps: [{ symbol: '000001', name: '平安银行', reason: '窗口首个交易日 2015-06-12 没有收盘价' }],
        }),
        scenario({ id: '2018-bear', label: '2018 熊市', ret: -0.3145, drawdown: -0.3555, worstDay: -0.0711 }),
        scenario({ id: '2020-covid', label: '2020 疫情', ret: -0.1208, drawdown: -0.1602, worstDay: -0.0512 }),
        scenario({ id: '2024-liquidity', label: '2024 流动性', ret: -0.0905, drawdown: -0.1188, worstDay: -0.0411 }),
      ],
    })
    stubReplay(body)
    renderTab('sig-render')
    fireEvent.click(screen.getByRole('button', { name: '开始回放' }))

    await screen.findByRole('heading', { level: 3, name: '2015 股灾' })
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(4)

    const crash = cardOf('2015 股灾')
    expect(within(crash).getByText('-35.21%')).toBeTruthy()
    expect(within(crash).getByText('-43.12%')).toBeTruthy()
    expect(within(crash).getByText('2015-06-12 → 2015-08-26')).toBeTruthy()
    expect(within(crash).getByText('2015-06-15 → 2015-08-24，其间 3 个交易日没有价格')).toBeTruthy()
    expect(within(crash).getByText('-8.73%')).toBeTruthy()
    expect(within(crash).getByText('2015-07-27')).toBeTruthy()
    expect(within(crash).getByText('62%')).toBeTruthy()
    expect(within(crash).getByText('1/2 只 · 纳入持仓的当前市值占比')).toBeTruthy()
    expect(within(crash).getByText('未纳入的持仓与原因')).toBeTruthy()
    expect(within(crash).getByText(/平安银行 000001/)).toBeTruthy()

    expect(within(crash).getByTestId('echart').textContent).toContain('组合净值路径')
    expect(screen.getAllByTestId('echart')).toHaveLength(4)

    expect(within(crash).getByText('-31.00%')).toBeTruthy()
    expect(screen.getByText('按现在的持仓比例回看历史，不代表未来；不构成建议。')).toBeTruthy()
  })

  it('unavailable 的情景只给原因，不出现 0.00%，也不画图', async () => {
    stubReplay(
      payload({
        scenarios: [
          scenario({ id: '2015-crash', label: '2015 股灾' }),
          scenario({
            id: '2018-bear',
            label: '2018 熊市',
            unavailable: '本机 daily_k 在窗口内只有 1 个交易日',
          }),
        ],
      }),
    )
    renderTab('sig-unavailable')
    fireEvent.click(screen.getByRole('button', { name: '开始回放' }))

    await screen.findByRole('heading', { level: 3, name: '2018 熊市' })
    const bear = cardOf('2018 熊市')
    expect(within(bear).getByText('本机 daily_k 在窗口内只有 1 个交易日')).toBeTruthy()
    expect(within(bear).queryByText(/0\.00%/)).toBeNull()
    expect(within(bear).queryByTestId('echart')).toBeNull()
    expect(within(bear).queryByText('组合收益')).toBeNull()

    expect(screen.queryByText(/^[+-]?0\.00%$/)).toBeNull()
    expect(window.document.body.textContent?.includes('0.00%')).toBe(false)
  })

  it('路径少于 2 个点不画图，说明有效交易日不足', async () => {
    stubReplay(
      payload({
        scenarios: [
          scenario({
            id: '2015-crash',
            label: '2015 股灾',
            path: { dates: ['2015-06-12'], portfolio: [1] },
          }),
        ],
      }),
    )
    renderTab('sig-short-path')
    fireEvent.click(screen.getByRole('button', { name: '开始回放' }))

    await screen.findByRole('heading', { level: 3, name: '2015 股灾' })
    const crash = cardOf('2015 股灾')
    expect(within(crash).queryByTestId('echart')).toBeNull()
    expect(within(crash).getByText('有效交易日不足 2 天，不画净值路径。')).toBeTruthy()
  })
})

describe('情景回放：缓存与隐私（QUOT-467）', () => {
  it('30 分钟内同一份持仓再点一次命中缓存，不发第二次请求', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-11-07T10:00:00Z'))
    const calls = stubReplay(payload())

    const first = renderTab('sig-cache')
    fireEvent.click(screen.getByRole('button', { name: '开始回放' }))
    await screen.findByRole('heading', { level: 3, name: '2015 股灾' })
    first.unmount()

    vi.setSystemTime(new Date('2026-11-07T10:29:00Z'))
    renderTab('sig-cache')
    fireEvent.click(screen.getByRole('button', { name: '开始回放' }))

    await screen.findByRole('heading', { level: 3, name: '2015 股灾' })
    expect(calls).toHaveLength(1)
  })

  it('超过 30 分钟缓存失效，会重新请求', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-11-07T11:00:00Z'))
    const calls = stubReplay(payload())

    const first = renderTab('sig-expired')
    fireEvent.click(screen.getByRole('button', { name: '开始回放' }))
    await screen.findByRole('heading', { level: 3, name: '2015 股灾' })
    first.unmount()

    vi.setSystemTime(new Date('2026-11-07T11:31:00Z'))
    renderTab('sig-expired')
    fireEvent.click(screen.getByRole('button', { name: '开始回放' }))
    await screen.findByRole('heading', { level: 3, name: '2015 股灾' })

    expect(calls).toHaveLength(2)
  })

  it('隐私模式只遮金额，百分比与 — 保留', async () => {
    setPrivacyMode(true)
    stubReplay(payload({ totalValue: 123456.78 }))
    renderTab('sig-privacy')
    fireEvent.click(screen.getByRole('button', { name: '开始回放' }))

    await screen.findByRole('heading', { level: 3, name: '2015 股灾' })
    const crash = cardOf('2015 股灾')

    expect(within(crash).getAllByText(MASK).length).toBeGreaterThanOrEqual(2)
    expect(within(crash).queryByText('100,000.00')).toBeNull()
    expect(within(crash).getByText('-35.21%')).toBeTruthy()
    expect(within(crash).getByText('-43.12%')).toBeTruthy()
    expect(within(crash).getByText('62%')).toBeTruthy()
    expect(screen.queryByText('123,456.78')).toBeNull()
  })

  it('没有持仓时按钮禁用并提示先记流水', () => {
    const calls = stubReplay(payload())
    renderTab('', 0)

    const button = screen.getByRole('button', { name: '开始回放' })
    expect((button as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText('还没有可回放的持仓')).toBeTruthy()
    expect(screen.getAllByText(/先记一笔流水，有持仓后才能回放/).length).toBeGreaterThanOrEqual(1)
    expect(calls).toHaveLength(0)
  })
})
