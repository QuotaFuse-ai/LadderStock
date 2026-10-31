/**
 * 资讯雷达 / 事件日历 / 候选池 / 我的持仓（R10）数据客户端。
 *
 * 四个页面都吃本机只读接口，契约见 `docs/specs/SPEC-RADAR-PAGES.md`：
 *
 *   GET    /api/news?date=&until=&source=&code=&limit=   资讯流（envelope，domain=news）
 *   GET    /api/events?month=&code=                      事件日历（envelope，domain=news）
 *   GET    /api/pool?date=&limit=                        候选池（envelope，domain=sentiment）
 *   GET    /api/positions?date=                          持仓（**平面 JSON**，隐私数据不进信封）
 *   POST   /api/positions · PATCH/DELETE /api/positions/{code}
 *
 * 三条硬规矩：
 * 1. **不重算指标**：候选分项、盈亏、集中度、行业分布全部用后端载荷；`null` 就是「没有」，
 *    解析层不补 0、不插值。
 * 2. **持仓不落浏览器存储**：本模块不 import `persist/` 与 `stores/`，页面数据只活在被调用栈里。
 * 3. 接口未接入（404 / 未连接）是「数据源暂缺」，页面进空态 / 离线态，不伪造数据。
 */

import { ApiError, isApiError, isEndpointMissing } from '../api'

const READ_TIMEOUT_MS = 8000

export type RadarStatus = 'ok' | 'partial' | 'missing'

// ---------------------------------------------------------------- 文案表

/** 资讯栏目（数据集名）→ 中文名。数据集名是唯一键，中文只做展示。 */
export const NEWS_SOURCE_LABEL: Record<string, string> = {
  announcement_cninfo: '巨潮公告',
  announcement_szse: '深交所公告',
  research_report: '研究报告',
  news_eastmoney_stock: '东财个股新闻',
  news_cctv: '新闻联播',
  flash_cls: '财联社快讯',
  flash_wallstreetcn: '华尔街见闻',
  flash_eastmoney: '东财快讯',
  lockup_calendar: '解禁日历',
  earnings_preview: '业绩预告',
}

export function sourceLabel(dataset: string): string {
  return NEWS_SOURCE_LABEL[dataset] ?? dataset
}

/** 事件类型兜底文案；载荷里带了 `label` 就以载荷为准。 */
export const EVENT_TYPE_LABEL: Record<string, string> = {
  earnings_preview: '业绩预告',
  shareholder_reduction: '股东减持',
  shareholder_increase: '股东增持',
  lockup_expiry: '限售解禁',
  ma_restructuring: '并购重组',
  contract_award: '中标合同',
  policy: '政策发布',
  shareholder_meeting: '股东大会',
}

/** 日历五小节的固定顺序与兜底文案（载荷里带 label 时以载荷为准）。 */
export const CALENDAR_SECTION_ORDER = [
  'earnings_disclosure',
  'lockup_expiry',
  'shareholder_meeting',
  'policy_window',
  'earnings_preview',
] as const

export const CALENDAR_SECTION_LABEL: Record<string, string> = {
  earnings_disclosure: '财报预约披露日',
  lockup_expiry: '解禁日',
  shareholder_meeting: '股东大会',
  policy_window: '政策发布窗口',
  earnings_preview: '业绩预告',
}

/** 候选池五个分项：固定顺序、固定键名（SPEC-CANDIDATE-POOL §3.1）。 */
export const POOL_PART_ORDER = ['strength', 'standing', 'capital', 'theme', 'turnover'] as const

export const POOL_PART_LABEL: Record<string, string> = {
  strength: '强度',
  standing: '地位',
  capital: '资金',
  theme: '题材',
  turnover: '换手',
}

export const POOL_PART_WEIGHT_LABEL: Record<string, string> = {
  strength: '连板高度',
  standing: '梯队相对位置',
  capital: '候选集内净额分位',
  theme: '板块涨幅',
  turnover: '换手率',
}

export type PoolPart = (typeof POOL_PART_ORDER)[number]

// ---------------------------------------------------------------- 类型

export interface NewsOrigin {
  dataset: string
  title: string
  url: string
}

