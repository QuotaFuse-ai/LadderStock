/**
 * FinanceCoffer 读侧指标客户端（估值口径）。
 *
 * 只做两件事：按 `valuation_basis` 取数、把响应解析成显式结构。
 * PE / PB / ROE 全部由 FinanceCoffer 服务端计算（`GET /v1/structured/{symbol}`），
 * 本模块**不复制任何公式、不连 FC 数据库、不读宽表**。
 *
 * 口径取值只有 `snapshot`（默认）/ `reference` 两种。响应回显：
 * - `basis`：本次实际生效口径；
 * - `default_basis`：构建期默认口径（服务端 `FC_METRICS_PROFILE`）；
 * - `available_bases`：该标的已构建的口径。
 *
 * 请求了未构建的口径时，服务端返回 `*_reason=basis_not_built` 且数值为 null。
 * 解析层原样保留 null 与原因码，**绝不回落到另一口径的数字**（`pe_cross` 只做对照，不参与展示）。
 */

import { ApiError } from '../api'

export type ValuationBasis = 'snapshot' | 'reference'

export const VALUATION_BASES: readonly ValuationBasis[] = ['snapshot', 'reference']

export function isValuationBasis(value: unknown): value is ValuationBasis {
  return typeof value === 'string' && (VALUATION_BASES as readonly string[]).includes(value)
}

/** 口径的界面文案：中文在前、协议值在后，切换控件与回显共用同一套。 */
export const VALUATION_BASIS_LABEL: Record<ValuationBasis, string> = {
  snapshot: '快照 snapshot',
  reference: '对标 reference',
}

export const DEFAULT_FC_API_BASE = '/fc-api'
const STRUCTURED_PATH = '/v1/structured'
const DEFAULT_TIMEOUT_MS = 8000

/**
 * FC 读侧地址。默认走同源 `/fc-api`（由本地壳或反代指向 FC），
 * 部署时可用 `VITE_FC_API_BASE` 覆盖为绝对地址（需 FC 放行 CORS）。
 */
export function resolveFinanceCofferBase(override?: string): string {
  const raw = (override ?? (import.meta.env.VITE_FC_API_BASE as string | undefined) ?? '').trim()
  return (raw || DEFAULT_FC_API_BASE).replace(/\/+$/, '')
}

/** `GET /v1/structured/{symbol}` 的读侧模型（字段与 QUOT-244 合入契约一一对应）。 */
export interface StructuredMetrics {
  symbol: string
  basis: ValuationBasis
  default_basis: ValuationBasis
  available_bases: ValuationBasis[]
  pe: number | null
  pe_source: string | null
  pe_reason: string | null
  /** 另一口径的对照值，只用于审计；界面 v1 不展示，更不拿它补缺。 */
  pe_cross: number | null
  pb: number | null
  pb_reason: string | null
  roe: number | null
  roe_reason: string | null
  price_date: string | null
  price_close: string | null
  report_date: string | null
  quarter_basis: string | null
}

function parseError(message: string): ApiError {
  return new ApiError('parse', message)
}

