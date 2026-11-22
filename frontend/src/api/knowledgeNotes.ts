/**
 * 知识库笔记 / 跨票检索 / 估值换算（E 包 / DEC-091，A 包前端同族写法）。
 *
 * 三面都是**本机面**的平面 JSON（不是 envelope）：
 * - 笔记 CRUD：`GET|POST /api/knowledge/notes`、`PUT|DELETE /api/knowledge/notes/{id}`、
 *   `GET /api/knowledge/notes/tags`——回环 + Host 读，写再加同源来源证据
 *   （浏览器同源页面自动满足，只发普通 fetch，与 `api/alert.ts` 同理）；
 * - 跨票检索：`GET /api/knowledge/search?q=`——向量层优先，不可用时后端降级
 *   本地 LIKE；**来源分开报**（`engine` + 每条 `source`），页面不把降级
 *   伪装成语义命中；
 * - 估值换算：`POST /api/valuation/estimate`——纯函数、不取数不写库，
 *   输出恒带 `disclaimer`「这是换算器，不是估值判断」（DEC-025 红线：研究
 *   管线的估值段不碰这里的任何输出）。
 *
 * 解析口径：缺字段就是 `null` / 空数组，不补 0；响应不是对象抛
 * `ApiError('parse')`。
 */

import { ApiError } from '../api'

const TIMEOUT_MS = 8000

// ---------------------------------------------------------------- 类型

export interface KnowledgeNote {
  id: number
  market: string
  symbol: string
  /** 与档案 key 同族的 `market:symbol`（随手记可为空串） */
  target: string
  title: string
  body: string
  tags: string[]
  created_at: string
  updated_at: string
}

export interface NoteDraft {
  body: string
  target?: string
  title?: string
  tags?: string[]
}

export interface TagCount {
  tag: string
  notes: number
}

export interface SearchHit {
  market: string
  symbol: string
  as_of: string | null
  status: string | null
  section: string | null
  score: number | null
  snippet: string
  /** `vector`（语义）或 `local`（字面降级）——页面分开标注 */
  source: string
}

export interface SearchPayload {
  q: string
  engine: string
  vector_error: string | null
  total: number
  hits: SearchHit[]
}

export interface EstimateInput {
  kind: string
  price?: number
  eps?: number
  bps?: number
  sps?: number
  dps?: number
  pe?: number
  fcf?: number
  growth?: number
  terminal_growth?: number
  discount?: number
  periods?: number
}

export interface EstimateResult {
  kind: string
  value: number
  value_pct?: string
  price?: number
  per_share?: number
  pv_explicit?: number
  pv_terminal?: number
  terminal_share?: number | null
  warnings?: string[]
  disclaimer: string
}