export interface NewsItem {
  id: string
  source: string
  title: string
  url: string
  published_at: string | null
  related_codes: string[]
  summary: string
  content_hash: string
  origins: NewsOrigin[]
  /** 可选：R6 当前不产出行业标签；有则按标签过滤，没有则页面退化为关键词过滤 */
  industries: string[]
}

export interface NewsSourceStatus {
  dataset: string
  tool: string
  status: string
  items: number
  detail: string
  source: string
  source_detail: string
  freshness: string
  confidence: string
  degraded: boolean
}

export interface ExtractedEvent {
  id: string
  type: string
  label: string
  event_date: string
  title: string
  codes: string[]
  evidence: { snippet: string; url: string; matched: string[]; dataset: string }
  item_id: string
  date_basis: string
  extractor: string
  ai_note: string | null
}

export interface NewsStreamPayload {
  window: { start: string; end: string }
  sources: string[]
  codes: string[]
  summary: {
    items: number
    fetched: number
    new_items: number
    known_items: number
    merged_duplicates: number
    dropped_undated: number
    outside_window: number
    stored: number
    events: number
    persisted: boolean
  }
  sources_status: NewsSourceStatus[]
  extraction: {
    events: number
    by_type: Record<string, number>
    skipped_no_url: number
    skipped_excluded_source: number
    suppressed_by_negation: number
  }
  items: NewsItem[]
  events: ExtractedEvent[]
  data_source_status: RadarStatus
  reason: string | null
  data_as_of: string | null
}

export interface CalendarEntry {
  id: string
  date: string
  code: string
  section: string
  label: string
  detail: string
  source: string
  provenance: Record<string, unknown>
}

export interface CalendarSection {
  key: string
  label: string
  status: 'sourced' | 'no_source'
  entries: CalendarEntry[]
  note: string
  data_source: string
  tool: string
  degraded: boolean
  /** `no_source` 专用：为什么没有、要补哪个上游工具 */
  reason: string
  ask: string
}

export interface CalendarExtractedEvent extends ExtractedEvent {
  date: string
  basis: 'rule_extracted'
  note: string
}

export interface EventCalendarPayload {
  month: string
  window: { start: string; end: string }
  codes: string[]
  sections: CalendarSection[]
  extracted_events: CalendarExtractedEvent[]
  data_source_status: RadarStatus
  reason: string | null
  data_as_of: string | null
}

export interface PoolCandidate {
  code: string
  name: string
  score: number | null
  parts: Record<PoolPart, number | null>
  absent_parts: string[]
  raw: {
    boards: number | null
    turnover_rate: number | null
    height: number | null
    net_inflow: number | null
    theme_tags: string[]
    theme_board_change_pct: number | null
  }
}

export interface CandidatePoolPayload {
  date: string
  available: boolean
  reason: string | null
  source_detail: string
  height: number | null
  candidate_count: number
  candidates: PoolCandidate[]
  weights: Record<string, number>
  formula: string
  counts: {
    limit_pool_rows: number
    dropped_duplicates: number
    unknown_code: number
    theme_boards: number
    fund_flow_rows: number
  }
  missing: string[]
  data_source_status: RadarStatus
  data_as_of: string | null
}

export interface PositionItem {
  code: string
  cost: number
  qty: number
  entry_date: string | null
  industry: string
  note: string
  latest_price: number | null
  market_value: number | null
  unrealized_pnl: number | null
  unrealized_pnl_pct: number | null
}

export interface PositionHolding {
  code: string
  weight_pct: number
  cost: number
}

export interface PositionsPayload {
  local_only: true
  store: 'local_sqlite'
  data_source_status: RadarStatus
  as_of: string | null
  price_source: string
  position_count: number
  valuation: {
    total_cost: number
    market_value: number | null
    priced_cost: number
    unpriced_cost: number
    coverage_pct: number | null
    unrealized_pnl: number | null
    unrealized_pnl_pct: number | null
  }
  items: PositionItem[]
  concentration: {
    available: boolean
    reason: string | null
    basis: 'cost' | 'market_value'
    top1_pct: number | null
    top3_pct: number | null
    hhi: number | null
    holdings: PositionHolding[]
  }
  sector_distribution: {
    available: boolean
    reason: string | null
    basis: 'cost' | 'market_value'
    labeled_count: number
    unlabeled_count: number
    industries: Array<{ industry: string; weight_pct: number; cost: number }>
  }
}

