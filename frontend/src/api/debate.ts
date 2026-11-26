/**
 * F7 多空对照（StockSenate 合议）只读客户端。
 *
 * 后端契约（`src/ladderstock/web/senate_actions.py`，前缀 `/api/senate`）：
 *
 *   GET /api/senate/debate/status                 能不能跑、装没装、有几份报告
 *   GET /api/senate/debate/reports?limit=N       报告清单（run_id / code / date / path）
 *   GET /api/senate/debate/{code}?date=          最近（或指定日）报告的归一化视图 + 门控 + 块
 *
 * 404 只有一种语义：**这只还没有合议报告**（不是接口没接、也不是代码不存在）。
 * 页面据此给「去跑一轮合议」的出口，不伪造任何辩论内容。
 *
 * 门控（`gate.passed=false`）不是错误态：报告本身可能 partial / 低质量，
 * 页面必须把「为什么不够格」逐条展示（`gate.reasons`），且绝不用空数组冒充多空论点。
 */

import { ApiError } from '../api'

const TIMEOUT_MS = 20000
const CLIENT_HEADER = { 'X-LDST-Client': '1' }

interface RequestOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  signal?: AbortSignal
  timeoutMs?: number
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, timeoutMs = TIMEOUT_MS } = options
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  const abortFromCaller = () => controller.abort()
  signal?.addEventListener('abort', abortFromCaller)
  try {
    const response = await fetch(path, {
      method,
      signal: controller.signal,
      cache: 'no-store',
      headers: {
        accept: 'application/json',
        ...(body === undefined ? {} : { 'content-type': 'application/json' }),
        ...(method === 'POST' ? CLIENT_HEADER : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
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
        `合议接口 ${path} 返回 HTTP ${response.status}${detail ? `：${detail}` : ''}`,
        response.status,
        detail,
      )
    }
    try {
      return (await response.json()) as T
    } catch {
      throw new ApiError('parse', `合议接口 ${path} 返回的内容不是合法 JSON`)
    }
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (signal?.aborted) throw new ApiError('timeout', `请求已取消：${path}`)
      throw new ApiError('timeout', `合议接口 ${path} 超时（${timeoutMs}ms）`)
    }
    throw new ApiError('not-connected', `合议接口不可达：${path}`)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abortFromCaller)
  }
}

// ---------------------------------------------------------------- 类型

function asRecord(value: unknown, what: string): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new ApiError('parse', `${what}不是对象`)
  }
  return value as Record<string, unknown>
}

function asText(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function asNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function asTextList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

export interface DebateSide {
  side: string
  points: string[]
  rebuttals: string[]
  confidence: number | null
  partial: boolean
  notes: string
}

export interface DebateFailedRole {
  role: string
  reason: string
}

export interface DebateGate {
  passed: boolean
  score: number | null
  threshold: number
  reasons: string[]
  min_points: number
  bull_points: number
  bear_points: number
  failed_roles: DebateFailedRole[]
}

export interface DebateView {
  runId: string | null
  code: string | null
  date: string | null
  generatedAt: string | null
  rating: string | null
  ratingLabel: string | null
  action: string | null
  horizon: string | null
  confidence: number | null
  bull: DebateSide
  bear: DebateSide
  bullRebuttals: string[]
  bearRebuttals: string[]
  flipConditions: string[]
  failedRoles: DebateFailedRole[]
  participants: number
  quality: { score: number | null; threshold: number; lowQuality: boolean; partial: boolean }
  reportDir: string | null
  gate: DebateGate
  blocks: unknown[]
  blocksError: string | null
}

export interface DebateStatus {
  home: string
  homeExists: boolean
  cli: string | null
  cliExists: boolean
  configExists: boolean
  reports: number
  latestRunId: string | null
}

export interface DebateReportRow {
  runId: string
  code: string | null
  date: string | null
  path: string
  hasState: boolean
}

// ---------------------------------------------------------------- 解析

function parseSide(value: unknown): DebateSide {
  const row = value === null || typeof value !== 'object' ? {} : (value as Record<string, unknown>)
  return {
    side: asText(row.side) ?? '',
    points: asTextList(row.points),
    rebuttals: asTextList(row.rebuttals),
    confidence: asNumber(row.confidence),
    partial: Boolean(row.partial),
    notes: asText(row.notes) ?? '',
  }
}

function parseFailedRoles(value: unknown): DebateFailedRole[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (item === null || typeof item !== 'object') return []
    const row = item as Record<string, unknown>
    const role = asText(row.role)
    if (!role) return []
    return [{ role, reason: asText(row.reason) ?? '' }]
  })
}

