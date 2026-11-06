/**
 * `/api/inbox` 本机提醒收件箱（DEC-058 / QUOT-432 后端，本页 QUOT-435）。
 *
 * 三条接口都是**本机隐私数据**的平面 JSON（不套 `LadderEnvelope`），
 * 带 `Cache-Control: no-store`，只对回环开放：
 *
 *   GET  /api/inbox?limit=50     收件箱条目 + 未读数 + 会产生提醒的定时任务
 *   POST /api/inbox/{id}/read    标一条已读（不存在返回 404，不静默成功）
 *   POST /api/inbox/read-all     全部标已读，返回本次实际改写条数
 *
 * 解析口径与 `api/radar.ts` 一致：缺字段就是 `null` / 空数组，不补 0、不编条目；
 * 响应不是对象属于契约破坏，直接抛 `ApiError('parse')`，屏幕显示原因而不是空白。
 */

import { ApiError, isApiError } from '../api'

const READ_TIMEOUT_MS = 8000

export interface InboxItem {
  id: string
  /** 产生这条提醒的任务 id */
  job: string
  /** 任务显示名；后端没有时为空串，页面退回 `job` */
  job_label: string
  kind: string
  title: string
  /** 已归一的事实行（后端逐行 strip、丢空行、限长） */
  lines: string[]
  /** 站内链接；外链由页面按「不自动打开」处理 */
  link: string | null
  is_read: boolean
  read_at: string | null
  created_at: string
}

export interface InboxJob {
  id: string
  label: string
  /** 调度表原文：`HH:MM` 时刻或 `30s` 这类间隔 */
  trigger: string
  enabled: boolean
}

export interface InboxPayload {
  local_only: true
  store: string
  items: InboxItem[]
  /** 未读总数由后端 COUNT 得出，不受 `limit` 截断影响 */
  unread: number | null
  jobs: InboxJob[]
}

export interface InboxReadResult {
  item: InboxItem
  unread: number | null
}

export interface InboxReadAllResult {
  updated: number
  read_at: string | null
  unread: number | null
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
      if (signal?.aborted) throw new ApiError('timeout', `请求已取消：${path}`)
      throw new ApiError('timeout', `${path} 超时（${timeoutMs}ms）`)
    }
    throw new ApiError('not-connected', `本机接口不可达：${path}`)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abortFromCaller)
  }
}

// ---------------------------------------------------------------- 解析工具

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

function flag(value: unknown): boolean {
  return value === true
}

function linesOf(value: unknown): string[] {
  return list(value)
    .map((line) => text(line).trim())
    .filter(Boolean)
}

function parseItem(value: unknown): InboxItem | null {
  const row = record(value)
  if (!row) return null
  const id = text(row.id).trim()
  // id 是主键：缺 id 的行没法标已读，整行不渲染，也不编一个假 id
  if (!id) return null
  const readAt = text(row.read_at).trim() || null
  return {
    id,
    job: text(row.job),
    job_label: text(row.job_label),
    kind: text(row.kind, 'digest'),
    title: text(row.title).trim(),
    lines: linesOf(row.lines),
    link: text(row.link).trim() || null,
    is_read: flag(row.is_read) || readAt !== null,
    read_at: readAt,
    created_at: text(row.created_at),
  }
}

function parseJob(value: unknown): InboxJob | null {
  const row = record(value)
  if (!row) return null
  const id = text(row.id).trim()
  if (!id) return null
  return {
    id,
    label: text(row.label).trim() || id,
    trigger: text(row.trigger).trim(),
    enabled: flag(row.enabled),
  }
}

export function parseInbox(raw: unknown): InboxPayload {
  const root = record(raw)
  if (!root) throw new ApiError('parse', '提醒收件箱响应不是对象')
  return {
    local_only: true,
    store: text(root.store, 'local_sqlite'),
    items: list(root.items)
      .map(parseItem)
      .filter((item): item is InboxItem => item !== null),
    unread: numberOrNull(root.unread),
    jobs: list(root.jobs)
      .map(parseJob)
      .filter((job): job is InboxJob => job !== null),
  }
}

function parseItemEnvelope(raw: unknown): InboxItem {
  const root = record(raw)
  const item = parseItem(root?.item)
  if (!root || !item) throw new ApiError('parse', '标已读响应里没有提醒条目')
  return item
}

// ---------------------------------------------------------------- 页面调用

export async function fetchInbox(signal?: AbortSignal): Promise<InboxPayload> {
  return parseInbox(await request<unknown>('/api/inbox', { signal }))
}

/** 标一条已读（幂等）：已读状态在 SQLite 里，页面不缓存。 */
export async function markInboxRead(id: string, signal?: AbortSignal): Promise<InboxReadResult> {
  const raw = await request<unknown>(`/api/inbox/${encodeURIComponent(id)}/read`, {
    method: 'POST',
    signal,
  })
  const root = record(raw)
  return {
    item: parseItemEnvelope(raw),
    unread: numberOrNull(root?.unread),
  }
}

export async function markAllInboxRead(signal?: AbortSignal): Promise<InboxReadAllResult> {
  const raw = await request<unknown>('/api/inbox/read-all', { method: 'POST', signal })
  const root = record(raw)
  if (!root) throw new ApiError('parse', '全部标已读响应不是对象')
  return {
    updated: numberOrNull(root.updated) ?? 0,
    read_at: text(root.read_at).trim() || null,
    unread: numberOrNull(root.unread),
  }
}
