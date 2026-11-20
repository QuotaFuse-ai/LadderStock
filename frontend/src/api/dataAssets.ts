/**
 * 数据资产客户端（`/api/data/assets*`）。
 *
 * 这一层只做两件事：拿清单、拿单票明细。**判覆盖、算期数都在服务端**——
 * 前端不连 PG、不猜"有没有"，只把服务端已有的行原样展示（缺就是缺）。
 */

import { ApiError } from '../api'

export interface DataAssetRow {
  symbol: string
  wide_as_of: string | null
  wide_rows: number
  financial_rows: number
  latest_report: string | null
  announcement_rows: number
  latest_ann_date: string | null
  announcement_with_text: number
  has_financial: boolean
  has_wide: boolean
  has_announcement: boolean
}

export interface DataAssetIndex {
  total: number
  with_financial: number
  with_wide: number
  with_announcement: number
  financial_rows: number
  announcement_rows: number
  items: DataAssetRow[]
}

export interface ReportRow {
  report_date: string
  report_type: string
  revenue: number | null
  net_profit: number | null
  quality_status: string | null
}

export interface AnnouncementRow {
  ann_date: string | null
  ann_type: string
  title: string
  content_chars: number
}

export interface CoverageKind {
  state: string
  source?: string | null
  as_of?: string | null
  age_days?: number | null
  reason?: string | null
}

export interface DataAssetDetail {
  symbol: string
  coverage: {
    kinds: Record<string, CoverageKind>
    ready: string[]
    missing: string[]
    complete: boolean
  }
  reports: ReportRow[]
  announcements: AnnouncementRow[]
}

const TIMEOUT_MS = 10000

function parseError(message: string): ApiError {
  return new ApiError('parse', message)
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
  const relay = () => controller.abort()
  signal?.addEventListener('abort', relay)
  try {
    const response = await fetch(path, {
      method: 'GET',
      signal: controller.signal,
      cache: 'no-store',
      headers: { accept: 'application/json' },
    })
    if (!response.ok) {
      let detail: string | null = null
      try {
        const payload = (await response.json()) as { detail?: unknown }
        detail = typeof payload.detail === 'string' ? payload.detail : null
      } catch {
        detail = null
      }
      throw new ApiError(
        'http',
        `数据资产接口 ${path} 返回 HTTP ${response.status}${detail ? `：${detail}` : ''}`,
        response.status,
        detail,
      )
    }
    try {
      return (await response.json()) as T
    } catch {
      throw parseError(`数据资产接口 ${path} 返回的内容不是合法 JSON`)
    }
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('timeout', `数据资产接口 ${path} 超时（${TIMEOUT_MS}ms）`)
    }
    throw new ApiError('not-connected', `数据资产接口 ${path} 不可达`)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', relay)
  }
}

export async function loadDataAssets(signal?: AbortSignal): Promise<DataAssetIndex> {
  const payload = await getJson<DataAssetIndex & { ok?: boolean }>('/api/data/assets?limit=400', signal)
  if (!Array.isArray(payload?.items)) {
    throw parseError('数据资产清单缺少 items 数组')
  }
  return payload
}

export async function loadDataAssetDetail(
  symbol: string,
  signal?: AbortSignal,
): Promise<DataAssetDetail> {
  const payload = await getJson<DataAssetDetail & { ok?: boolean }>(
    `/api/data/assets/${encodeURIComponent(symbol)}`,
    signal,
  )
  if (!payload?.coverage?.kinds) {
    throw parseError(`标的 ${symbol} 的明细缺少 coverage.kinds`)
  }
  return payload
}

/** 知识库分类目录：分类分布 + 字段清单。知识库离线时 ok=false，页面按此显示原因。 */
export interface KnowledgeField {
  key: string
  label: string
  type: string
  sample: string
}

export interface KnowledgeCatalog {
  ok: boolean
  points: number
  doc_kinds: Record<string, number>
  periods: Record<string, number>
  kinds_inventory?: string[]
  fields?: KnowledgeField[]
  error: string | null
}

export interface KnowledgeHit {
  score: number
  symbol: string
  ann_date: string
  title: string
  doc_kind: string
  period: string
  provider: string
  section: string
  chunk_index: number
  chunk_total: number
  text: string
}

export interface KnowledgeSearchResult {
  ok: boolean
  query: string
  top_k: number
  hits: KnowledgeHit[]
}

export async function loadKnowledgeCatalog(signal?: AbortSignal): Promise<KnowledgeCatalog> {
  const payload = await getJson<KnowledgeCatalog>('/api/data/knowledge', signal)
  if (typeof payload?.ok !== 'boolean') {
    throw parseError('知识库目录缺少 ok 字段')
  }
  return payload
}

export async function searchKnowledge(
  query: string,
  options: { symbol?: string; docKind?: string; period?: string; topK?: number } = {},
  signal?: AbortSignal,
): Promise<KnowledgeSearchResult> {
  const params = new URLSearchParams({ q: query })
  if (options.symbol) params.set('symbol', options.symbol)
  if (options.docKind) params.set('doc_kind', options.docKind)
  if (options.period) params.set('period', options.period)
  if (options.topK) params.set('top_k', String(options.topK))
  const payload = await getJson<KnowledgeSearchResult>(`/api/data/knowledge/search?${params.toString()}`, signal)
  if (!Array.isArray(payload?.hits)) {
    throw parseError('知识库检索结果缺少 hits 数组')
  }
  return payload
}

/** 与 LadderStock 其他取数一致的「结论 + 原因 + 代码」文案。 */
export function describeDataAssetsError(error: unknown): {
  title: string
  detail: string
  code: string | null
} {
  if (error instanceof ApiError) {
    if (error.kind === 'not-connected') {
      return {
        title: '数据资产接口不可达',
        detail: '确认主程序已启动，并且 FinanceCoffer 的 Postgres 在运行（默认 127.0.0.1:15432）。',
        code: null,
      }
    }
    if (error.kind === 'timeout') {
      return { title: '读取超时', detail: error.message, code: 'TIMEOUT' }
    }
    if (error.kind === 'parse') {
      return { title: '响应解析失败', detail: error.message, code: 'E_PARSE' }
    }
    return {
      title: '读取失败',
      detail: error.detail ?? error.message,
      code: error.status ? `HTTP ${error.status}` : null,
    }
  }
  return {
    title: '读取失败',
    detail: error instanceof Error ? error.message : String(error),
    code: null,
  }
}
