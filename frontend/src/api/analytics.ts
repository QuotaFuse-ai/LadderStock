/**
 * `/api/analytics/summary` 组合统计（B 包 / DEC-089 后端）。
 *
 * 平面 JSON（不套信封）、`Cache-Control: no-store`、只对回环开放——与
 * `api/inbox.ts` 同一条隐私口径。解析规则一致：缺字段就是 `null` / 空数组，
 * 不补 0；响应不是对象属于契约破坏，抛 `ApiError('parse')`。
 */

import { ApiError, isApiError } from '../api'

const TIMEOUT_MS = 15000

export interface RealizedStats {
  count: number
  wins: number
  losses: number
  win_rate: number | null
  profit_factor: number | null
  avg_win: number | null
  avg_loss: number | null
  gross_win: number
  gross_loss: number
}

export interface PerfMetrics {
  twr: number | null
  twr_annualized: number | null
  xirr: number | null
  max_drawdown: number | null
  sharpe: number | null
  annual_volatility: number | null
  invested: number | null
  end_value: number | null
  gain: number | null
  dividends: number | null
  fees: number | null
  realized: RealizedStats | null
}

export interface Curve {
  dates: string[]
  twr_index: number[]
}

export interface BenchmarkBlock {
  code: string
  label: string
  start?: string | null
  end?: string | null
  return: number | null
  annualized: number | null
  reason: string | null
}

export interface AttributionRow {
  symbol: string
  name: string
  realized: number
  dividends: number
  fees: number
  net: number
  status: 'holding' | 'closed' | string
}

export interface BehaviorRow {
  bucket: string
  label: string
  count: number
  avg_forward_return: number
  win_rate: number
  window_days: number
}

// ---------------------------------------------------------------- 导出（DEC-090）

export type ExportFormat = 'csv' | 'xlsx' | 'pdf'
export type ExportSection = 'attribution' | 'behavior' | 'mistakes'

const EXPORT_ACCEPT: Record<ExportFormat, string> = {
  csv: 'text/csv',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  pdf: 'application/pdf',
}

const EXPORT_FILENAME: Record<ExportFormat, string> = {
  csv: '',
  xlsx: 'analytics.xlsx',
  pdf: 'analytics.pdf',
}

/**
 * 下载一个导出件（浏览器原生下载：`fetch` → blob → a[download]）。
 *
 * XLSX 缺 openpyxl / PDF 缺 reportlab 或中文字体时后端回 503 + 人话——原样抛
 * 给页面说人话，不静默换格式（DEC-090 §1）。
 */
export async function downloadAnalyticsExport(
  format: ExportFormat,
  section: ExportSection,
  days = 365,
): Promise<void> {
  const path = `/api/analytics/export?format=${format}&section=${section}&days=${days}`
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
  try {
    const response = await fetch(path, {
      signal: controller.signal,
      cache: 'no-store',
      credentials: 'same-origin',
      headers: { accept: EXPORT_ACCEPT[format] },
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
        `导出失败：HTTP ${response.status}${detail ? `：${detail}` : ''}`,
        response.status,
        detail,
      )
    }
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = format === 'csv' ? `analytics-${section}.csv` : EXPORT_FILENAME[format]
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
  } catch (error) {
    if (isApiError(error)) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('timeout', `导出超时（${TIMEOUT_MS}ms）`)
    }
    throw new ApiError('not-connected', '本机接口不可达：导出接口')
  } finally {
    clearTimeout(timer)
  }
}

export interface MistakeRow {
  symbol: string
  date: string
  shares: number
  proceeds: number
  cost: number
  pnl: number
  pnl_pct: number | null
  hold_days: number | null
}

export interface AnalyticsSummary {
  ok: boolean
  reason: string
  as_of: string | null
  start: string | null
  span_days: number | null
  metrics: PerfMetrics | null
  curve: Curve | null
  benchmark: BenchmarkBlock
  excess_return: number | null
  attribution: AttributionRow[]
  behavior: { buckets: BehaviorRow[]; window_days?: number; reason: string }
  mistakes: MistakeRow[]
  warnings: string[]
  notes: string[]
}

// ---------------------------------------------------------------- 请求