export interface PositionInput {
  code: string
  cost: number
  qty: number
  entry_date?: string
  industry?: string
  note?: string
}

export type PositionPatch = Partial<Omit<PositionInput, 'code'>>

export interface PositionMutation {
  ok: true
  local_only: true
  store: 'local_sqlite'
  position?: PositionItem
  removed?: { code: string; cost: number; qty: number }
}

export interface NewsQuery {
  date: string
  until?: string | null
  sources?: string[] | null
  codes?: string[] | null
  limit?: number
}

export interface CalendarQuery {
  month: string
  codes?: string[] | null
  limit?: number
}

export interface PoolQuery {
  date: string
  limit?: number
}

export interface PositionsQuery {
  date?: string | null
}

export interface RadarSource {
  news(query: NewsQuery, signal?: AbortSignal): Promise<NewsStreamPayload>
  calendar(query: CalendarQuery, signal?: AbortSignal): Promise<EventCalendarPayload>
  pool(query: PoolQuery, signal?: AbortSignal): Promise<CandidatePoolPayload>
  positions(query: PositionsQuery, signal?: AbortSignal): Promise<PositionsPayload>
  addPosition(input: PositionInput, signal?: AbortSignal): Promise<PositionMutation>
  updatePosition(code: string, patch: PositionPatch, signal?: AbortSignal): Promise<PositionMutation>
  removePosition(code: string, signal?: AbortSignal): Promise<PositionMutation>
}

// ---------------------------------------------------------------- 请求

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
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
      throw new ApiError('parse', `${path} 返回的内容不是合法 JSON`, null, null)
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
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

function stringList(value: unknown): string[] {
  return list(value)
    .map((item) => text(item).trim())
    .filter(Boolean)
}

function flag(value: unknown): boolean {
  return value === true
}

/** envelope 的 `payload`；也接受直接返回载荷的平面接口（容错但不猜结构）。 */
function payloadOf(raw: unknown, what: string): Record<string, unknown> {
  const root = record(raw)
  if (!root) throw new ApiError('parse', `${what}响应不是对象`)
  const inner = record(root.payload)
  return inner ?? root
}

function parseStatus(value: unknown, fallback: RadarStatus): RadarStatus {
  return value === 'ok' || value === 'partial' || value === 'missing' ? value : fallback
}

// ---------------------------------------------------------------- 资讯流

function parseOrigin(value: unknown): NewsOrigin | null {
  const row = record(value)
  if (!row) return null
  const dataset = text(row.dataset)
  if (!dataset) return null
  return { dataset, title: text(row.title), url: text(row.url) }
}

function parseNewsItem(value: unknown): NewsItem | null {
  const row = record(value)
  if (!row) return null
  const id = text(row.id)
  const title = text(row.title)
  if (!id || !title) return null
  const published = text(row.published_at)
  return {
    id,
    source: text(row.source),
    title,
    url: text(row.url),
    published_at: published || null,
    related_codes: stringList(row.related_codes),
    summary: text(row.summary),
    content_hash: text(row.content_hash),
    origins: list(row.origins).map(parseOrigin).filter((item): item is NewsOrigin => item !== null),
    industries: stringList(row.industries),
  }
}

function parseEvidence(value: unknown): ExtractedEvent['evidence'] {
  const row = record(value) ?? {}
  return {
    snippet: text(row.snippet),
    url: text(row.url),
    matched: stringList(row.matched),
    dataset: text(row.dataset),
  }
}

function parseEvent(value: unknown): ExtractedEvent | null {
  const row = record(value)
  if (!row) return null
  const id = text(row.id)
  const type = text(row.type)
  if (!id || !type) return null
  const published = text(row.event_date) || text(row.date)
  const note = text(row.ai_note)
  return {
    id,
    type,
    label: text(row.label) || EVENT_TYPE_LABEL[type] || type,
    event_date: published,
    title: text(row.title),
    codes: stringList(row.codes),
    evidence: parseEvidence(row.evidence),
    item_id: text(row.item_id),
    date_basis: text(row.date_basis),
    extractor: text(row.extractor),
    ai_note: note || null,
  }
}

