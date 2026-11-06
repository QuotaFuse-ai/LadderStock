import { describe, expect, it } from 'vitest'

import { ApiError } from '../src/api'
import {
  parseEarningsBriefSummary,
  parseEventStudySummary,
  type ResearchOverview,
} from '../src/api/research'
import {
  buildAssistantContext,
  describeAssistantError,
  serializeAssistantContext,
} from '../src/components/research/sections/aiContext'

/**
 * QUOT-454 页级上下文白名单：
 * 只允许页面已展示、且非私有的字段进入上下文。下面的断言把「允许的键」钉死成
 * 一份精确集合——新增字段必须同步改测试，避免论据 / 持仓 / 令牌被顺手塞进来。
 */

const OVERVIEW: ResearchOverview = {
  code: '600000',
  name: '浦发银行',
  as_of: '2026-11-06',
  dataSourceStatus: 'ok',
  kline: {
    bars: [
      { date: '2026-11-04', open: 10.1, high: 10.3, low: 10, close: 10.2, volume: 1000 },
      { date: '2026-11-05', open: 10.2, high: 10.5, low: 10.1, close: 10.4, volume: 1200 },
      { date: '2026-11-06', open: 10.4, high: 10.6, low: 10.2, close: 10.3, volume: 900 },
    ],
    count: 3,
    requested: 120,
    reason: null,
  },
  quote: {
    quote: {
      name: '浦发银行',
      price: 10.3,
      prev_close: 10.4,
      change: -0.1,
      change_pct: -0.96,
      open: 10.4,
      high: 10.6,
      low: 10.2,
      volume: 900,
      amount: 9200,
      timestamp: '2026-11-06 15:00:00',
      source_detail: null,
    },
    reason: null,
  },
  financials: {
    financials: {
      report_name: '2026Q3',
      report_date: '2026-09-30',
      eps: 1.2,
      revenue: 120360000000,
      net_profit: 42000000000,
      roe: 9.8,
      revenue_yoy: 5.6,
      profit_yoy: 4.2,
    },
    reason: null,
  },
}

describe('财报前瞻摘要解析', () => {
  it('兼容四段信封，只保留期次 / 一致预期字段', () => {
    const summary = parseEarningsBriefSummary(
      {
        payload: {
          code: '600000',
          name: '浦发银行',
          periods: 6,
          consensus: {
            as_of: '2026-10-31',
            analyst_count: 12,
            eps_current_year: 1.5,
            eps_next_year: 1.7,
            history: [{ quarter: '2026Q1' }],
          },
          earnings: [
            { report_date: '2026-09-30', actual: { eps: 1.2 } },
            { report_date: '2026-06-30', actual: { eps: 0.9 } },
          ],
          stats: { with_forecast: 2, latest_report_date: '2026-09-30' },
          data_source_status: 'ok',
        },
        data_source_status: 'ok',
      },
      '600000',
    )

    expect(summary).toMatchObject({
      code: '600000',
      periods: 6,
      latestReportDate: '2026-09-30',
      reportDates: ['2026-09-30', '2026-06-30'],
      withForecast: 2,
      consensus: { asOf: '2026-10-31', analystCount: 12, epsCurrentYear: 1.5, epsNextYear: 1.7 },
    })
    // 明细行与历史序列不进摘要
    expect(summary).not.toHaveProperty('earnings')
    expect(summary.consensus).not.toHaveProperty('history')
  })

  it('缺数时给 null 与原因，不补 0', () => {
    const summary = parseEarningsBriefSummary(
      {
        payload: {
          code: '600000',
          consensus: null,
          earnings: [],
          consensus_reason: '本地无缓存',
          earnings_reason: '未同步',
        },
      },
      '600000',
    )
    expect(summary.consensus).toBeNull()
    expect(summary.latestReportDate).toBeNull()
    expect(summary.reportDates).toEqual([])
    expect(summary.consensusReason).toBe('本地无缓存')
    expect(summary.earningsReason).toBe('未同步')
  })
})

describe('事件回看摘要解析', () => {
  it('保留类型与条数，丢弃事件明细与收益', () => {
    const summary = parseEventStudySummary(
      {
        payload: {
          code: '600000',
          benchmark: '000300',
          trading_days: 240,
          event_types: [
            {
              dataset: 'holder_trades',
              label: '增持减持',
              count: 3,
              dates: ['2026-01-02'],
              events: [{ date: '2026-01-02', window: { excess_return: 0.01 } }],
              sample: { mean_excess_return: 0.01 },
            },
            { dataset: 'dragon_tiger', label: '龙虎榜', count: 0 },
          ],
        },
      },
      '600000',
    )

    expect(summary.types).toEqual([
      { key: 'holder_trades', label: '增持减持', count: 3 },
      { key: 'dragon_tiger', label: '龙虎榜', count: 0 },
    ])
    expect(summary.total).toBe(3)
    expect(summary.tradingDays).toBe(240)
    expect(summary).not.toHaveProperty('events')
    expect(summary.types[0]).not.toHaveProperty('events')
  })
})

