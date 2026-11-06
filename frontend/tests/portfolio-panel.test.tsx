// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MASK, setPrivacyMode } from '../src/lib/privacy'
import { PortfolioPanel } from '../src/screens/my-stocks/PortfolioPanel'

/**
 * QUOT-434 持仓页：表单字段显隐 / 校验 / 隐私遮罩 / 表头排序 /
 * 迁移卡片的接口路径与二次确认 / 收益子页空态 / 记一笔与删流水。
 */

vi.mock('../src/charts/EChart', () => ({
  EChart: ({ ariaLabel }: { ariaLabel?: string }) => <div data-testid="echart">{ariaLabel ?? 'chart'}</div>,
}))

interface Call {
  url: string
  method: string
  body: unknown
}

type Handler = (call: Call) => unknown

function stubApi(handlers: Record<string, Handler>): Call[] {
  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const call: Call = {
        url,
        method: init?.method ?? 'GET',
        body: init?.body ? (JSON.parse(String(init.body)) as unknown) : undefined,
      }
      calls.push(call)
      const prefix = Object.keys(handlers).find((key) => url.startsWith(key))
      if (!prefix) throw new Error(`unexpected fetch: ${url}`)
      return new Response(JSON.stringify(handlers[prefix](call)), { status: 200 })
    }),
  )
  return calls
}

interface ItemSeed {
  symbol: string
  name?: string
  shares?: number
  unit_cost?: number
  latest_price?: number | null
  market_value?: number | null
  pnl?: number | null
  pnl_pct?: number | null
  realized_pnl?: number
  dividends?: number
}

function item(seed: ItemSeed) {
  return {
    symbol: seed.symbol,
    name: seed.name ?? '',
    shares: seed.shares ?? 100,
    unit_cost: seed.unit_cost ?? 10,
    cost_total: (seed.shares ?? 100) * (seed.unit_cost ?? 10),
    opened_at: '2026-01-05',
    lots: [],
    realized_pnl: seed.realized_pnl ?? 0,
    dividends: seed.dividends ?? 0,
    fees: 0,
    trade_count: 1,
    latest_price: seed.latest_price === undefined ? 12.5 : seed.latest_price,
    change: 0.1,
    change_pct: 0.81,
    market_value: seed.market_value === undefined ? 1250 : seed.market_value,
    pnl: seed.pnl === undefined ? 250 : seed.pnl,
    pnl_pct: seed.pnl_pct === undefined ? 25 : seed.pnl_pct,
    price_reason: null,
  }
}

function portfolioPayload(over: Record<string, unknown> = {}) {
  return {
    local_only: true,
    store: 'local_sqlite',
    method: 'average',
    method_label: '移动加权平均',
    methods: [
      { id: 'average', label: '移动加权平均' },
      { id: 'fifo', label: '先进先出' },
    ],
    today: '2026-11-05',
    price_lookup: { dataset: 'daily_k', date: '2026-11-04', reason: null },
    items: [],
    closed: [],
    issues: [],
    ledger: [],
    legacy: [],
    migration_pending: 0,
    migration: { backup: null, migrated: [], removed_stale: [], failed: [], error: null },
    totals_cny: {
      currency: 'CNY',
      available: false,
      market_value: null,
      cost: null,
      pnl: null,
      pnl_pct: null,
      priced_count: 0,
      unpriced_count: 0,
      reason: '本机还没有持仓流水',
    },
    ...over,
  }
}

const EMPTY_PERF = {
  as_of: null,
  groups: [],
  skipped: [],
  gaps: [],
  sources: [],
  notes: ['本机还没有交易流水，记一笔之后会自动计算收益率'],
}

