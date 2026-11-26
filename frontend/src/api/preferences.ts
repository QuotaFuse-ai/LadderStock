/**
 * `/api/preferences` 偏好面：让「我记得你的偏好」变成能改、能删的东西。
 *
 * 四条接口都是本机平面 JSON，只对回环开放：
 *
 *   GET  /api/preferences         我记住的全部（含类别中文名、被提到几次）
 *   POST /api/preferences/add     自己写一条（不经过抽取规则）
 *   POST /api/preferences/delete  删一条（按文本匹配）
 *   POST /api/preferences/clear   一次清空
 *
 * 解析口径与 `api/schedule.ts` 一致：缺字段是 null / 空数组，不补默认值、不编条目；
 * 响应不是对象属于契约破坏，直接抛 `ApiError('parse')`。
 */

import { ApiError, isApiError } from '../api'

const READ_TIMEOUT_MS = 8000

export interface Preference {
  text: string
  kind: string
  /** 类别中文名由服务端给（`【关注范围】`），前端不自己维护一份表 */
  kind_label: string
  hits: number
  created_at: string
  updated_at: string
}

export interface PreferencesPayload {
  ok: boolean
  count: number
  items: Preference[]
  labels: Record<string, string>
  /** 偏好文件位置：用户想备份或直接看一眼时有路径可查 */
  path: string | null
  max_items: number
}

interface RequestOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  timeoutMs?: number
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, timeoutMs = READ_TIMEOUT_MS } = options
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
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
      throw new ApiError('timeout', `${path} 超时（${timeoutMs}ms）`)
    }
    throw new ApiError('not-connected', `本机接口不可达：${path}`)
  } finally {
    clearTimeout(timer)
  }
}

function record(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function text(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return fallback
}

function count(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function parseLabels(value: unknown): Record<string, string> {
  const raw = record(value)
  if (!raw) return {}
  const out: Record<string, string> = {}
  for (const [key, label] of Object.entries(raw)) {
    const resolved = text(label)
    if (resolved) out[key] = resolved
  }
  return out
}

function parseItem(value: unknown): Preference | null {
  const raw = record(value)
  if (!raw) return null
  const body = text(raw.text)
  if (!body) return null
  const kind = text(raw.kind)
  return {
    text: body,
    kind,
    kind_label: text(raw.kind_label, kind),
    hits: count(raw.hits),
    created_at: text(raw.created_at),
    updated_at: text(raw.updated_at),
  }
}

export function parsePreferences(value: unknown): PreferencesPayload {
  const raw = record(value)
  if (!raw) throw new ApiError('parse', '/api/preferences 返回的内容不是对象')
  const items = Array.isArray(raw.items)
    ? raw.items.map(parseItem).filter((item): item is Preference => item !== null)
    : []
  return {
    ok: raw.ok !== false,
    count: count(raw.count) || items.length,
    items,
    labels: parseLabels(raw.labels),
    path: typeof raw.path === 'string' ? raw.path : null,
    max_items: count(raw.max_items),
  }
}

export async function fetchPreferences(): Promise<PreferencesPayload> {
  return parsePreferences(await request<unknown>('/api/preferences'))
}

export async function addPreference(bodyText: string, kind?: string): Promise<PreferencesPayload> {
  if (!bodyText.trim()) throw new ApiError('parse', '空白偏好不写')
  return parsePreferences(
    await request<unknown>('/api/preferences/add', {
      method: 'POST',
      body: kind ? { text: bodyText, kind } : { text: bodyText },
    }),
  )
}

export async function deletePreference(bodyText: string): Promise<PreferencesPayload> {
  return parsePreferences(
    await request<unknown>('/api/preferences/delete', { method: 'POST', body: { text: bodyText } }),
  )
}

export async function clearPreferences(): Promise<PreferencesPayload> {
  return parsePreferences(await request<unknown>('/api/preferences/clear', { method: 'POST' }))
}
