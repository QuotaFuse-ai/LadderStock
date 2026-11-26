/**
 * `/api/brief` F11 简报面（晨报 / 收盘复盘 / 持仓异动）。
 *
 *   GET  /api/brief/today?kind=     今日三份简报的实时内容 + 当天状态
 *   GET  /api/brief/history?limit=  最近几天的推送状态
 *   GET  /api/brief/config          通知开关
 *   PUT  /api/brief/config          改开关
 *   POST /api/brief/{kind}/run      生成 → 去重 → 收件箱 →（可选）投递
 *
 * 解析口径与 `api/inbox.ts` 一致：缺字段就是 `null` / 空数组，不补 0；
 * 顶层不是对象属于契约破坏，直接抛 `ApiError('parse')`。
 * 写请求带 `X-LDST-Client: 1`：后端写面要求同源证据，本机页面的 Origin 是
 * `http://127.0.0.1:<port>`，两边任一即可通过。
 */

import { ApiError, isApiError } from '../api'

const READ_TIMEOUT_MS = 8000
const RUN_TIMEOUT_MS = 20000

export interface BriefItem {
  kind: string
  title: string
  lines: string[]
  /** 取数出处（如 `daily_k · quotes.daily_change_map`），页面必须显示 */
  source: string
  link: string | null
  /** 内容指纹：同日重复生成时用它去重 */
  fingerprint: string
}

export interface BriefView {
  job: string
  kind: string
  label: string
  trade_day: string
  status: string
  detail: string
  generated_at: string
  title: string
  items: BriefItem[]
}

export interface BriefRunRecord {
  kind: string
  label: string
  status: string
  detail: string
  items: number
  pushed: number
  generated_at: string
}

export interface BriefSettings {
  notify: boolean
  channel: string
  threshold: number
}

export interface BriefPayload {
  local_only: true
  day: string
  settings: BriefSettings
  briefs: BriefView[]
  published: { fingerprint: string; title: string; kind: string; job: string; at: string }[]
  runs: BriefRunRecord[]
}

export interface BriefHistoryDay {
  trade_day: string
  briefs: BriefRunRecord[]
  published: number
  path: string
}

export interface BriefRunResult {
  ok: boolean
  kind: string
  trade_day: string
  title: string
  detail: string
  pushed: { title: string; fingerprint: string }[]
  skipped: { title: string; fingerprint: string; reason: string }[]
  delivered: { channel: string; ok: boolean; detail: string }[]
  state_path: string
  brief: BriefView
}