function renderPanel() {
  return render(
    <MemoryRouter initialEntries={['/my-stocks?tab=hold']}>
      <Routes>
        <Route path="/my-stocks" element={<PortfolioPanel />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  window.localStorage.clear()
  setPrivacyMode(false)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  setPrivacyMode(false)
  window.localStorage.clear()
})

describe('记一笔：类型切换字段显隐（QUOT-434）', () => {
  it('买入显示 数量 / 成交价 / 费用；分红只显示到账金额；拆股只显示比例', async () => {
    stubApi({
      '/api/portfolio/perf': () => EMPTY_PERF,
      '/api/portfolio': () => portfolioPayload(),
    })
    renderPanel()
    await screen.findByText('还没有持仓')

    expect(screen.getByLabelText(/^数量（股）/)).toBeTruthy()
    expect(screen.getByLabelText(/^成交价/)).toBeTruthy()
    expect(screen.getByLabelText(/^费用（选填）/)).toBeTruthy()

    fireEvent.click(screen.getByRole('radio', { name: '现金分红' }))
    expect(screen.getByLabelText(/^到账金额/)).toBeTruthy()
    expect(screen.queryByLabelText(/^数量（股）/)).toBeNull()
    expect(screen.queryByLabelText(/^成交价/)).toBeNull()

    fireEvent.click(screen.getByRole('radio', { name: '送转 / 拆股' }))
    expect(screen.getByLabelText(/^每 1 股变成几股/)).toBeTruthy()
    expect(screen.queryByLabelText(/^到账金额/)).toBeNull()

    fireEvent.click(screen.getByRole('radio', { name: '期初持仓' }))
    expect(screen.getByLabelText(/^数量（股）/)).toBeTruthy()
    expect(screen.getByLabelText(/^每股成本/)).toBeTruthy()
    expect(screen.queryByLabelText(/^费用（选填）/)).toBeNull()
  })
})

describe('记一笔：校验与后端同口径（QUOT-434）', () => {
  it('代码 / 日期 / 数量不合法时给出原因，且不写库', async () => {
    const calls = stubApi({
      '/api/portfolio/perf': () => EMPTY_PERF,
      '/api/portfolio': () => portfolioPayload(),
    })
    renderPanel()
    await screen.findByText('还没有持仓')

    fireEvent.click(screen.getByRole('button', { name: '记一笔' }))
    expect(await screen.findByText('代码要 6 位数字（可带 sh / sz / bj 前缀）')).toBeTruthy()

    fireEvent.change(screen.getByLabelText(/^代码/), { target: { value: '900901' } })
    fireEvent.change(screen.getByLabelText(/^日期/), { target: { value: '2026-11-05' } })
    fireEvent.click(screen.getByRole('button', { name: '记一笔' }))
    expect(await screen.findByText(/B 股（900、200 开头）暂不支持/)).toBeTruthy()

    fireEvent.change(screen.getByLabelText(/^代码/), { target: { value: '600519' } })
    fireEvent.change(screen.getByLabelText(/^日期/), { target: { value: '2026-02-30' } })
    fireEvent.click(screen.getByRole('button', { name: '记一笔' }))
    expect(await screen.findByText('日期要是真实存在的日期，格式 YYYY-MM-DD')).toBeTruthy()

    fireEvent.change(screen.getByLabelText(/^日期/), { target: { value: '2026-11-05' } })
    fireEvent.change(screen.getByLabelText(/^数量（股）/), { target: { value: '0' } })
    fireEvent.change(screen.getByLabelText(/^成交价/), { target: { value: '12.5' } })
    fireEvent.click(screen.getByRole('button', { name: '记一笔' }))
    expect(await screen.findByText('买入要写大于 0 的数量')).toBeTruthy()

    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0)
  })

  it('填对后 POST /api/portfolio/ledger 用写接口字段名 trade_date，并重取视图', async () => {
    const calls = stubApi({
      '/api/portfolio/perf': () => EMPTY_PERF,
      '/api/portfolio': () => portfolioPayload(),
    })
    renderPanel()
    await screen.findByText('还没有持仓')

    fireEvent.change(screen.getByLabelText(/^代码/), { target: { value: 'sh600519' } })
    fireEvent.change(screen.getByLabelText(/^日期/), { target: { value: '2026-11-05' } })
    fireEvent.change(screen.getByLabelText(/^数量（股）/), { target: { value: '100' } })
    fireEvent.change(screen.getByLabelText(/^成交价/), { target: { value: '12.5' } })
    fireEvent.click(screen.getByRole('button', { name: '记一笔' }))

    await waitFor(() => expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1))
    const post = calls.find((call) => call.method === 'POST')
    expect(post?.url).toBe('/api/portfolio/ledger')
    expect(post?.body).toEqual({
      symbol: '600519',
      type: 'buy',
      trade_date: '2026-11-05',
      shares: 100,
      price: 12.5,
    })
    expect(await screen.findByText(/已记一笔买入/)).toBeTruthy()
    await waitFor(() =>
      expect(calls.filter((call) => call.method === 'GET' && call.url.startsWith('/api/portfolio?'))).toHaveLength(2),
    )
  })
})

