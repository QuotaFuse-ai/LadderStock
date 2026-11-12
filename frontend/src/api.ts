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
  /** 控制面 `detail.code`（如 `SENATE_STALE`）；没有结构化错误体时为 null */
  readonly code: string | null

  constructor(
    kind: ApiErrorKind,
    message: string,
    status: number | null = null,
    detail: string | null = null,
    code: string | null = null,
  ) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.status = status
    this.detail = detail
    this.code = code
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

/** 把任意异常整理成「一句话结论 + 原因」，供 ErrorBlock 使用。
 *
 * 全部白话（QUOT-559 全局去 CLI）：用户可见面不出现命令行、可执行文件名与端口。
 */
export function describeApiError(error: unknown): { title: string; detail: string; code: string | null } {
  if (isApiError(error)) {
    if (error.kind === 'not-connected') {
      return {
        title: '本机服务未连接',
        detail: '看板的本机服务还没有就绪。点「重试」重新连接；若反复出现，用顶栏状态灯或「重启平台」把它拉起来，无需任何手动操作。',
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
    // 强制演示 ?state=error 时没有真实错误对象：String(undefined) 会把 "undefined" 印上屏
    detail: error == null ? '没有可用的错误详情；点「重试」重新取数。' : error instanceof Error ? error.message : String(error),
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
  // 日志写面（QUOT-561 / DEC-083）与控制面同款：服务端要同源 `Origin` 或这个显式头
  const isControlWrite =
    (path.startsWith('/control/') ||
      path.startsWith('/supervisor/') ||
      path.startsWith('/journal/')) &&
    method !== 'GET'
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
      // FastAPI 错误体统一是 {"detail": "..."}；控制面的 400/409 靠它给出可读原因。
      // Senate 桥接的错误体是 {"detail": {"code": "SENATE_*", "message": "..."}}，
      // 这里把 message 当 detail、code 单独留出来给页面做中文映射。
      let detail: string | null = null
      let code: string | null = null
      try {
        const parsed = (await response.json()) as { detail?: unknown }
        if (typeof parsed.detail === 'string') {
          detail = parsed.detail
        } else if (parsed.detail !== null && typeof parsed.detail === 'object') {
          const structured = parsed.detail as { code?: unknown; message?: unknown }
          if (typeof structured.message === 'string') detail = structured.message
          if (typeof structured.code === 'string') code = structured.code
        }
      } catch {
        detail = null
      }
      throw new ApiError(
        'http',
        `本地接口 ${path} 返回 HTTP ${response.status}${detail ? `：${detail}` : ''}`,
        response.status,
        detail,
        code,
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
    throw new ApiError('not-connected', '本机服务还没有就绪，暂时连不上；点「重试」或用顶栏「重启平台」把它拉起来。')
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

/** 标的字典的一条命中（QUOT-405，字段对齐参考库 `symbolSearch`）。 */
export interface SymbolMatch {
  symbol: string
  name: string
  market: string | null
  kind: string | null
  board: string | null
  /** pypinyin 不在依赖里时为 `null`（拼音匹配待后续单接入） */
  pinyin: string | null
}

export interface SymbolsSearchPayload {
  query: string
  limit: number
  count: number
  matches: SymbolMatch[]
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

/** 记一笔的写请求（QUOT-561 / DEC-083）：字段白名单，服务端多一个键就 400。 */
export interface JournalTradeInput {
  code: string
  side: 'buy' | 'sell'
  price: number
  qty: number
  note?: string
  /** `YYYY-MM-DD`；不传 = 今天 */
  trade_date?: string
  plan_price?: number
}

/** 写响应：扁平，只回这一笔（不含其它成交、不含任何聚合）。 */
export interface JournalTradeWritten {
  ok: true
  local_only: true
  store: 'local_sqlite'
  id: number
  trade_date: string
  code: string
  side: 'buy' | 'sell'
  price: number
  qty: number
  plan_price: number | null
  note: string
  created_at: string
}

/** 写面就绪探测的应答（`GET /api/journal/write-info`，只读、不建库）。 */
export interface JournalWriteInfo {
  ok: true
  local_only: true
  store: 'local_sqlite'
  writable: boolean
  write_path: string
  sides: Array<'buy' | 'sell'>
  note_max_len: number
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
  severity?: 'conflict' | 'info'
}

// ---------------------------------------------------------------- 端口诊断 + 安全自愈（QUOT-547 契约 / QUOT-548 消费）
// 字段与 `web/control.py::_diagnose_peer` / `heal_ports` 一一对应。
// 占用分类 / 健康 / 可修复性全部由后端判定（DEC-079 origin 口径），前端只翻译展示。

/** 占用四分类：自有残留 / 已认领 peer / 外来进程 / 空闲。 */
export type PortClass = 'own_child' | 'adopted_peer' | 'foreign' | 'free'

export interface PortDiagnosisEntry {
  port: number
  role: 'mcp' | 'web' | 'extra' | string
  class: PortClass
  pid: number | null
  process: string | null
  /** 实时命令行（后端已打码）；查不到为 null */
  cmdline: string[] | null
  listening: boolean
  /** 后端白话理由（技术口径原文，面板收进「技术细节」） */
  reason: string
}

export interface PeerPortDiagnosis {
  peer: string
  health: { ok: boolean | null; detail: string }
  ports: PortDiagnosisEntry[]
}

export interface PortsDiagnosisPayload {
  ok: boolean
  peers: PeerPortDiagnosis[]
}

/** 安全自愈结果：动作由后端按占用分类决定（adopt / restart / report / none）。 */
export interface PortHealResult {
  ok: boolean
  peer: string
  action: 'adopt' | 'restart' | 'report' | 'none'
  detail: string
  /** adopt / restart 后后端回读的最新诊断（前端直接用它刷新，不重算） */
  ports: PortDiagnosisEntry[]
  health: { ok: boolean | null; detail: string }
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
  actions: {
    can_install: boolean
    can_start: boolean
    can_stop: boolean
    can_open: boolean
    /** 有可写配置键（后端白名单非空）才允许打开配置抽屉 */
    can_config: boolean
  }
}

/** 工具组件（如 ProxyFleet）：只读观测，不参与 peer 的安装/启停任务。 */
export interface ComponentSnapshot {
  id: string
  kind: 'component'
  product: string
  role: string
  directory: string
  directory_exists: boolean
  status: PeerStatus
  status_label: string
  detail: string
  ports: { console: number; gateway: number | null }
  listening: { console: PortOwner[]; gateway: PortOwner[] }
  health: { ok: boolean | null; detail: string }
  open_url: string | null
  managed_by: string
  notes: string[]
  actions: { can_open: boolean }
}

export interface BoardPayload {
  ok: boolean
  control_enabled: boolean
  peers: PeerSnapshot[]
  /** `config/sources.yaml` 的 peers: 块原文（QUOT-420）：注册表唯一真源。 */
  registry?: RegistryEntry[]
  components?: ComponentSnapshot[]
  port_conflicts: Array<PortConflict & { peer: string }>
  ports: { board: number; mcp: number } & Record<string, { mcp: number; web: number | null } | number>
}

/** 注册表里的一条 peer（来自 yaml；控制面未纳管时也照样列出）。 */
export interface RegistryEntry {
  id: string
  product: string
  package: string
  default_url: string
  health_path: string
  default_port: number | null
  role: string
  repo: string | null
  installed: boolean
  status: PeerStatus
  status_label: string
}

/** 导入一条 peer 定义（QUOT-420）：只写 config/sources.yaml，不执行任何命令。 */
export interface PeerImportPayload {
  id: string
  product: string
  package: string
  default_url: string
  health_path: string
  default_port: number
  role: string
  repo: string
}

/** 页面一键命令（后端白名单；argv 不接受前端传入）。 */
export interface PageCommand {
  key: string
  route: string
  label: string
  argv: string[]
  running: boolean
  last_job: JobView | null
  /**
   * 成本/范围原文（QUOT-533 契约）：目前只有 `industry-sync` 带回。
   * 前端只渲染，不改写、不据此推导条数。
   */
  note?: string
}

export interface PageCommandsPayload {
  ok: boolean
  commands: PageCommand[]
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
  /** 后端判定是否可编辑（白名单非空 + env 父目录存在）；前端只按它渲染 */
  editable: boolean
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

/** 看板自己（每日复盘）的模型配置；密钥只回 `api_key_set`，不回明文。 */
export interface SelfAiBackend {
  id: string
  type: string
  base_url_env: string
  base_url: string | null
  base_url_source: 'process' | 'file' | null
  api_key_env: string
  api_key_set: boolean
  api_key_source: 'process' | 'file' | null
  models: string[]
  configured: boolean
}

export interface SelfAiStatus {
  configured: boolean
  default_model: string
  fallback_chain: string[]
  models: string[]
  backends: SelfAiBackend[]
  model_override: string | null
  env_file: string | null
  env_file_exists: boolean
  config_path: string | null
  sections: string[]
  missing_env: string[]
  hint: string
  writable_keys: string[]
  subscription_cli: boolean
  /** 订阅通道探测结果（QUOT-401）；老后端没有这个字段时前端按空数组处理 */
  cli_channels?: CliChannel[]
  /** 当前生效的订阅通道 id；`null` 表示走 HTTP 端点 */
  active_cli?: string | null
  updated?: string[]
  cleared?: string[]
}

/** 订阅通道的四态：只有 `ok` 能点「使用此通道」；其余给出具体原因，不灰不假装。 */
export type CliState = 'ok' | 'missing' | 'broken' | 'no_login'

export interface CliChannel {
  id: 'codex' | 'claude' | 'workbuddy' | string
  label: string
  blurb: string
  exe: string | null
  login_files: string[]
  version: string | null
  state: CliState
  detail: string
}

export interface CliChannelsPayload {
  ok: boolean
  channels: CliChannel[]
  active: string | null
  any_available: boolean
  env_file: string
}

/** CLI 探针结果：字段与 HTTP 探针同形，多带一条通道快照。 */
export interface CliTestResult extends AiTestResult {
  channel?: CliChannel
}

// ---------------------------------------------------------------- AI 按模块配置（QUOT-498 / QUOT-504）

/** 生效来源三档；词表以后端 `sources` 为准，这里只列已知值。 */
export type AiModelSource = 'role' | 'module' | 'global'

/** 角色行：value 是生效值（含继承），configured 表示本级真的写了覆盖。 */
export interface AiRoleStatus {
  value: string
  source: AiModelSource
  configured: boolean
  inherited_value: string | null
}

export interface AiModuleStatus extends AiRoleStatus {
  fallback_chain: string[]
  agent_id: string
  kb_scope: string
  roles: Record<string, AiRoleStatus>
}

/** `GET /control/ai/modules` 的真实响应形状（QUOT-503 交付后回读核对）。 */
export interface AiModulesPayload {
  modules: Record<string, AiModuleStatus>
  models: string[]
  sources: AiModelSource[]
  config_path: string
}

/** PUT 与 GET 同形，多带写盘结果两个键。 */
export interface AiModulesSavePayload extends AiModulesPayload {
  updated: string
  changed: boolean
}

/** PUT 请求体：只带要改的键；空串 / 空数组 = 清除本级覆盖、回退继承。 */
export interface AiModuleOverride {
  model?: string
  fallback_chain?: string[]
  memory?: { agent_id?: string; kb_scope?: string }
  roles?: Record<string, { model: string }>
}

/** `POST /control/ai/modules/test`：失败也是 200，按 `ok` 分色。 */
export interface AiModuleTestResult {
  ok: boolean
  module: string
  role: string | null
  model: string
  source: AiModelSource
  latency_ms: number
  reply_head: string | null
  error: string | null
}

// ---------------------------------------------------------------- StockSenate 辩论角色多模型（QUOT-518 / QUOT-520）

/** provider 清单逐项来自后端；`models` 是候选建议，前端不硬编码。 */
export interface SenateProvider {
  name: string
  label: string
  api_key_env: string | null
  base_url: string | null
  models: string[]
}

/** 16 个 canonical 角色之一；`api_key_env` 回显的是全局 `[llm].api_key_env`。 */
export interface SenateRoleStatus {
  role: string
  label: string
  configured: boolean
  provider: string | null
  model: string | null
  api_key_env: string | null
}

export interface SenateEngine {
  authority: string
  reload: string
  provider: string | null
  file_provider: string | null
  env_override: boolean | null
  config_file: string
  contract_source: string
  roles_authoritative?: boolean
  hint: string
  reload_hint: string
}

export interface SenateGlobal {
  provider: string | null
  deep_think_llm: string | null
  quick_think_llm: string | null
  backend_url_set: boolean | null
  api_key_env: string | null
}

/** `GET /control/peers/senate/role-models` 的真实响应形状（QUOT-518）。 */
export interface SenateRoleModelsPayload {
  engine: SenateEngine
  roles: SenateRoleStatus[]
  unconfigured_roles: string[]
  role_count: number
  global: SenateGlobal
  providers: SenateProvider[]
  aliases: Record<string, string>
  directory_exists?: boolean
  config_exists?: boolean
  env_file?: string
  env_keys?: string[]
  revision: string
  /** PUT 响应额外带回：本次写入的角色 / 别名归一 / 是否写了全局 key 变量。 */
  updated?: string[]
  aliases_rewritten?: Record<string, string>
  api_key_env_written?: boolean
}

/** PUT 请求体里的单个角色：只允许 provider / model / 变量名。 */
export interface SenateRoleEntry {
  provider: string
  model: string
  api_key_env?: string
}

/** `POST /control/peers/senate/role-models/test`：失败也是 200，按 `ok` 区分。 */
export interface SenateTestResult {
  ok: boolean
  skipped?: boolean
  role: string
  provider: string
  provider_source?: string
  model: string
  latency_ms: number
  error_code: string | null
  message: string
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

// ---------------------------------------------------------------- supervisor（QUOT-482 / QUOT-484）

/**
 * 账本里的进程来源（QUOT-482-A `supervisor/ledger.py::LedgerOrigin`）。
 * - `spawned`：本任 supervisor 生命周期内自己拉起；
 * - `adopted`：崩溃重启后对账确认仍存活、继续托管的前任进程；
 * - `spawned-via-task`：SYSTEM 计划任务垫片拉起（QUOT-485，账本 `task.origin`）。
 * `null` = 账本没有这条记录（观测态）。
 */
export type SupervisorOrigin = 'spawned' | 'adopted' | 'spawned-via-task' | null

/** 归属只来自后端账本（`engine._ownership`）：前端不扫端口、不推断。 */
export type SupervisorOwnership = 'managed' | 'external'

export type SelfHealStatus = 'ok' | 'healing' | 'gave_up'

export interface SelfHealInfo {
  attempts: number
  max: number
  status: SelfHealStatus
}

/** `GET /api/supervisor/state` 的每模块条目（482-A 归属视角 + 482-C 自愈视角）。 */
export interface SupervisorModule {
  module: string
  ports: number[]
  pid: number | null
  /** 对外三值（stopped 折叠为 ok） */
  health: 'ok' | 'restarting' | 'needs_human'
  /** 原始生命周期：starting / running / stopping / stopped / failed / restarting / needs_human / down / unknown */
  state: string
  restarts_5min: number
  restart_count: number
  stopping_by_user: boolean
  origin: SupervisorOrigin
  cmdline_hash: string | null
  last_exit_reason: string | null
  self_heal: SelfHealInfo
  /** 482-A 合并视图才有的字段；482-C 单独部署时缺省 */
  ownership?: SupervisorOwnership
  evidence?: string
  listening?: boolean
  /** 482-B 未落地的停止预览接口若给出命令行，就在这里；缺省表示后端未提供 */
  cmdline?: string | null
  /** 仅 proxypool 模块：端口映射清单（QUOT-490 / QUOT-506）；其余模块缺省 */
  mappings?: ProxyMapping[]
  /** QUOT-556：账本标记为外部认领的 peer（adopt-only，重启必须用户确认） */
  adopted_peer?: boolean
}

export interface SupervisorStatePayload {
  ok: boolean
  engine: {
    running: boolean
    window_seconds: number
    max_restarts: number
    backoff: number[]
  } | null
  modules: SupervisorModule[]
  order?: string[]
  /** false = 启停引擎未接线（482-B），页面据此只显示观测
   *  true = 引擎已注入，归属/自愈数据可用 */
  configured?: boolean
  supervisor_pid?: number | null
  /** QUOT-556：事件总线最新 seq（前端据此发现新事件再拉 /events）；未接线时缺省 */
  events_latest?: number
}

// ------------------------------------------------ peer 级健康事件（QUOT-556 / QUOT-557）

/** 端口占用者分类（S4 / 与 web/port_diagnostics.py 同一套取值）。 */
export type SupervisorConflictOwner = 'own_child' | 'adopted_peer' | 'foreign' | 'free'

export interface SupervisorPortConflict {
  port: number
  owner: SupervisorConflictOwner
  pid: number | null
}

export type SupervisorEventType = 'crash' | 'offline' | 'port_conflict' | 'needs_human' | 'recovered'

/**
 * `GET /api/supervisor/events?since=<seq>` 的一条事件（S5 弹窗订阅源）。
 * `reason` 是后端写好的白话（不打印 argv / 技术栈），前端可直接展示；
 * `conflicts` 只在 `port_conflict` 事件上非空（占用者分类，S4）。
 */
export interface SupervisorHealthEvent {
  seq: number
  ts: string
  module: string
  event: SupervisorEventType
  health: string
  state: string
  reason: string
  pid: number | null
  ports: number[]
  origin: SupervisorOrigin
  adopted_peer: boolean
  conflicts: SupervisorPortConflict[]
}

export interface SupervisorEventsPayload {
  ok: boolean
  /** false = 事件总线未接线（自愈未启用）：前端不弹窗、不报错，停止轮询 */
  configured: boolean
  latest: number
  events: SupervisorHealthEvent[]
}

/**
 * 一键确认重启请求（QUOT-556）：认领态 peer 必须 `confirm: true`——
 * 确认只授权「拉起替代实例」，绝不授权终止任何外部进程（引擎闸门兜底）。
 */
export interface SupervisorRestartRequest {
  modules: string[]
  confirm?: boolean
}

export interface SupervisorRestartPayload {
  ok: boolean
  modules: Record<string, { ok: boolean; error: string | null }>
}

/**
 * 停止执行请求：`confirm: true` 是显式确认意图，缺省时后端必须拒绝。
 * 482-B 落地前该端点不存在（404），前端如实报「未接入」，不假装已停。
 */
export interface SupervisorStopRequest {
  confirm: true
  modules: string[]
}

// ---------------------------------------------------------------- 代理池（QUOT-507）

/**
 * proxypool 模块载荷（QUOT-490 / QUOT-506 契约）：
 * 模块条目在 supervisor 通用字段之外多带 `mappings`；每条 mapping 自带 `state`。
 * `owner` 只由后端给出，前端不按端口 / 进程推断归属。
 */
export type ProxyMappingState = 'running' | 'stopped' | 'conflict' | 'needs_human'
export type ProxyMappingOwner = 'ai' | 'human'

export interface ProxyMapping {
  id: string
  /** 监听地址，后端原样给出（`127.0.0.1:8788` 或 `8788`）；前端只展示，不改端口 */
  listen: string | number
  upstream_host: string
  upstream_port: number
  enabled: boolean
  owner: ProxyMappingOwner
  /** 后端 `state` 直出；缺省按「已停止」呈现，不让行留空 */
  state?: ProxyMappingState
  /** 冲突端口（后端给出时优先；缺省才回落 `listen` 里的端口） */
  conflict_port?: number | null
  /** 最近一次探活 RTT（毫秒）；缺省显示「未探活」 */
  rtt_ms?: number | null
}

/** `pool probe` 的单条结果。 */
export interface ProxyProbeEntry {
  id?: string
  mapping_id?: string
  ok: boolean
  rtt_ms?: number | null
  error?: string | null
}

export interface ProxyProbeResponse {
  ok: boolean
  results?: ProxyProbeEntry[]
}

export interface ProxyPoolTarget {
  mapping_ids?: string[]
}

export interface ProxySetUpstreamRequest {
  mapping_id: string
  upstream_host: string
  upstream_port: number
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
  /**
   * 标的字典检索（QUOT-405）：本机缓存反查的代码 + 名称，只读。
   * 未接入（后端 404）或查无此代码时调用方按空结果回落，不编名称。
   */
  symbolsSearch: (q: string, limit = 10, signal?: AbortSignal) =>
    request<LadderEnvelope<SymbolsSearchPayload>>(
      `/symbols/search?q=${encodeURIComponent(q)}&limit=${limit}`,
      { signal },
    ),
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
  /**
   * 记一笔（QUOT-561 / DEC-083）：只写本机 SQLite，与本地记录用的是同一个写入函数；
   * 读接口仍只有上面的聚合统计，逐笔明细不因这次写入而开出去。
   */
  journalAddTrade: (body: JournalTradeInput) =>
    request<JournalTradeWritten>('/journal/trades', { method: 'POST', body, timeoutMs: CONTROL_TIMEOUT_MS }),
  /**
   * 日志写面是否就绪（QUOT-561）：只读探测，回不改库的固定形状；旧后端 404 时
   * 页面按「本机服务版本较旧」如实说明，不给假入口。
   */
  journalWriteInfo: (signal?: AbortSignal) =>
    request<JournalWriteInfo>(`/journal/write-info`, { signal }),

  // 控制面
  control: {
    board: (signal?: AbortSignal) =>
      request<BoardPayload>('/control/peers', { signal, timeoutMs: CONTROL_TIMEOUT_MS }),
    peer: (id: string, signal?: AbortSignal) =>
      request<PeerSnapshot>(`/control/peers/${encodeURIComponent(id)}`, {
        signal,
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
    importPeer: (body: PeerImportPayload) =>
      request<BoardPayload & { imported: string; backup: string }>('/control/peers/import', {
        method: 'POST',
        body,
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
    /** 端口诊断（QUOT-547）：逐模块端口占用四分类 + PID + health + 白话理由，只读。 */
    ports: (signal?: AbortSignal) =>
      request<PortsDiagnosisPayload>('/control/ports', { signal, timeoutMs: CONTROL_TIMEOUT_MS }),
    /**
     * 安全自愈（QUOT-547）：只重拉 own_child；adopted_peer 只认领 + 健康复检；
     * foreign 只报告 + 引导。动作选择在后端，前端不传动作参数、不执行命令。
     */
    healPorts: (peerId: string) =>
      request<PortHealResult>(`/control/ports/${encodeURIComponent(peerId)}/heal`, {
        method: 'POST',
        body: {},
        timeoutMs: 30000,
      }),
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
    /** 看板自己的模型配置（`config/ai.env`）；与上面的 peer 目标分开。 */
    selfAi: (signal?: AbortSignal) =>
      request<SelfAiStatus>('/control/self-ai', { signal, timeoutMs: CONTROL_TIMEOUT_MS }),
    saveSelfAi: (values: Record<string, string>, clear: string[] = []) =>
      request<SelfAiStatus>('/control/self-ai', {
        method: 'PUT',
        body: { values, clear },
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
    testSelfAi: (body: { base_url: string; model: string; api_key?: string }) =>
      request<AiTestResult>('/control/self-ai/test', {
        method: 'POST',
        body,
        timeoutMs: 20000,
      }),
    /** 订阅版 CLI 通道探测（QUOT-401）：装没装 / 跑得起来 / 登录态在不在。 */
    selfAiCli: (signal?: AbortSignal) =>
      request<CliChannelsPayload>('/control/self-ai/cli', {
        signal,
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
    /** 真跑一次本机 CLI 的探针；失败也是 200，按 `ok` 分色。 */
    testSelfAiCli: (cli: string) =>
      request<CliTestResult>('/control/self-ai/cli/test', {
        method: 'POST',
        body: { cli },
        timeoutMs: 120000,
      }),
    /** 按模块配置（QUOT-498）：模块/角色清单、生效值与来源、模型全集都从这里读。 */
    aiModules: (signal?: AbortSignal) =>
      request<AiModulesPayload>('/control/ai/modules', { signal, timeoutMs: CONTROL_TIMEOUT_MS }),
    /** 只写变更键；响应是回读后的全量状态，前端必须用它刷新而不是改本地 state。 */
    saveAiModule: (module: string, override: AiModuleOverride) =>
      request<AiModulesSavePayload>('/control/ai/modules', {
        method: 'PUT',
        body: { module, ...override },
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
    /** 用该模块/角色当前生效模型真跑一次；失败也是 200，按 `ok` 分色。 */
    testAiModule: (module: string, role?: string) =>
      request<AiModuleTestResult>('/control/ai/modules/test', {
        method: 'POST',
        body: { module, ...(role === undefined ? {} : { role }) },
        timeoutMs: 120000,
      }),
    /** StockSenate 辩论角色（QUOT-518）：16 角色、provider 清单与磁盘指纹。 */
    senateRoleModels: (signal?: AbortSignal) =>
      request<SenateRoleModelsPayload>('/control/peers/senate/role-models', {
        signal,
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
    /** 校验后原子写 `llm_config.toml`；`expected_revision` 不符回 `SENATE_STALE`。 */
    saveSenateRoleModels: (roles: Record<string, SenateRoleEntry>, expectedRevision: string | null) =>
      request<SenateRoleModelsPayload>('/control/peers/senate/role-models', {
        method: 'PUT',
        body: { roles, ...(expectedRevision ? { expected_revision: expectedRevision } : {}) },
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
    /** 对单个角色发一次最小 chat 探测；失败也是 200，按 `ok` / `error_code` 区分。 */
    testSenateRoleModel: (body: {
      role: string
      provider?: string
      model?: string
      api_key_env?: string
    }) =>
      request<SenateTestResult>('/control/peers/senate/role-models/test', {
        method: 'POST',
        body,
        timeoutMs: 120000,
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
    /** 页面一键命令清单（白名单 + 最近一次任务）。 */
    commands: (signal?: AbortSignal) =>
      request<PageCommandsPayload>('/control/commands', { signal, timeoutMs: CONTROL_TIMEOUT_MS }),
    runCommand: (key: string) =>
      request<JobHandle>(`/control/commands/${encodeURIComponent(key)}`, {
        method: 'POST',
        body: {},
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
  },

  // supervisor（QUOT-482-A 归属 + 482-C 自愈；QUOT-484 前端消费）
  supervisor: {
    /** 每模块归属（managed/external，证据来自账本）+ 自愈三态；仅回环可读。 */
    state: (signal?: AbortSignal) =>
      request<SupervisorStatePayload>('/supervisor/state', {
        signal,
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
    /**
     * peer 级健康事件轮询（QUOT-556，S5 弹窗订阅源）：`since` = 上次见过的最大
     * seq，返回其后事件（升序）与当前 `latest`。未接线时 `configured=false`。
     */
    events: (since = 0, signal?: AbortSignal) =>
      request<SupervisorEventsPayload>(`/supervisor/events?since=${Math.max(0, Math.floor(since))}`, {
        signal,
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
    /**
     * 一键确认重启（QUOT-556 / S5 弹窗主操作）：认领态 peer 必须显式 `confirm: true`。
     * 确认只授权拉起替代实例，绝不授权终止外部进程；逐模块结果在 `modules` 里。
     */
    restart: (body: SupervisorRestartRequest) =>
      request<SupervisorRestartPayload>('/supervisor/restart', {
        method: 'POST',
        body,
        timeoutMs: 30000,
      }),
    /**
     * 人工解除 `needs_human`（482-C 已有写面）：清重拉窗口、健康回 ok。
     * 这是「需人工处理」卡片上「重试」调用的后端动作。
     */
    reset: (module: string) =>
      request<{ ok: boolean; module: string }>(
        `/supervisor/state/${encodeURIComponent(module)}/reset`,
        { method: 'POST', body: {}, timeoutMs: CONTROL_TIMEOUT_MS },
      ),
    /**
     * 停止清单执行（QUOT-484 契约）：必须显式 `confirm: true` + 模块清单，
     * 不接受无参数全停。482-B 未落地时回 404，调用方按「未接入」如实提示。
     */
    stop: (body: SupervisorStopRequest) =>
      request<{ ok: boolean; stopped: string[]; skipped: string[] }>('/supervisor/stop', {
        method: 'POST',
        body,
        timeoutMs: 30000,
      }),
    /**
     * QUOT-507 代理池动作（QUOT-490 / QUOT-506 契约）。
     * 后端未落地时 404；调用方如实提示「接口未接入」，不把界面留在加载中。
     * `mapping_ids` 缺省 = 整池。`setUpstream` **不会**自动重启（契约明确）。
     */
    poolStart: (body: ProxyPoolTarget = {}) =>
      request<{ ok: boolean; started?: string[] }>('/supervisor/pool/start', {
        method: 'POST',
        body,
        timeoutMs: 30000,
      }),
    poolStop: (body: ProxyPoolTarget = {}) =>
      request<{ ok: boolean; stopped?: string[] }>('/supervisor/pool/stop', {
        method: 'POST',
        body,
        timeoutMs: 30000,
      }),
    poolProbe: (body: ProxyPoolTarget = {}) =>
      request<ProxyProbeResponse>('/supervisor/pool/probe', {
        method: 'POST',
        body,
        timeoutMs: 30000,
      }),
    poolSetUpstream: (body: ProxySetUpstreamRequest) =>
      request<{ ok: boolean; mapping_id?: string }>('/supervisor/pool/upstream', {
        method: 'PUT',
        body,
        timeoutMs: CONTROL_TIMEOUT_MS,
      }),
  },
}
