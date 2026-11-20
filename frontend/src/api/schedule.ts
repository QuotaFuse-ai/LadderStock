/**
 * `/api/schedule` 定时任务面（第④步前端入口）。
 *
 * 三条接口都是本机平面 JSON（不套 `LadderEnvelope`），只对回环开放：
 *
 *   GET  /api/schedule/jobs        任务清单 + 今日产物
 *   POST /api/schedule/run         立即跑一次（后台线程，立刻返回）
 *   POST /api/schedule/toggle      开 / 关一个任务
 *
 * 解析口径与 `api/inbox.ts` 一致：缺字段就是 null / 空数组，不补 0、不编条目；
 * 响应不是对象属于契约破坏，直接抛 `ApiError('parse')`。
 */

import { ApiError, isApiError } from '../api'

const READ_TIMEOUT_MS = 8000

export interface ScheduleDigestRef {
  date: string
  path: string
  written_at: string | null
}

export interface ScheduleJob {
  id: string
  name: string
  kind: string
  kind_label: string
  trigger: string
  enabled: boolean
  channel: string
  question: string | null
  symbols: string[]
  alert_pct: number | null
  move_pct: number | null
  min_gap_min: number | null
  latest_digest: ScheduleDigestRef | null
  /** 上一次手动执行的结局（落盘在 data/schedule_runs/<job>.json） */
  last_run: ScheduleRunState | null
  /** 仅 plan_tick 有：推进时允不允许新建任务（默认 false） */
  allow_plan_create: boolean | null
}

/** 后台高频任务（快照 / 同步 / 摘要…）：页面上只读展示，不给开关与执行按钮 */
export interface ScheduleBackgroundJob extends ScheduleJob {
  /** 只在交易时段跑：非交易时段连手动点火都不允许 */
  session_bound: boolean
}

export interface ScheduleRunState {
  status: string
  started_at: string | null
  finished_at: string | null
  detail: string | null
}

export interface SchedulePlanReviewRow {
  at: string
  date: string
  status: string
  /** 这一轮成没成：false 的那几行就是「它到底在哪一步挂的」 */
  ok: boolean
  summary: string
}

export interface SchedulePlan {
  id: string
  goal: string
  symbols: string[]
  label: string
  frequency: string
  expires_at: string
  /** active / paused（连续失败自动暂停）/ done */
  status: string
  runs: number
  /** 连续失败次数；成功会清零，到 `max_failures` 服务端自动暂停 */
  failures: number
  last_error: string
  last_run_at: string
  last_summary: string
  /** 逐轮复盘：日期 + 成没成 + 那一轮的结论 */
  review: SchedulePlanReviewRow[]
}

export interface SchedulePlansPayload {
  ok: boolean
  detail?: string
  items: SchedulePlan[]
}

export interface ScheduleJobsPayload {
  ok: boolean
  detail?: string
  jobs: ScheduleJob[]
  /** 后台高频任务：只读展示，让用户知道快照/同步在按表跑 */
  background: ScheduleBackgroundJob[]
  /** 用户亲口交待过的长期目标（「盯住浦发一周」这类）；没有就是空列表 */
  plans: SchedulePlansPayload
  warnings: string[]
  source: string | null
  today: string | null
}

export interface RunResult {
  id: string
  started: boolean
  status: string
  detail?: string | null
  inbox_id?: string | null
  path?: string | null
}