describe('隐私遮罩复用顶栏开关（QUOT-434）', () => {
  it('遮住 数量 / 成本 / 市值 / 盈亏 / 已实现 / 分红，保留 现价 与 盈亏%，`—` 不遮', async () => {
    stubApi({
      '/api/portfolio/perf': () => EMPTY_PERF,
      '/api/portfolio': () =>
        portfolioPayload({
          items: [
            item({
              symbol: '600519',
              name: '贵州茅台',
              shares: 100,
              unit_cost: 12.5,
              latest_price: 15,
              market_value: 1500,
              pnl: 250,
              pnl_pct: 25,
              realized_pnl: 10,
              dividends: 5,
            }),
            item({ symbol: '000858', name: '五粮液', latest_price: null, market_value: null, pnl: null, pnl_pct: null }),
          ],
        }),
    })
    renderPanel()
    const name = await screen.findByText('贵州茅台')
    const row = name.closest('tr') as HTMLElement
    expect(within(row).queryAllByText(MASK)).toHaveLength(0)
    expect(within(row).getByText('15')).toBeTruthy()
    expect(within(row).getByText('+25.00%')).toBeTruthy()

    setPrivacyMode(true)
    await waitFor(() => expect(within(row).getAllByText(MASK)).toHaveLength(6))
    // 现价与盈亏% 不遮
    expect(within(row).getByText('15')).toBeTruthy()
    expect(within(row).getByText('+25.00%')).toBeTruthy()
    // 缺失值照旧 `—`
    const emptyRow = screen.getByText('五粮液').closest('tr') as HTMLElement
    expect(within(emptyRow).getAllByText('—').length).toBeGreaterThan(0)

    setPrivacyMode(false)
    await waitFor(() => expect(within(row).getByText('100')).toBeTruthy())
  })
})

describe('收益子页的遮罩口径（QUOT-434）', () => {
  it('金额遮成掩码，收益率百分比保留', async () => {
    stubApi({
      '/api/portfolio/perf': () => ({
        as_of: '2026-11-06',
        skipped: [],
        gaps: [],
        sources: ['daily_k'],
        notes: ['只算 A 股 / 人民币'],
        groups: [
          {
            market: 'A股',
            currency: 'CNY',
            label: '人民币 · A 股',
            ok: true,
            reason: '',
            start: '2026-11-05',
            end: '2026-11-06',
            span_days: 1,
            twr: 0.0095,
            twr_annualized: null,
            xirr: null,
            mwr_period: 0.0176,
            invested: 1250,
            proceeds: 0,
            dividends: 0,
            fees: 0,
            end_value: 1272,
            gain: 22,
            excluded: [],
            notes: ['时间加权剔除申赎现金流'],
            warnings: [],
            series: null,
          },
        ],
      }),
      '/api/portfolio': () => portfolioPayload(),
    })
    setPrivacyMode(true)
    renderPanel()

    expect(await screen.findByText('收益率')).toBeTruthy()
    expect(await screen.findByText('+0.95%')).toBeTruthy()
    expect(screen.getByText('+1.76%')).toBeTruthy()
    expect(screen.getAllByText(MASK).length).toBeGreaterThan(0)
    expect(screen.queryByText('1,272.00')).toBeNull()
  })
})

describe('持仓表：表头排序（QUOT-434）', () => {
  it('点「持仓市值」升序，再点降序；隐私模式下遮住的列不给排序', async () => {
    stubApi({
      '/api/portfolio/perf': () => EMPTY_PERF,
      '/api/portfolio': () =>
        portfolioPayload({
          items: [
            item({ symbol: '600000', name: '甲', market_value: 100 }),
            item({ symbol: '600001', name: '乙', market_value: 200 }),
          ],
        }),
    })
    renderPanel()
    await screen.findByText('甲')

    const header = screen.getByRole('columnheader', { name: '持仓市值' })
    fireEvent.click(within(header).getByRole('button'))
    const rows = () => Array.from(document.querySelectorAll('tbody tr')).map((row) => row.textContent ?? '')
    expect(rows()[0]).toContain('甲')

    fireEvent.click(within(header).getByRole('button'))
    expect(rows()[0]).toContain('乙')

    setPrivacyMode(true)
    await waitFor(() =>
      expect(within(screen.getByRole('columnheader', { name: '持仓市值' })).queryByRole('button')).toBeNull(),
    )
  })
})

