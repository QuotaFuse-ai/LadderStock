import { describe, expect, it } from 'vitest'

import { parseEarningsBrief } from '../src/api/earningsBrief'
import {
  actualText,
  dispersionText,
  forecastText,
  gapLines,
  reactionSentence,
  sourceNote,
} from '../src/lib/earningsBrief'
import { earningsEnvelope } from './fixtures/earningsBrief'

/**
 * QUOT-450：财报前瞻与复盘的数据解析与展示口径。
 *
 * 重点守三件事：
 * 1. 缺数原样是 `null`（不补 0、不拿报告期末冒充发布日）；
 * 2. 「没取到的」逐条覆盖每一处 `—`；
 * 3. 统计句只展示后端给的样本数，前端不求平均。
 */

describe('parseEarningsBrief', () => {
  it('四段信封：一致预期、明细、stats 与取数时刻都解析出来', () => {
    const result = parseEarningsBrief(earningsEnvelope(), '600000')
    expect(result.name).toBe('浦发银行')
    expect(result.periods).toBe(6)
    expect(result.consensus?.consensus[0].min).toBe(1.45)
    expect(result.consensus?.consensus[0].max).toBe(1.67)
    expect(result.earnings).toHaveLength(2)
    expect(result.earnings[0].actual.eps).toBe(1.17)
    expect(result.earnings[0].publishDate).toBeNull()
    expect(result.stats.withReaction).toBe(0)
    expect(result.fetchedAt).toBe('2026-11-05T17:56:55+00:00')
  })

  it('缺键不猜：没有共识就是 null，没给的数值保持 null，不变成 0', () => {
    const result = parseEarningsBrief(
      earningsEnvelope({ consensus: null, consensus_reason: '本地没有一致预期快照' }),
      '600000',
    )
    expect(result.consensus).toBeNull()
    expect(result.consensusReason).toBe('本地没有一致预期快照')
    expect(result.earnings[1].actual.eps).toBeNull()
    expect(result.earnings[1].actual.net_profit).toBeNull()
    expect(result.earnings[1].reactionChangePct).toBeNull()
  })

  it('平铺响应（没有 payload 层）也能解析', () => {
    const result = parseEarningsBrief(earningsEnvelope().payload, '600000')
    expect(result.code).toBe('600000')
    expect(result.earnings).toHaveLength(2)
  })
})

describe('展示口径', () => {
  const result = parseEarningsBrief(earningsEnvelope(), '600000')

  it('一致预期分歧度 = 最高 ÷ 最低，两端缺失时不给', () => {
    expect(dispersionText(1.45, 1.67)).toBe('1.15 倍')
    expect(dispersionText(null, 1.67)).toBeNull()
    expect(dispersionText(0, 1.67)).toBeNull()
  })

  it('实际值：千分位 + 亿元单位，缺的项不出现', () => {
    expect(actualText(result.earnings[0])).toContain('EPS 1.17 元')
    expect(actualText(result.earnings[0])).toContain('营收 1,381.33 亿元')
    expect(actualText(result.earnings[0])).toContain('归母净利 405.72 亿元')
    expect(actualText(result.earnings[0])).toContain('净利同比 +4.52%')
    // eps / net_profit 都缺，但营收还在：只展示现有项，不补 0
    const second = actualText(result.earnings[1])
    expect(second).not.toContain('EPS')
    expect(second).not.toContain('归母净利')
    expect(second).toContain('营收 937.77 亿元')
  })

  it('预期列没有就老老实实是 —，不拿当期预期倒填', () => {
    expect(forecastText(null)).toBe('—')
  })

  it('没取到的：发布日 / 反应日 / 历史预期 / 预约披露日逐条都有', () => {
    const gaps = gapLines(result).join('\n')
    expect(gaps).toContain('发布日')
    expect(gaps).toContain('反应日 / 当天涨跌')
    expect(gaps).toContain('历史预期')
    expect(gaps).toContain('不拿当期预期倒填历史')
    expect(gaps).toContain('预约披露日')
    // stats 说 6 期里 0 期有发布日 → 6/6，而不是按夹具里两行算出来的数
    expect(gaps).toContain('6/6 期缺')
  })

  it('统计句：样本不足时只报样本数，不给平均', () => {
    const base = {
      periods: 6,
      withForecast: 0,
      withPublishDate: 0,
      withReaction: 0,
      latestReportDate: null,
      latestReportEps: null,
    }
    expect(reactionSentence(base)).toContain('样本不足 0 次，不给平均')
    expect(reactionSentence({ ...base, withReaction: 3 })).toContain('样本不足 3 次，不给平均')
  })

  it('来源注脚写实际数据集与上游工具，不照抄参考库的腾讯 K 线', () => {
    const note = sourceNote(result, '11-06 01:56')
    expect(note).toContain('ths_eps_forecast')
    expect(note).toContain('eastmoney_finance_indicators')
    expect(note).toContain('earnings_forecast')
    expect(note).toContain('取于 11-06 01:56')
    expect(note).not.toContain('腾讯 K 线')
  })
})
