import { describe, expect, it } from 'vitest'

import type { AnalyticsSummary } from '../src/api/analytics'
import {
  ANALYTICS_SHARE_SIZE,
  ANALYTICS_SHARE_STATEMENT,
  analyticsShareSummary,
  buildAnalyticsShareCard,
} from '../src/lib/analyticsShareCard'

/**
 * C 包第二半（DEC-090 §2）：统计页分享卡的数据纪律。
 *
 * 图和文字共用 `buildAnalyticsShareCard` 的一份数据：数字来自页面正在渲染的
 * 聚合、缺数写「—」、不重算；文字版与图片同源同序；不含持仓明细。
 */

function summary(overrides: Partial<AnalyticsSummary> = {}): AnalyticsSummary {
  return {
    ok: true,
    reason: '',
    as_of: '2026-11-21',
    start: '2025-11-21',
    span_days: 366,
    metrics: {
      twr: 0.1234,
      twr_annualized: 0.12,
      xirr: 0.11,
      max_drawdown: 0.081,
      sharpe: 1.05,
      annual_volatility: 0.18,
      invested: 100000,
      end_value: 112340,
      gain: 12340,
      dividends: 800,
      fees: 120,
      realized: {
        count: 10,
        wins: 6,
        losses: 4,
        win_rate: 0.6,
        profit_factor: 1.8,
        avg_win: 2200,
        avg_loss: 1100,
        gross_win: 13200,
        gross_loss: 4400,
      },
    },
    curve: { dates: [], twr_index: [] },
    benchmark: {
      code: '000300',
      label: '沪深300',
      start: '2025-11-21',
      end: '2026-11-21',
      return: 0.091,
      annualized: null,
      reason: null,
    },
    excess_return: 0.0324,
    attribution: [
      {
        symbol: '600000',
        name: '浦发银行',
        realized: 3200,
        dividends: 200,
        fees: 30,
        net: 3370,
        status: 'closed',
      },
    ],
    behavior: { buckets: [], window_days: 5, reason: '' },
    mistakes: [
      {
        symbol: '000001',
        date: '2026-10-09',
        shares: 1000,
        proceeds: 9000,
        cost: 10500,
        pnl: -1500,
        pnl_pct: -0.1429,
        hold_days: 12,
      },
    ],
    warnings: [],
    notes: [],
    ...overrides,
  }
}

describe('分享卡数据构建', () => {
  it('正常聚合：比率按页面同口径换算为百分比字符串', () => {
    const data = buildAnalyticsShareCard({ summary: summary(), days: 365 })
    expect(data.windowLabel).toContain('365')
    expect(data.start).toBe('2025-11-21')
    expect(data.asOf).toBe('2026-11-21')

    const twr = data.rows.find((row) => row.label === '总收益（TWR）')
    expect(twr?.value).toBe('12.34%')
    expect(twr?.missing).toBe(false)

    const excess = data.rows.find((row) => row.label === '超额（vs 沪深300）')
    expect(excess?.value).toBe('3.24%')

    const pf = data.rows.find((row) => row.label === '盈亏比')
    expect(pf?.value).toBe('1.80')

    expect(data.attributionTop[0].value).toContain('3,200')
    expect(data.mistakesTop[0].value).toContain('-1,500')
    expect(data.mistakesTop[0].value).toContain('-14.29%')
  })

  it('缺数口径：metrics 为 null 时全部写「—」并进 warnings，不补 0', () => {
    const data = buildAnalyticsShareCard({
      summary: summary({
        metrics: null,
        excess_return: null,
        benchmark: {
          code: '000300',
          label: '沪深300',
          return: null,
          annualized: null,
          reason: 'index_daily 无本窗口数据',
        },
      }),
      days: 90,
    })
    for (const row of data.rows) {
      expect(row.missing).toBe(true)
      expect(row.value).toBe('—')
    }
    expect(data.warnings.join('\n')).toContain('index_daily 无本窗口数据')
    expect(data.benchmarkMissing).toBe('index_daily 无本窗口数据')
  })

  it('长图尺寸是竖版 720×1280（「长图」口径）', () => {
    expect(ANALYTICS_SHARE_SIZE).toEqual({ width: 720, height: 1280 })
  })
})

describe('分享文字版', () => {
  it('与图片同源同序：声明、区间、指标、归因、错题本、生成时间', () => {
    const lines = analyticsShareSummary(buildAnalyticsShareCard({ summary: summary(), days: 365 }))
    const text = lines.join('\n')
    expect(text).toContain(ANALYTICS_SHARE_STATEMENT)
    expect(text).toContain('区间：2025-11-21 → 2026-11-21')
    expect(text).toContain('- 总收益（TWR）：12.34%')
    expect(text).toContain('归因（按已实现盈亏前五）：')
    expect(text).toContain('- 浦发银行（600000） · 已了结：')
    expect(text).toContain('错题本（最近前五）：')
    expect(text).toContain('生成：')
    // 隐私口径：只有逐票聚合行，不带持仓数量/成本明细
    expect(text).not.toContain('shares')
    expect(text).not.toContain('成本 10,500')
  })

  it('没有归因/错题时明说「无」，不硬凑行', () => {
    const lines = analyticsShareSummary(
      buildAnalyticsShareCard({
        summary: summary({ attribution: [], mistakes: [] }),
        days: 90,
      }),
    )
    const text = lines.join('\n')
    expect(text).toContain('归因：无')
    expect(text).toContain('错题本：无')
  })
})