function parseSourceStatus(value: unknown): NewsSourceStatus | null {
  const row = record(value)
  if (!row) return null
  const dataset = text(row.dataset)
  if (!dataset) return null
  return {
    dataset,
    tool: text(row.tool),
    status: text(row.status, 'unknown'),
    items: numberOrNull(row.items) ?? 0,
    detail: text(row.detail),
    source: text(row.source),
    source_detail: text(row.source_detail),
    freshness: text(row.freshness),
    confidence: text(row.confidence),
    degraded: flag(row.degraded),
  }
}

/** 来源账本 → 页面状态：全挂 missing、任一路降级 partial、其余 ok。 */
function newsStatus(statuses: NewsSourceStatus[], items: number, exitCode: number | null): RadarStatus {
  if (statuses.length > 0) {
    const ok = statuses.filter((row) => row.status === 'ok' && !row.degraded).length
    if (ok === 0) return 'missing'
    if (ok < statuses.length) return 'partial'
    return items > 0 ? 'ok' : 'missing'
  }
  if (exitCode === 0) return items > 0 ? 'ok' : 'missing'
  if (exitCode === 8) return 'partial'
  return 'missing'
}

export function parseNewsStream(raw: unknown): NewsStreamPayload {
  const payload = payloadOf(raw, '资讯流')
  const items = list(payload.items).map(parseNewsItem).filter((item): item is NewsItem => item !== null)
  const events = list(payload.events ?? payload.extracted_events)
    .map(parseEvent)
    .filter((item): item is ExtractedEvent => item !== null)
  const statuses = list(payload.sources_status)
    .map(parseSourceStatus)
    .filter((item): item is NewsSourceStatus => item !== null)
  const summary = record(payload.summary) ?? {}
  const extraction = record(payload.extraction) ?? {}
  const window = record(payload.window) ?? {}
  const exitCode = numberOrNull(payload.exit_code)
  const status = parseStatus(payload.data_source_status, newsStatus(statuses, items.length, exitCode))
  const failed = statuses.filter((row) => row.status !== 'ok' || row.degraded)
  const reason =
    text(payload.reason) ||
    (status === 'missing'
      ? failed.map((row) => `${sourceLabel(row.dataset)}：${row.detail || row.status}`).join('；') ||
        '所有资讯栏目都不可用'
      : null)

  return {
    window: { start: text(window.start), end: text(window.end) },
    sources: stringList(payload.sources),
    codes: stringList(payload.codes),
    summary: {
      items: numberOrNull(summary.items) ?? items.length,
      fetched: numberOrNull(summary.fetched) ?? 0,
      new_items: numberOrNull(summary.new_items) ?? 0,
      known_items: numberOrNull(summary.known_items) ?? 0,
      merged_duplicates: numberOrNull(summary.merged_duplicates) ?? 0,
      dropped_undated: numberOrNull(summary.dropped_undated) ?? 0,
      outside_window: numberOrNull(summary.outside_window) ?? 0,
      stored: numberOrNull(summary.stored) ?? 0,
      events: numberOrNull(summary.events) ?? events.length,
      persisted: flag(summary.persisted),
    },
    sources_status: statuses,
    extraction: {
      events: numberOrNull(extraction.events) ?? events.length,
      by_type: Object.fromEntries(
        Object.entries(record(extraction.by_type) ?? {}).map(([key, value]) => [key, numberOrNull(value) ?? 0]),
      ),
      skipped_no_url: numberOrNull(extraction.skipped_no_url) ?? 0,
      skipped_excluded_source: numberOrNull(extraction.skipped_excluded_source) ?? 0,
      suppressed_by_negation: list(extraction.suppressed_by_negation).length,
    },
    items,
    events,
    data_source_status: status,
    reason,
    data_as_of: text(payload.data_as_of) || text(window.end) || null,
  }
}

// ---------------------------------------------------------------- 事件日历

function parseCalendarEntry(value: unknown): CalendarEntry | null {
  const row = record(value)
  if (!row) return null
  const date = text(row.date).slice(0, 10)
  const id = text(row.id)
  if (!id || !date) return null
  return {
    id,
    date,
    code: text(row.code),
    section: text(row.section),
    label: text(row.label),
    detail: text(row.detail),
    source: text(row.source),
    provenance: record(row.provenance) ?? {},
  }
}

