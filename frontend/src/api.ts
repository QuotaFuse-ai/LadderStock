/**
 * 本地 API 客户端。
 *
 * 两条互相分层的面（DEC-014）：
 * - 数据面 `/api/review|sentiment|ladder|watch/snapshot|validation|health|state/config`：
 *   只读 GET，响应一律 `LadderEnvelope`。
 * - `/api/journal/stats`（DEC-015 Tier 1）：本机交易日志的**聚合数字**，只读 GET，
 *   回环独占，扁平 JSON（**不是** envelope），无 `note` / 明细 / 标的清单。
 * - 控制面 `/api/control/*`：读写都有，响应是普通 JSON（不是 envelope），
 *   仅回环可用；`LDST_CONTROL_ENABLED=0` 时整族 404。
 *
 * 未接入时抛 `ApiError`，界面显示「未接入 / 离线」，**不编造数据**。
 */

export const API_BASE = '/api'
const DEFAULT_TIMEOUT_MS = 5000
const CONTROL_TIMEOUT_MS = 15000
/** 控制面写请求的来源证据（QUOT-178）：服务端要求 Origin 同源或这个显式头 */
const CONTROL_CLIENT_HEADER = 'X-LDST-Client'

// ---------------------------------------------------------------- 错误

export type ApiErrorKind = 'not-connected' | 'http' | 'parse' | 'timeout'

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number | null
  /** 服务端 `detail` 原文（FastAPI 的错误体），用于「先结论后原因」的文案 */
  readonly detail: string | null

  constructor(kind: ApiErrorKind, message: string, status: number | null = null, detail: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.status = status
    this.detail = detail
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

/**
 * 端点尚未上线（后端 404）：`/api/market/*` 在 Q172-8 落地前就是这种状态。
 * 语义是「这一段暂时没有数据源」，不是「请求失败」，页面不该整屏报错。
 */
export function isEndpointMissing(error: unknown): boolean {
  return isApiError(error) && error.kind === 'http' && error.status === 404
}

/** 把任意异常整理成「一句话结论 + 原因」，供 ErrorBlock 使用 */
export function describeApiError(error: unknown): { title: string; detail: string; code: string | null } {
  if (isApiError(error)) {
    if (error.kind === 'not-connected') {
      return {
        title: '本地服务未连接',
        detail: '先运行 `ldst run` 启动本机只读 API（默认 127.0.0.1:5950），再点重试。',
        code: null,
      }
    }
    if (error.kind === 'timeout') {
      return { title: '取数超时', detail: error.message, code: 'TIMEOUT' }
    }
    if (error.kind === 'parse') {
      return { title: '响应解析失败', detail: error.message, code: 'E_PARSE' }
    }
    return {
      title: '取数失败',
      detail: error.detail ?? error.message,
      code: error.status ? `HTTP ${error.status}` : null,
    }
  }
  return {
    title: '取数失败',
    detail: error instanceof Error ? error.message : String(error),
    code: null,
  }
}

// ---------------------------------------------------------------- 传输

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT'
  body?: unknown
  timeoutMs?: number
  signal?: AbortSignal
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, timeoutMs = DEFAULT_TIMEOUT_MS, signal } = options
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  const abortFromCaller = () => controller.abort()
  signal?.addEventListener('abort', abortFromCaller)
  const isControlWrite = path.startsWith('/control/') && method !== 'GET'
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method,
      signal: controller.signal,
      headers: {
        accept: 'application/json',
        ...(body === undefined ? {} : { 'content-type': 'application/json' }),
        // 跨站 no-cors 简单请求带不上自定义头（会触发预检，服务端无 CORS 放行）
        ...(isControlWrite ? { [CONTROL_CLIENT_HEADER]: '1' } : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
    if (!response.ok) {
      // FastAPI 错误体统一是 {"detail": "..."}；控制面的 400/409 靠它给出可读原因
      let detail: string | null = null
      try {
        const parsed = (await response.json()) as { detail?: unknown }
        detail = typeof parsed.detail === 'string' ? parsed.detail : null
      } catch {
        detail = null
      }
      throw new ApiError(
        'http',
        `本地接口 ${path} 返回 HTTP ${response.status}${detail ? `：${detail}` : ''}`,
        response.status,
        detail,
      )
    }
    try {
      return (await response.json()) as T
    } catch {
      throw new ApiError('parse', `本地接口 ${path} 返回的内容不是合法 JSON`)
    }
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (signal?.aborted) throw new ApiError('timeout', `请求已取消：${path}`)
      throw new ApiError('timeout', `本地接口 ${path} 超时（${timeoutMs}ms）`)
    }
    throw new ApiError(
      'not-connected',
      `本地接口 ${path} 未接入：需先 ` + '`ldst run`' + ' 启动只读 API',
    )
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abortFromCaller)
  }
}

