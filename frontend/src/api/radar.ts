/**
 * 资讯雷达 / 事件日历 / 候选池 / 我的持仓（R10）数据客户端。
 *
 * 四个页面都吃本机只读接口，契约见 `docs/specs/SPEC-RADAR-PAGES.md`：
 *
 *   GET    /api/news?date=&until=&source=&code=&limit=   资讯流（envelope，domain=news）
 *   GET    /api/events?month=&code=                      事件日历（envelope，domain=news）
 *   GET    /api/events/timeline?days=7|30|90             按日事件时间轴（**平面 JSON**，回环独占）
 *   GET    /api/pool?date=&limit=                        候选池（envelope，domain=sentiment）
 *   GET    /api/indices                                  首页指数条（**envelope**，domain=sentiment）
 *   GET    /api/watchlist                                我的自选（**平面 JSON**，回环独占）
 *   POST   /api/watchlist                                加一条自选（幂等，DEC-055）
 *   POST   /api/watchlist/{code}/delete                  删一条自选（DEC-055）
 *   GET    /api/portfolio?method=                        持仓账本（**平面 JSON**，只用于「是否持有」标记）
 *   GET    /api/positions?date=                          持仓（**平面 JSON**，隐私数据不进信封）
 *   POST   /api/positions                               按 code upsert 一条持仓
 *   POST   /api/positions/{code}/delete                 硬删除一条持仓
 *
 * 写面四条、都是 `POST`（删除也不换 `DELETE` 动词）：换动词会绕开
 * 「写方法必须带同源 Origin 或 `X-LDST-Client: 1`」这条防线。
 *
 * 三条硬规矩：
 * 1. **不重算指标**：候选分项、盈亏、集中度、行业分布全部用后端载荷；`null` 就是「没有」，
 *    解析层不补 0、不插值。
 * 2. **持仓不落浏览器存储**：本模块不 import `persist/` 与 `stores/`，页面数据只活在被调用栈里。
 * 3. 接口未接入（404 / 未连接）是「数据源暂缺」，页面进空态 / 离线态，不伪造数据。
 */

import { ApiError, isApiError, isEndpointMissing } from '../api'
import { envelopeMetaOf, type EnvelopeFields } from './envelope'

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

/** 时间轴三类 kind 的固定顺序与文案（QUOT-436）。 */
export const TIMELINE_KIND_ORDER = ['lockup', 'earnings', 'disclosure'] as const

export const TIMELINE_KIND_LABEL: Record<TimelineKind, string> = {
  lockup: '解禁',
  earnings: '业绩预告',
  disclosure: '公告',
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
  // QUOT-372 口径定稿（老板原话）：资金分项 = 当日实时主力净额（元）· 净占比（%）
  capital: '当日实时主力净额（元）· 净占比（%）',
  theme: '板块涨幅',
  turnover: '换手率',
}

export type PoolPart = (typeof POOL_PART_ORDER)[number]

/**
 * 资金分项的数据来源三态（QUOT-372）：`realtime` = 当日实时批量分区命中；
 * `daily_fallback` = 回落日线（不是当日实时，页面必须标「回放」）；`none` = 没有资金数据。
 */
export type FundFreshness = 'realtime' | 'daily_fallback' | 'none'

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

/** QUOT-436：按日事件时间轴。只收来源写出来的日期，没查到的在 `gaps` 里。 */
export type TimelineKind = 'lockup' | 'earnings' | 'disclosure'

export interface TimelineEvent {
  date: string
  kind: TimelineKind
  symbol: string
  name: string
  title: string
  detail: string[]
  source: string
  fetched_at: string | null
  estimated: boolean
  ref: string
}

export interface TimelineSymbol {
  symbol: string
  name: string
  origin: Array<'自选' | '持有'>
}

export interface EventTimelinePayload {
  local_only: true
  store: string
  window: { from: string; to: string; days: number }
  generated_at: string
  symbols: TimelineSymbol[]
  events: TimelineEvent[]
  overdue: TimelineEvent[]
  gaps: string[]
  data_source_status: RadarStatus
  notice: string
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
    /** 主力净占比（%，当日实时批量工具定稿字段）；回落日线没有这列 → null，显示「—」 */
    main_net_pct: number | null
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
  /** 资金分项口径与取数时刻（QUOT-372）：挨着 `data_as_of`，后端恒给（缺数时为 'none' + null） */
  fund_freshness: FundFreshness
  fund_fetched_at: string | null
}

// ---------------------------------------------------------------- 首页指数条（§9.1，信封）

/**
 * 一格指数。四格的顺序 / 条数 / 名称由后端 `core/indices.py:INDEX_WHITELIST` 写死
 * （`index_list` 是按涨幅排序的榜，不含这四个指数，还混老三板个股，所以不能当前端入参）。
 * `market` 后端给的是 `SH` / `SZ`，不是 SPEC-HOME-V2 §2.2 假设的 `cn/hk/us`。
 */
export interface IndexCell {
  code: string
  market: string | null
  name: string | null
  available: boolean
  price: number | null
  change: number | null
  change_pct: number | null
  prev_close: number | null
  /** peer 行里的行情时间（ISO 局部时间，**不带时区猜测**）；非交易时段它就是上一交易日收盘时刻。 */
  as_of: string | null
  reason: string | null
}

export interface IndicesPayload extends EnvelopeFields {
  available: boolean
  reason: string | null
  /** 后端给的口径句（「快照，不是逐笔实时」），前端不重写第二份。 */
  footnote: string
  price_source: string
  datasets: string[]
  cells: IndexCell[]
  data_source_status: RadarStatus
}

