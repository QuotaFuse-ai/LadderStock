// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { parseEventStudy } from '../src/api/research'
import type {
  EventStudy,
  EventStudyEvent,
  EventStudyRequest,
  EventStudySample,
  EventStudyType,
  EventWindow,
} from '../src/api/research'
import { EventSection } from '../src/components/research/sections/EventSection'

/**
 * QUOT-451 事件回看：
 * - 七枚 chip 要分开「有数 / 0 次 / 未接入」，0 条不许写成 `—`，未接入不许写成 0 次；
 * - 口径文案必须取自接口回声（前 5 / 后 20 / ≥ 8）；
 * - 均值只认 `sample.status === 'ok'`，样本不足写「不给平均」；
 * - 沪深300 基准缺失时拒绝出平均并说明缺什么；
 * - 单事件可复算：起止交易日 + 两条序列的起止收盘 + 超额公式。
 */

const MISSING = '—'

function windowOf(overrides: Partial<EventWindow> = {}): EventWindow {
  return {
    start: '2026-09-25',
    end: '2026-10-15',
    stockStartClose: 10.1,
    stockEndClose: 11.1605,
    benchmarkStartClose: 4200,
    benchmarkEndClose: 4310.5,
    stockReturn: 0.105,
    benchmarkReturn: 0.02631,
    // 11.1605 / 10.1 - 1 - (4310.5 / 4200 - 1) = 0.07869 → +7.87%
    excessReturn: 0.07869,
    reason: null,
    ...overrides,
  }
}

const uncomputable = windowOf({
  start: null,
  end: null,
  stockStartClose: null,
  stockEndClose: null,
  benchmarkStartClose: null,
  benchmarkEndClose: null,
  stockReturn: null,
  benchmarkReturn: null,
  excessReturn: null,
  reason: '之后本地 000300 只有 1 个交易日',
})

function sampleOf(overrides: Partial<EventStudySample> = {}): EventStudySample {
  return {
    required: 8,
    windowAvailable: 0,
    status: 'insufficient_sample',
    meanStockReturn: null,
    meanBenchmarkReturn: null,
    meanExcessReturn: null,
    reason: null,
    ...overrides,
  }
}

function eventOf(date: string, detail: Record<string, unknown>, window = windowOf()): EventStudyEvent {
  return { date, detail, window }
}

function typeOf(
  dataset: string,
  label: string,
  events: EventStudyEvent[],
  overrides: Partial<EventStudyType> = {},
): EventStudyType {
  return {
    dataset,
    label,
    count: events.length,
    dates: events.map((event) => event.date),
    events,
    sample: sampleOf({ windowAvailable: events.length }),
    ...overrides,
  }
}

const holderEvents = [
  eventOf('2026-03-01', { direction: '增持', holder: '上海国际集团', change_shares_10k: 120.5 }),
  eventOf('2026-06-01', { direction: '增持', holder: '上海国际集团', change_shares_10k: 88.2 }),
  eventOf('2026-03-03', { direction: '减持', holder: '某财务公司', change_shares_10k: 40 }),
]

function dragonEvents(): EventStudyEvent[] {
  return Array.from({ length: 9 }, (_, index) =>
    eventOf(`2026-10-${String(15 - index).padStart(2, '0')}`, {
      reason: '日涨幅偏离值达 7%',
      net_amount: 12345678,
    }),
  )
}

const summaryStudy: EventStudy = {
  code: '600000',
  name: '浦发银行',
  windowBefore: 5,
  windowAfter: 20,
  benchmark: '000300',
  formula: '窗口收益 = close[end] / close[start] - 1；超额 = 个股 - 000300',
  tradingDays: 250,
  benchmarkRows: 250,
  eventTypes: [
    typeOf('earnings_preview', '业绩预告', [
      eventOf('2026-04-20', { forecast_type: '预增', indicator: '归母净利润' }),
      eventOf('2026-07-20', { forecast_type: '预增', indicator: '归母净利润' }),
      eventOf('2026-10-20', { forecast_type: '略增', indicator: '营业收入' }),
    ]),
    typeOf('holder_trades', '股东增减持', holderEvents),
    typeOf('share_buyback', '股份回购', [], { count: 0 }),
    typeOf('lockup_calendar', '限售解禁', [
      eventOf('2026-05-10', { type: '定向增发', shares_10k: 500, ratio_pct: 1.2 }),
      eventOf('2026-08-10', { type: '定向增发', shares_10k: 500, ratio_pct: 1.2 }),
      eventOf('2026-09-10', { type: '定向增发', shares_10k: 500, ratio_pct: 1.2 }),
      eventOf('2026-10-10', { type: '定向增发', shares_10k: 500, ratio_pct: 1.2 }),
    ]),
    typeOf('dragon_tiger', '龙虎榜', dragonEvents()),
  ],
  dataSourceStatus: 'ok',
  dataAsOf: '2026-11-05',
  fetchedAt: '2026-11-05T15:00:00+08:00',
}

