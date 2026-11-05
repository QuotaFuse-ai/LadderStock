/**
 * 财报前瞻与复盘（QUOT-450）客户端。
 *
 * 契约见 `docs/specs/SPEC-RESEARCH-PAGES.md` §2.5：
 *   GET /api/research/{code}/earnings-brief?periods=6
 *
 * 后端只读本机缓存（consensus_forecast / finance_indicators / earnings_preview），
 * 缺数一律 `null` + 原因，**不补 0**。前端只做轻量解析与展示，不做任何统计量计算；
 * 发布日 / 反应日 / 当天涨跌当前无源，解析层原样保留 null 与 reason。
 */

import { ApiError } from '../api'

export interface EarningsActual {
  eps: number | null
  bps: number | null
  revenue: number | null
  net_profit: number | null
  roe: number | null
  gross_margin: number | null
  net_margin: number | null
  revenue_yoy: number | null
  profit_yoy: number | null
}

/** 同报告期的业绩预告（`earnings_preview`）；单位沿用上游。 */
export interface EarningsForecastRecord {
  indicator: string | null
  forecast_type: string | null
  report_date: string | null
  notice_date: string | null
  amount_lower: number | null
  amount_upper: number | null
  change_pct_lower: number | null
  change_pct_upper: number | null
  prior_year_amount: number | null
  content: string | null
  reason: string | null
}

export interface EarningsPeriod {
  reportDate: string
  reportName: string | null
  actual: EarningsActual
  forecast: EarningsForecastRecord | null
  forecastReason: string | null
  publishDate: string | null
  publishDateReason: string | null
  reactionDate: string | null
  reactionDateReason: string | null
  reactionChangePct: number | null
  reactionChangePctReason: string | null
}

export interface ConsensusYear {
  year: number | null
  analystCount: number | null
  min: number | null
  mean: number | null
  max: number | null
  industryAvg: number | null
}

export interface ConsensusSnapshot {
  asOf: string | null
  name: string | null
  currentYear: number | null
  epsCurrentYear: number | null
  epsNextYear: number | null
  analystCount: number | null
  ratings: Record<string, number> | null
  consensus: ConsensusYear[]
}

export interface EarningsStats {
  periods: number | null
  withForecast: number | null
  withPublishDate: number | null
  withReaction: number | null
  latestReportDate: string | null
  latestReportEps: number | null
}

export interface EarningsBriefResult {
  code: string
  name: string | null
  periods: number
  consensus: ConsensusSnapshot | null
  consensusReason: string | null
  earnings: EarningsPeriod[]
  earningsReason: string | null
  stats: EarningsStats
  dataSourceStatus: 'ok' | 'missing'
  dataAsOf: string | null
  /** 本次读取本机缓存的时刻（信封 `meta.fetched_at`），注脚用 */
  fetchedAt: string | null
}

export type EarningsBriefLoader = (code: string, signal?: AbortSignal) => Promise<EarningsBriefResult>

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