interface RequestOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  signal?: AbortSignal
  timeoutMs?: number
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, timeoutMs = READ_TIMEOUT_MS } = options
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
      headers: {
        accept: 'application/json',
        'X-LDST-Client': '1',
        ...(body === undefined ? {} : { 'content-type': 'application/json' }),
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

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function parseJob(value: unknown): ScheduleJob | null {
  const row = record(value)
  if (!row) return null
  const id = text(row.id).trim()
  if (!id) return null
  const digest = record(row.latest_digest)
  const run = record(row.last_run)
  return {
    id,
    name: text(row.name).trim() || id,
    kind: text(row.kind).trim(),
    kind_label: text(row.kind_label).trim() || text(row.kind),
    trigger: text(row.trigger).trim(),
    enabled: row.enabled === true,
    channel: text(row.channel, '-'),
    question: text(row.question).trim() || null,
    symbols: list(row.symbols).map((item) => text(item)).filter(Boolean),
    alert_pct: numberOrNull(row.alert_pct),
    move_pct: numberOrNull(row.move_pct),
    min_gap_min: numberOrNull(row.min_gap_min),
    latest_digest: digest
      ? {
          date: text(digest.date),
          path: text(digest.path),
          written_at: text(digest.written_at).trim() || null,
        }
      : null,
    last_run: run ? {
      status: text(run.status, 'unknown'),
      started_at: text(run.started_at).trim() || null,
      finished_at: text(run.finished_at).trim() || null,
      detail: text(run.detail).trim() || null,
    } : null,
    allow_plan_create:
      typeof row.allow_plan_create === 'boolean' ? row.allow_plan_create : null,
  }
}

function parsePlan(value: unknown): SchedulePlan | null {
  const row = record(value)
  if (!row) return null
  const id = text(row.id).trim()
  if (!id) return null
  return {
    id,
    goal: text(row.goal).trim(),
    symbols: list(row.symbols).map((item) => text(item)).filter(Boolean),
    label: text(row.label).trim() || id,
    frequency: text(row.frequency).trim() || 'daily',
    expires_at: text(row.expires_at).trim(),
    status: text(row.status).trim() || 'active',
    runs: numberOrNull(row.runs) ?? 0,
    failures: numberOrNull(row.failures) ?? 0,
    last_error: text(row.last_error).trim(),
    last_run_at: text(row.last_run_at).trim(),
    last_summary: text(row.last_summary).trim(),
    review: list(row.review)
      .map((item) => {
        const line = record(item)
        if (!line) return null
        return {
          at: text(line.at).trim(),
          date: text(line.date).trim(),
          status: text(line.status).trim(),
          ok: line.ok === true,
          summary: text(line.summary).trim(),
        }
      })
      .filter((line): line is SchedulePlanReviewRow => line !== null),
  }
}

function parsePlans(value: unknown): SchedulePlansPayload {
  const root = record(value)
  if (!root) return { ok: false, items: [] }
  return {
    ok: root.ok !== false,
    detail: text(root.detail).trim() || undefined,
    items: list(root.items)
      .map(parsePlan)
      .filter((plan): plan is SchedulePlan => plan !== null),
  }
}

export function parseSchedule(raw: unknown): ScheduleJobsPayload {
  const root = record(raw)
  if (!root) throw new ApiError('parse', '定时任务响应不是对象')
  return {
    ok: root.ok !== false,
    detail: text(root.detail).trim() || undefined,
    jobs: list(root.jobs)
      .map(parseJob)
      .filter((job): job is ScheduleJob => job !== null),
    background: list(root.background)
      .map((value) => {
        const job = parseJob(value)
        const row = record(value)
        if (!job) return null
        return { ...job, session_bound: row?.session_bound === true }
      })
      .filter((job): job is ScheduleBackgroundJob => job !== null),
    plans: parsePlans(root.plans),
    warnings: list(root.warnings).map((item) => text(item)).filter(Boolean),
    source: text(root.source).trim() || null,
    today: text(root.today).trim() || null,
  }
}

export async function fetchScheduleJobs(signal?: AbortSignal): Promise<ScheduleJobsPayload> {
  return parseSchedule(await request<unknown>('/api/schedule/jobs', { signal }))
}

export async function runScheduleJob(id: string): Promise<RunResult> {
  const raw = await request<unknown>('/api/schedule/run', { method: 'POST', body: { id } })
  const root = record(raw) ?? {}
  return {
    id: text(root.id, id),
    started: root.started === true,
    status: text(root.status, 'running'),
    detail: text(root.detail).trim() || null,
    inbox_id: text(root.inbox_id).trim() || null,
    path: text(root.path).trim() || null,
  }
}

export async function toggleScheduleJob(id: string, enabled: boolean): Promise<void> {
  await request<unknown>('/api/schedule/toggle', { method: 'POST', body: { id, enabled } })
}

/**
 * 改「跨轮任务推进时允不允许新建任务」的开关（默认关）。
 *
 * 关着：推进只产出一轮研究结论，不会再繁殖一条同标任务；
 * 开着：推进里若出现「持续跟踪…」，会再建一条跨轮任务。
 */
export async function setPlanCreateFlag(id: string, allow: boolean): Promise<void> {
  await request<unknown>('/api/schedule/plan/create-flag', {
    method: 'POST',
    body: { id, allow },
  })
}

/**
 * 改一条跨轮任务的状态：`active`（熔断后恢复，失败计数归零）/ `paused` / `done`。
 *
 * 熔断停下的任务必须能被用户放行——否则「它自己停了」就是死路，用户只能去改 JSON。
 */
export async function setPlanStatus(id: string, status: string): Promise<SchedulePlansPayload> {
  const raw = await request<unknown>('/api/schedule/plan/status', {
    method: 'POST',
    body: { id, status },
  })
  const root = record(raw) ?? {}
  return parsePlans(root.plans)
}