// ---------------------------------------------------------------- 数据面契约

export type EnvelopeFreshness = 'realtime' | 'cached' | 'archive'
export type EnvelopeConfidence = 'high' | 'medium' | 'low'
export type EnvelopeSourceStatus = 'ok' | 'degraded' | 'partial'

/** 与 `core/models.py:LadderEnvelope` 字段一一对应。 */
export interface LadderEnvelope<T> {
  domain: 'review' | 'sentiment' | 'validation' | 'trade_log' | 'market'
  topic: string
  event_id: string | null
  data_as_of: string | null
  payload: T & { data_source_status?: 'ok' | 'missing' }
  source: 'ladderstock'
  source_detail: string
  fetched_at: string
  freshness: EnvelopeFreshness
  confidence: EnvelopeConfidence
  source_status: EnvelopeSourceStatus
}

export interface SentimentResult {
  date: string
  available: boolean
  reason: string | null
  temperature: number | null
  promotion_rate: number | null
  seal_rate: number | null
  limit_up_count: number
  broken_count: number
  yesterday_limit_up_count: number
  promoted_count: number
  max_board: number | null
  gap: boolean
  gap_levels: number[]
  missing: string[]
  formula?: string
  source_detail?: string
}

export interface LadderStock {
  code: string
  name: string
  boards: number
  first_seal_time: string | number | null
  seal_time: string | number | null
}

export interface LadderLevel {
  boards: number
  count: number
  stocks: LadderStock[]
}

export interface LadderResult {
  date: string
  available: boolean
  reason: string | null
  source_detail: string
  height: number | null
  count: number
  ladder: LadderLevel[]
  gap: boolean
  gap_levels: number[]
  leader_candidates: LadderStock[]
  unknown_board: number
}

/**
 * 后端 `ai/error_taxonomy.py:FailureKind` 的镜像：四类面向用户的失败 + `internal` 兜底。
 * 前端不重新分类，只按这个 kind 决定呈现与可执行操作。
 */
export type AiFailureKind = 'network' | 'quota' | 'format' | 'truncated' | 'internal'

export const AI_FAILURE_KINDS: readonly AiFailureKind[] = [
  'network',
  'quota',
  'format',
  'truncated',
  'internal',
]

export function isAiFailureKind(value: unknown): value is AiFailureKind {
  return typeof value === 'string' && (AI_FAILURE_KINDS as readonly string[]).includes(value)
}

/** `review.ai_failures[]`（`ai/orchestrator.py:_result` 产出，字段一一对应）。 */
export interface ReviewAiFailure {
  section: string
  kind: AiFailureKind
  /** 后端已脱敏：类别标题 + 原因，如「网络请求失败：连接超时（10s）」 */
  message: string
  /** 后端给出的可行动建议（前端的按钮没接上时用作文字兜底） */
  action: string
  retryable: boolean
}

export interface ReviewSectionSummary {
  name: string
  title: string
  status: string
  mode: string | null
  model: string | null
  degraded: boolean
  narrative: string[]
  notes: string[]
  metrics: Record<string, unknown>
}

