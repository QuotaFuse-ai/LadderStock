/**
 * `/api/coverage` F12 数据集覆盖看板 + 受控补采。
 *
 *   GET  /api/coverage/board?dataset=    62 个数据集逐个的落盘情况
 *   GET  /api/coverage/gaps              只挑需要处理的
 *   GET  /api/coverage/plan?dataset=     这批补采会请求什么（不发请求）
 *   POST /api/coverage/backfill?dataset=&execute=false  默认真跑=false
 *
 * 解析口径与 `api/brief.ts` 一致：缺字段就是 `null` / 空数组，不补 0；
 * 顶层不是对象属于契约破坏，直接抛 `ApiError('parse')`。
 * `execute` 默认 false 是刻意的：后端缺省也是 dry-run，两层都不替用户拍板。
 */

import { ApiError, isApiError } from '../api'

const READ_TIMEOUT_MS = 8000
/** board 现场扫 62 张表，实测 0.3 秒；真跑补采要等上游，给 45 秒 */
const BOARD_TIMEOUT_MS = 12000
const BACKFILL_TIMEOUT_MS = 45000

export interface CoverageRow {
  dataset: string
  table: string
  registered: boolean
  primary: string
  rows: number
  codes: number
  latest: string | null
  age_days: number | null
  date_column: string | null
  pool_codes: number
  pool_covered: number
  pool_relevant: boolean
  /** 池内覆盖比例；事件型 / 非逐票为 null（页面显示「—」） */
  coverage: number | null
  state: string
  reason: string
}

export interface CoverageSummary {
  total: number
  counts: Record<string, number>
  ok: number
  /** 缺口数（不含「未在 sync.yaml 注册」的未接入数据集） */
  needs_attention: number
  unregistered: number
}

export interface CoverageBoard {
  local_only: true
  generated_at: string
  elapsed_ms: number
  pool: { size: number; codes: string[]; truncated: boolean }
  declared: number
  datasets: CoverageRow[]
  gaps: CoverageRow[]
  /** 未在 sync.yaml 注册的数据集（还没接入，不是缺口） */
  unregistered: CoverageRow[]
  summary: CoverageSummary
  labels: Record<string, string>
}

export interface CoveragePlan {
  dataset: string
  primary: string
  fallback: string[]
  per_code: boolean
  codes: string[]
  code_count: number
  max_codes: number
  requests_hint: string
}

export interface CoverageBackfill extends CoveragePlan {
  execute: boolean
  root: string
  status: string
  hint?: string
  detail?: string
  rows_stored?: number | null
  total_rows?: number | null
  source?: string | null
  elapsed_ms?: number
  sync?: Record<string, number>
}

// ---------------------------------------------------------------- 请求