function parseCalendarSection(value: unknown): CalendarSection | null {
  const row = record(value)
  if (!row) return null
  const key = text(row.key)
  if (!key) return null
  const status = row.status === 'sourced' ? 'sourced' : 'no_source'
  return {
    key,
    label: text(row.label) || CALENDAR_SECTION_LABEL[key] || key,
    status,
    entries: list(row.entries)
      .map(parseCalendarEntry)
      .filter((item): item is CalendarEntry => item !== null),
    note: text(row.note),
    data_source: text(row.data_source),
    tool: text(row.tool),
    degraded: flag(row.degraded),
    reason: text(row.reason),
    ask: text(row.ask),
  }
}

export function parseEventCalendar(raw: unknown): EventCalendarPayload {
  const payload = payloadOf(raw, '事件日历')
  const sections = list(payload.sections)
    .map(parseCalendarSection)
    .filter((item): item is CalendarSection => item !== null)
  const events = list(payload.extracted_events)
    .map(parseEvent)
    .map((event) => {
      const row = record(list(payload.extracted_events).find((item) => record(item)?.id === event?.id)) ?? {}
      return event === null
        ? null
        : {
            ...event,
            date: text(row.date) || event.event_date,
            basis: 'rule_extracted' as const,
            note: text(row.note),
          }
    })
    .filter((item): item is CalendarExtractedEvent => item !== null)
  const exitCode = numberOrNull(payload.exit_code)
  const sourced = sections.filter((section) => section.status === 'sourced')
  const derived: RadarStatus =
    sourced.some((section) => section.degraded)
      ? 'partial'
      : sourced.length === 0
        ? 'missing'
        : exitCode === 8 || exitCode === 4
          ? 'partial'
          : 'ok'
  const status = parseStatus(payload.data_source_status, derived)
  const failed = sections.filter(
    (section) => section.status === 'no_source' && section.degraded,
  )
  const window = record(payload.window) ?? {}

  return {
    month: text(payload.month),
    window: { start: text(window.start), end: text(window.end) },
    codes: stringList(payload.codes),
    sections,
    extracted_events: events,
    data_source_status: status,
    reason:
      text(payload.reason) ||
      (failed.length > 0 ? failed.map((section) => `${section.label}：${section.reason}`).join('；') : null),
    data_as_of: text(payload.data_as_of) || text(window.start) || null,
  }
}

// ---------------------------------------------------------------- 候选池

function parsePoolCandidate(value: unknown): PoolCandidate | null {
  const row = record(value)
  if (!row) return null
  const code = text(row.code)
  if (!code) return null
  const parts = record(row.parts) ?? {}
  const raw = record(row.raw) ?? {}
  return {
    code,
    name: text(row.name) || code,
    score: numberOrNull(row.score),
    parts: {
      strength: numberOrNull(parts.strength),
      standing: numberOrNull(parts.standing),
      capital: numberOrNull(parts.capital),
      theme: numberOrNull(parts.theme),
      turnover: numberOrNull(parts.turnover),
    },
    absent_parts: stringList(row.absent_parts),
    raw: {
      boards: numberOrNull(raw.boards),
      turnover_rate: numberOrNull(raw.turnover_rate),
      height: numberOrNull(raw.height),
      net_inflow: numberOrNull(raw.net_inflow),
      theme_tags: stringList(raw.theme_tags),
      theme_board_change_pct: numberOrNull(raw.theme_board_change_pct),
    },
  }
}

export function parseCandidatePool(raw: unknown): CandidatePoolPayload {
  const payload = payloadOf(raw, '候选池')
  const candidates = list(payload.candidates)
    .map(parsePoolCandidate)
    .filter((item): item is PoolCandidate => item !== null)
  const counts = record(payload.counts) ?? {}
  const available = payload.available === undefined ? candidates.length > 0 : flag(payload.available)

  return {
    date: text(payload.date),
    available,
    reason: text(payload.reason) || null,
    source_detail: text(payload.source_detail),
    height: numberOrNull(payload.height),
    candidate_count: numberOrNull(payload.candidate_count) ?? candidates.length,
    candidates,
    weights: Object.fromEntries(
      Object.entries(record(payload.weights) ?? {}).map(([key, value]) => [key, numberOrNull(value) ?? 0]),
    ),
    formula: text(payload.formula),
    counts: {
      limit_pool_rows: numberOrNull(counts.limit_pool_rows) ?? 0,
      dropped_duplicates: numberOrNull(counts.dropped_duplicates) ?? 0,
      unknown_code: numberOrNull(counts.unknown_code) ?? 0,
      theme_boards: numberOrNull(counts.theme_boards) ?? 0,
      fund_flow_rows: numberOrNull(counts.fund_flow_rows) ?? 0,
    },
    missing: stringList(payload.missing),
    data_source_status: parseStatus(payload.data_source_status, available ? 'ok' : 'missing'),
    data_as_of: text(payload.data_as_of) || text(payload.date) || null,
  }
}