/** `/api/review` 的 `review` 正文；保留索引签名，未知字段不因类型收窄而丢。 */
export interface ReviewReport extends Record<string, unknown> {
  sections?: ReviewSectionSummary[]
  ai_failures?: ReviewAiFailure[]
}

export interface ReviewPayload {
  date: string
  review: ReviewReport | null
}

// ---------------------------------------------------------------- R11 周报契约

/** 周度趋势（`weekly/facts.py:_trend()`）：首值、末值、变化、均值、样本数。 */
export interface WeeklyTrend {
  first: number | null
  last: number | null
  change: number | null
  avg: number | null
  samples: number
}

export interface WeeklySentimentDay {
  date: string
  available: boolean
  temperature: number | null
  promotion_rate: number | null
  seal_rate: number | null
  limit_up_count: number | null
  broken_count: number | null
  max_board: number | null
  gap_levels: number[]
  reason: string | null
  /** `artifact`（当日复盘产物）/ `recomputed`（同一批函数补算） */
  source: string | null
}

export interface WeeklyLadderDay {
  date: string
  available: boolean
  height: number | null
  count: number | null
  gap: boolean
  gap_levels: number[]
  unknown_board: number | null
  reason: string | null
}

export interface WeeklyBreadthBand {
  band: string
  count: number
  share_pct: number | null
}

export interface WeeklyThemeRow {
  name: string
  days_on_top: number
  streak_days: number
  first_date: string
  last_date: string
  best_change_pct: number | null
  avg_change_pct: number | null
  days: string[]
}

export interface WeeklyValidationItem {
  generated_date: string
  id: string | null
  text: string | null
  metric: string | null
  operator: string | null
  target: unknown
  actual: number | null
  settled: boolean
  passed: boolean | null
  next_session: string | null
}

export interface WeeklyLeader {
  code: string
  name: string | null
  boards: number | null
  date: string
}

export interface WeeklyMissingDay {
  date: string
  reason: string
  has_report: boolean
}

/** 周度事实包：纯程序聚合，页面拿它算不出任何新数字，只展示。 */
export interface WeeklyFacts extends Record<string, unknown> {
  week?: string
  range?: { start?: string; end?: string }
  session_days?: string[]
  missing_days?: WeeklyMissingDay[]
  available?: boolean
  reason?: string | null
  sentiment?: {
    series?: WeeklySentimentDay[]
    temperature?: WeeklyTrend
    promotion_rate?: WeeklyTrend
    seal_rate?: WeeklyTrend
    limit_up_total?: number | null
    broken_total?: number | null
    gap_days?: number
  }
  ladder?: {
    series?: WeeklyLadderDay[]
    height?: WeeklyTrend
    peak_height?: number | null
    count_total?: number | null
    gap_days?: number
    leaders?: WeeklyLeader[]
  }
  breadth?: {
    available?: boolean
    reason?: string | null
    distribution?: WeeklyBreadthBand[]
    up_count?: number
    down_count?: number
    flat_count?: number
    samples?: number
    up_share_pct?: number | null
  }
  themes?: {
    available?: boolean
    reason?: string | null
    window?: number
    observed?: number
    themes?: WeeklyThemeRow[]
  }
  validation?: {
    available?: boolean
    reason?: string | null
    conditions?: number
    settled?: number
    passed?: number
    failed?: number
    unknown?: number
    pending?: number
    pass_rate?: number | null
    items?: WeeklyValidationItem[]
  }
  ai_coverage?: {
    days_with_checkpoint?: number
    sections_expected?: number
    sections_done?: number
    sections_degraded?: number
    sections_failed?: number
    sections_pending?: number
  }
  caliber?: Record<string, string>
  source_detail?: string
}

/**
 * `/api/weekly` 的 `weekly` 正文（`weekly/pipeline.py:build_report`）。
 * `sections` 与每日复盘同形（`ReviewSectionSummary`）：AI 只写 `narrative`，指标在 `metrics`。
 */
