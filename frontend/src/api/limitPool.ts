/**
 * 涨停样本（`/limit-up`）只读客户端。
 *
 *   GET /api/limit-pool?date=YYYY-MM-DD
 *
 * 响应是 `LadderEnvelope`（`domain=sentiment`、`topic=limit_pool`），`payload`
 * 与后端 SPEC 逐键对应。解析层只做类型收敛：`null` 保持 `null`，不补 0、
 * 不插值、不重算任何指标。接口未接入（404 / 未连接）由页面转成空态 / 离线态，
 * **绝不回落到演示数据**（演示源只在 `?demo=1` 时显式启用）。
 */

import { ApiError, isApiError, isEndpointMissing } from '../api'

const READ_TIMEOUT_MS = 8000

export const LIMIT_POOL_PATH = '/api/limit-pool'

export type LimitPoolStatus = 'ok' | 'partial' | 'missing'

/** 一行涨停样本；除 `code` 外全部可为 `null`（后端缺字段时原样透传）。 */
export interface LimitPoolRow {
  code: string
  name: string | null
  streak: number | null
  /** HHMMSS 原样字符串，**不做时区换算** */
  first_seal_time: string | null
  last_seal_time: string | null
  reason: string | null
  trade_date: string | null
}

export interface LimitPoolPayload {
  date: string
  available: boolean
  reason: string | null
  source_detail: string
  count: number
  data_source_status: LimitPoolStatus
  rows: LimitPoolRow[]
}

export interface LimitPoolQuery {
  date: string
}

export interface LimitPoolSource {
  load: (query: LimitPoolQuery, signal?: AbortSignal) => Promise<LimitPoolPayload>
}

// ---------------------------------------------------------------- 解析

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function numberOrNull(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

/** 封板时间：字符串原样保留；数字补足到 6 位（HHMMSS），不换算时区。 */
function sealTime(value: unknown): string | null {
  if (typeof value === 'string') return value.trim() || null
  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(Math.trunc(value)).padStart(6, '0')
  }
  return null
}

function parseStatus(value: unknown, fallback: LimitPoolStatus): LimitPoolStatus {
  return value === 'ok' || value === 'partial' || value === 'missing' ? value : fallback
}

/** envelope 的 `payload`；也接受直接返回载荷的平面接口（容错但不猜结构）。 */
function payloadOf(raw: unknown): Record<string, unknown> {
  const root = record(raw)
  if (!root) throw new ApiError('parse', '涨停样本响应不是对象')
  return record(root.payload) ?? root
}

function parseRow(value: unknown): LimitPoolRow | null {
  const row = record(value)
  if (!row) return null
  const code = text(row.code)
  if (!code) return null
  return {
    code,
    name: text(row.name) || null,
    streak: numberOrNull(row.streak),
    first_seal_time: sealTime(row.first_seal_time),
    last_seal_time: sealTime(row.last_seal_time),
    reason: text(row.reason) || null,
    trade_date: text(row.trade_date) || null,
  }
}

export function parseLimitPool(raw: unknown): LimitPoolPayload {
  const payload = payloadOf(raw)
  const rows = list(payload.rows)
    .map(parseRow)
    .filter((row): row is LimitPoolRow => row !== null)
  const available = payload.available === undefined ? rows.length > 0 : payload.available === true

  return {
    date: text(payload.date),
    available,
    reason: text(payload.reason) || null,
    source_detail: text(payload.source_detail),
    count: numberOrNull(payload.count) ?? rows.length,
    data_source_status: parseStatus(payload.data_source_status, available ? 'ok' : 'missing'),
    rows,
  }
}

// ---------------------------------------------------------------- 传输

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), READ_TIMEOUT_MS)
  const abortFromCaller = () => controller.abort()
  signal?.addEventListener('abort', abortFromCaller)
  try {
    const response = await fetch(path, {
      method: 'GET',
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
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (signal?.aborted) throw new ApiError('timeout', `请求已取消：${path}`)
      throw new ApiError('timeout', `本地接口 ${path} 超时（${READ_TIMEOUT_MS}ms）`)
    }
    throw new ApiError('not-connected', `本地接口 ${path} 未接入：需先启动 ldst run`)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abortFromCaller)
  }
}

export const httpLimitPoolSource: LimitPoolSource = {
  load: async (query, signal) => {
    const params = new URLSearchParams({ date: query.date })
    return parseLimitPool(await request<unknown>(`${LIMIT_POOL_PATH}?${params.toString()}`, signal))
  },
}

/** 端点尚未上线（404）：按「数据源暂缺」处理，不是整屏报错。 */
export function isLimitPoolEndpointMissing(error: unknown): boolean {
  return isEndpointMissing(error)
}

/** 本机服务没起 → 离线态。 */
export function isLimitPoolOffline(error: unknown): boolean {
  return isApiError(error) && error.kind === 'not-connected'
}