// ---------------------------------------------------------------- 我的持仓

function parsePositionItem(value: unknown): PositionItem | null {
  const row = record(value)
  if (!row) return null
  const code = text(row.code)
  if (!code) return null
  const entry = text(row.entry_date)
  return {
    code,
    cost: numberOrNull(row.cost) ?? 0,
    qty: numberOrNull(row.qty) ?? 0,
    entry_date: entry || null,
    industry: text(row.industry),
    note: text(row.note),
    latest_price: numberOrNull(row.latest_price),
    market_value: numberOrNull(row.market_value),
    unrealized_pnl: numberOrNull(row.unrealized_pnl),
    unrealized_pnl_pct: numberOrNull(row.unrealized_pnl_pct),
  }
}

function basisOf(value: unknown): 'cost' | 'market_value' {
  return value === 'market_value' ? 'market_value' : 'cost'
}

export function parsePositions(raw: unknown): PositionsPayload {
  const payload = record(raw)
  if (!payload) throw new ApiError('parse', '持仓响应不是对象')
  const items = list(payload.items)
    .map(parsePositionItem)
    .filter((item): item is PositionItem => item !== null)
  const valuation = record(payload.valuation) ?? {}
  const concentration = record(payload.concentration) ?? {}
  const sector = record(payload.sector_distribution) ?? {}
  const status = parseStatus(payload.data_source_status, items.length > 0 ? 'ok' : 'missing')

  return {
    local_only: true,
    store: 'local_sqlite',
    data_source_status: status,
    as_of: text(payload.as_of) || null,
    price_source: text(payload.price_source, 'daily_k'),
    position_count: numberOrNull(payload.position_count) ?? items.length,
    valuation: {
      total_cost: numberOrNull(valuation.total_cost) ?? 0,
      market_value: numberOrNull(valuation.market_value),
      priced_cost: numberOrNull(valuation.priced_cost) ?? 0,
      unpriced_cost: numberOrNull(valuation.unpriced_cost) ?? 0,
      coverage_pct: numberOrNull(valuation.coverage_pct),
      unrealized_pnl: numberOrNull(valuation.unrealized_pnl),
      unrealized_pnl_pct: numberOrNull(valuation.unrealized_pnl_pct),
    },
    items,
    concentration: {
      available: flag(concentration.available) && items.length > 0,
      reason: text(concentration.reason) || null,
      basis: basisOf(concentration.basis),
      top1_pct: numberOrNull(concentration.top1_pct),
      top3_pct: numberOrNull(concentration.top3_pct),
      hhi: numberOrNull(concentration.hhi),
      holdings: list(concentration.holdings)
        .map((value) => {
          const row = record(value)
          const code = text(row?.code)
          if (!row || !code) return null
          return {
            code,
            weight_pct: numberOrNull(row.weight_pct) ?? 0,
            cost: numberOrNull(row.cost) ?? 0,
          }
        })
        .filter((row): row is PositionHolding => row !== null),
    },
    sector_distribution: {
      available: flag(sector.available),
      reason: text(sector.reason) || null,
      basis: basisOf(sector.basis),
      labeled_count: numberOrNull(sector.labeled_count) ?? 0,
      unlabeled_count: numberOrNull(sector.unlabeled_count) ?? 0,
      industries: list(sector.industries)
        .map((value) => {
          const row = record(value)
          const industry = text(row?.industry)
          if (!row || !industry) return null
          return {
            industry,
            weight_pct: numberOrNull(row.weight_pct) ?? 0,
            cost: numberOrNull(row.cost) ?? 0,
          }
        })
        .filter((row): row is NonNullable<typeof row> => row !== null),
    },
  }
}