const earningsDetail: EventStudy = {
  ...summaryStudy,
  dataSourceStatus: 'partial',
  eventTypes: [
    typeOf(
      'earnings_preview',
      '业绩预告',
      [
        eventOf('2026-10-20', { forecast_type: '略增', indicator: '营业收入' }),
        eventOf('2026-07-20', { forecast_type: '预增', indicator: '归母净利润' }),
        eventOf('2026-04-20', { forecast_type: '预增', indicator: '归母净利润' }, uncomputable),
      ],
      {
        count: 3,
        sample: sampleOf({
          windowAvailable: 2,
          reason:
            '可计算窗口 2 < 8，按契约不给均值；之后本地 000300 只有 1 个交易日（1/3 个事件）',
        }),
      },
    ),
  ],
}

const dragonDetail: EventStudy = {
  ...summaryStudy,
  eventTypes: [
    typeOf('dragon_tiger', '龙虎榜', dragonEvents(), {
      count: 9,
      sample: sampleOf({
        windowAvailable: 9,
        status: 'ok',
        meanStockReturn: 0.0521,
        meanBenchmarkReturn: 0.0089,
        meanExcessReturn: 0.0432,
      }),
    }),
  ],
}

const holderDetail: EventStudy = {
  ...summaryStudy,
  eventTypes: [
    typeOf('holder_trades', '股东增减持', holderEvents, {
      count: 3,
      sample: sampleOf({
        windowAvailable: 3,
        reason: '可计算窗口 3 < 8，按契约不给均值',
      }),
    }),
  ],
}

const benchmarkMissingStudy: EventStudy = {
  ...summaryStudy,
  tradingDays: 0,
  benchmarkRows: 0,
  dataSourceStatus: 'missing',
  dataAsOf: null,
}

const benchmarkMissingDetail: EventStudy = {
  ...benchmarkMissingStudy,
  eventTypes: [
    typeOf('lockup_calendar', '限售解禁', summaryStudy.eventTypes[3].events, {
      count: 4,
      sample: sampleOf({
        windowAvailable: 0,
        reason:
          '可计算窗口 0 < 8，按契约不给均值；本地 index_daily 没有 000300 的日线，回放不了事件窗口（4/4 个事件）',
      }),
    }),
  ],
}

afterEach(() => {
  cleanup()
})

function makeLoader(detailBySource: Record<string, EventStudy>, base: EventStudy = summaryStudy) {
  return vi.fn(async (request: EventStudyRequest): Promise<EventStudy> => {
    const source = request.sources?.[0]
    if (source) {
      const detail = detailBySource[source]
      if (!detail) throw new Error(`测试没有为 ${source} 准备明细`)
      return detail
    }
    return base
  })
}

function chip(name: string) {
  return screen.getByRole('button', { name: new RegExp(name) })
}

async function runReview(name: string) {
  fireEvent.click(chip(name))
  fireEvent.click(screen.getByRole('button', { name: /开始回看/ }))
  return screen.findByRole('table')
}

describe('parseEventStudy（QUOT-451 接口形状）', () => {
  it('解包四段信封：snake_case → camelCase，边界收盘原值不丢，data_as_of 取信封层', () => {
    const parsed = parseEventStudy({
      payload: {
        code: '600000',
        name: '浦发银行',
        window_before: 5,
        window_after: 20,
        benchmark: '000300',
        formula: '窗口收益 = close[end] / close[start] - 1；超额 = 个股 - 000300',
        trading_days: 250,
        benchmark_rows: 250,
        data_source_status: 'ok',
        event_types: [
          {
            dataset: 'holder_trades',
            label: '股东增减持',
            count: 2,
            dates: ['2026-01-02'],
            sample: {
              required: 8,
              window_available: 2,
              status: 'insufficient_sample',
              reason: '可计算窗口 2 < 8',
            },
            events: [
              {
                date: '2026-01-02',
                detail: { direction: '增持' },
                window: {
                  start: '2025-12-25',
                  end: '2026-02-03',
                  stock_start_close: 10,
                  stock_end_close: 11,
                  benchmark_start_close: 4000,
                  benchmark_end_close: 4080,
                  stock_return: 0.1,
                  benchmark_return: 0.02,
                  excess_return: 0.08,
                  reason: null,
                },
              },
            ],
          },
        ],
      },
      data_source_status: 'ok',
      data_as_of: '2026-11-05',
      meta: { fetched_at: '2026-11-05T15:00:00+08:00' },
    })

    expect(parsed.code).toBe('600000')
    expect(parsed.windowBefore).toBe(5)
    expect(parsed.windowAfter).toBe(20)
    expect(parsed.dataAsOf).toBe('2026-11-05')
    expect(parsed.fetchedAt).toBe('2026-11-05T15:00:00+08:00')
    const window = parsed.eventTypes[0].events[0].window
    expect(window.stockStartClose).toBe(10)
    expect(window.stockEndClose).toBe(11)
    expect(window.benchmarkStartClose).toBe(4000)
    expect(window.benchmarkEndClose).toBe(4080)
    expect(window.excessReturn).toBe(0.08)
    expect(parsed.eventTypes[0].sample.windowAvailable).toBe(2)
  })

  it('缺少 event_types 直接解析失败，不猜七枚 chip', () => {
    expect(() => parseEventStudy({ payload: { code: '600000' } })).toThrow(/event_types/)
  })
})