export interface WeeklyReport extends Record<string, unknown> {
  week?: string
  range?: { start?: string; end?: string }
  headline?: string[]
  available?: boolean
  reason?: string | null
  ai?: { model?: string | null; degraded?: boolean }
  sections?: ReviewSectionSummary[]
  ai_failures?: ReviewAiFailure[]
  facts?: WeeklyFacts
}

export interface WeeklyPayload {
  week: string
  weekly: WeeklyReport | null
}

/**
 * 解析 `ai_failures`：只认结构完整的条目，缺 `section` 的丢弃，不猜、不补。
 * `kind` 不在后端枚举内时归 `internal`（与后端兜底语义一致）。
 */
export function parseAiFailures(value: unknown): ReviewAiFailure[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (!item || typeof item !== 'object') return []
    const record = item as Record<string, unknown>
    const section = typeof record.section === 'string' ? record.section.trim() : ''
    if (!section) return []
    return [
      {
        section,
        kind: isAiFailureKind(record.kind) ? record.kind : 'internal',
        message:
          typeof record.message === 'string' && record.message.trim()
            ? record.message
            : 'AI 生成失败',
        action: typeof record.action === 'string' ? record.action : '',
        retryable: record.retryable === true,
      },
    ]
  })
}

export interface WatchSnapshotRow {
  code: string
  /** 状态文件里没有名称来源，后端恒给 `null`（server.py:_watch_rows），前端不得当作字符串 */
  name: string | null
  limit_up_count: number | null
  change_pct: number | null
  status: string | null
}

export interface WatchSnapshotPayload {
  trade_date: string | null
  rows: WatchSnapshotRow[]
}

export interface ValidationCondition {
  /** 盘上条件不一定带标的代码：`server.py:_validation_conditions` 对缺失值恒给 `null` */
  code: string | null
  /** 同上：条件文案缺失时后端给 `null`，前端不得当作字符串拼接 */
  trigger: string | null
  note?: string | null
}

/** `PUT /api/control/validation/conditions` 的请求条目（QUOT-197） */
export interface ValidationConditionWrite {
  code?: string | null
  /** 必填且非空，最长 200 字（服务端校验，空串直接 400） */
  trigger: string
  note?: string | null
}

/** 写入响应：服务端回显**落盘后**的完整条件列表（`saved` = 条数） */
export interface ValidationConditionsSaved {
  trade_date: string
  conditions: Array<{ id?: string; code?: string | null; trigger?: string | null; note?: string | null }>
  updated_at: string
  saved: number
}

export interface ValidationReconcile {
  status?: string
  next_session?: string | null
  [key: string]: unknown
}

export interface ValidationPayload {
  trade_date: string
  conditions: ValidationCondition[]
  reconcile: ValidationReconcile | null
  /** `missing` = 这一天还没有 validation 缓存（不是错误，是空态） */
  data_source_status?: 'ok' | 'missing'
}

export interface StateConfigPayload {
  ok: boolean
  read_only: boolean
  config_dir: string
  data_dir: string
  files: Array<{ file: string; present: boolean }>
  state_index_present: boolean
  state_schema_version: number | null
  ports: { web: number; mcp: number }
}

export interface HealthPayload {
  ok: boolean
  name: string
  version: string
  time: string
  data_dir: string
  read_only: boolean
}

// ---------------------------------------------------------------- 市场聚合契约（QUOT-198）
//
// `/api/market/history` 与 `/api/market/theme` 属后端缺口（Q172-8），**当前不存在**：
// 未上线时返回 404，前端把 404 视为「该段暂未接入」而不是页面故障（见 `isEndpointMissing`）。
// 字段是前端按「市场」屏需要的形状定义的契约草案，后端上线时以本文件为准对齐。

/** 近 N 日情绪温度序列（CH-01 折线）；单日可用但历史不可用时 `points` 为空。 */
export interface MarketHistoryPoint {
  date: string
  temperature: number | null
}