// ---------------------------------------------------------------- 请求

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  body?: unknown
  timeoutMs?: number
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, timeoutMs = TIMEOUT_MS } = options
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(path, {
      method,
      signal: controller.signal,
      cache: 'no-store',
      credentials: 'same-origin',
      headers: body === undefined ? { accept: 'application/json' } : {
        accept: 'application/json',
        'content-type': 'application/json',
      },
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
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('timeout', `${path} 超时（${timeoutMs}ms）`)
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

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function intOr(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.trunc(value) : fallback
}

export function parseNote(row: unknown): KnowledgeNote {
  const src = record(row) ?? {}
  const market = text(src.market)
  const symbol = text(src.symbol)
  return {
    id: intOr(src.id, 0),
    market,
    symbol,
    target: text(src.target) || (market && symbol ? `${market}:${symbol}` : symbol),
    title: text(src.title),
    body: text(src.body),
    tags: list(src.tags).map((item) => text(item)).filter((item) => item.length > 0),
    created_at: text(src.created_at),
    updated_at: text(src.updated_at),
  }
}

function parseTag(row: unknown): TagCount | null {
  const src = record(row) ?? {}
  const tag = text(src.tag)
  if (!tag) return null
  return { tag, notes: intOr(src.notes, 0) }
}

export function parseHit(row: unknown): SearchHit {
  const src = record(row) ?? {}
  return {
    market: text(src.market),
    symbol: text(src.symbol),
    as_of: typeof src.as_of === 'string' ? src.as_of : null,
    status: typeof src.status === 'string' ? src.status : null,
    section: typeof src.section === 'string' ? src.section : null,
    score: numberOrNull(src.score),
    snippet: text(src.snippet),
    source: text(src.source, 'local'),
  }
}

// ---------------------------------------------------------------- 接口

export async function fetchNotes(filter: { target?: string; tag?: string; limit?: number } = {}): Promise<KnowledgeNote[]> {
  const params = new URLSearchParams()
  if (filter.target) params.set('target', filter.target)
  if (filter.tag) params.set('tag', filter.tag)
  if (filter.limit) params.set('limit', String(filter.limit))
  const query = params.toString()
  const payload = await request<unknown>(`/api/knowledge/notes${query ? `?${query}` : ''}`)
  const src = record(payload) ?? {}
  return list(src.items).map(parseNote)
}

export async function fetchTags(): Promise<TagCount[]> {
  const payload = await request<unknown>('/api/knowledge/notes/tags')
  const src = record(payload) ?? {}
  return list(src.tags)
    .map(parseTag)
    .filter((item): item is TagCount => item !== null)
}

export async function createNote(draft: NoteDraft): Promise<KnowledgeNote> {
  const payload = await request<unknown>('/api/knowledge/notes', {
    method: 'POST',
    body: {
      body: draft.body,
      ...(draft.target ? { target: draft.target } : {}),
      ...(draft.title ? { title: draft.title } : {}),
      ...(draft.tags && draft.tags.length > 0 ? { tags: draft.tags } : {}),
    },
  })
  const src = record(payload) ?? {}
  return parseNote(src.note)
}

export async function updateNote(id: number, patch: NoteDraft): Promise<KnowledgeNote> {
  const payload = await request<unknown>(`/api/knowledge/notes/${id}`, {
    method: 'PUT',
    body: patch,
  })
  const src = record(payload) ?? {}
  return parseNote(src.note)
}

export async function removeNote(id: number): Promise<void> {
  await request<unknown>(`/api/knowledge/notes/${id}`, { method: 'DELETE' })
}

export async function searchKnowledge(query: string, limit = 8): Promise<SearchPayload> {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  const payload = await request<unknown>(`/api/knowledge/search?${params.toString()}`)
  const src = record(payload) ?? {}
  return {
    q: text(src.q, query),
    engine: text(src.engine, 'local'),
    vector_error: typeof src.vector_error === 'string' ? src.vector_error : null,
    total: intOr(src.total, 0),
    hits: list(src.hits).map(parseHit),
  }
}

export async function estimateValuation(input: EstimateInput): Promise<EstimateResult> {
  const payload = await request<unknown>('/api/valuation/estimate', {
    method: 'POST',
    body: input,
    timeoutMs: 15000,
  })
  const src = record(payload)
  if (!src || typeof src.value !== 'number' || typeof src.kind !== 'string') {
    throw new ApiError('parse', '换算结果的形状不对（缺 kind 或 value）')
  }
  return {
    kind: src.kind,
    value: src.value,
    value_pct: typeof src.value_pct === 'string' ? src.value_pct : undefined,
    price: numberOrNull(src.price) ?? undefined,
    per_share: numberOrNull(src.per_share) ?? undefined,
    pv_explicit: numberOrNull(src.pv_explicit) ?? undefined,
    pv_terminal: numberOrNull(src.pv_terminal) ?? undefined,
    terminal_share: numberOrNull(src.terminal_share),
    warnings: list(src.warnings).map((item) => text(item)),
    disclaimer: text(src.disclaimer),
  }
}