export function parsePositionMutation(raw: unknown): PositionMutation {
  const row = record(raw)
  if (!row || row.ok === false) throw new ApiError('parse', '持仓写入响应缺少 ok=true')
  const position = parsePositionItem(row.position)
  const removedRow = record(row.removed)
  return {
    ok: true,
    local_only: true,
    store: 'local_sqlite',
    ...(position ? { position } : {}),
    ...(removedRow
      ? {
          removed: {
            code: text(removedRow.code),
            cost: numberOrNull(removedRow.cost) ?? 0,
            qty: numberOrNull(removedRow.qty) ?? 0,
          },
        }
      : {}),
  }
}

// ---------------------------------------------------------------- 查询串

function withParams(
  path: string,
  entries: Array<[string, string | number | string[] | null | undefined]>,
): string {
  const params = new URLSearchParams()
  for (const [key, value] of entries) {
    if (value === null || value === undefined || value === '') continue
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, String(item))
      continue
    }
    params.set(key, String(value))
  }
  const suffix = params.toString()
  return suffix ? `${path}?${suffix}` : path
}

export const httpRadarSource: RadarSource = {
  news: async (query, signal) =>
    parseNewsStream(
      await request<unknown>(
        withParams('/api/news', [
          ['date', query.date],
          ['until', query.until],
          ['source', query.sources],
          ['code', query.codes],
          ['limit', query.limit],
        ]),
        { signal },
      ),
    ),
  calendar: async (query, signal) =>
    parseEventCalendar(
      await request<unknown>(
        withParams('/api/events', [
          ['month', query.month],
          ['code', query.codes],
          ['limit', query.limit],
        ]),
        { signal },
      ),
    ),
  pool: async (query, signal) =>
    parseCandidatePool(
      await request<unknown>(
        withParams('/api/pool', [
          ['date', query.date],
          ['limit', query.limit],
        ]),
        { signal },
      ),
    ),
  positions: async (query, signal) =>
    parsePositions(
      await request<unknown>(withParams('/api/positions', [['date', query.date]]), { signal }),
    ),
  addPosition: async (input, signal) =>
    parsePositionMutation(await request<unknown>('/api/positions', { method: 'POST', body: input, signal })),
  updatePosition: async (code, patch, signal) =>
    parsePositionMutation(
      await request<unknown>(`/api/positions/${encodeURIComponent(code)}`, {
        method: 'PATCH',
        body: patch,
        signal,
      }),
    ),
  removePosition: async (code, signal) =>
    parsePositionMutation(
      await request<unknown>(`/api/positions/${encodeURIComponent(code)}`, {
        method: 'DELETE',
        signal,
      }),
    ),
}

// ---------------------------------------------------------------- 错误文案

export interface RadarError {
  title: string
  detail: string
  code: string | null
}

export function describeRadarError(error: unknown, subject: string): RadarError {
  if (isApiError(error)) {
    if (error.kind === 'not-connected') {
      return {
        title: `${subject}接口未接入`,
        detail: '先运行 `ldst run` 启动本机只读 API（默认 127.0.0.1:5950），再点重试。',
        code: null,
      }
    }
    if (error.kind === 'timeout') {
      return { title: `${subject}取数超时`, detail: error.message, code: 'TIMEOUT' }
    }
    if (error.kind === 'parse') {
      return { title: `${subject}响应解析失败`, detail: error.message, code: 'E_PARSE' }
    }
    if (error.status === 404) {
      return {
        title: `${subject}接口未接入`,
        detail: error.detail ?? '后端尚未提供该本机接口（HTTP 404）。',
        code: 'HTTP 404',
      }
    }
    if (error.status === 403) {
      return {
        title: `${subject}仅限本机`,
        detail: error.detail ?? '该接口只在回环地址可用（127.0.0.1）。',
        code: 'HTTP 403',
      }
    }
    return {
      title: `${subject}取数失败`,
      detail: error.detail ?? error.message,
      code: error.status ? `HTTP ${error.status}` : null,
    }
  }
  return {
    title: `${subject}取数失败`,
    detail: error instanceof Error ? error.message : String(error),
    code: null,
  }
}

/** 接口没接（404）→ 页面进空态而不是错误态。 */
export function isRadarEndpointMissing(error: unknown): boolean {
  return isEndpointMissing(error)
}

/** 本机服务没起 → 离线态。 */
export function isRadarOffline(error: unknown): boolean {
  return isApiError(error) && error.kind === 'not-connected'
}