export interface MarketHistoryPayload {
  date: string
  available: boolean
  reason: string | null
  /** 按日期升序；`temperature` 为 `null` 表示该日没算出来（画面上断点，不补 0） */
  points: MarketHistoryPoint[]
  /** 请求的窗口天数，用于文案「近 N 日」 */
  window: number
}

/** 题材强度行（CH-03 横向条）。 */
export interface MarketThemeRow {
  theme: string
  /** 强度分（0–100），排序与画布长度都用它 */
  strength: number
  limit_up_count: number | null
  leader: string | null
  /** 较前一日的强度变化（正负均可） */
  delta: number | null
}

export interface MarketThemePayload {
  date: string
  available: boolean
  reason: string | null
  /** 后端按 strength 降序给出；前端仍会再排一次，避免依赖后端顺序 */
  themes: MarketThemeRow[]
}

// ---------------------------------------------------------------- 日志聚合契约（DEC-015 Tier 1）
//
// `GET /api/journal/stats` 是本机私有库的**唯一** HTTP 出口，扁平 JSON、回环独占。
// 服务端只回聚合数字：没有 `symbols`（标的清单）、没有逐笔明细、没有 `note`。
// 无样本的指标（胜率 / 平均盈亏 / 盈亏比）一律 `null`，前端展示「样本不足」。

export interface JournalDeviationReview {
  /** 偏离计划阈值（百分比，1.0 = 1%） */
  threshold_pct: number
  /** 偏离计划的成交笔数（只回次数，不回明细） */
  count: number
}

export interface JournalStatsPayload {
  local_only: true
  store: 'local_sqlite'
  data_source_status: 'ok' | 'missing'
  range: { from: string | null; to: string | null }
  total_trades: number
  buy_count: number
  sell_count: number
  closed_rounds: number
  wins: number
  losses: number
  flats: number
  win_rate: number | null
  avg_win: number | null
  avg_loss: number | null
  profit_loss_ratio: number | null
  realized_pnl: number
  unmatched_sell_qty: number
  deviation_review: JournalDeviationReview
}

export interface JournalStatsQuery {
  /** 起始日 `YYYY-MM-DD`（含），不传 = 不限 */
  from?: string
  /** 结束日 `YYYY-MM-DD`（含），不传 = 不限 */
  to?: string
  /** 偏离计划阈值 `0 ≤ x ≤ 1`，默认 0.01 */
  deviation?: number
}

// ---------------------------------------------------------------- 控制面契约
//
// 全部字段与 `web/control.py` 的 `snapshot()` / `board()` / `Job.view()` 一一对应。
// 状态**只**来自后端的 `status` / `status_label`，前端不自己推断。

export type PeerStatus =
  | 'unconfigured'
  | 'not_installed'
  | 'installed'
  | 'starting'
  | 'running'
  | 'stopped'
  | 'failed'

export interface PortOwner {
  pid: number | null
  process: string | null
}

export interface PortConflict {
  port: number
  pid: number | null
  process: string | null
  note: string
  foreign?: boolean
}

export interface JobView {
  id: string
  peer: string
  action: 'install' | 'start' | 'stop' | string
  status: 'queued' | 'running' | 'ok' | 'failed'
  step: string
  progress: number
  started_at: string | null
  ended_at: string | null
  log_tail: string[]
  error: string | null
}

export interface PeerSnapshot {
  id: string
  product: string
  role: string
  directory: string
  directory_exists: boolean
  registered: boolean
  status: PeerStatus
  status_label: string
  detail: string
  installed: boolean
  version: string | null
  venv: string | null
  ports: { mcp: number; web: number | null; web_label: string }
  listening: { mcp: PortOwner[]; web: PortOwner[] }
  health: { ok: boolean | null; detail: string }
  dist: { wheel: string | null; editable: boolean; env_file: string }
  port_conflicts: PortConflict[]
  last_job: JobView | null
  open_url: string | null
  actions: { can_install: boolean; can_start: boolean; can_stop: boolean; can_open: boolean }
}

