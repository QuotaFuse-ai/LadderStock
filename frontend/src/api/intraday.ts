/**
 * 「盯盘 · 竞价与盘中」（QUOT-302）只读客户端。
 *
 *   GET /api/intraday?date=YYYY-MM-DD               6 个时点的状态汇总（读 status.json）
 *   GET /api/intraday/snapshot?date&slot=HH:MM       单个时点的快照原文
 *
 * 字段名与语义逐键对齐 `docs/specs/SPEC-INTRADAY-READ`（DEC-039），**不自行猜字段**：
 * 时点行是 `slot / status / recorded / available / reason / file / detail / price_field /
 * stocks / fresh / at`，快照是 `payload.snapshot` 里嵌一层的 `<HHMM>.json` 原文。
 *
 * 三条硬规矩（与 `/api/limit-pool` 客户端同口径）：
 * 1. **不重算**：页面只显示载荷里的读数，`null` 就是「没有」，不补 0、不从别的字段推；
 * 2. **不硬编码时点表**：`slots[]` 有几个就渲染几个，时刻本身来自后端；
 * 3. `status` 的取值由采集侧决定，未知值原样透出（后端也是原样透出），不改写成已知项。
 *
 * 未接入（404）/ 未连接由页面转成空态 / 离线态，**绝不回落到演示数据**
 * ——演示源只在 `?demo=1` 时显式启用。这两支接口回环独占（非回环 403），
 * 403 的 `detail` 由后端给出，页面按取数失败呈现。
 */

import { ApiError, isApiError, isEndpointMissing } from '../api'

const READ_TIMEOUT_MS = 8000

export const INTRADAY_SUMMARY_PATH = '/api/intraday'
export const INTRADAY_SNAPSHOT_PATH = '/api/intraday/snapshot'

export type IntradayDataStatus = 'ok' | 'missing'

/**
 * `status.json` 的六个已知时点状态（`core/intraday.py` 的 `STATUS_*`）。
 * 只是「已知」清单：渲染层据此选文案，未知值仍原样显示，不折叠成 `missing`。
 */
export const INTRADAY_SLOT_STATUSES = ['ok', 'degraded', 'already', 'skipped', 'missing', 'failed'] as const

export type IntradaySlotStatus = (typeof INTRADAY_SLOT_STATUSES)[number]

export function isKnownSlotStatus(value: unknown): value is IntradaySlotStatus {
  return typeof value === 'string' && (INTRADAY_SLOT_STATUSES as readonly string[]).includes(value)
}

/** `status.json` 里记着「这个时点落过盘」的三种状态（同后端 `_INTRADAY_STORED`）。 */
const STORED_STATUSES: readonly IntradaySlotStatus[] = ['ok', 'degraded', 'already']

export function statusImpliesStored(status: string): boolean {
  return isKnownSlotStatus(status) && STORED_STATUSES.includes(status)
}

// ---------------------------------------------------------------- 类型

/** 时点条的一行；除 `slot` / `status` / 三个布尔之外全部可为 `null`（后端不补默认值）。 */
export interface IntradaySlotRow {
  slot: string
  status: string
  /** `status.json` 里有没有这条记录。`false` = 这个时点当天没跑过 */
  recorded: boolean
  /** 磁盘上此刻到底有没有快照文件 */
  available: boolean
  reason: string | null
  file: string | null
  detail: string | null
  price_field: string | null
  stocks: number | null
  fresh: number | null
  at: string | null
}

export interface IntradaySummaryPayload {
  date: string
  available: boolean
  reason: string | null
  /** `status.json` 在不在：「调度器没跑过这一天」与「跑了但没抓到」的分界 */
  has_status_file: boolean
  slot_count: number
  snapshot_count: number
  status_updated_at: string | null
  data_source_status: IntradayDataStatus
  slots: IntradaySlotRow[]
}

/** `<HHMM>.json` 里 `yesterday_limit_up.overall`（采集侧算好，页面只用不改）。 */
export interface IntradayLimitUpOverall {
  count: number | null
  avg_change_pct: number | null
  up: number | null
  flat: number | null
  down: number | null
  limit_up: number | null
}

export interface IntradayBoardBucket {
  board: string
  count: number | null
  avg_change_pct: number | null
}

/** 分板位与最高板的逐只读数；除 `code` 外均可为 `null`。 */
export interface IntradayStockReading {
  code: string
  name: string | null
  yesterday_streak: number | null
  reading_field: string | null
  reading: number | null
  change_pct: number | null
  at_limit_up: boolean | null
  fresh: boolean | null
  reason: string | null
}

export interface IntradayYesterdayLimitUp {
  count: number | null
  fresh: number | null
  stale: number | null
  missing: string[]
  overall: IntradayLimitUpOverall | null
  by_board: IntradayBoardBucket[]
  top_board: { streak: number | null; stocks: IntradayStockReading[] } | null
  stocks: IntradayStockReading[]
}

export interface IntradayOneToTwo {
  available: boolean
  yesterday_first_board: number | null
  today_second_board: number | null
  promoted: number | null
  rate_pct: number | null
  reason: string | null
}