async function request<T>(path: string): Promise<T> {  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
  try {
    const response = await fetch(path, {
      signal: controller.signal,
      cache: 'no-store',
      credentials: 'same-origin',
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
        `${path} 返回 HTTP ${response.status}${detail ? `：${detail}` : ''}`,
        response.status,
        detail,
      )
    }
    try {
      return (await response.json()) as T
    } catch {
      throw new ApiError('parse', `${path} 返回的内容不是合法 JSON`)
    }
  } catch (error) {
    if (isApiError(error)) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('timeout', `${path} 超时（${TIMEOUT_MS}ms）`)
    }
    throw new ApiError('not-connected', `本机接口不可达：${path}`)
  } finally {
    clearTimeout(timer)
  }
}

// ---------------------------------------------------------------- 解析

function record(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function boolOr(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function parseMetrics(value: unknown): PerfMetrics | null {
  const src = record(value)
  if (!src) return null
  const realized = record(src.realized)
  return {
    twr: num(src.twr),
    twr_annualized: num(src.twr_annualized),
    xirr: num(src.xirr),
    max_drawdown: num(src.max_drawdown),
    sharpe: num(src.sharpe),
    annual_volatility: num(src.annual_volatility),
    invested: num(src.invested),
    end_value: num(src.end_value),
    gain: num(src.gain),
    dividends: num(src.dividends),
    fees: num(src.fees),
    realized: realized
      ? {
          count: num(realized.count) ?? 0,
          wins: num(realized.wins) ?? 0,
          losses: num(realized.losses) ?? 0,
          win_rate: num(realized.win_rate),
          profit_factor: num(realized.profit_factor),
          avg_win: num(realized.avg_win),
          avg_loss: num(realized.avg_loss),
          gross_win: num(realized.gross_win) ?? 0,
          gross_loss: num(realized.gross_loss) ?? 0,
        }
      : null,
  }
}

export async function fetchAnalytics(days = 365): Promise<AnalyticsSummary> {
  const parsed = await request<unknown>(`/api/analytics/summary?days=${days}`)
  const src = record(parsed)
  if (!src) throw new ApiError('parse', '/api/analytics/summary 返回的不是对象')
  const curve = record(src.curve)
  const benchmark = record(src.benchmark)
  const behavior = record(src.behavior)
  return {
    ok: boolOr(src.ok, false),
    reason: text(src.reason),
    as_of: typeof src.as_of === 'string' ? src.as_of : null,
    start: typeof src.start === 'string' ? src.start : null,
    span_days: num(src.span_days),
    metrics: parseMetrics(src.metrics),
    curve: curve
      ? {
          dates: list(curve.dates).map((item) => text(item)),
          twr_index: list(curve.twr_index).map((item) => num(item) ?? 0),
        }
      : null,
    benchmark: {
      code: text(benchmark?.code, '000300'),
      label: text(benchmark?.label, '沪深300'),
      start: typeof benchmark?.start === 'string' ? benchmark.start : null,
      end: typeof benchmark?.end === 'string' ? benchmark.end : null,
      return: num(benchmark?.return),
      annualized: num(benchmark?.annualized),
      reason: typeof benchmark?.reason === 'string' ? benchmark.reason : null,
    },
    excess_return: num(src.excess_return),
    attribution: list(src.attribution).map((row) => {
      const item = record(row)
      return {
        symbol: text(item?.symbol),
        name: text(item?.name),
        realized: num(item?.realized) ?? 0,
        dividends: num(item?.dividends) ?? 0,
        fees: num(item?.fees) ?? 0,
        net: num(item?.net) ?? 0,
        status: text(item?.status, 'holding'),
      }
    }),
    behavior: {
      buckets: list(behavior?.buckets).map((row) => {
        const item = record(row)
        return {
          bucket: text(item?.bucket),
          label: text(item?.label),
          count: num(item?.count) ?? 0,
          avg_forward_return: num(item?.avg_forward_return) ?? 0,
          win_rate: num(item?.win_rate) ?? 0,
          window_days: num(item?.window_days) ?? 5,
        }
      }),
      window_days: num(behavior?.window_days) ?? undefined,
      reason: text(behavior?.reason),
    },
    mistakes: list(src.mistakes).map((row) => {
      const item = record(row)
      return {
        symbol: text(item?.symbol),
        date: text(item?.date),
        shares: num(item?.shares) ?? 0,
        proceeds: num(item?.proceeds) ?? 0,
        cost: num(item?.cost) ?? 0,
        pnl: num(item?.pnl) ?? 0,
        pnl_pct: num(item?.pnl_pct),
        hold_days: num(item?.hold_days),
      }
    }),
    warnings: list(src.warnings).map((item) => text(item)),
    notes: list(src.notes).map((item) => text(item)),
  }
}