describe('上下文白名单', () => {
  const earnings = parseEarningsBriefSummary({
    payload: {
      code: '600000',
      consensus: { as_of: '2026-10-31', analyst_count: 12, eps_current_year: 1.5 },
      earnings: [{ report_date: '2026-09-30' }],
      stats: { with_forecast: 1, latest_report_date: '2026-09-30' },
    },
  })
  const events = parseEventStudySummary({
    payload: {
      code: '600000',
      event_types: [{ dataset: 'holder_trades', label: '增持减持', count: 3 }],
      benchmark: '000300',
      trading_days: 240,
    },
  })

  const context = buildAssistantContext({
    code: '600000',
    name: '浦发银行',
    date: '2026-11-06',
    tab: 'overview',
    overview: OVERVIEW,
    earnings,
    events,
  })

  it('顶层键集合固定', () => {
    expect(Object.keys(context).sort()).toEqual(
      [
        'code',
        'date',
        'earnings',
        'events',
        'financials',
        'kline',
        'name',
        'page',
        'quote',
        'tab',
        'unavailable',
      ].sort(),
    )
  })

  it('K 线区间取自页面已有的收盘价，不新增指标', () => {
    expect(context.kline).toEqual({
      bars: 3,
      start: '2026-11-04',
      end: '2026-11-06',
      firstClose: 10.2,
      lastClose: 10.3,
      lowClose: 10.2,
      highClose: 10.4,
    })
  })

  it('行情 / 财报 / 事件只保留页面展示过的原值', () => {
    expect(context.quote).toMatchObject({ price: 10.3, changePct: -0.96 })
    expect(context.financials).toMatchObject({ reportDate: '2026-09-30', roe: 9.8 })
    expect(context.earnings).toMatchObject({ periods: 1, latestReportDate: '2026-09-30' })
    expect(context.events).toEqual({ total: 3, types: [{ label: '增持减持', count: 3 }] })
  })

  it('序列化后不含论点 / 持仓 / 令牌字段，也不含提示词', () => {
    const text = serializeAssistantContext(context)
    const parsed = JSON.parse(text) as Record<string, unknown>
    for (const forbidden of ['thesis', 'holding', 'position', 'token', 'api_key', 'prompt', 'secret']) {
      expect(text.toLowerCase()).not.toContain(forbidden)
    }
    expect(parsed.code).toBe('600000')
    expect(parsed.tab).toBe('overview')
    expect(text).not.toContain('Bearer ')
  })

  it('缺数项把原因收进 unavailable，不补默认值', () => {
    const missing = buildAssistantContext({
      code: '600000',
      name: null,
      date: '2026-11-06',
      tab: 'research',
      overview: null,
      earnings: null,
      events: null,
    })
    expect(missing.quote).toBeNull()
    expect(missing.kline).toBeNull()
    expect(missing.financials).toBeNull()
    expect(missing.earnings).toBeNull()
    expect(missing.events).toBeNull()
    expect(missing.name).toBeNull()
    expect(missing.unavailable).toEqual([])
  })
})

describe('助手错误分类', () => {
  it('本机未连接 / 未配置 Key / 超时 / 额度 / 上游分别有可读文案', () => {
    expect(describeAssistantError(new ApiError('not-connected', 'x')).kind).toBe('offline')
    expect(
      describeAssistantError(
        new ApiError('http', 'x', 503, '未配置 AI 通道：缺少 LDST_LLM_API_KEY。'),
      ).kind,
    ).toBe('no-key')
    expect(
      describeAssistantError(new ApiError('http', 'x', 503, '订阅版 CLI 不可用')).kind,
    ).toBe('channel')
    expect(describeAssistantError(new ApiError('timeout', 'x')).kind).toBe('timeout')
    expect(describeAssistantError(new ApiError('http', 'x', 504, 'AI 响应超时')).kind).toBe(
      'timeout',
    )
    expect(describeAssistantError(new ApiError('http', 'x', 429, '额度用尽')).kind).toBe('quota')
    expect(describeAssistantError(new ApiError('parse', '空回答')).kind).toBe('upstream')
    expect(describeAssistantError(new ApiError('http', 'x', 502, '上游失败')).kind).toBe(
      'upstream',
    )
  })

  it('未配置 Key 的文案把服务的说明原样带出，且可重试', () => {
    const info = describeAssistantError(
      new ApiError('http', 'x', 503, '未配置 AI 通道：缺少 LDST_LLM_API_KEY。请到「设置 → 接入 AI」配置后再试。'),
    )
    expect(info.title).toBe('研究助手未配置模型')
    expect(info.detail).toContain('LDST_LLM_API_KEY')
    expect(info.retryable).toBe(true)
  })
})