describe('旧仓迁移卡片（QUOT-434）', () => {
  const legacy = [
    {
      id: 7,
      symbol: '600519',
      name: '',
      shares: 100,
      cost: 12.5,
      opened_at: '2026-01-05',
      state: 'conflict',
      why: '这个代码已经有交易流水：这条可能是重复录入',
      migrated_trade: 21,
    },
    {
      id: 8,
      symbol: '000858',
      name: '',
      shares: 0,
      cost: null,
      opened_at: '',
      state: 'skipped',
      why: '数量是 0，不算持有',
      migrated_trade: null,
    },
    {
      id: 9,
      symbol: '600036',
      name: '',
      shares: 100,
      cost: 30,
      opened_at: '',
      state: 'stale',
      why: '已经转成期初持仓流水',
      migrated_trade: 22,
    },
  ]

  it('conflict 的 adopt / dismiss 都要二次确认，确认后才发对应 POST', async () => {
    const calls = stubApi({
      '/api/portfolio/perf': () => EMPTY_PERF,
      '/api/portfolio': () =>
        portfolioPayload({
          legacy,
          migration_pending: 1,
          migration: {
            backup: 'journal.sqlite.bak-portfolio-20261105-120000',
            migrated: [],
            removed_stale: [],
            failed: [],
            error: null,
          },
        }),
    })
    renderPanel()
    await screen.findByText('要你确认的旧持有记录')

    // `stale` 不再打扰：已迁移那条不出现
    expect(screen.queryByText('已经转成期初持仓流水')).toBeNull()
    expect(screen.getByText(/journal\.sqlite\.bak-portfolio-20261105-120000/)).toBeTruthy()
    expect(screen.getByText(/旧记录只标记、不删除/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '按它现在的数替换那笔期初持仓' }))
    const adoptDialog = await screen.findByRole('dialog')
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0)
    fireEvent.click(
      within(adoptDialog).getByRole('button', { name: '按它现在的数替换那笔期初持仓' }),
    )
    await waitFor(() => expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1))
    expect(calls.find((call) => call.method === 'POST')?.url).toBe('/api/portfolio/legacy/7/adopt')

    fireEvent.click(screen.getByRole('button', { name: '保留那笔、不要这条' }))
    const dismissDialog = await screen.findByRole('dialog')
    fireEvent.click(within(dismissDialog).getByRole('button', { name: '保留那笔、不要这条' }))
    await waitFor(() => expect(calls.filter((call) => call.method === 'POST')).toHaveLength(2))
    expect(calls.filter((call) => call.method === 'POST')[1]?.url).toBe('/api/portfolio/legacy/7/dismiss')

    // skipped 只能「不要这条」
    fireEvent.click(screen.getByRole('button', { name: '不要这条' }))
    const skippedDialog = await screen.findByRole('dialog')
    fireEvent.click(within(skippedDialog).getByRole('button', { name: '不要这条' }))
    await waitFor(() => expect(calls.filter((call) => call.method === 'POST')).toHaveLength(3))
    expect(calls.filter((call) => call.method === 'POST')[2]?.url).toBe('/api/portfolio/legacy/8/dismiss')
  })
})

describe('子页（QUOT-434）', () => {
  it('收益子页空态：没有流水时不画假线', async () => {
    stubApi({
      '/api/portfolio/perf': () => EMPTY_PERF,
      '/api/portfolio': () => portfolioPayload(),
    })
    renderPanel()

    expect(await screen.findByText('还没有可算的收益率')).toBeTruthy()
    expect(
      screen.getAllByText('本机还没有交易流水，记一笔之后会自动计算收益率').length,
    ).toBeGreaterThan(0)
    expect(screen.queryByTestId('echart')).toBeNull()
  })

  it('交易流水按日期倒序，删除要二次确认后才 POST /delete', async () => {
    const calls = stubApi({
      '/api/portfolio/perf': () => EMPTY_PERF,
      '/api/portfolio': () =>
        portfolioPayload({
          ledger: [
            {
              id: 1,
              symbol: '600519',
              name: '贵州茅台',
              type: 'buy',
              date: '2026-10-01',
              shares: 100,
              price: 10,
              fee: null,
              amount: null,
              ratio: null,
              note: '',
              migrated_from: null,
              undated: false,
              created_at: '2026-10-01T10:00:00+08:00',
            },
            {
              id: 2,
              symbol: '600519',
              name: '贵州茅台',
              type: 'sell',
              date: '2026-10-09',
              shares: 50,
              price: 12,
              fee: 1,
              amount: null,
              ratio: null,
              note: '减仓',
              migrated_from: null,
              undated: false,
              created_at: '2026-10-09T10:00:00+08:00',
            },
          ],
        }),
    })
    renderPanel()
    await screen.findByText('还没有持仓')

    fireEvent.click(screen.getByRole('tab', { name: '交易流水' }))
    await screen.findByText('交易流水（2 笔）')

    const table = screen.getByText('交易流水（2 笔）').closest('table') as HTMLElement
    const rows = Array.from(table.querySelectorAll('tbody tr')).map((row) => row.textContent ?? '')
    expect(rows[0]).toContain('2026-10-09')
    expect(rows[1]).toContain('2026-10-01')

    fireEvent.click(screen.getByRole('button', { name: '删除 600519 2026-10-09 卖出' }))
    const dialog = await screen.findByRole('dialog')
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: '删除' }))
    await waitFor(() => expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1))
    expect(calls.find((call) => call.method === 'POST')?.url).toBe('/api/portfolio/ledger/2/delete')
  })
})