export interface BoardPayload {
  ok: boolean
  control_enabled: boolean
  peers: PeerSnapshot[]
  port_conflicts: Array<PortConflict & { peer: string }>
  ports: { board: number; mcp: number } & Record<string, { mcp: number; web: number | null } | number>
}

export interface ConfigEntry {
  set: boolean
  secret: boolean
  preview?: string
  value?: string | null
}

export interface PeerConfig {
  peer: string
  env_file: string
  exists: boolean
  keys: string[]
  values: Record<string, ConfigEntry>
  notes: string[]
  updated?: string[]
  cleared?: string[]
}

export interface AiTestResult {
  ok: boolean
  latency_ms: number
  model_seen: string | null
  error: string | null
}

export interface PeerLogs {
  peer: string
  path: string
  exists: boolean
  lines: string[]
}

export interface JobHandle {
  job_id: string
  status: string
}

// ---------------------------------------------------------------- 端点

export const api = {
  // 数据面
  review: (date: string, signal?: AbortSignal) =>
    request<LadderEnvelope<ReviewPayload>>(`/review?date=${encodeURIComponent(date)}`, { signal }),
  /** R11 周报：只读本机 `weekly/<week>.json`，非法 week → 400，无产物 → 200 + missing */
  weekly: (week: string, signal?: AbortSignal) =>
    request<LadderEnvelope<WeeklyPayload>>(`/weekly?week=${encodeURIComponent(week)}`, { signal }),
  sentiment: (date: string, signal?: AbortSignal) =>
    request<LadderEnvelope<{ sentiment: SentimentResult }>>(
      `/sentiment?date=${encodeURIComponent(date)}`,
      { signal },
    ),
  ladder: (date: string, signal?: AbortSignal) =>
    request<LadderEnvelope<{ ladder: LadderResult }>>(`/ladder?date=${encodeURIComponent(date)}`, {
      signal,
    }),
  watchSnapshot: (signal?: AbortSignal) =>
    request<LadderEnvelope<WatchSnapshotPayload>>('/watch/snapshot', { signal }),
  /**
   * 近 N 日情绪温度序列（Q172-8 缺口）。后端未上线时抛 `http` 404，
   * 调用方用 `isEndpointMissing` 判成「未接入」，不当作页面错误。
   */
  marketHistory: (date: string, window = 20, signal?: AbortSignal) =>
    request<LadderEnvelope<MarketHistoryPayload>>(
      `/market/history?date=${encodeURIComponent(date)}&window=${window}`,
      { signal },
    ),
  /** 题材轮动（Q172-8 缺口，来源 QuotaFuse Wire `theme_news`）；404 语义同上。 */
  marketTheme: (date: string, signal?: AbortSignal) =>
    request<LadderEnvelope<MarketThemePayload>>(`/market/theme?date=${encodeURIComponent(date)}`, {
      signal,
    }),
  validation: (date: string, signal?: AbortSignal) =>
    request<LadderEnvelope<ValidationPayload>>(`/validation?date=${encodeURIComponent(date)}`, {
      signal,
    }),
  health: (signal?: AbortSignal) => request<HealthPayload>('/health', { signal }),
  stateConfig: (signal?: AbortSignal) => request<StateConfigPayload>('/state/config', { signal }),
  /** 本机交易日志聚合统计（DEC-015 Tier 1）：扁平 JSON，**不是** `LadderEnvelope` */
  journalStats: (query: JournalStatsQuery = {}, signal?: AbortSignal) => {
    const params = new URLSearchParams()
    if (query.from) params.set('from', query.from)
    if (query.to) params.set('to', query.to)
    if (query.deviation !== undefined) params.set('deviation', String(query.deviation))
    const search = params.toString()
    return request<JournalStatsPayload>(`/journal/stats${search ? `?${search}` : ''}`, { signal })
  },

  // 控制面
  control: {
    board: (signal?: AbortSignal) =>
      request<BoardPayload>('/control/peers', { signal, timeoutMs: CONTROL_TIMEOUT_MS }),
    peer: (id: string, signal?: AbortSignal) =>
      request<PeerSnapshot>(`/control/peers/${encodeURIComponent(id)}`, {
        signal,
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
    install: (id: string) =>
      request<JobHandle>(`/control/peers/${encodeURIComponent(id)}/install`, {
        method: 'POST',
        body: {},
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
    start: (id: string) =>
      request<JobHandle>(`/control/peers/${encodeURIComponent(id)}/start`, {
        method: 'POST',
        body: {},
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
    stop: (id: string) =>
      request<JobHandle>(`/control/peers/${encodeURIComponent(id)}/stop`, {
        method: 'POST',
        body: {},
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
    logs: (id: string, tail = 200, signal?: AbortSignal) =>
      request<PeerLogs>(`/control/peers/${encodeURIComponent(id)}/logs?tail=${tail}`, { signal }),
    config: (id: string, signal?: AbortSignal) =>
      request<PeerConfig>(`/control/peers/${encodeURIComponent(id)}/config`, { signal }),
    saveConfig: (id: string, values: Record<string, string>, clear: string[] = []) =>
      request<PeerConfig>(`/control/peers/${encodeURIComponent(id)}/config`, {
        method: 'PUT',
        body: { values, clear },
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
    aiTest: (body: { peer: string; base_url: string; model: string; api_key?: string }) =>
      request<AiTestResult>('/control/ai/test', {
        method: 'POST',
        body,
        timeoutMs: 20000,
      }),
    /** 生成五段复盘（后台任务，轮询 `control.job` 直到 ok / failed）。 */
    generateAi: (body: { date: string; force?: boolean }) =>
      request<JobHandle>('/control/ai/generate', {
        method: 'POST',
        body,
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
    /** 只重试失败 / 未完成段落；传 `section` 限定单段（QUOT-236）。 */
    retryAi: (body: { date: string; section?: string }) =>
      request<JobHandle>('/control/ai/retry', {
        method: 'POST',
        body,
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
    /**
     * 保存「明日验证条件」到本机 validation 缓存（QUOT-197）。
     * `merge` 按 `code+trigger` 去重追加；`replace` 整段覆盖（只改 `conditions`，
     * 文件里已有的 `reconcile` 原样保留，写前备份 `.bak`）。
     */
    writeValidationConditions: (body: {
      trade_date: string
      conditions: ValidationConditionWrite[]
      mode: 'merge' | 'replace'
    }) =>
      request<ValidationConditionsSaved>('/control/validation/conditions', {
        method: 'PUT',
        body,
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
    /**
     * 探测「后端写通道」是否就绪，**不写入任何文件**。
     *
     * 故意用 GET 打同一个路径：路由已注册但只允许 PUT 时 FastAPI 回 405
     * （在路由层就被拦下，不进入 `_guard`，更不会写盘）→ 就绪；
     * 控制面整族关闭或端点未合入时回 404 → 未就绪。
     */
    validationWriteProbe: async (signal?: AbortSignal): Promise<{ ready: boolean; detail: string }> => {
      try {
        await request<unknown>('/control/validation/conditions', {
          signal,
          timeoutMs: CONTROL_TIMEOUT_MS,
        })
        return { ready: true, detail: '控制面已响应' }
      } catch (error) {
        if (isApiError(error) && error.kind === 'http') {
          if (error.status === 405) return { ready: true, detail: 'PUT 路由已注册（GET 返回 405）' }
          if (error.status === 404) {
            return { ready: false, detail: '控制面未启用或端点未合入（HTTP 404）' }
          }
        }
        throw error
      }
    },
    job: (jobId: string, signal?: AbortSignal) =>
      request<JobView>(`/control/jobs/${encodeURIComponent(jobId)}`, {
        signal,
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
  },
}