function toNumber(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  // Decimal 可能被序列化成字符串（如 "15.25"），这是同一语义、不是补值
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function toText(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

function parseBases(value: unknown): ValuationBasis[] {
  if (!Array.isArray(value)) {
    throw parseError('FinanceCoffer 响应缺少 available_bases 数组')
  }
  const bases: ValuationBasis[] = []
  for (const item of value) {
    if (isValuationBasis(item) && !bases.includes(item)) bases.push(item)
  }
  return bases
}

/**
 * 严格解析：`basis` / `default_basis` / `available_bases` 是契约字段，缺失即报解析错误，
 * 不猜、不用默认值补齐。数值与原因码按原样保留（null 就是 null）。
 */
export function parseStructuredMetrics(payload: unknown, symbol: string): StructuredMetrics {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw parseError('FinanceCoffer 响应不是 JSON 对象')
  }
  const record = payload as Record<string, unknown>
  if (!isValuationBasis(record.basis)) {
    throw parseError('FinanceCoffer 响应缺少合法 basis（snapshot | reference）')
  }
  if (!isValuationBasis(record.default_basis)) {
    throw parseError('FinanceCoffer 响应缺少合法 default_basis（snapshot | reference）')
  }
  return {
    symbol,
    basis: record.basis,
    default_basis: record.default_basis,
    available_bases: parseBases(record.available_bases),
    pe: toNumber(record.pe),
    pe_source: toText(record.pe_source),
    pe_reason: toText(record.pe_reason),
    pe_cross: toNumber(record.pe_cross),
    pb: toNumber(record.pb),
    pb_reason: toText(record.pb_reason),
    roe: toNumber(record.roe),
    roe_reason: toText(record.roe_reason),
    price_date: toText(record.price_date),
    price_close: toText(record.price_close),
    report_date: toText(record.report_date),
    quarter_basis: toText(record.quarter_basis),
  }
}

export interface FetchStructuredOptions {
  baseUrl?: string
  signal?: AbortSignal
  timeoutMs?: number
}

function structuredUrl(symbol: string, basis: ValuationBasis, base: string): string {
  const query = new URLSearchParams({ valuation_basis: basis })
  return `${base}${STRUCTURED_PATH}/${encodeURIComponent(symbol)}?${query.toString()}`
}

/**
 * 读侧取数。每次切换口径都会带 `valuation_basis` 重新请求，不做跨口径缓存。
 * 未接入 / 超时 / HTTP 错误一律抛 `ApiError`，由界面显示原因并给重试，不返回空壳数据。
 */
export async function fetchStructuredMetrics(
  symbol: string,
  basis: ValuationBasis,
  options: FetchStructuredOptions = {},
): Promise<StructuredMetrics> {
  const url = structuredUrl(symbol, basis, resolveFinanceCofferBase(options.baseUrl))
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  const abortFromCaller = () => controller.abort()
  options.signal?.addEventListener('abort', abortFromCaller)
  try {
    const response = await fetch(url, {
      method: 'GET',
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
        `FinanceCoffer ${url} 返回 HTTP ${response.status}${detail ? `：${detail}` : ''}`,
        response.status,
        detail,
      )
    }
    let payload: unknown
    try {
      payload = await response.json()
    } catch {
      throw parseError(`FinanceCoffer ${url} 返回的内容不是合法 JSON`)
    }
    return parseStructuredMetrics(payload, symbol)
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (options.signal?.aborted) throw new ApiError('timeout', `请求已取消：${url}`)
      throw new ApiError('timeout', `FinanceCoffer ${url} 超时（${timeoutMs}ms）`)
    }
    throw new ApiError('not-connected', `FinanceCoffer 未接入：${url} 不可达`)
  } finally {
    clearTimeout(timer)
    options.signal?.removeEventListener('abort', abortFromCaller)
  }
}

/** 与 LadderStock 其他取数一致的「结论 + 原因 + 代码」文案。 */
export function describeFinanceCofferError(error: unknown): {
  title: string
  detail: string
  code: string | null
} {
  if (error instanceof ApiError) {
    if (error.kind === 'not-connected') {
      return {
        title: 'FinanceCoffer 未接入',
        detail:
          '先启动 FC 读侧 API，并让 /fc-api 指向它；也可用 VITE_FC_API_BASE 指定地址（需 FC 放行 CORS）。',
        code: null,
      }
    }
    if (error.kind === 'timeout') {
      return { title: '估值取数超时', detail: error.message, code: 'TIMEOUT' }
    }
    if (error.kind === 'parse') {
      return { title: '估值响应解析失败', detail: error.message, code: 'E_PARSE' }
    }
    return {
      title: '估值取数失败',
      detail: error.detail ?? error.message,
      code: error.status ? `HTTP ${error.status}` : null,
    }
  }
  return {
    title: '估值取数失败',
    detail: error instanceof Error ? error.message : String(error),
    code: null,
  }
}
