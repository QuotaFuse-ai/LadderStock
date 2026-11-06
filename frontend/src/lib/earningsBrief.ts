/**
 * 「财报前瞻与复盘」的展示层（QUOT-450）。
 *
 * 数字来源：
 * - 一致预期的均值 / 区间 / 机构数、各报告期实际值、业绩预告：**接口原样返回**，
 *   这里只做千分位与单位换算，不补 0、不推算。
 * - 统计句（此前几次财报后涨跌的绝对值平均、最近一次是平均的几倍）：契约要求
 *   后端算好再回传，本层**不做除法**；接口只给 `stats.with_reaction` 时，
 *   按「样本不足 N 次，不给平均」如实展示。
 * - 发布日 / 反应日 / 当天涨跌当前无源（`finance_indicators` 没有公告日字段），
 *   一律 `—`，原因进「没取到的」。
 */

import { formatChangePct, formatMoney, isFiniteNumber, MISSING } from './format'
import type {
  ConsensusSnapshot,
  ConsensusYear,
  EarningsBriefResult,
  EarningsForecastRecord,
  EarningsPeriod,
  EarningsStats,
} from '../api/earningsBrief'

/** 统计句的样本下限：此前至少 4 次可算的反应日才给平均（与参考库口径一致）。 */
export const REACTION_SAMPLE_MIN = 4

export const EARNINGS_INTRO =
  '市场一致预期多少，过去几次财报的实际值与预期差多少；只描述已经公布的财报，不预测下一次，不构成投资建议。'

export const MOVE_RULE =
  '涨跌口径 = 披露后首个交易日收盘 ÷ 前一交易日收盘 − 1，含当天大盘涨跌'

export function epsText(value: number | null | undefined): string {
  return isFiniteNumber(value) ? `${value.toFixed(2)} 元` : MISSING
}

/** 金额：沿用 `formatMoney` 的万 / 亿换算，补上「元」 */
export function moneyText(value: number | null | undefined): string {
  return isFiniteNumber(value) ? `${formatMoney(value)}元` : MISSING
}

export function pctText(value: number | null | undefined): string {
  return isFiniteNumber(value) ? formatChangePct(value).text : MISSING
}

/** 区间：`1.45 ~ 1.67 元`；任一端缺失就显示 `—`，不用另一端冒充区间 */
export function rangeText(low: number | null, high: number | null, unit = '元'): string {
  if (!isFiniteNumber(low) || !isFiniteNumber(high)) return MISSING
  return `${low} ~ ${high}${unit ? ` ${unit}` : ''}`
}

/**
 * 最高 ÷ 最低的倍数（分歧度）。后端契约未返回 dispersion，本函数只用接口返回的
 * max / min 做一次显示换算；任一端缺失或最低价非正时返回 null（页面写 `—`）。
 */
export function dispersionText(low: number | null, high: number | null): string | null {
  if (!isFiniteNumber(low) || !isFiniteNumber(high) || low <= 0) return null
  return `${(high / low).toFixed(2)} 倍`
}

export function consensusYearOf(snapshot: ConsensusSnapshot): ConsensusYear | null {
  const byYear = snapshot.currentYear === null
    ? null
    : snapshot.consensus.find((row) => row.year === snapshot.currentYear) ?? null
  return byYear ?? snapshot.consensus[0] ?? null
}

export function nextConsensusYearOf(snapshot: ConsensusSnapshot): ConsensusYear | null {
  const current = consensusYearOf(snapshot)
  if (!current || current.year === null) return null
  return snapshot.consensus.find((row) => row.year === current.year! + 1) ?? null
}

/** 实际值一格：EPS / 营收 / 归母净利 / 净利同比，缺的项不出现，全缺为 `—` */
export function actualText(period: EarningsPeriod): string {
  const parts: string[] = []
  if (period.actual.eps !== null) parts.push(`EPS ${epsText(period.actual.eps)}`)
  if (period.actual.revenue !== null) parts.push(`营收 ${moneyText(period.actual.revenue)}`)
  if (period.actual.net_profit !== null) parts.push(`归母净利 ${moneyText(period.actual.net_profit)}`)
  if (period.actual.profit_yoy !== null) parts.push(`净利同比 ${pctText(period.actual.profit_yoy)}`)
  return parts.length > 0 ? parts.join(' · ') : MISSING
}