export interface IntradaySentimentBlock {
  available: boolean
  reason: string | null
  limit_up: number | null
  broken: number | null
  limit_down: number | null
  max_streak: number | null
  seal_rate_pct: number | null
  broken_rate_pct: number | null
  one_to_two: IntradayOneToTwo | null
}

export interface IntradaySnapshot {
  date: string
  slot: string
  captured_at: string | null
  price_field: string | null
  /** 取数来源（dataset / tool / source_detail / trail），原样展示不解读 */
  source: { dataset: string | null; tool: string | null; source_detail: string | null; trail: string[] }
  yesterday_limit_up: IntradayYesterdayLimitUp | null
  sentiment: IntradaySentimentBlock | null
  /** 采集侧后续新增的键原样保留，页面不解读也不丢 */
  extra: Record<string, unknown>
}

export interface IntradaySnapshotPayload {
  date: string
  slot: string
  available: boolean
  reason: string | null
  data_source_status: IntradayDataStatus
  snapshot: IntradaySnapshot | null
}

export interface IntradayQuery {
  date: string
}

export interface IntradaySnapshotQuery extends IntradayQuery {
  slot: string
}

export interface IntradaySource {
  summary: (query: IntradayQuery, signal?: AbortSignal) => Promise<IntradaySummaryPayload>
  snapshot: (query: IntradaySnapshotQuery, signal?: AbortSignal) => Promise<IntradaySnapshotPayload>
}

// ---------------------------------------------------------------- 解析

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function textOrNull(value: unknown): string | null {
  return text(value) || null
}

/** 有限数字才认；字符串形态的 `'3'` 不猜成数字之外的任何加工。 */
function numberOrNull(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  return null
}

/** 布尔严格按 `true` / `false` 收敛；缺字段是 `null`（「没有这个信息」），不当成 `false`。 */
function booleanOrNull(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null
}

function requiredBoolean(value: unknown): boolean {
  return value === true
}

function parseStatus(value: unknown, fallback: IntradayDataStatus): IntradayDataStatus {
  return value === 'ok' || value === 'missing' ? value : fallback
}

/** envelope 的 `payload`；也接受直接返回载荷的平面响应（容错但不猜结构）。 */
function payloadOf(raw: unknown, label: string): Record<string, unknown> {
  const root = record(raw)
  if (!root) throw new ApiError('parse', `${label}响应不是对象`)
  return record(root.payload) ?? root
}

function parseSlotRow(value: unknown): IntradaySlotRow | null {
  const row = record(value)
  if (!row) return null
  const slot = text(row.slot)
  if (!slot) return null
  const status = text(row.status)
  return {
    slot,
    // 后端对没有记录的时点给 `missing`；缺字段时同样按 missing 呈现，但不改写已知值
    status: status || 'missing',
    recorded: requiredBoolean(row.recorded),
    available: requiredBoolean(row.available),
    reason: textOrNull(row.reason),
    file: textOrNull(row.file),
    detail: textOrNull(row.detail),
    price_field: textOrNull(row.price_field),
    stocks: numberOrNull(row.stocks),
    fresh: numberOrNull(row.fresh),
    at: textOrNull(row.at),
  }
}

export function parseIntradaySummary(raw: unknown): IntradaySummaryPayload {
  const payload = payloadOf(raw, '盘中时点汇总')
  const slots = list(payload.slots)
    .map(parseSlotRow)
    .filter((row): row is IntradaySlotRow => row !== null)
  const available = requiredBoolean(payload.available)
  return {
    date: text(payload.date),
    available,
    reason: textOrNull(payload.reason),
    has_status_file: requiredBoolean(payload.has_status_file),
    slot_count: numberOrNull(payload.slot_count) ?? slots.length,
    // 有文件的行数由后端给；缺失时退回逐行计数，不谎报（后端本来就是同一定义）
    snapshot_count: numberOrNull(payload.snapshot_count) ?? slots.filter((row) => row.available).length,
    status_updated_at: textOrNull(payload.status_updated_at),
    data_source_status: parseStatus(payload.data_source_status, available ? 'ok' : 'missing'),
    slots,
  }
}

const SNAPSHOT_KNOWN_KEYS: readonly string[] = [
  'date',
  'slot',
  'captured_at',
  'price_field',
  'source',
  'yesterday_limit_up',
  'sentiment',
]

function parseStockReading(value: unknown): IntradayStockReading | null {
  const row = record(value)
  if (!row) return null
  const code = text(row.code)
  if (!code) return null
  return {
    code,
    name: textOrNull(row.name),
    yesterday_streak: numberOrNull(row.yesterday_streak),
    reading_field: textOrNull(row.reading_field),
    reading: numberOrNull(row.reading),
    change_pct: numberOrNull(row.change_pct),
    at_limit_up: booleanOrNull(row.at_limit_up),
    fresh: booleanOrNull(row.fresh),
    reason: textOrNull(row.reason),
  }
}

