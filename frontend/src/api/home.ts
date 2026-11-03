/**
 * 首页 v2 两个新读接口的客户端（SPEC-HOME-V2 / DEC-052 / QUOT-380）。
 *
 *   GET /api/indices      指数条：信封面，点位在 `payload.cells[]`
 *   GET /api/watchlist    我的自选：平面 JSON、回环独占、no-store，`note` 不外露
 *
 * 屏幕层只要「参考页 8 格 + 自选行」的统一形状，解析层在这里把后端形状摊平：
 * - 后端回几格就用几格；没回的参考格补空（`value: null` + 原因），顺序固定，缺数不搬格子。
 * - 数字照抄，`available=false` / `price_status=missing` 就是 null + 原因，不补 0、不顶替。
 * - 404 原样抛出：屏幕按块级「未接入」处理，不当作整屏故障。
 */

import { ApiError, isApiError } from '../api'
import type { IndexQuoteItem, IndicesPayload, LadderEnvelope, WatchlistItem, WatchlistPayload } from '../api'

const READ_TIMEOUT_MS = 8000

type Raw = Record<string, unknown>

/** 参考页 8 格（SPEC-HOME-V2 §2.1）：A 股四格后端已交付，美/港股无源时后端回不可用，前端兜底补格。 */
const REFERENCE_SLOTS: readonly {
  key: string
  name: string
  market: IndexQuoteItem['market']
  aliases: readonly string[]
}[] = [
  { key: 'sh000001', name: '上证指数', market: 'cn', aliases: ['sh000001', '000001'] },
  { key: 'sh000300', name: '沪深300', market: 'cn', aliases: ['sh000300', '000300'] },
  { key: 'sz399001', name: '深证成指', market: 'cn', aliases: ['sz399001', '399001'] },
  { key: 'sz399006', name: '创业板指', market: 'cn', aliases: ['sz399006', '399006'] },
  { key: 'dji', name: '道琼斯', market: 'us', aliases: ['DJI', 'dji'] },
  { key: 'ixic', name: '纳斯达克综合', market: 'us', aliases: ['IXIC', 'ixic'] },
  { key: 'hsi', name: '恒生指数', market: 'hk', aliases: ['HSI', 'hsi'] },
  { key: 'hstech', name: '恒生科技', market: 'hk', aliases: ['HSTECH', 'hstech'] },
]

const US_SLOT_REASON = '美股取数管道未接入'

function asRecord(value: unknown): Raw | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? (value as Raw) : null
}

function asList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function numberOrNull(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

function textOrNull(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

function marketOf(value: unknown, code: string): IndexQuoteItem['market'] {
  const raw = textOrNull(value)?.toUpperCase()
  if (raw === 'SH' || raw === 'SZ' || raw === 'CN' || raw === 'A') return 'cn'
  if (raw === 'HK') return 'hk'
  if (raw === 'US') return 'us'
  if (/^(sh|sz)\d{6}$/i.test(code)) return 'cn'
  return /^\d{6}$/.test(code) ? 'cn' : null
}

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), READ_TIMEOUT_MS)
  const abortFromCaller = () => controller.abort()
  signal?.addEventListener('abort', abortFromCaller)
  try {
    const response = await fetch(path, {
      method: 'GET',
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
      throw new ApiError('parse', `${path} 返回的内容不是合法 JSON`, null, null)
    }
  } catch (error) {
    if (isApiError(error)) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (signal?.aborted) throw new ApiError('timeout', `请求已取消：${path}`)
      throw new ApiError('timeout', `${path} 超时（${READ_TIMEOUT_MS}ms）`)
    }
    throw new ApiError('not-connected', `本机接口不可达：${path}`)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abortFromCaller)
  }
}

function normalizeIndexCell(value: unknown): IndexQuoteItem | null {
  const row = asRecord(value)
  if (!row) return null
  const code = textOrNull(row.code)
  if (!code) return null
  const available = row.available !== false
  const value_ = available ? (numberOrNull(row.value) ?? numberOrNull(row.price)) : null
  return {
    code,
    name: textOrNull(row.name),
    market: marketOf(row.market, code),
    value: value_,
    change_pct: value_ === null ? null : numberOrNull(row.change_pct),
    reason: textOrNull(row.reason) ?? textOrNull(row.price_reason),
  }
}