/** 市场后缀：白名单为 A 股 4 + 美股 2 + 港股 2；港股无合规源，格子在但显示「源未接入」。 */
export const INDEX_MARKET_LABEL: Record<string, string> = {
  SH: 'A 股',
  SZ: 'A 股',
  BJ: 'A 股',
  HK: '港股',
  US: '美股',
}

/** 未登记的市场值返回 `null` → 该行不显示后缀（后缀是修饰信息，缺失不阻塞读数）。 */
export function indexMarketLabel(market: string | null): string | null {
  if (!market) return null
  return INDEX_MARKET_LABEL[market.toUpperCase()] ?? null
}

/** 行情源数据集 → 中文（§8 禁止后端 key 直显）。 */
export const QUOTE_SOURCE_LABEL: Record<string, string> = {
  index_quote: '指数快照',
  index_quote_us: '美股指数快照',
  spot_quote: '实时行情快照',
  daily_k: '本机日线缓存',
}

export function quoteSourceLabel(dataset: string): string {
  return QUOTE_SOURCE_LABEL[dataset] ?? dataset
}

// ---------------------------------------------------------------- 我的自选（§9.2，平面）

/** 一条自选：`code` 是主键，其余全部来自 `spot_quote`，取不到就是 `null`。 */
export interface WatchlistItem {
  code: string
  added_at: string | null
  available: boolean
  name: string | null
  market: string | null
  latest_price: number | null
  change: number | null
  change_pct: number | null
  prev_close: number | null
  price_as_of: string | null
  /** `ok` / `unavailable`（这批没取到报价）；未登记值原样保留，文案退到原因。 */
  price_status: string
  price_reason: string | null
  /**
   * 三个指标列（QUOT-427 / DEC-060）：上游 `spot_quote` 只做投影，不算、不缓存。
   * `0` 是有效读数（亏损股 PE 可以为负、停牌换手可以是 0），`null` 才是「没有」。
   */
  pe_ttm: number | null
  pb: number | null
  turnover_pct: number | null
  /** 三项里缺哪一项就写哪一项的中文名；全齐时是 `null`，行 tooltip 用它解释 `—`。 */
  metrics_reason: string | null
}

/**
 * 平面 JSON：**没有 `payload` / `domain` / `fetched_at` 这三个信封键**（DEC-052），
 * 所以这里不套 `payloadOf()`，直接读顶层；外壳四键恒为 `null`，徽标据此出「未知」，
 * 不拿请求日期顶替数据时点。
 */
export interface WatchlistPayload {
  local_only: true
  store: 'local_sqlite'
  available: boolean
  /** 空态文案由接口给，指回本机 CLI（`ldst watch add`），页面不自编。 */
  reason: string | null
  /** 只数：后端没给就是 `null`，不 `?? 0`（R-07 家族）。 */
  watch_count: number | null
  data_source_status: RadarStatus
  price_source: string
  price_as_of: string | null
  source_detail: string | null
  items: WatchlistItem[]
  freshness: null
  source_status: null
  data_as_of: string | null
  fetched_at: null
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
  /**
   * 持仓表（schema v2）只有 `code/cost/qty/entry_date/industry/note`，**没有名称列**，
   * 所以这一版后端不给（SPEC-HOME-V2 §5.2）。解析层留字段、不拿 code 或 industry 顶替。
   */
  name?: string | null
  /** 当日盈亏 = `daily_k` 的 `close(T) − close(T−1)` × 股数；算不出是 `null` + 原因（QUOT-380 口径 4）。 */
  daily_pnl?: number | null
  daily_pnl_status?: DailyPnlStatus
  daily_pnl_reason?: string | null
}

/** `ok` 算得出 / `missing` 缓存里没有 T 或 T−1 日行 / `not_computed` 本次没算 / `partial` 只算出一半。 */
export type DailyPnlStatus = 'ok' | 'missing' | 'not_computed' | 'partial'

/** 取数水位（`price_lookup` / `daily_lookup`）：都只指本机缓存，`reason` 说清为什么没有。 */
export interface LookupStatus {
  dataset: string | null
  date: string | null
  prev_date: string | null
  backend: string | null
  reason: string | null
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
    /** 半截总数一律 `null`（QUOT-380 口径 5）：只有每条都算得出才给合计。 */
    daily_pnl?: number | null
    daily_pnl_status?: DailyPnlStatus
    /** 恒为 `CNY`：这一版不建「代码→市场→币种→汇率」模型，所以也不做币种汇总。 */
    currency?: string | null
    daily_pnl_missing?: Array<{ code: string; reason: string | null }>
  }
  items: PositionItem[]
  /** 现价水位：只指本机缓存，`reason` 说清那天没有记录。 */
  price_lookup?: LookupStatus
  /** 当日盈亏水位：与 `price_lookup` 同一个 T 日，两个出口才算同一个交易日。 */
  daily_lookup?: LookupStatus

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

