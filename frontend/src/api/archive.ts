/**
 * F8 研究档案只读客户端（`/api/research/archive/*`，前缀属研究数据面）。
 *
 * 后端契约（`src/ladderstock/web/research.py`）：
 *
 *   GET  /api/research/archive/list?limit=N      档案表（每份一份体检）
 *   GET  /api/research/archive/search?q=&limit=  按代码 / 日期 / 正文检索，命中回片段
 *   GET  /api/research/archive/{code}?date=      某份档案的明细
 *   POST /api/research/archive/{code}/export?date=  合并导出完整 Markdown
 *
 * 404 = 这份档案不存在（业务空态）；导出接口另用 `research_archive.export` 的错误文案。
 * 页面不伪造档案：没跑过就是空列表，缺段就照实写「缺 X 段」。
 */

import { ApiError } from '../api'

const TIMEOUT_MS = 20000
const CLIENT_HEADER = { 'X-LDST-Client': '1' }

interface RequestOptions {
  method?: 'GET' | 'POST'
  signal?: AbortSignal
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', signal } = options
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
  const abortFromCaller = () => controller.abort()
  signal?.addEventListener('abort', abortFromCaller)
  try {
    const response = await fetch(path, {
      method,
      signal: controller.signal,
      cache: 'no-store',
      headers: {
        accept: 'application/json',
        ...(method === 'POST' ? CLIENT_HEADER : {}),
      },
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
        `档案接口 ${path} 返回 HTTP ${response.status}${detail ? `：${detail}` : ''}`,
        response.status,
        detail,
      )
    }
    try {
      return (await response.json()) as T
    } catch {
      throw new ApiError('parse', `档案接口 ${path} 返回的内容不是合法 JSON`)
    }
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('timeout', `档案接口 ${path} 超时（${TIMEOUT_MS}ms）`)
    }
    throw new ApiError('not-connected', `档案接口不可达：${path}`)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abortFromCaller)
  }
}

function asRecord(value: unknown, what: string): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new ApiError('parse', `${what}不是对象`)
  }
  return value as Record<string, unknown>
}

function asText(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function asTextList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

export interface ArchiveStageRow {
  stage: string
  title: string
  hasJson: boolean
  hasMd: boolean
  degraded: boolean
  unverified: boolean | null
  sources: number
  failure: string | null
}

export interface ArchiveItem {
  code: string
  date: string
  stageCount: number
  totalStages: number
  complete: boolean
  missing: string[]
  degraded: string[]
  unverified: string[]
  failed: string[]
  updatedAt: string | null
  stages: ArchiveStageRow[]
  matches: Array<{ stage: string; stageTitle: string; text: string }>
  matchedBy: 'meta' | 'content' | null
}

export interface ArchiveExportResult {
  ok: boolean
  code: string
  date: string
  path: string
  bytes: number
  complete: boolean
  missing: string[]
}

function parseItem(value: unknown): ArchiveItem | null {
  if (value === null || typeof value !== 'object') return null
  const row = value as Record<string, unknown>
  const code = asText(row.code)
  const date = asText(row.date)
  if (!code || !date) return null
  const stages = Array.isArray(row.stages) ? row.stages : []
  const matches = Array.isArray(row.matches) ? row.matches : []
  return {
    code,
    date,
    stageCount: typeof row.stage_count === 'number' ? row.stage_count : stages.length,
    totalStages: typeof row.total_stages === 'number' ? row.total_stages : 6,
    complete: Boolean(row.complete),
    missing: asTextList(row.missing),
    degraded: asTextList(row.degraded),
    unverified: asTextList(row.unverified),
    failed: asTextList(row.failed),
    updatedAt: asText(row.updated_at),
    stages: stages.flatMap((entry) => {
      if (entry === null || typeof entry !== 'object') return []
      const stage = entry as Record<string, unknown>
      const name = asText(stage.stage)
      if (!name) return []
      return [
        {
          stage: name,
          title: asText(stage.title) ?? name,
          hasJson: Boolean(stage.has_json),
          hasMd: Boolean(stage.has_md),
          degraded: Boolean(stage.degraded),
          unverified: typeof stage.unverified === 'boolean' ? stage.unverified : null,
          sources: typeof stage.sources === 'number' ? stage.sources : 0,
          failure: asText(stage.failure),
        },
      ]
    }),
    matches: matches.flatMap((entry) => {
      if (entry === null || typeof entry !== 'object') return []
      const match = entry as Record<string, unknown>
      const text = asText(match.text)
      if (!text) return []
      return [
        {
          stage: asText(match.stage) ?? '',
          stageTitle: asText(match.stage_title) ?? '',
          text,
        },
      ]
    }),
    matchedBy: row.matched_by === 'meta' || row.matched_by === 'content' ? row.matched_by : null,
  }
}

export async function loadArchiveList(limit = 30, signal?: AbortSignal): Promise<ArchiveItem[]> {
  const payload = asRecord(
    await request<unknown>(`/api/research/archive/list?limit=${limit}`, { signal }),
    '档案表',
  )
  return (Array.isArray(payload.items) ? payload.items : []).flatMap((item) => {
    const parsed = parseItem(item)
    return parsed ? [parsed] : []
  })
}

export async function searchArchive(
  keyword: string,
  limit = 20,
  signal?: AbortSignal,
): Promise<ArchiveItem[]> {
  const payload = asRecord(
    await request<unknown>(
      `/api/research/archive/search?q=${encodeURIComponent(keyword)}&limit=${limit}`,
      { signal },
    ),
    '档案检索',
  )
  return (Array.isArray(payload.hits) ? payload.hits : []).flatMap((item) => {
    const parsed = parseItem(item)
    return parsed ? [parsed] : []
  })
}

export async function loadArchiveItem(
  code: string,
  date?: string | null,
  signal?: AbortSignal,
): Promise<ArchiveItem | null> {
  const query = date ? `?date=${encodeURIComponent(date)}` : ''
  try {
    const payload = asRecord(
      await request<unknown>(`/api/research/archive/${encodeURIComponent(code)}${query}`, { signal }),
      '档案明细',
    )
    return parseItem(payload.item)
  } catch (error) {
    if (error instanceof ApiError && error.kind === 'http' && error.status === 404) return null
    throw error
  }
}

/** 合并导出完整 MD；返回落盘路径供页面提示（写盘在 data/research_archive/<date>/<code>.md）。 */
export async function exportArchive(
  code: string,
  date?: string | null,
  signal?: AbortSignal,
): Promise<ArchiveExportResult> {
  const query = date ? `?date=${encodeURIComponent(date)}` : ''
  const payload = asRecord(
    await request<unknown>(`/api/research/archive/${encodeURIComponent(code)}/export${query}`, {
      method: 'POST',
      signal,
    }),
    '档案导出',
  )
  return {
    ok: Boolean(payload.ok),
    code: asText(payload.code) ?? code,
    date: asText(payload.date) ?? (date ?? ''),
    path: asText(payload.path) ?? '',
    bytes: typeof payload.bytes === 'number' ? payload.bytes : 0,
    complete: Boolean(payload.complete),
    missing: asTextList(payload.missing),
  }
}