function num(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function int(value: unknown): number | null {
  const parsed = num(value)
  return parsed === null ? null : Math.trunc(parsed)
}

function nums(value: unknown): Record<string, number> | null {
  const record = asRecord(value)
  if (!record) return null
  const result: Record<string, number> = {}
  for (const [key, entry] of Object.entries(record)) {
    const parsed = num(entry)
    if (parsed !== null) result[key] = parsed
  }
  return result
}

function parseActual(value: unknown): EarningsActual {
  const record = asRecord(value) ?? {}
  return {
    eps: num(record.eps),
    bps: num(record.bps),
    revenue: num(record.revenue),
    net_profit: num(record.net_profit),
    roe: num(record.roe),
    gross_margin: num(record.gross_margin),
    net_margin: num(record.net_margin),
    revenue_yoy: num(record.revenue_yoy),
    profit_yoy: num(record.profit_yoy),
  }
}

function parseForecast(value: unknown): EarningsForecastRecord | null {
  const record = asRecord(value)
  if (!record) return null
  return {
    indicator: text(record.indicator),
    forecast_type: text(record.forecast_type),
    report_date: text(record.report_date),
    notice_date: text(record.notice_date),
    amount_lower: num(record.amount_lower),
    amount_upper: num(record.amount_upper),
    change_pct_lower: num(record.change_pct_lower),
    change_pct_upper: num(record.change_pct_upper),
    prior_year_amount: num(record.prior_year_amount),
    content: text(record.content),
    reason: text(record.reason),
  }
}

function parsePeriod(value: unknown): EarningsPeriod | null {
  const record = asRecord(value)
  if (!record) return null
  const reportDate = text(record.report_date)
  if (!reportDate) return null
  return {
    reportDate,
    reportName: text(record.report_name),
    actual: parseActual(record.actual),
    forecast: parseForecast(record.forecast),
    forecastReason: text(record.forecast_reason),
    publishDate: text(record.publish_date),
    publishDateReason: text(record.publish_date_reason),
    reactionDate: text(record.reaction_date),
    reactionDateReason: text(record.reaction_date_reason),
    reactionChangePct: num(record.reaction_change_pct),
    reactionChangePctReason: text(record.reaction_change_pct_reason),
  }
}

function parseConsensusYear(value: unknown): ConsensusYear | null {
  const record = asRecord(value)
  if (!record) return null
  return {
    year: int(record.year),
    analystCount: int(record.analyst_count),
    min: num(record.min),
    mean: num(record.mean),
    max: num(record.max),
    industryAvg: num(record.industry_avg),
  }
}

function parseConsensus(value: unknown): ConsensusSnapshot | null {
  const record = asRecord(value)
  if (!record) return null
  const rows = Array.isArray(record.consensus)
    ? record.consensus.map(parseConsensusYear).filter((row): row is ConsensusYear => row !== null)
    : []
  return {
    asOf: text(record.as_of),
    name: text(record.name),
    currentYear: int(record.current_year),
    epsCurrentYear: num(record.eps_current_year),
    epsNextYear: num(record.eps_next_year),
    analystCount: int(record.analyst_count),
    ratings: nums(record.ratings),
    consensus: rows,
  }
}

function parseStats(value: unknown): EarningsStats {
  const record = asRecord(value) ?? {}
  return {
    periods: int(record.periods),
    withForecast: int(record.with_forecast),
    withPublishDate: int(record.with_publish_date),
    withReaction: int(record.with_reaction),
    latestReportDate: text(record.latest_report_date),
    latestReportEps: num(record.latest_report_eps),
  }
}

/**
 * 解析四段信封（`payload / data_source_status / data_as_of / meta`）。
 * 兼容平铺响应；缺键不猜：没有就 null，页面据此出 `—` + 原因。
 */
export function parseEarningsBrief(raw: unknown, expectedCode?: string): EarningsBriefResult {
  const root = asRecord(raw)
  if (!root) throw new ApiError('parse', '财报接口返回的不是 JSON 对象')
  const payload = asRecord(root.payload) ?? root
  const meta = asRecord(root.meta)
  const earnings = Array.isArray(payload.earnings)
    ? payload.earnings.map(parsePeriod).filter((row): row is EarningsPeriod => row !== null)
    : []
  return {
    code: text(payload.code) ?? expectedCode ?? '',
    name: text(payload.name),
    periods: int(payload.periods) ?? earnings.length,
    consensus: parseConsensus(payload.consensus),
    consensusReason: text(payload.consensus_reason),
    earnings,
    earningsReason: text(payload.earnings_reason),
    stats: parseStats(payload.stats),
    dataSourceStatus: text(payload.data_source_status) === 'missing' ? 'missing' : 'ok',
    dataAsOf: text(root.data_as_of),
    fetchedAt: text(meta?.fetched_at) ?? text(root.fetched_at),
  }
}

const TIMEOUT_MS = 20000

export const httpEarningsBriefLoader: EarningsBriefLoader = async (code, signal) => {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
  const abortFromCaller = () => controller.abort()
  signal?.addEventListener('abort', abortFromCaller)
  const path = `/api/research/${encodeURIComponent(code)}/earnings-brief?periods=6`
  try {
    const response = await fetch(path, {
      signal: controller.signal,
      cache: 'no-store',
      headers: { accept: 'application/json' },
    })
    if (!response.ok) {
      let detail: string | null = null
      try {
        const parsed = (await response.json()) as { detail?: unknown }
        detail = typeof parsed.detail === 'string' ? parsed.detail : null
      } catch {
        detail = null
      }
      throw new ApiError(
        'http',
        `财报接口 ${path} 返回 HTTP ${response.status}${detail ? `：${detail}` : ''}`,
        response.status,
        detail,
      )
    }
    let body: unknown
    try {
      body = await response.json()
    } catch {
      throw new ApiError('parse', `财报接口 ${path} 返回的内容不是合法 JSON`)
    }
    return parseEarningsBrief(body, code)
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (signal?.aborted) throw new ApiError('timeout', `请求已取消：${path}`)
      throw new ApiError('timeout', `财报接口 ${path} 超时（${TIMEOUT_MS}ms）`)
    }
    throw new ApiError('not-connected', `财报接口不可达：${path}`)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abortFromCaller)
  }
}
