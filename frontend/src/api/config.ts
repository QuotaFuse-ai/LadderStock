/**
 * 运行配置的只读接口（配置可视化页用）。
 *
 * 只读是刻意的：页面上的值必须来自"进程能读到的那个文件 + 磁盘上真实的分区"，
 * 不允许出现"页面显示 85 只、实际跑的是 76 只"这种分叉。写操作仍走
 * `/api/schedule/toggle|run`，改触发时间与代码池只能改 yaml。
 */

const READ_TIMEOUT_MS = 8000
/** 采集进度要开一次 duckdb 做十几条统计，给宽一点；超时也能看其它分页 */
const PROGRESS_TIMEOUT_MS = 15000

async function getJSON<T>(path: string, signal?: AbortSignal, timeoutMs = READ_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  const onAbort = () => controller.abort()
  signal?.addEventListener('abort', onAbort)
  try {
    const response = await fetch(path, { signal: controller.signal, headers: { Accept: 'application/json' } })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    return (await response.json()) as T
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', onAbort)
  }
}

export interface SyncDatasetRow {
  name: string
  primary: string
  fallback: string[]
  mode: string
  codes: number
  codes_from: string | null
  /** 最近一个日分区的日期；从没落过就是 null，不拿今天顶替 */
  latest_day: string | null
  stale_days: number | null
  today_ready: boolean
}

export interface SyncConfigPayload {
  ok: boolean
  today: string
  datasets: SyncDatasetRow[]
  count: number
  covered: number
  stale: number
  base_pool: number
}

export interface BackfillPayload {
  ok: boolean
  source: string | null
  stage: string | null
  start: string | null
  end: string
  target_years: number | null
  throttle_ms: number | null
  gap_rate_threshold: number | null
  universe: number
  latest_report: { name: string; files: string[] } | null
}

export function fetchSyncConfig(signal?: AbortSignal): Promise<SyncConfigPayload> {
  return getJSON<SyncConfigPayload>('/api/config/sync', signal)
}

export function fetchBackfillConfig(signal?: AbortSignal): Promise<BackfillPayload> {
  return getJSON<BackfillPayload>('/api/config/backfill', signal)
}

/** 域状态：ok 全覆盖 / partial 没盖满 / never 一条没有 / blocked 被前置条件挡 /
 *  lost 采到没落库 / ondemand 本来就不每日全量 */
export type ProgressStatus = 'ok' | 'partial' | 'never' | 'blocked' | 'lost' | 'ondemand'

export interface ProgressDomain {
  key: string
  label: string
  source: string
  /** 已覆盖的实体数（只/条），或行数（target 为 0 时） */
  got: number
  unit: string
  /** 分母：代码池只数；0 表示这个域没有固定分母（当日榜单、资讯类） */
  target: number
  coverage: number | null
  rows: number | null
  latest: string | null
  lag_days: number | null
  status: ProgressStatus
  cadence: string
  detail: string
  /** 公告域专用：分母 = 池 ∪ 当日异动（池外），见后端 news/candidates.py */
  denominator?: number
  pool_size?: number
  extra_movers?: string[]
  extra_total?: number
  /** 当天异动票数（池内 + 池外）与池内数 */
  movers_total?: number
  movers_in_pool?: number
  /** 前 8 只异动票（code + 涨跌幅），本轮真正会请求的名单 */
  movers_top?: { code: string; pct: number | null }[]
  movers_day?: string | null
  movers_threshold?: number
  candidates_filtered?: boolean
  candidates_fallback?: string
  covered?: number | null
  /** 本轮真正请求的票数（只含异动票）；分母里其余票是按策略没请求 */
  requested?: number | null
  /** 请求了却没取回的票数 */
  requested_missing?: number | null
  strategy_skipped?: number | null
  candidates_note?: string
}

export interface ProgressRun {
  job_id: string
  status: string
  hint: string
  started_at: string
  finished_at: string | null
  age_hours: number | null
  detail: string
  /** 结构化「哪几个源不可用」（wire 采集落盘）；其他任务通常为空数组 */
  sources_unavailable?: { id: string; name?: string; status?: string; error?: string }[]
  /** 名单总数（列表只带前 12 条） */
  sources_unavailable_total?: number
}

export interface FeedCell {
  dataset: string
  status: string
  items: number | null
  detail: string
}

export interface FeedRun {
  at: string
  window: string
  fetched: number | null
  stored: number | null
  cells: FeedCell[]
}

export interface ProgressPayload {
  ok: boolean
  today: string
  as_of: string
  base_pool: number
  domains: ProgressDomain[]
  counts: { ok: number; partial: number; ondemand: number; broken: number }
  runs: ProgressRun[]
  matrix: FeedRun[]
  /** 查询层开不了时（有写任务占着库），这里带原因，页面照常显示其它区块 */
  duckdb_error: string | null
  note: string
}

export function fetchProgress(signal?: AbortSignal): Promise<ProgressPayload> {
  return getJSON<ProgressPayload>('/api/config/progress', signal, PROGRESS_TIMEOUT_MS)
}

export interface PoolChoice {
  /** null = 标的字典里的全部活跃票 */
  size: number | null
  label: string
  note: string
}

export interface PoolPayload {
  ok: boolean
  size: number
  updated_at: string | null
  file: string
  /** 有多少个数据集靠 *base_pool 共用这份池子 */
  anchor_users: number
  codes_preview: string[]
  universe: { symbols: number | null; active: number | null; as_of: string | null; error: string | null }
  choices: PoolChoice[]
  limits: { per_call: number; batch_per_call: number; max_batch: number; note: string }
  warning: string | null
  /** 只有 POST 返回里有 */
  applied?: boolean
  backup?: string
  codes_written?: number
  note?: string
}

export function fetchPool(signal?: AbortSignal): Promise<PoolPayload> {
  return getJSON<PoolPayload>('/api/config/pool', signal)
}

/** 只改 sync.yaml 的代码池，不触发任何采集 */
export async function applyPool(size: number | null, signal?: AbortSignal): Promise<PoolPayload> {
  return postPool({ mode: 'size', size }, signal)
}

/** 手填一批代码（6 位），同样只改配置 */
export async function applyPoolCodes(codes: string[], signal?: AbortSignal): Promise<PoolPayload> {
  return postPool({ mode: 'codes', codes }, signal)
}

async function postPool(body: Record<string, unknown>, signal?: AbortSignal): Promise<PoolPayload> {
  const response = await fetch('/api/config/pool', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `HTTP ${response.status}`)
  }
  return (await response.json()) as PoolPayload
}