function parseGate(value: unknown): DebateGate {
  const row = asRecord(value ?? {}, '合议门控')
  return {
    passed: Boolean(row.passed),
    score: asNumber(row.score),
    threshold: asNumber(row.threshold) ?? 70,
    reasons: asTextList(row.reasons),
    min_points: asNumber(row.min_points) ?? 2,
    bull_points: asNumber(row.bull_points) ?? 0,
    bear_points: asNumber(row.bear_points) ?? 0,
    failed_roles: parseFailedRoles(row.failed_roles),
  }
}

export function parseDebateView(payload: unknown, fallbackCode: string | null = null): DebateView {
  const root = asRecord(payload, '合议视图')
  const normalized = asRecord(root.normalized ?? {}, '合议结论')
  const clash = asRecord(normalized.clash ?? {}, '合议交锋')
  const quality = asRecord(normalized.quality ?? {}, '合议质量')
  return {
    runId: asText(root.run_id) ?? asText(normalized.run_id),
    code: asText(root.code) ?? asText(normalized.code) ?? fallbackCode,
    date: asText(root.date) ?? asText(normalized.date),
    generatedAt: asText(normalized.generated_at),
    rating: asText(normalized.rating),
    ratingLabel: asText(normalized.rating_label),
    action: asText(normalized.action),
    horizon: asText(normalized.horizon),
    confidence: asNumber(normalized.confidence),
    bull: parseSide(normalized.bull),
    bear: parseSide(normalized.bear),
    bullRebuttals: asTextList(clash.bull_rebuttals),
    bearRebuttals: asTextList(clash.bear_rebuttals),
    flipConditions: asTextList(normalized.flip_conditions),
    failedRoles: parseFailedRoles(normalized.failed_roles),
    participants: asNumber(normalized.participants) ?? 0,
    quality: {
      score: asNumber(quality.score),
      threshold: asNumber(quality.threshold) ?? 70,
      lowQuality: Boolean(quality.low_quality),
      partial: Boolean(quality.partial),
    },
    reportDir: asText(root.report_dir),
    gate: parseGate(root.gate),
    blocks: Array.isArray(root.blocks) ? root.blocks : [],
    blocksError: asText(root.blocks_error),
  }
}

// ---------------------------------------------------------------- 接口

/** `GET /api/senate/debate/status`：页面用它解释「为什么这轮没有辩论」。 */
export async function loadDebateStatus(signal?: AbortSignal): Promise<DebateStatus> {
  const payload = asRecord(await request<unknown>('/api/senate/debate/status', { signal }), '合议可用性')
  return {
    home: asText(payload.home) ?? '',
    homeExists: Boolean(payload.home_exists),
    cli: asText(payload.cli),
    cliExists: Boolean(payload.cli_exists),
    configExists: Boolean(payload.config_exists),
    reports: asNumber(payload.reports) ?? 0,
    latestRunId: asText(payload.latest_run_id),
  }
}

/** `GET /api/senate/debate/reports`：报告清单（按 mtime 倒序）。 */
export async function loadDebateReports(limit = 20, signal?: AbortSignal): Promise<DebateReportRow[]> {
  const payload = asRecord(
    await request<unknown>(`/api/senate/debate/reports?limit=${limit}`, { signal }),
    '合议报告清单',
  )
  const items = Array.isArray(payload.items) ? payload.items : []
  return items.flatMap((item) => {
    if (item === null || typeof item !== 'object') return []
    const row = item as Record<string, unknown>
    const runId = asText(row.run_id)
    const path = asText(row.path)
    if (!runId || !path) return []
    return [
      {
        runId,
        code: asText(row.code),
        date: asText(row.date),
        path,
        hasState: Boolean(row.has_state),
      },
    ]
  })
}

/**
 * `GET /api/senate/debate/{code}`：最近（或指定日）报告。
 *
 * 404 = 这只还没有合议报告，返回 `null`（业务空态），其余错误照常抛出。
 */
export async function loadDebate(
  code: string,
  date?: string | null,
  signal?: AbortSignal,
): Promise<DebateView | null> {
  const query = date ? `?date=${encodeURIComponent(date)}` : ''
  try {
    const payload = await request<unknown>(
      `/api/senate/debate/${encodeURIComponent(code)}${query}`,
      { signal },
    )
    return parseDebateView(payload, code)
  } catch (error) {
    if (error instanceof ApiError && error.kind === 'http' && error.status === 404) return null
    throw error
  }
}

/** `POST /api/senate/analysis`：起一轮后台合议（分钟级，不阻塞页面）。 */
export async function startSenateAnalysis(
  code: string,
  date?: string | null,
  signal?: AbortSignal,
): Promise<{ taskId: string | null }> {
  const payload = asRecord(
    await request<unknown>('/api/senate/analysis', {
      method: 'POST',
      body: { code, date: date ?? null },
      signal,
      timeoutMs: 30000,
    }),
    '合议任务',
  )
  return { taskId: asText(payload.task_id) ?? asText(payload.id) }
}