function normalizeIndices(raw: unknown): IndicesPayload {
  const envelope = asRecord(raw) ?? {}
  const payload = asRecord(envelope.payload) ?? envelope
  const list = asList(payload.cells).length > 0 ? asList(payload.cells) : asList(payload.items)
  const items = list.map(normalizeIndexCell).filter((item): item is IndexQuoteItem => item !== null)
  const status = textOrNull(payload.data_source_status) ?? textOrNull(envelope.source_status)
  return {
    items,
    data_source_status:
      status === 'ok' || status === 'partial' || status === 'missing'
        ? status
        : items.some((item) => item.value !== null)
          ? 'partial'
          : 'missing',
    footnote: textOrNull(payload.footnote),
    as_of: textOrNull(payload.as_of) ?? textOrNull(envelope.data_as_of),
  }
}

function completeReferenceSlots(items: IndexQuoteItem[]): IndexQuoteItem[] {
  const used = new Set<number>()
  const result: IndexQuoteItem[] = []
  for (const slot of REFERENCE_SLOTS) {
    let match = -1
    for (let index = 0; index < items.length; index += 1) {
      if (used.has(index)) continue
      const item = items[index]
      const code = item.code.toLowerCase()
      if (slot.aliases.some((alias) => alias.toLowerCase() === code) || item.name === slot.name) {
        match = index
        break
      }
    }
    if (match >= 0) {
      used.add(match)
      result.push(items[match])
    } else {
      result.push({
        code: slot.key,
        name: slot.name,
        market: slot.market,
        value: null,
        change_pct: null,
        reason: slot.market === 'us' ? US_SLOT_REASON : '源未接入',
      })
    }
  }
  items.forEach((item, index) => {
    if (!used.has(index)) result.push(item)
  })
  return result
}

function normalizeWatchlistItem(value: unknown): WatchlistItem | null {
  const row = asRecord(value)
  if (!row) return null
  const code = textOrNull(row.code)
  if (!code) return null
  const priced = row.available !== false && textOrNull(row.price_status) !== 'missing'
  return {
    code,
    name: textOrNull(row.name),
    latest_price: priced ? numberOrNull(row.latest_price) : null,
    change_pct: priced ? numberOrNull(row.change_pct) : null,
    reason: textOrNull(row.price_reason) ?? textOrNull(row.reason) ?? (priced ? null : '现价未取到'),
    change: priced ? numberOrNull(row.change) : null,
    price_as_of: textOrNull(row.price_as_of),
  }
}

function normalizeWatchlist(raw: unknown): WatchlistPayload {
  const body = asRecord(raw) ?? {}
  const available = body.available !== false
  const items = available
    ? asList(body.items)
        .map(normalizeWatchlistItem)
        .filter((item): item is WatchlistItem => item !== null)
    : []
  const status = textOrNull(body.data_source_status)
  return {
    local_only: true,
    watch_count: numberOrNull(body.watch_count) ?? (available ? items.length : null),
    items,
    as_of: textOrNull(body.price_as_of) ?? textOrNull(body.as_of),
    price_source: textOrNull(body.price_source),
    data_source_status:
      status === 'ok' || status === 'partial' || status === 'missing' ? status : available ? 'ok' : 'missing',
    reason: available ? null : textOrNull(body.reason),
  }
}

/** 指数条：`/api/indices`，固定回参考页 8 格（缺的格带原因）。 */
export async function fetchIndices(signal?: AbortSignal): Promise<LadderEnvelope<IndicesPayload>> {
  const raw = await request<unknown>('/api/indices', signal)
  const envelope = asRecord(raw) ?? {}
  const payload = normalizeIndices(raw)
  const items = completeReferenceSlots(payload.items)
  return {
    domain: 'sentiment',
    topic: textOrNull(envelope.topic) ?? 'index_quote',
    event_id: textOrNull(envelope.event_id),
    data_as_of: textOrNull(envelope.data_as_of) ?? payload.as_of ?? null,
    source: 'ladderstock',
    source_detail: textOrNull(envelope.source_detail) ?? '',
    fetched_at: textOrNull(envelope.fetched_at) ?? '',
    freshness: (textOrNull(envelope.freshness) ?? 'snapshot') as LadderEnvelope<unknown>['freshness'],
    confidence: (textOrNull(envelope.confidence) ?? 'medium') as LadderEnvelope<unknown>['confidence'],
    source_status: (textOrNull(envelope.source_status) ?? 'unknown') as LadderEnvelope<unknown>['source_status'],
    payload: {
      ...payload,
      items,
      data_source_status: payload.data_source_status,
    },
  }
}

/** 自选：`/api/watchlist`（回环独占、平面 JSON、no-store）。 */
export async function fetchWatchlist(signal?: AbortSignal): Promise<WatchlistPayload> {
  const raw = await request<unknown>('/api/watchlist', signal)
  return normalizeWatchlist(raw)
}