export interface PositionMutation {  ok: true
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

export type TimelineDays = 7 | 30 | 90

export interface TimelineQuery {
  days: TimelineDays
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
  timeline(query: TimelineQuery, signal?: AbortSignal): Promise<EventTimelinePayload>
  pool(query: PoolQuery, signal?: AbortSignal): Promise<CandidatePoolPayload>
  positions(query: PositionsQuery, signal?: AbortSignal): Promise<PositionsPayload>
  addPosition(input: PositionInput, signal?: AbortSignal): Promise<PositionMutation>
  removePosition(code: string, signal?: AbortSignal): Promise<PositionMutation>
}

// ---------------------------------------------------------------- 请求

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

// ---------------------------------------------------------------- 事件时间轴

const TIMELINE_KINDS: readonly TimelineKind[] = ['lockup', 'earnings', 'disclosure']

function parseTimelineEvent(value: unknown): TimelineEvent | null {
  const row = record(value)
  if (!row) return null
  const date = text(row.date).slice(0, 10)
  const title = text(row.title)
  const kind = text(row.kind) as TimelineKind
  if (!date || !title || !TIMELINE_KINDS.includes(kind)) return null
  const raw = text(row.fetched_at)
  return {
    date,
    kind,
    symbol: text(row.symbol),
    name: text(row.name),
    title,
    detail: stringList(row.detail),
    source: text(row.source),
    fetched_at: raw || null,
    estimated: flag(row.estimated),
    ref: text(row.ref),
  }
}

function parseTimelineSymbol(value: unknown): TimelineSymbol | null {
  const row = record(value)
  if (!row) return null
  const symbol = text(row.symbol)
  if (!symbol) return null
  const origin = stringList(row.origin).filter(
    (item): item is '自选' | '持有' => item === '自选' || item === '持有',
  )
  return { symbol, name: text(row.name), origin }
}

export function parseEventTimeline(raw: unknown): EventTimelinePayload {
  const payload = payloadOf(raw, '事件时间轴')
  const window = record(payload.window) ?? {}
  const symbols = list(payload.symbols)
    .map(parseTimelineSymbol)
    .filter((item): item is TimelineSymbol => item !== null)
  const parseEvents = (value: unknown): TimelineEvent[] =>
    list(value)
      .map(parseTimelineEvent)
      .filter((item): item is TimelineEvent => item !== null)

  return {
    local_only: true,
    store: text(payload.store),
    window: {
      from: text(window.from),
      to: text(window.to),
      days: numberOrNull(window.days) ?? 30,
    },
    generated_at: text(payload.generated_at),
    symbols,
    events: parseEvents(payload.events),
    overdue: parseEvents(payload.overdue),
    gaps: stringList(payload.gaps),
    data_source_status: parseStatus(
      payload.data_source_status,
      symbols.length === 0 ? 'missing' : 'ok',
    ),
    notice: text(payload.notice),
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
      main_net_pct: numberOrNull(raw.main_net_pct),
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
    fund_freshness: parseFundFreshness(payload.fund_freshness),
    fund_fetched_at: text(payload.fund_fetched_at) || null,
  }
}

function parseFundFreshness(value: unknown): FundFreshness {
  const raw = text(value)
  return raw === 'realtime' || raw === 'daily_fallback' ? raw : 'none'
}

// ---------------------------------------------------------------- 首页指数条（§9.1，信封）

function parseIndexCell(value: unknown): IndexCell | null {
  const row = record(value)
  if (!row) return null
  const code = text(row.code)
  if (!code) return null
  return {
    code,
    market: text(row.market) || null,
    name: text(row.name) || null,
    available: flag(row.available),
    price: numberOrNull(row.price),
    change: numberOrNull(row.change),
    change_pct: numberOrNull(row.change_pct),
    prev_close: numberOrNull(row.prev_close),
    as_of: text(row.as_of) || null,
    reason: text(row.reason) || null,
  }
}

/**
 * 指数条走信封（与 `/api/news` `/api/pool` 同一条通道）：载荷在 `root.payload`，
 * 外壳的溯源字段由 `envelopeMetaOf()` 单独读出（QUOT-331），两者不混为一份。
 * **缺格也在 `cells[]` 里**（`available=false` + 原因），所以前端按数组原序渲染几格就是几格，
 * 不排序、不补格、不给取不到的格写 0。
 */
export function parseIndices(raw: unknown): IndicesPayload {
  const payload = payloadOf(raw, '指数快照')
  const meta = envelopeMetaOf(raw)
  const cells = list(payload.cells)
    .map(parseIndexCell)
    .filter((cell): cell is IndexCell => cell !== null)
  const priced = cells.some((cell) => cell.available && cell.price !== null)
  return {
    ...meta,
    available: flag(payload.available) || priced,
    reason: text(payload.reason) || null,
    footnote: text(payload.footnote, '快照，不是逐笔实时'),
    price_source: text(payload.price_source, 'index_quote'),
    datasets: stringList(payload.datasets),
    cells,
    data_source_status: parseStatus(payload.data_source_status, priced ? 'ok' : 'missing'),
    data_as_of: text(payload.data_as_of) ?? meta.data_as_of,
  }
}

// ---------------------------------------------------------------- 我的自选（§9.2，平面）

function parseWatchlistItem(value: unknown): WatchlistItem | null {
  const row = record(value)
  if (!row) return null
  const code = text(row.code)
  // 代码是主键：缺主键的行是脏数据不是缺数，整行不渲染（SPEC-HOME-V2 §4.2）
  if (!code) return null
  return {
    code,
    added_at: text(row.added_at) || null,
    available: flag(row.available),
    name: text(row.name) || null,
    market: text(row.market) || null,
    latest_price: numberOrNull(row.latest_price),
    change: numberOrNull(row.change),
    change_pct: numberOrNull(row.change_pct),
    prev_close: numberOrNull(row.prev_close),
    price_as_of: text(row.price_as_of) || null,
    price_status: text(row.price_status, 'unknown'),
    price_reason: text(row.price_reason) || null,
    pe_ttm: numberOrNull(row.pe_ttm),
    pb: numberOrNull(row.pb),
    turnover_pct: numberOrNull(row.turnover_pct),
    metrics_reason: text(row.metrics_reason) || null,
  }
}

/**
 * 自选是平面 JSON：**顶层就是载荷，不套 `payloadOf()`**（§9.2 的结构性保证——
 * `payload` / `domain` / `fetched_at` 这三个信封键一个都不出现，隐私数据才到不了
 * export / MCP / AI 三条出口，DEC-015）。
 * 因此没有外壳可读：`freshness` / `source_status` / `fetched_at` 恒为 `null`，
 * 徽标据此出「未知」而不是猜一个 `cached`；数据时点用载荷里的 `price_as_of`。
 */
export function parseWatchlist(raw: unknown): WatchlistPayload {
  const root = record(raw)
  if (!root) throw new ApiError('parse', '自选响应不是对象')
  const items = list(root.items)
    .map(parseWatchlistItem)
    .filter((item): item is WatchlistItem => item !== null)
  const priced = items.some((item) => item.latest_price !== null)
  return {
    local_only: true,
    store: 'local_sqlite',
    available: flag(root.available) && items.length > 0,
    reason: text(root.reason) || null,
    watch_count: numberOrNull(root.watch_count),
    data_source_status: parseStatus(root.data_source_status, priced ? 'ok' : 'missing'),
    price_source: text(root.price_source, 'spot_quote'),
    price_as_of: text(root.price_as_of) || null,
    source_detail: text(root.source_detail) || null,
    items,
    freshness: null,
    source_status: null,
    data_as_of: text(root.price_as_of) || null,
    fetched_at: null,
  }
}

// ---------------------------------------------------------------- 自选写入（DEC-055）

/** `POST /api/watchlist` 与 `…/{code}/delete` 的平面响应：**没有信封键**。 */
export interface WatchMutation {
  ok: true
  local_only: true
  store: 'local_sqlite'
  watch?: { code: string; created_at: string | null }
  removed?: { code: string }
}

export function parseWatchMutation(raw: unknown): WatchMutation {
  const row = record(raw)
  if (!row || row.ok === false) throw new ApiError('parse', '自选写入响应缺少 ok=true')
  const watch = record(row.watch)
  const removed = record(row.removed)
  const watchCode = text(watch?.code)
  const removedCode = text(removed?.code)
  return {
    ok: true,
    local_only: true,
    store: 'local_sqlite',
    ...(watchCode
      ? { watch: { code: watchCode, created_at: text(watch?.created_at) || null } }
      : {}),
    ...(removedCode ? { removed: { code: removedCode } } : {}),
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
    name: text(row.name) || null,
    daily_pnl: numberOrNull(row.daily_pnl),
    daily_pnl_status: dailyStatusOf(row.daily_pnl_status),
    daily_pnl_reason: text(row.daily_pnl_reason) || null,
  }
}

/** 未登记的当日盈亏状态词按「本次没算」处理：它比 `missing` 更少一层信息，不能合并。 */
function dailyStatusOf(value: unknown): DailyPnlStatus {
  return value === 'ok' || value === 'missing' || value === 'partial' ? value : 'not_computed'
}

function parseLookup(value: unknown): LookupStatus | undefined {
  const row = record(value)
  if (!row) return undefined
  return {
    dataset: text(row.dataset) || null,
    date: text(row.date) || null,
    prev_date: text(row.prev_date) || null,
    backend: text(row.backend) || null,
    reason: text(row.reason) || null,
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
      daily_pnl: numberOrNull(valuation.daily_pnl),
      daily_pnl_status: dailyStatusOf(valuation.daily_pnl_status),
      currency: text(valuation.currency) || null,
      daily_pnl_missing: list(valuation.daily_pnl_missing)
        .map((value) => {
          const row = record(value)
          const code = text(row?.code)
          if (!row || !code) return null
          return { code, reason: text(row.reason) || null }
        })
        .filter((entry): entry is { code: string; reason: string | null } => entry !== null),
    },
    items,
    price_lookup: parseLookup(payload.price_lookup),
    daily_lookup: parseLookup(payload.daily_lookup),
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
  timeline: async (query, signal) =>
    parseEventTimeline(
      await request<unknown>(
        withParams('/api/events/timeline', [['days', query.days]]),
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
  removePosition: async (code, signal) =>
    parsePositionMutation(
      await request<unknown>(`/api/positions/${encodeURIComponent(code)}/delete`, {
        method: 'POST',
        signal,
      }),
    ),
}

// ---------------------------------------------------------------- 首页两路取数（§9.1 / §9.2）

/**
 * 首页指数条与自选没有并进 `RadarSource`：那个接口是雷达四屏的取数契约，
 * `screens/radar/demo.ts` 按它逐方法给夹具。首页两路是首页的依赖，
 * 把它们塞进去等于让雷达屏的演示源凭空多出两个自己不用的方法。
 * 传输仍然复用同一个 `request()`：超时、404、断连的口径全仓只有一套。
 */
export async function fetchIndices(signal?: AbortSignal): Promise<IndicesPayload> {
  return parseIndices(await request<unknown>('/api/indices', { signal }))
}

export async function fetchWatchlist(signal?: AbortSignal): Promise<WatchlistPayload> {
  return parseWatchlist(await request<unknown>('/api/watchlist', { signal }))
}

/** 加一条自选（幂等）：写本机 SQLite，回环独占 + 来源证据（DEC-055）。 */
export async function addWatch(code: string, signal?: AbortSignal): Promise<WatchMutation> {
  return parseWatchMutation(
    await request<unknown>('/api/watchlist', { method: 'POST', body: { code }, signal }),
  )
}

/** 删一条自选：不在清单里后端回 404，不静默成功。 */
export async function removeWatch(code: string, signal?: AbortSignal): Promise<WatchMutation> {
  return parseWatchMutation(
    await request<unknown>(`/api/watchlist/${encodeURIComponent(code)}/delete`, {
      method: 'POST',
      signal,
    }),
  )
}

/**
 * 「是否持有」标记（QUOT-431）：只读 `GET /api/portfolio` 的 `items[].symbol`。
 *
 * `items` 是流水重放后仍有份额的持仓（清仓的进 `closed`，不算持有）；
 * 这里只要代码集合，不把数量 / 成本带进自选页。接口失败时调用方保留
 * 「标记暂缺」，自选表格本身照常展示（两个数据面互不拖累）。
 */
export async function fetchPortfolioSymbols(signal?: AbortSignal): Promise<Set<string>> {
  const raw = await request<unknown>(withParams('/api/portfolio', [['method', 'average']]), {
    signal,
  })
  const root = record(raw)
  const symbols = list(root?.items)
    .map((item) => text(record(item)?.symbol).trim().toUpperCase())
    .filter(Boolean)
  return new Set(symbols)
}

// ---------------------------------------------------------------- 我的持仓账本（QUOT-434）

/**
 * `GET /api/portfolio` 与 `GET /api/portfolio/perf` 的解析层。
 *
 * 两条硬规矩：
 * 1. **不重算**：持仓、成本、盈亏、分币种合计、收益率全部用后端载荷；
 *    `null` 就是「没有」，这里不补 0、不插值。
 * 2. **写面只发一种形状**：新增流水用后端字段名 `trade_date`（`POST /api/portfolio/ledger`
 *    的白名单就是它）；读面 `ledger[]` 里同一个值叫 `date`，两边不要混。
 */

/** 成本法：`fifo` 先进先出 / `average` 移动加权平均（与后端 `COST_METHODS` 同键）。 */
export type PortfolioMethod = 'fifo' | 'average'

export type TradeType = 'opening' | 'buy' | 'sell' | 'dividend' | 'split'

export type LegacyState = 'pending' | 'stale' | 'conflict' | 'skipped' | 'dismissed'

export interface PortfolioLot {
  trade_id: number
  date: string
  shares: number
  unit_cost: number
}

export interface PortfolioItem {
  symbol: string
  name: string
  shares: number
  unit_cost: number
  cost_total: number
  opened_at: string
  lots: PortfolioLot[]
  realized_pnl: number
  dividends: number
  fees: number
  trade_count: number
  latest_price: number | null
  change: number | null
  change_pct: number | null
  market_value: number | null
  pnl: number | null
  pnl_pct: number | null
  price_reason: string | null
}

export interface PortfolioTrade {
  id: number
  symbol: string
  name: string
  type: string
  date: string
  shares: number | null
  price: number | null
  fee: number | null
  amount: number | null
  ratio: number | null
  note: string
  migrated_from: number | null
  undated: boolean
  created_at: string
}

export interface PortfolioLegacyRow {
  id: number
  symbol: string
  name: string
  shares: number | null
  cost: number | null
  opened_at: string
  state: LegacyState
  why: string
  migrated_trade: number | null
}

export interface PortfolioIssue {
  id: number
  symbol: string
  date: string
  type: string
  why: string
}

export interface PortfolioClosedRow {
  symbol: string
  name: string
  realized_pnl: number
  dividends: number
  fees: number
  last_date: string
}

export interface PortfolioTotalsCny {
  currency: string
  available: boolean
  market_value: number | null
  cost: number | null
  pnl: number | null
  pnl_pct: number | null
  priced_count: number
  unpriced_count: number
  reason: string | null
}

export interface PortfolioMigration {
  backup: string | null
  migrated: Array<{ position_id: number; trade_id: number; symbol: string }>
  removed_stale: string[]
  failed: Array<{ position_id: number; symbol: string; why: string }>
  error: string | null
}

export interface PortfolioPayload {
  local_only: boolean
  store: string
  method: PortfolioMethod
  method_label: string
  methods: Array<{ id: string; label: string }>
  today: string
  price_lookup: { dataset: string; date: string | null; reason: string | null }
  items: PortfolioItem[]
  closed: PortfolioClosedRow[]
  issues: PortfolioIssue[]
  ledger: PortfolioTrade[]
  legacy: PortfolioLegacyRow[]
  migration_pending: number
  migration: PortfolioMigration
  totals_cny: PortfolioTotalsCny
}

export interface PortfolioPerfGroup {
  market: string
  currency: string
  label: string
  ok: boolean
  reason: string
  start: string | null
  end: string | null
  span_days: number | null
  twr: number | null
  twr_annualized: number | null
  xirr: number | null
  mwr_period: number | null
  invested: number | null
  proceeds: number | null
  dividends: number | null
  fees: number | null
  end_value: number | null
  gain: number | null
  excluded: Array<{ symbol: string; name: string; reason: string }>
  notes: string[]
  warnings: string[]
  series: { dates: string[]; twr_index: Array<number | null> } | null
}

export interface PortfolioPerfPayload {
  as_of: string | null
  groups: PortfolioPerfGroup[]
  skipped: Array<{ id: number; symbol: string; why: string }>
  gaps: Array<{ symbol: string; reason: string }>
  sources: string[]
  notes: string[]
}

export interface PortfolioReplayHolding {
  symbol: string
  name: string
  weight: number | null
  return: number | null
  contribution: number | null
  first_date: string
  last_date: string
  halted_days: number
}

export interface PortfolioReplayGap {
  symbol: string
  name: string
  reason: string
}

/** 一段压力窗口的组合读数；`return` 是小数（`-0.3521` = −35.21%）。 */
export interface PortfolioReplayStats {
  return: number | null
  max_drawdown: number | null
  max_drawdown_peak: string | null
  max_drawdown_trough: string | null
  worst_day: number | null
  worst_day_date: string | null
  days: number | null
  included_value: number | null
  value_change: number | null
}

export interface PortfolioReplayScenario {
  id: string
  label: string
  start: string
  end: string
  /** 非 `null`：这段窗口算不了，只显示原因，不给任何收益读数。 */
  unavailable: string | null
  benchmark: null
  benchmark_error: string
  portfolio: PortfolioReplayStats | null
  /** 纳入持仓的当前市值占比（0..1）。 */
  coverage: number | null
  included: number
  holdings: PortfolioReplayHolding[]
  gaps: PortfolioReplayGap[]
  /** 净值路径，首日 = 1.0；少于 2 个点时不画。 */
  path: { dates: string[]; portfolio: number[] } | null
}

export interface PortfolioReplayGroup {
  market: string
  currency: string
  label: string
  total_value: number | null
  /** 当前持仓只数（含没纳入的）。 */
  input: number
  scenarios: PortfolioReplayScenario[]
}

export interface PortfolioReplayPayload {
  scenario: string
  as_of: string | null
  groups: PortfolioReplayGroup[]
  notes: string[]
  sources: string[]
}

/** 新增流水的请求体：字段名与 `POST /api/portfolio/ledger` 白名单一致。 */
export interface TradeInput {
  symbol: string
  name?: string
  type: TradeType
  trade_date: string
  shares?: number
  price?: number
  fee?: number
  amount?: number
  ratio?: number
  note?: string
}

function integerOf(value: unknown, fallback = 0): number {
  const parsed = numberOrNull(value)
  return parsed === null ? fallback : Math.trunc(parsed)
}

function reasonOf(value: unknown): string | null {
  const raw = text(value).trim()
  return raw || null
}

function parseLot(value: unknown): PortfolioLot | null {
  const row = record(value)
  if (!row) return null
  return {
    trade_id: integerOf(row.trade_id),
    date: text(row.date),
    shares: numberOrNull(row.shares) ?? 0,
    unit_cost: numberOrNull(row.unit_cost) ?? 0,
  }
}

function parseItem(value: unknown): PortfolioItem | null {
  const row = record(value)
  if (!row) return null
  const symbol = text(row.symbol).trim().toUpperCase()
  if (!symbol) return null
  return {
    symbol,
    name: text(row.name),
    shares: numberOrNull(row.shares) ?? 0,
    unit_cost: numberOrNull(row.unit_cost) ?? 0,
    cost_total: numberOrNull(row.cost_total) ?? 0,
    opened_at: text(row.opened_at),
    lots: list(row.lots).map(parseLot).filter((item): item is PortfolioLot => item !== null),
    realized_pnl: numberOrNull(row.realized_pnl) ?? 0,
    dividends: numberOrNull(row.dividends) ?? 0,
    fees: numberOrNull(row.fees) ?? 0,
    trade_count: integerOf(row.trade_count),
    latest_price: numberOrNull(row.latest_price),
    change: numberOrNull(row.change),
    change_pct: numberOrNull(row.change_pct),
    market_value: numberOrNull(row.market_value),
    pnl: numberOrNull(row.pnl),
    pnl_pct: numberOrNull(row.pnl_pct),
    price_reason: reasonOf(row.price_reason),
  }
}

function parseTrade(value: unknown): PortfolioTrade | null {
  const row = record(value)
  if (!row) return null
  const id = numberOrNull(row.id)
  const symbol = text(row.symbol).trim().toUpperCase()
  const type = text(row.type)
  if (id === null || !symbol || !type) return null
  return {
    id: Math.trunc(id),
    symbol,
    name: text(row.name),
    type,
    date: text(row.date),
    shares: numberOrNull(row.shares),
    price: numberOrNull(row.price),
    fee: numberOrNull(row.fee),
    amount: numberOrNull(row.amount),
    ratio: numberOrNull(row.ratio),
    note: text(row.note),
    migrated_from: numberOrNull(row.migrated_from),
    undated: flag(row.undated),
    created_at: text(row.created_at),
  }
}

function parseLegacyState(value: unknown): LegacyState {
  return value === 'pending' || value === 'stale' || value === 'conflict' || value === 'skipped' || value === 'dismissed'
    ? value
    : 'skipped'
}

function parseLegacy(value: unknown): PortfolioLegacyRow | null {
  const row = record(value)
  if (!row) return null
  const id = numberOrNull(row.id)
  if (id === null) return null
  return {
    id: Math.trunc(id),
    symbol: text(row.symbol).trim().toUpperCase(),
    name: text(row.name),
    shares: numberOrNull(row.shares),
    cost: numberOrNull(row.cost),
    opened_at: text(row.opened_at),
    state: parseLegacyState(row.state),
    why: text(row.why),
    migrated_trade: numberOrNull(row.migrated_trade),
  }
}

function parseIssue(value: unknown): PortfolioIssue | null {
  const row = record(value)
  if (!row) return null
  const id = numberOrNull(row.id)
  if (id === null) return null
  return {
    id: Math.trunc(id),
    symbol: text(row.symbol).trim().toUpperCase(),
    date: text(row.date),
    type: text(row.type),
    why: text(row.why),
  }
}

function parseClosed(value: unknown): PortfolioClosedRow | null {
  const row = record(value)
  if (!row) return null
  const symbol = text(row.symbol).trim().toUpperCase()
  if (!symbol) return null
  return {
    symbol,
    name: text(row.name),
    realized_pnl: numberOrNull(row.realized_pnl) ?? 0,
    dividends: numberOrNull(row.dividends) ?? 0,
    fees: numberOrNull(row.fees) ?? 0,
    last_date: text(row.last_date),
  }
}

function parseCnyTotals(value: unknown): PortfolioTotalsCny {
  const row = record(value) ?? {}
  return {
    currency: text(row.currency) || 'CNY',
    available: flag(row.available),
    market_value: numberOrNull(row.market_value),
    cost: numberOrNull(row.cost),
    pnl: numberOrNull(row.pnl),
    pnl_pct: numberOrNull(row.pnl_pct),
    priced_count: integerOf(row.priced_count),
    unpriced_count: integerOf(row.unpriced_count),
    reason: reasonOf(row.reason),
  }
}

function parseMigration(value: unknown): PortfolioMigration {
  const row = record(value) ?? {}
  return {
    backup: reasonOf(row.backup),
    migrated: list(row.migrated)
      .map(record)
      .filter((item): item is Record<string, unknown> => item !== null)
      .map((item) => ({
        position_id: integerOf(item.position_id),
        trade_id: integerOf(item.trade_id),
        symbol: text(item.symbol).trim().toUpperCase(),
      })),
    removed_stale: stringList(row.removed_stale),
    failed: list(row.failed)
      .map(record)
      .filter((item): item is Record<string, unknown> => item !== null)
      .map((item) => ({
        position_id: integerOf(item.position_id),
        symbol: text(item.symbol).trim().toUpperCase(),
        why: text(item.why),
      })),
    error: reasonOf(row.error),
  }
}

function parsePortfolio(raw: unknown): PortfolioPayload {
  const root = record(raw)
  if (!root) throw new ApiError('parse', '持仓接口返回的不是对象')
  const lookup = record(root.price_lookup) ?? {}
  return {
    local_only: flag(root.local_only),
    store: text(root.store),
    method: root.method === 'fifo' ? 'fifo' : 'average',
    method_label: text(root.method_label),
    methods: list(root.methods)
      .map(record)
      .filter((item): item is Record<string, unknown> => item !== null)
      .map((item) => ({ id: text(item.id), label: text(item.label) }))
      .filter((item) => Boolean(item.id)),
    today: text(root.today),
    price_lookup: {
      dataset: text(lookup.dataset),
      date: reasonOf(lookup.date),
      reason: reasonOf(lookup.reason),
    },
    items: list(root.items).map(parseItem).filter((item): item is PortfolioItem => item !== null),
    closed: list(root.closed).map(parseClosed).filter((item): item is PortfolioClosedRow => item !== null),
    issues: list(root.issues).map(parseIssue).filter((item): item is PortfolioIssue => item !== null),
    ledger: list(root.ledger).map(parseTrade).filter((item): item is PortfolioTrade => item !== null),
    legacy: list(root.legacy).map(parseLegacy).filter((item): item is PortfolioLegacyRow => item !== null),
    migration_pending: integerOf(root.migration_pending),
    migration: parseMigration(root.migration),
    totals_cny: parseCnyTotals(root.totals_cny),
  }
}

function parsePerfGroup(value: unknown): PortfolioPerfGroup | null {
  const row = record(value)
  if (!row) return null
  const series = record(row.series)
  const dates = series ? stringList(series.dates) : []
  const indexes = series ? list(series.twr_index).map(numberOrNull) : []
  return {
    market: text(row.market),
    currency: text(row.currency) || 'CNY',
    label: text(row.label) || text(row.currency) || '人民币',
    ok: flag(row.ok),
    reason: text(row.reason),
    start: reasonOf(row.start),
    end: reasonOf(row.end),
    span_days: numberOrNull(row.span_days),
    twr: numberOrNull(row.twr),
    twr_annualized: numberOrNull(row.twr_annualized),
    xirr: numberOrNull(row.xirr),
    mwr_period: numberOrNull(row.mwr_period),
    invested: numberOrNull(row.invested),
    proceeds: numberOrNull(row.proceeds),
    dividends: numberOrNull(row.dividends),
    fees: numberOrNull(row.fees),
    end_value: numberOrNull(row.end_value),
    gain: numberOrNull(row.gain),
    excluded: list(row.excluded)
      .map(record)
      .filter((item): item is Record<string, unknown> => item !== null)
      .map((item) => ({
        symbol: text(item.symbol).trim().toUpperCase(),
        name: text(item.name),
        reason: text(item.reason),
      })),
    notes: stringList(row.notes),
    warnings: stringList(row.warnings),
    series:
      dates.length === indexes.length && dates.length >= 2
        ? { dates, twr_index: indexes }
        : null,
  }
}

function parsePerf(raw: unknown): PortfolioPerfPayload {
  const root = record(raw)
  if (!root) throw new ApiError('parse', '收益率接口返回的不是对象')
  return {
    as_of: reasonOf(root.as_of),
    groups: list(root.groups).map(parsePerfGroup).filter((item): item is PortfolioPerfGroup => item !== null),
    skipped: list(root.skipped)
      .map(record)
      .filter((item): item is Record<string, unknown> => item !== null)
      .map((item) => ({
        id: integerOf(item.id),
        symbol: text(item.symbol).trim().toUpperCase(),
        why: text(item.why),
      })),
    gaps: list(root.gaps)
      .map(record)
      .filter((item): item is Record<string, unknown> => item !== null)
      .map((item) => ({ symbol: text(item.symbol).trim().toUpperCase(), reason: text(item.reason) })),
    sources: stringList(root.sources),
    notes: stringList(root.notes),
  }
}

function parseReplayHolding(value: unknown): PortfolioReplayHolding | null {
  const row = record(value)
  if (!row) return null
  const symbol = text(row.symbol).trim().toUpperCase()
  if (!symbol) return null
  return {
    symbol,
    name: text(row.name),
    weight: numberOrNull(row.weight),
    return: numberOrNull(row.return),
    contribution: numberOrNull(row.contribution),
    first_date: text(row.first_date),
    last_date: text(row.last_date),
    halted_days: integerOf(row.halted_days),
  }
}

function parseReplayGap(value: unknown): PortfolioReplayGap | null {
  const row = record(value)
  if (!row) return null
  const symbol = text(row.symbol).trim().toUpperCase()
  const reason = text(row.reason)
  if (!symbol && !reason) return null
  return { symbol, name: text(row.name), reason }
}

function parseReplayStats(value: unknown): PortfolioReplayStats | null {
  const row = record(value)
  if (!row) return null
  return {
    return: numberOrNull(row.return),
    max_drawdown: numberOrNull(row.max_drawdown),
    max_drawdown_peak: reasonOf(row.max_drawdown_peak),
    max_drawdown_trough: reasonOf(row.max_drawdown_trough),
    worst_day: numberOrNull(row.worst_day),
    worst_day_date: reasonOf(row.worst_day_date),
    days: numberOrNull(row.days),
    included_value: numberOrNull(row.included_value),
    value_change: numberOrNull(row.value_change),
  }
}

/** 净值路径：日期与净值必须一一对应且至少 2 个点，否则当作没给（不画图）。 */
function parseReplayPath(value: unknown): { dates: string[]; portfolio: number[] } | null {
  const row = record(value)
  if (!row) return null
  const dates = list(row.dates).map((item) => text(item))
  const points: number[] = []
  for (const item of list(row.portfolio)) {
    const parsed = numberOrNull(item)
    if (parsed === null) return null
    points.push(parsed)
  }
  if (dates.length < 2 || dates.some((date) => !date) || points.length !== dates.length) return null
  return { dates, portfolio: points }
}

function parseReplayScenario(value: unknown): PortfolioReplayScenario | null {
  const row = record(value)
  if (!row) return null
  const id = text(row.id).trim()
  if (!id) return null
  return {
    id,
    label: text(row.label) || id,
    start: text(row.start),
    end: text(row.end),
    unavailable: reasonOf(row.unavailable),
    benchmark: null,
    benchmark_error: text(row.benchmark_error),
    portfolio: parseReplayStats(row.portfolio),
    coverage: numberOrNull(row.coverage),
    included: integerOf(row.included),
    holdings: list(row.holdings)
      .map(parseReplayHolding)
      .filter((item): item is PortfolioReplayHolding => item !== null),
    gaps: list(row.gaps)
      .map(parseReplayGap)
      .filter((item): item is PortfolioReplayGap => item !== null),
    path: parseReplayPath(row.path),
  }
}

function parseReplayGroup(value: unknown): PortfolioReplayGroup | null {
  const row = record(value)
  if (!row) return null
  const currency = text(row.currency) || 'CNY'
  return {
    market: text(row.market),
    currency,
    label: text(row.label) || currency,
    total_value: numberOrNull(row.total_value),
    input: integerOf(row.input),
    scenarios: list(row.scenarios)
      .map(parseReplayScenario)
      .filter((item): item is PortfolioReplayScenario => item !== null),
  }
}

/** 情景回放：缺字段一律 `null` / `[]`，不因为一个字段缺失让整页报错。 */
export function parseReplayPayload(raw: unknown): PortfolioReplayPayload {
  const root = record(raw)
  if (!root) throw new ApiError('parse', '情景回放接口返回的不是对象')
  return {
    scenario: text(root.scenario) || 'all',
    as_of: reasonOf(root.as_of),
    groups: list(root.groups)
      .map(parseReplayGroup)
      .filter((item): item is PortfolioReplayGroup => item !== null),
    notes: stringList(root.notes),
    sources: stringList(root.sources),
  }
}

/** 持仓账本（流水重放 + 行情拼接 + 旧仓分类）。 */
export async function fetchPortfolio(
  method: PortfolioMethod,
  signal?: AbortSignal,
): Promise<PortfolioPayload> {
  return parsePortfolio(
    await request<unknown>(withParams('/api/portfolio', [['method', method]]), { signal }),
  )
}

/** 收益子页：TWR / 资金加权 / 净值序列（后端只读本机 daily_k）。 */
export async function fetchPortfolioPerf(signal?: AbortSignal): Promise<PortfolioPerfPayload> {
  return parsePerf(await request<unknown>('/api/portfolio/perf', { signal }))
}

/** 情景回放：按当前持仓市值权重回看四段固定压力窗口（只读本机 daily_k，不联网）。 */
export async function fetchPortfolioReplay(signal?: AbortSignal): Promise<PortfolioReplayPayload> {
  return parseReplayPayload(
    await request<unknown>(withParams('/api/portfolio/replay', [['scenario', 'all']]), { signal }),
  )
}

/** 记一笔：只写本机 `portfolio_trades`，失败原因原样抛给页面。 */
export async function addPortfolioTrade(
  input: TradeInput,
  signal?: AbortSignal,
): Promise<PortfolioTrade | null> {
  const raw = await request<unknown>('/api/portfolio/ledger', {
    method: 'POST',
    body: input,
    signal,
  })
  return parseTrade(record(raw)?.trade)
}

/** 删一笔流水：硬删，后端删前先备份。 */
export async function deletePortfolioTrade(id: number, signal?: AbortSignal): Promise<void> {
  await request<unknown>(`/api/portfolio/ledger/${id}/delete`, { method: 'POST', signal })
}

/** 旧记录确认转成期初流水（已有按它转过的原地替换，不新增第二笔）。 */
export async function adoptLegacy(positionId: number, signal?: AbortSignal): Promise<void> {
  await request<unknown>(`/api/portfolio/legacy/${positionId}/adopt`, { method: 'POST', signal })
}

/** 旧记录标成「不要这条」：只写 mark，不删 `positions` 行。 */
export async function dismissLegacy(positionId: number, signal?: AbortSignal): Promise<void> {
  await request<unknown>(`/api/portfolio/legacy/${positionId}/dismiss`, { method: 'POST', signal })
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
        detail: '本机只读 API 未连接（ldst run 未启动，默认 127.0.0.1:5950）；启动后点重试。',
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