/** 预期一格：优先用同一报告期的业绩预告，没有就 `—`；不拿当期一致预期倒填历史 */
export function forecastText(forecast: EarningsForecastRecord | null): string {
  if (!forecast) return MISSING
  const head = forecast.forecast_type ?? forecast.indicator ?? '业绩预告'
  const hasAmount = forecast.amount_lower !== null || forecast.amount_upper !== null
  const amount = hasAmount
    ? `归母净利 ${rangeText(forecast.amount_lower, forecast.amount_upper)}`
    : forecast.content
  const notice = forecast.notice_date ? `（${forecast.notice_date} 公告）` : ''
  return amount ? `${head} · ${amount}${notice}` : `${head}${notice}`
}

function firstReason(periods: EarningsPeriod[], pick: (period: EarningsPeriod) => string | null): string {
  for (const period of periods) {
    const reason = pick(period)
    if (reason) return reason
  }
  return ''
}

/**
 * 「没取到的」逐条说明：每处 `—` 都能在这里找到对应条目。
 * 数量优先用后端 `stats`（统计量不在前端重算），缺字段时才退回逐行计数。
 */
export function gapLines(result: EarningsBriefResult): string[] {
  const gaps: string[] = []
  const periods = result.earnings
  const total = result.stats.periods ?? periods.length

  if (!result.consensus) {
    gaps.push(
      `一致预期：${result.consensusReason ?? '本地没有这只代码的一致预期快照（未同步或上游无数据）'}`,
    )
  }
  if (periods.length === 0) {
    gaps.push(
      `财报明细：${result.earningsReason ?? '本地没有这只代码的报告期记录（未同步）'}`,
    )
  } else {
    const missingPublish = total - (result.stats.withPublishDate ?? periods.filter((p) => !p.publishDate).length)
    if (missingPublish > 0) {
      const reason = firstReason(periods, (p) => p.publishDateReason)
      gaps.push(
        `发布日：${reason || '本地没有公告日字段，不拿报告期末或抓取日冒充'}（${missingPublish}/${total} 期缺）`,
      )
    }
    const missingReaction = total - (result.stats.withReaction ?? periods.filter((p) => p.reactionChangePct === null).length)
    if (missingReaction > 0) {
      const reason = firstReason(periods, (p) => p.reactionDateReason)
      gaps.push(
        `反应日 / 当天涨跌：${reason || '缺发布日，无法定位披露后的首个交易日'}（${missingReaction}/${total} 期缺）`,
      )
    }
    const missingForecast = total - (result.stats.withForecast ?? periods.filter((p) => !p.forecast).length)
    if (missingForecast > 0) {
      const reason = firstReason(periods, (p) => p.forecastReason)
      gaps.push(
        `历史预期：${reason || '同报告期没有业绩预告'}（${missingForecast}/${total} 期缺）；`
          + '历史逐期一致预期未接入，不拿当期预期倒填历史',
      )
    }
    const missingActual = periods.filter(
      (p) => p.actual.eps === null || p.actual.net_profit === null,
    ).length
    if (missingActual > 0) {
      gaps.push(`实际值：${missingActual} 期缺 EPS 或归母净利，表格显示 —`)
    }
  }
  gaps.push('预约披露日：上游 TickHub-CN 没有「预约披露日」工具，下一期的披露日期本期不提供')
  return gaps
}

/**
 * 统计句。契约要求均值与倍数由后端算好回传；接口只给可算样本数时，样本不足
 * 就明确写「样本不足 N 次，不给平均」，前端不自己求平均、不做除法。
 */
export function reactionSentence(stats: EarningsStats): string {
  const n = stats.withReaction ?? 0
  if (n < REACTION_SAMPLE_MIN) {
    return `样本不足 ${n} 次，不给平均（口径：此前至少 ${REACTION_SAMPLE_MIN} 次财报后可算，才给绝对值平均）。`
  }
  return `可算 ${n} 次，但接口未返回绝对值平均与「最近一次 ÷ 平均」倍数，本卡不在这里做除法。`
}

/** 来源注脚：写实际数据集与上游工具，不照抄参考库的源名。 */
export function sourceNote(result: EarningsBriefResult, fetchedLabel: string): string {
  const parts = [
    '同花顺一致预期（TickHub-CN ths_eps_forecast → 本机 consensus_forecast 缓存）',
    '东财财务指标（eastmoney_finance_indicators → finance_indicators 缓存）',
    '东财业绩预告（earnings_forecast → earnings_preview 缓存）',
  ]
  const asOf = result.consensus?.asOf ? `；一致预期快照 ${result.consensus.asOf}` : ''
  const fetched = fetchedLabel && fetchedLabel !== MISSING ? ` · 取于 ${fetchedLabel}` : ''
  return `来源：${parts.join('、')}${asOf}${fetched}。${MOVE_RULE}。`
}