interface RequestOptions {
  method?: 'GET' | 'POST'
  signal?: AbortSignal
  timeoutMs?: number
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', signal, timeoutMs = READ_TIMEOUT_MS } = options
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  const abortFromCaller = () => controller.abort()
  signal?.addEventListener('abort', abortFromCaller)
  try {
    const response = await fetch(path, {
      method,
      signal: controller.signal,
      cache: 'no-store',
      credentials: 'same-origin',
      headers: { accept: 'application/json', 'X-LDST-Client': '1' },
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
      if (signal?.aborted) throw new ApiError('timeout', `请求已取消：${path}`)
      throw new ApiError('timeout', `${path} 超时（${timeoutMs}ms）`)
    }
    throw new ApiError('not-connected', `本机接口不可达：${path}`)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abortFromCaller)
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
  if (typeof value === 'string') return value
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return fallback
}

function numberOr(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function nullableNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function nullableText(value: unknown): string | null {
  const raw = text(value).trim()
  return raw || null
}

function boolOr(value: unknown, fallback: boolean): boolean {
  if (typeof value === 'boolean') return value
  return fallback
}

function strings(value: unknown): string[] {
  return list(value)
    .map((item) => text(item).trim())
    .filter(Boolean)
}

export function parseRow(value: unknown): CoverageRow | null {
  const row = record(value)
  if (!row) return null
  const dataset = text(row.dataset).trim()
  // 没有数据集名的行无法定位，宁可不渲染
  if (!dataset) return null
  return {
    dataset,
    table: text(row.table, dataset),
    registered: boolOr(row.registered, false),
    primary: text(row.primary),
    rows: numberOr(row.rows, 0),
    codes: numberOr(row.codes, 0),
    latest: nullableText(row.latest),
    age_days: nullableNumber(row.age_days),
    date_column: nullableText(row.date_column),
    pool_codes: numberOr(row.pool_codes, 0),
    pool_covered: numberOr(row.pool_covered, 0),
    pool_relevant: boolOr(row.pool_relevant, false),
    coverage: nullableNumber(row.coverage),
    state: text(row.state, 'unknown'),
    reason: text(row.reason),
  }
}

function parseSummary(value: unknown): CoverageSummary {
  const row = record(value) ?? {}
  const counts: Record<string, number> = {}
  const rawCounts = record(row.counts) ?? {}
  for (const [key, entry] of Object.entries(rawCounts)) {
    counts[key] = numberOr(entry, 0)
  }
  return {
    total: numberOr(row.total, 0),
    counts,
    ok: numberOr(row.ok, 0),
    needs_attention: numberOr(row.needs_attention, 0),
    unregistered: numberOr(row.unregistered, counts.unregistered ?? 0),
  }
}

export function parseBoard(raw: unknown): CoverageBoard {
  const root = record(raw)
  if (!root) throw new ApiError('parse', '覆盖看板响应不是对象')
  const pool = record(root.pool) ?? {}
  const labels: Record<string, string> = {}
  const rawLabels = record(root.labels) ?? {}
  for (const [key, entry] of Object.entries(rawLabels)) {
    labels[key] = text(entry, key)
  }
  return {
    local_only: true,
    generated_at: text(root.generated_at),
    elapsed_ms: numberOr(root.elapsed_ms, 0),
    pool: {
      size: numberOr(pool.size, 0),
      codes: strings(pool.codes),
      truncated: boolOr(pool.truncated, false),
    },
    declared: numberOr(root.declared, 0),
    datasets: list(root.datasets)
      .map(parseRow)
      .filter((row): row is CoverageRow => row !== null),
    gaps: list(root.gaps)
      .map(parseRow)
      .filter((row): row is CoverageRow => row !== null),
    unregistered: list(root.unregistered)
      .map(parseRow)
      .filter((row): row is CoverageRow => row !== null),
    summary: parseSummary(root.summary),
    labels,
  }
}

function parsePlan(raw: unknown): CoveragePlan {
  const row = record(raw)
  if (!row) throw new ApiError('parse', '补采计划响应不是对象')
  return {
    dataset: text(row.dataset),
    primary: text(row.primary),
    fallback: strings(row.fallback),
    per_code: boolOr(row.per_code, true),
    codes: strings(row.codes),
    code_count: numberOr(row.code_count, 0),
    max_codes: numberOr(row.max_codes, 100),
    requests_hint: text(row.requests_hint),
  }
}

// ---------------------------------------------------------------- 页面调用

export async function fetchCoverageBoard(signal?: AbortSignal): Promise<CoverageBoard> {
  return parseBoard(
    await request<unknown>('/api/coverage/board', { signal, timeoutMs: BOARD_TIMEOUT_MS }),
  )
}

export async function fetchCoverageGaps(signal?: AbortSignal): Promise<CoverageRow[]> {
  const raw = await request<unknown>('/api/coverage/gaps', { signal, timeoutMs: BOARD_TIMEOUT_MS })
  return list(record(raw)?.gaps)
    .map(parseRow)
    .filter((row): row is CoverageRow => row !== null)
}

export async function fetchCoveragePlan(
  dataset: string,
  options: { codes?: string[]; maxCodes?: number; signal?: AbortSignal } = {},
): Promise<CoveragePlan> {
  const { codes, maxCodes, signal } = options
  const query = new URLSearchParams({ dataset })
  if (codes?.length) query.set('codes', codes.join(','))
  if (maxCodes) query.set('max_codes', String(maxCodes))
  return parsePlan(await request<unknown>(`/api/coverage/plan?${query.toString()}`, { signal }))
}

/** `execute` 必须显式传 true，页面按钮的二次确认就是这一层。 */
export async function runCoverageBackfill(
  dataset: string,
  options: { execute: boolean; codes?: string[]; maxCodes?: number; signal?: AbortSignal },
): Promise<CoverageBackfill> {
  const { execute, codes, maxCodes, signal } = options
  const query = new URLSearchParams({ dataset, execute: execute ? 'true' : 'false' })
  if (codes?.length) query.set('codes', codes.join(','))
  if (maxCodes) query.set('max_codes', String(maxCodes))
  const raw = await request<unknown>(`/api/coverage/backfill?${query.toString()}`, {
    method: 'POST',
    signal,
    timeoutMs: BACKFILL_TIMEOUT_MS,
  })
  const row = record(raw)
  if (!row) throw new ApiError('parse', '补采响应不是对象')
  const plan = parsePlan(row)
  return {
    ...plan,
    execute: boolOr(row.execute, false),
    root: text(row.root),
    status: text(row.status, 'unknown'),
    hint: nullableText(row.hint) ?? undefined,
    detail: nullableText(row.detail) ?? undefined,
    rows_stored: nullableNumber(row.rows_stored),
    total_rows: nullableNumber(row.total_rows),
    source: nullableText(row.source),
    elapsed_ms: nullableNumber(row.elapsed_ms) ?? undefined,
    sync: (record(row.sync) ?? undefined) as Record<string, number> | undefined,
  }
}