// ---------------------------------------------------------------- 请求

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT'
  signal?: AbortSignal
  timeoutMs?: number
  body?: unknown
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', signal, timeoutMs = READ_TIMEOUT_MS, body } = options
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  const abortFromCaller = () => controller.abort()
  signal?.addEventListener('abort', abortFromCaller)
  try {
    const headers: Record<string, string> = {
      accept: 'application/json',
      'X-LDST-Client': '1',
    }
    if (body !== undefined) headers['content-type'] = 'application/json'
    const response = await fetch(path, {
      method,
      signal: controller.signal,
      cache: 'no-store',
      credentials: 'same-origin',
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
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

function boolOr(value: unknown, fallback: boolean): boolean {
  if (typeof value === 'boolean') return value
  return fallback
}

function numberOr(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function lines(value: unknown): string[] {
  return list(value)
    .map((line) => text(line).trim())
    .filter(Boolean)
}

function parseItem(value: unknown): BriefItem | null {
  const row = record(value)
  if (!row) return null
  const title = text(row.title).trim()
  // 没有标题的条目没有意义：宁可少一条，也不渲染一个空壳
  if (!title) return null
  return {
    kind: text(row.kind),
    title,
    lines: lines(row.lines),
    source: text(row.source),
    link: text(row.link).trim() || null,
    fingerprint: text(row.fingerprint),
  }
}

function parseView(value: unknown): BriefView | null {
  const row = record(value)
  if (!row) return null
  const kind = text(row.kind).trim()
  if (!kind) return null
  return {
    job: text(row.job),
    kind,
    label: text(row.label, kind),
    trade_day: text(row.trade_day),
    status: text(row.status, 'unknown'),
    detail: text(row.detail),
    generated_at: text(row.generated_at),
    title: text(row.title),
    items: list(row.items)
      .map(parseItem)
      .filter((item): item is BriefItem => item !== null),
  }
}

function parseSettings(value: unknown): BriefSettings {
  const row = record(value) ?? {}
  return {
    notify: boolOr(row.notify, false),
    channel: text(row.channel, 'console'),
    threshold: numberOr(row.threshold, 5),
  }
}

export function parseBriefToday(raw: unknown): BriefPayload {
  const root = record(raw)
  if (!root) throw new ApiError('parse', '简报响应不是对象')
  return {
    local_only: true,
    day: text(root.day),
    settings: parseSettings(root.settings),
    briefs: list(root.briefs)
      .map(parseView)
      .filter((view): view is BriefView => view !== null),
    published: list(root.published)
      .map((value) => {
        const row = record(value)
        if (!row) return null
        return {
          fingerprint: text(row.fingerprint),
          title: text(row.title),
          kind: text(row.kind),
          job: text(row.job),
          at: text(row.at),
        }
      })
      .filter((row): row is NonNullable<typeof row> => row !== null),
    runs: list(root.runs).map((value) => {
      const row = record(value) ?? {}
      return {
        kind: text(row.kind),
        label: text(row.label),
        status: text(row.status),
        detail: text(row.detail),
        items: numberOr(row.items, 0),
        pushed: numberOr(row.pushed, 0),
        generated_at: text(row.generated_at),
      }
    }),
  }
}

// ---------------------------------------------------------------- 页面调用

export async function fetchBriefToday(signal?: AbortSignal): Promise<BriefPayload> {
  return parseBriefToday(await request<unknown>('/api/brief/today', { signal }))
}

export async function fetchBriefHistory(
  limit = 7,
  signal?: AbortSignal,
): Promise<BriefHistoryDay[]> {
  const raw = await request<unknown>(`/api/brief/history?limit=${limit}`, { signal })
  return list(record(raw)?.days).map((value) => {
    const row = record(value) ?? {}
    return {
      trade_day: text(row.trade_day),
      briefs: list(row.briefs).map((entry) => {
        const item = record(entry) ?? {}
        return {
          kind: text(item.kind),
          label: text(item.label),
          status: text(item.status),
          detail: text(item.detail),
          items: numberOr(item.items, 0),
          pushed: numberOr(item.pushed, 0),
          generated_at: text(item.generated_at),
        }
      }),
      published: numberOr(row.published, 0),
      path: text(row.path),
    }
  })
}

export async function fetchBriefConfig(signal?: AbortSignal): Promise<BriefSettings> {
  const raw = await request<unknown>('/api/brief/config', { signal })
  return parseSettings(record(raw)?.settings)
}

export async function updateBriefConfig(
  changes: Partial<BriefSettings>,
  signal?: AbortSignal,
): Promise<BriefSettings> {
  const raw = await request<unknown>('/api/brief/config', {
    method: 'PUT',
    signal,
    body: changes,
  })
  return parseSettings(record(raw)?.settings)
}

/** 生成并发布一份简报：后端去重，重复调用不会重复推送。 */
export async function runBrief(
  kind: string,
  options: { notify?: boolean; signal?: AbortSignal } = {},
): Promise<BriefRunResult> {
  const { notify, signal } = options
  const query = notify === undefined ? '' : `?notify=${notify ? 1 : 0}`
  const raw = await request<unknown>(`/api/brief/${encodeURIComponent(kind)}/run${query}`, {
    method: 'POST',
    signal,
    timeoutMs: RUN_TIMEOUT_MS,
  })
  const root = record(raw)
  if (!root) throw new ApiError('parse', '简报运行响应不是对象')
  const view = parseView(root.brief)
  if (!view) throw new ApiError('parse', '简报运行响应里没有简报内容')
  return {
    ok: boolOr(root.ok, false),
    kind: text(root.kind),
    trade_day: text(root.trade_day),
    title: text(root.title),
    detail: text(root.detail),
    pushed: list(root.pushed).map((value) => {
      const row = record(value) ?? {}
      return { title: text(row.title), fingerprint: text(row.fingerprint) }
    }),
    skipped: list(root.skipped).map((value) => {
      const row = record(value) ?? {}
      return {
        title: text(row.title),
        fingerprint: text(row.fingerprint),
        reason: text(row.reason),
      }
    }),
    delivered: list(root.delivered).map((value) => {
      const row = record(value) ?? {}
      return { channel: text(row.channel), ok: boolOr(row.ok, false), detail: text(row.detail) }
    }),
    state_path: text(root.state_path),
    brief: view,
  }
}