describe('事件回看卡片（QUOT-451）', () => {
  it('七枚 chip 分开显示「有数 / 0 次 / 未接入」，口径取自接口回声', async () => {
    const loader = makeLoader({})
    render(<EventSection code="600000" date="2026-11-05" loader={loader} />)

    expect(
      await screen.findByText(
        /事件前 5 个交易日到后 20 个交易日\s*相对沪深300 的累计超额收益，次数 ≥ 8 才给平均/,
      ),
    ).toBeTruthy()
    expect(loader).toHaveBeenCalledWith({ symbol: '600000' }, expect.anything())

    const expected: Array<[string, string]> = [
      ['业绩预告', '3 次'],
      ['股东增持', '2 次'],
      ['股东减持', '1 次'],
      ['回购方案', '0 次'],
      ['机构调研', '未接入'],
      ['龙虎榜', '9 次'],
      ['限售解禁', '4 次'],
    ]
    for (const [name, text] of expected) {
      const button = chip(name)
      expect(button.textContent).toContain(name)
      expect(button.textContent).toContain(text)
      expect(button.textContent).not.toContain(MISSING)
    }

    expect(chip('业绩预告').getAttribute('aria-pressed')).toBe('true')
    expect(chip('龙虎榜').getAttribute('aria-pressed')).toBe('false')
    expect(chip('机构调研').hasAttribute('disabled')).toBe(true)
    // 0 条是真实读数，不能被说成「未接入」
    expect(chip('回购方案').textContent).not.toContain('未接入')
  })

  it('0 次 chip 给空态并禁用回看，不把 0 写成 —', async () => {
    const loader = makeLoader({})
    render(<EventSection code="600000" date="2026-11-05" loader={loader} />)
    await screen.findByText(/事件前 5 个交易日/)

    fireEvent.click(chip('回购方案'))
    expect(await screen.findByText('回购方案：0 次')).toBeTruthy()
    expect(screen.getByRole('button', { name: /开始回看/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /开始回看/ }).hasAttribute('disabled')).toBe(true)
    expect(loader).toHaveBeenCalledTimes(1)
  })

  it('开始回看只取该类型：逐次给窗口收益 / 基准 / 超额，均值用接口现算', async () => {
    const loader = makeLoader({ dragon_tiger: dragonDetail })
    render(<EventSection code="600000" date="2026-11-05" loader={loader} />)
    await screen.findByText(/事件前 5 个交易日/)

    const table = await runReview('龙虎榜')
    await waitFor(() =>
      expect(loader).toHaveBeenCalledWith({ symbol: '600000', sources: ['dragon_tiger'] }, expect.anything()),
    )

    expect(within(table).getByText('上榜日')).toBeTruthy()
    expect(within(table).getByText('窗口收益')).toBeTruthy()
    expect(within(table).getByText('基准收益')).toBeTruthy()
    expect(within(table).getByText('超额')).toBeTruthy()
    expect(within(table).getAllByText('2026-10-15').length).toBeGreaterThan(0)
    expect(within(table).getAllByText('+10.50%').length).toBe(9)
    expect(within(table).getAllByText('+2.63%').length).toBe(9)
    expect(within(table).getAllByText('+7.87%').length).toBe(9)

    expect(screen.getByText(/平均（接口现算，样本 9 次）/)).toBeTruthy()
    expect(screen.getByText('+5.21%')).toBeTruthy()
    expect(screen.getByText('+0.89%')).toBeTruthy()
    expect(screen.getByText('+4.32%')).toBeTruthy()
  })

  it('展开单个事件：起止交易日 + 两条序列的起止收盘 + 超额公式可手算', async () => {
    const loader = makeLoader({ dragon_tiger: dragonDetail })
    render(<EventSection code="600000" date="2026-11-05" loader={loader} />)
    await screen.findByText(/事件前 5 个交易日/)

    const table = await runReview('龙虎榜')
    const expanders = within(table).getAllByRole('button', { name: '展开复算明细' })
    fireEvent.click(expanders[0])

    expect(await screen.findByText(/起 2026-09-25 收 10\.1/)).toBeTruthy()
    expect(screen.getByText(/止 2026-10-15 收 11\.1605/)).toBeTruthy()
    expect(screen.getByText(/沪深300 起 4200 → 止 4310\.5/)).toBeTruthy()
    // 11.1605 / 10.1 - 1 = 10.50%；4310.5 / 4200 - 1 = 2.63%；超额 7.87%
    expect(screen.getByText(/个股 = 11\.1605 \/ 10\.1 − 1 =/)).toBeTruthy()
    expect(screen.getByText(/基准 = 4310\.5 \/ 4200 − 1 =/)).toBeTruthy()
    expect(screen.getByText(/超额 = 个股 − 基准 =/)).toBeTruthy()
  })

  it('窗口不可算：三列都是 —，写明原因，绝不落成 0%', async () => {
    const loader = makeLoader({ earnings_preview: earningsDetail })
    render(<EventSection code="600000" date="2026-11-05" loader={loader} />)
    await screen.findByText(/事件前 5 个交易日/)

    const table = await runReview('业绩预告')
    expect(within(table).getAllByText(MISSING).length).toBe(3)
    expect(within(table).queryByText('+0.00%')).toBeNull()

    const expanders = within(table).getAllByRole('button', { name: '展开复算明细' })
    fireEvent.click(expanders[2])
    expect(
      await screen.findByText('窗口未算出来：之后本地 000300 只有 1 个交易日'),
    ).toBeTruthy()
  })

  it('样本不足：明说「不给平均」并带出最常见不算原因，页面上没有均值', async () => {
    const loader = makeLoader({ earnings_preview: earningsDetail })
    render(<EventSection code="600000" date="2026-11-05" loader={loader} />)
    await screen.findByText(/事件前 5 个交易日/)

    await runReview('业绩预告')

    expect(screen.getByText('样本不足 2 次，不给平均。')).toBeTruthy()
    expect(screen.getByText(/可计算窗口 2 < 8，按契约不给均值/)).toBeTruthy()
    expect(screen.queryByText(/平均（接口现算/)).toBeNull()
  })

  it('沪深300 基准缺失：拒绝出平均并说明缺什么（不是 0 次、也不是 0%）', async () => {
    const loader = makeLoader(
      { lockup_calendar: benchmarkMissingDetail },
      benchmarkMissingStudy,
    )
    render(<EventSection code="600000" date="2026-11-05" loader={loader} />)
    await screen.findByText(/事件前 5 个交易日/)
    expect(screen.getByText('基准缺失')).toBeTruthy()

    await runReview('限售解禁')

    expect(screen.getByText('沪深300 基准缺失，拒绝出平均。')).toBeTruthy()
    expect(
      screen.getByText(/本地 index_daily 没有可用的 000300 交易日历，窗口两端对不齐/),
    ).toBeTruthy()
    expect(screen.getByText(/回放不了事件窗口/)).toBeTruthy()
    expect(screen.queryByText(/平均（接口现算/)).toBeNull()
    expect(screen.queryByText(/\+0\.00%/)).toBeNull()
    expect(screen.queryByText(/0\.00%/)).toBeNull()
  })

  it('增持 / 减持拆 chip：只展示该方向事件，均值仍按接口合计口径说明', async () => {
    const loader = makeLoader({ holder_trades: holderDetail })
    render(<EventSection code="600000" date="2026-11-05" loader={loader} />)
    await screen.findByText(/事件前 5 个交易日/)

    const table = await runReview('股东增持')
    await waitFor(() =>
      expect(loader).toHaveBeenCalledWith({ symbol: '600000', sources: ['holder_trades'] }, expect.anything()),
    )

    expect(within(table).getByText('2026-03-01')).toBeTruthy()
    expect(within(table).getByText('2026-06-01')).toBeTruthy()
    expect(within(table).queryByText('2026-03-03')).toBeNull()
    expect(screen.getByText(/接口按「股东增减持」合计给样本（3 次可算）/)).toBeTruthy()
    expect(screen.queryByText(/平均（接口现算/)).toBeNull()
  })
})