function parseYesterdayLimitUp(value: unknown): IntradayYesterdayLimitUp | null {
  const block = record(value)
  if (!block) return null
  const overall = record(block.overall)
  const topBoard = record(block.top_board)
  return {
    count: numberOrNull(block.count),
    fresh: numberOrNull(block.fresh),
    stale: numberOrNull(block.stale),
    missing: list(block.missing).map(text).filter((code) => code !== ''),
    overall: overall
      ? {
          count: numberOrNull(overall.count),
          avg_change_pct: numberOrNull(overall.avg_change_pct),
          up: numberOrNull(overall.up),
          flat: numberOrNull(overall.flat),
          down: numberOrNull(overall.down),
          limit_up: numberOrNull(overall.limit_up),
        }
      : null,
    by_board: list(block.by_board).flatMap((item) => {
      const bucket = record(item)
      if (!bucket) return []
      const board = text(bucket.board)
      if (!board) return []
      return [{ board, count: numberOrNull(bucket.count), avg_change_pct: numberOrNull(bucket.avg_change_pct) }]
    }),
    top_board: topBoard
      ? {
          streak: numberOrNull(topBoard.streak),
          stocks: list(topBoard.stocks)
            .map(parseStockReading)
            .filter((row): row is IntradayStockReading => row !== null),
        }
      : null,
    stocks: list(block.stocks)
      .map(parseStockReading)
      .filter((row): row is IntradayStockReading => row !== null),
  }
}

function parseSentiment(value: unknown): IntradaySentimentBlock | null {
  const block = record(value)
  if (!block) return null
  const oneToTwo = record(block.one_to_two)
  return {
    available: requiredBoolean(block.available),
    reason: textOrNull(block.reason),
    limit_up: numberOrNull(block.limit_up),
    broken: numberOrNull(block.broken),
    limit_down: numberOrNull(block.limit_down),
    max_streak: numberOrNull(block.max_streak),
    seal_rate_pct: numberOrNull(block.seal_rate_pct),
    broken_rate_pct: numberOrNull(block.broken_rate_pct),
    one_to_two: oneToTwo
      ? {
          available: requiredBoolean(oneToTwo.available),
          yesterday_first_board: numberOrNull(oneToTwo.yesterday_first_board),
          today_second_board: numberOrNull(oneToTwo.today_second_board),
          promoted: numberOrNull(oneToTwo.promoted),
          rate_pct: numberOrNull(oneToTwo.rate_pct),
          reason: textOrNull(oneToTwo.reason),
        }
      : null,
  }
}

function parseSnapshot(value: unknown): IntradaySnapshot | null {
  const block = record(value)
  if (!block) return null
  const source = record(block.source) ?? {}
  return {
    date: text(block.date),
    slot: text(block.slot),
    captured_at: textOrNull(block.captured_at),
    price_field: textOrNull(block.price_field),
    source: {
      dataset: textOrNull(source.dataset),
      tool: textOrNull(source.tool),
      source_detail: textOrNull(source.source_detail),
      trail: list(source.trail).map(text).filter((item) => item !== ''),
    },
    yesterday_limit_up: parseYesterdayLimitUp(block.yesterday_limit_up),
    sentiment: parseSentiment(block.sentiment),
    extra: Object.fromEntries(Object.entries(block).filter(([key]) => !SNAPSHOT_KNOWN_KEYS.includes(key))),
  }
}

export function parseIntradaySnapshot(raw: unknown): IntradaySnapshotPayload {
  const payload = payloadOf(raw, '盘中快照')
  const available = requiredBoolean(payload.available)
  return {
    date: text(payload.date),
    slot: text(payload.slot),
    available,
    reason: textOrNull(payload.reason),
    data_source_status: parseStatus(payload.data_source_status, available ? 'ok' : 'missing'),
    snapshot: available ? parseSnapshot(payload.snapshot) : null,
  }
}

// ---------------------------------------------------------------- 传输

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
      throw new ApiError('parse', `${path} 返回的内容不是合法 JSON`)
    }
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (signal?.aborted) throw new ApiError('timeout', `请求已取消：${path}`)
      throw new ApiError('timeout', `本地接口 ${path} 超时（${READ_TIMEOUT_MS}ms）`)
    }
    throw new ApiError('not-connected', `本地接口 ${path} 未接入：需先启动 ldst run`)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abortFromCaller)
  }
}

export const httpIntradaySource: IntradaySource = {
  summary: async (query, signal) => {
    const params = new URLSearchParams({ date: query.date })
    const path = `${INTRADAY_SUMMARY_PATH}?${params.toString()}`
    return parseIntradaySummary(await request<unknown>(path, signal))
  },
  snapshot: async (query, signal) => {
    const params = new URLSearchParams({ date: query.date, slot: query.slot })
    const path = `${INTRADAY_SNAPSHOT_PATH}?${params.toString()}`
    return parseIntradaySnapshot(await request<unknown>(path, signal))
  },
}

/** 端点尚未上线（404）：按「数据源暂缺」处理，不是整屏报错。 */
export function isIntradayEndpointMissing(error: unknown): boolean {
  return isEndpointMissing(error)
}

/** 本机服务没起 → 离线态。 */
export function isIntradayOffline(error: unknown): boolean {
  return isApiError(error) && error.kind === 'not-connected'
}
