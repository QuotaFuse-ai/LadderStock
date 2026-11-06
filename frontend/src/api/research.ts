/**
 * 个股研究（R8）只读数据客户端。
 *
 * 六段产物是后端 R1/R3 的落盘契约：
 * `<data_dir>/research/<code>/<date>/<stage>.{json,md}` +
 * `<data_dir>/state/research_checkpoint/<code>/<date>.json`（SPEC-RESEARCH-PROVENANCE）。
 *
 * 浏览器读不到本机文件，所以页面只认下面三个只读接口（契约见
 * `docs/specs/SPEC-RESEARCH-PAGES.md` §2，与 MCP `research_status` 的载荷同形）：
 *
 *   GET  /api/research/{code}?date=YYYY-MM-DD   六段状态 + 各段产物（JSON + Markdown）
 *   GET  /api/research/{code}/overview          概览：K 线 + 快照 + 财务指标（QUOT-400）
 *   POST /api/research/{code}/run               { date, only } → { job_id, status }
 *   GET  /api/research/jobs/{job_id}            任务进度（1s 轮询，复用设置页模式）
 *
 * 404 有三种语义（QUOT-400，后端用 `detail` 文案区分，见 `classify404`）：
 * 路由没接（FastAPI 默认 Not Found）→「接口未接入」；「还没有任何研究记录」/
 * 「没有研究产物」→ 业务空态「这只还没跑过研究」；「未知证券代码」→ 代码不存在。
 * **不伪造数据**；`?demo=1` 的演示源是显式开关，且页面带水印。
 */

import { ApiError } from '../api'
import { parseKline, type KlineResult } from '../charts/klineView'

export const RESEARCH_STAGES = [
  'company',
  'financial',
  'forecast',
  'valuation',
  'risk',
  'report',
] as const

export type ResearchStageName = (typeof RESEARCH_STAGES)[number]

export const STAGE_TITLE: Record<ResearchStageName, string> = {
  company: '公司画像',
  financial: '财务',
  forecast: '盈利预期',
  valuation: '估值',
  risk: '风险',
  report: '报告合成',
}

export type StageStatus = 'ok' | 'degraded' | 'failed' | 'pending'

export const STAGE_STATUS_LABEL: Record<StageStatus, string> = {
  ok: '完成',
  degraded: '降级',
  failed: '失败',
  pending: '待跑',
}

export interface ProvenanceSource {
  id: string
  dataset: string
  params?: Record<string, unknown>
  retrieved_at?: string | null
  value?: number | string | null
  unit?: string | null
  endpoint?: string | null
  as_of?: string | null
  url?: string | null
  verified?: boolean
}

export interface ProvenanceSlot {
  values: Record<string, { sources: string[]; unverified: boolean }>
  sources: ProvenanceSource[]
  unverified: boolean
}

export interface StageFailure {
  kind?: string | null
  message?: string | null
  action?: string | null
  retryable?: boolean
  detail?: string | null
}

/** 各段结构化产物（`build_document()` 的键集合，见 research/artifacts.py）。 */
export interface StageDocument {
  code: string
  date: string
  stage: ResearchStageName
  title?: string
  status: StageStatus
  mode?: string | null
  model?: string | null
  degraded?: boolean
  narrative: string[]
  notes: string[]
  facts: Record<string, unknown>
  ai?: Record<string, unknown>
  provenance: ProvenanceSlot
  failure?: StageFailure | null
  attempts?: unknown[]
}

export interface StageArtifact {
  document: StageDocument
  markdown: string
}

export interface ResearchStage {
  name: ResearchStageName
  title: string
  status: StageStatus
  mode?: string | null
  degraded?: boolean
  artifact?: StageArtifact | null
}

export interface ResearchStatus {
  code: string
  date: string
  available: boolean
  complete: boolean
  checkpoint?: string | null
  updated_at?: string | null
  stages: ResearchStage[]
  pending: ResearchStageName[]
  failed: ResearchStageName[]
  source_status: 'ok' | 'partial' | 'degraded'
  reason?: string | null
  data_source_status?: string
}

export type ResearchJobStatus = 'queued' | 'running' | 'ok' | 'degraded' | 'failed'

export interface ResearchJob {
  id: string
  status: ResearchJobStatus
  stage?: ResearchStageName | null
  progress: number
  step?: string | null
  error?: string | null
}

export interface ResearchRunHandle {
  job_id: string
  status: ResearchJobStatus | string
}

/** 页面的取数通道：测试与演示可注入，真实通道只有 HTTP。 */
export interface ResearchSource {
  status: (code: string, date: string, signal?: AbortSignal) => Promise<ResearchStatus>
  /** 概览（QUOT-400）：K 线 + 快照 + 财务指标，不依赖六段产物 */
  overview: (code: string, signal?: AbortSignal) => Promise<ResearchOverview>
  run: (code: string, date: string, only: ResearchStageName) => Promise<ResearchRunHandle>
  job: (jobId: string, signal?: AbortSignal) => Promise<ResearchJob>
}

const CODE_PATTERN = /^\d{6}(?:\.(?:SH|SZ|BJ))?$/i

/** 与 `research/checkpoint.normalize_code` 同一口径：校验后再拼地址。 */
export function normalizeResearchCode(raw: string | undefined | null): string | null {
  const text = String(raw ?? '').trim().toUpperCase()
  return CODE_PATTERN.test(text) ? text : null
}

export function isStageName(value: unknown): value is ResearchStageName {
  return typeof value === 'string' && (RESEARCH_STAGES as readonly string[]).includes(value)
}

function isStageStatus(value: unknown): value is StageStatus {
  return value === 'ok' || value === 'degraded' || value === 'failed' || value === 'pending'
}

function parseError(message: string): ApiError {
  return new ApiError('parse', message)
}

function asRecord(value: unknown, what: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw parseError(`${what} 不是 JSON 对象`)
  }
  return value as Record<string, unknown>
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

export function parseProvenanceSlot(value: unknown): ProvenanceSlot {
  const record = asRecord(value ?? {}, 'provenance')
  const rawValues = record.values && typeof record.values === 'object' ? record.values : {}
  const values: ProvenanceSlot['values'] = {}
  for (const [key, entry] of Object.entries(rawValues as Record<string, unknown>)) {
    const item = entry && typeof entry === 'object' ? (entry as Record<string, unknown>) : {}
    values[key] = {
      sources: asStringArray(item.sources),
      unverified: item.unverified !== false,
    }
  }
  const sources: ProvenanceSource[] = []
  if (Array.isArray(record.sources)) {
    for (const entry of record.sources) {
      if (!entry || typeof entry !== 'object') continue
      const item = entry as Record<string, unknown>
      const id = asString(item.id)
      const dataset = asString(item.dataset)
      if (!id || !dataset) continue
      sources.push({
        id,
        dataset,
        params:
          item.params && typeof item.params === 'object'
            ? (item.params as Record<string, unknown>)
            : undefined,
        retrieved_at: asString(item.retrieved_at),
        value:
          typeof item.value === 'number' || typeof item.value === 'string' ? item.value : null,
        unit: asString(item.unit),
        endpoint: asString(item.endpoint),
        as_of: asString(item.as_of),
        url: asString(item.url),
        verified: item.verified === true,
      })
    }
  }
  return {
    values,
    sources,
    unverified: record.unverified !== false || sources.length === 0,
  }
}

function parseDocument(value: unknown, stage: ResearchStageName): StageDocument | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  const status = isStageStatus(record.status) ? record.status : 'pending'
  return {
    code: String(record.code ?? ''),
    date: String(record.date ?? ''),
    stage,
    title: asString(record.title) ?? STAGE_TITLE[stage],
    status,
    mode: asString(record.mode),
    model: asString(record.model),
    degraded: record.degraded === true,
    narrative: asStringArray(record.narrative),
    notes: asStringArray(record.notes),
    facts:
      record.facts && typeof record.facts === 'object' && !Array.isArray(record.facts)
        ? (record.facts as Record<string, unknown>)
        : {},
    ai:
      record.ai && typeof record.ai === 'object' && !Array.isArray(record.ai)
        ? (record.ai as Record<string, unknown>)
        : undefined,
    provenance: parseProvenanceSlot(record.provenance),
    failure:
      record.failure && typeof record.failure === 'object'
        ? (record.failure as StageFailure)
        : null,
    attempts: Array.isArray(record.attempts) ? record.attempts : [],
  }
}

/**
 * 解析 `GET /api/research/{code}` 的响应。
 *
 * 兼容两种外壳：直接平铺（本文契约）与 MCP `research_status` 的
 * `{payload: {research: {...}}}`（同一后端两处复用）。`stages` 缺失即解析失败——
 * 六段是页面骨架，不猜。
 */
export function parseResearchStatus(payload: unknown, expectedCode?: string): ResearchStatus {
  let root = asRecord(payload, '研究状态响应')
  // FastAPI 也可能把状态包在 payload / research 下（MCP 同形）
  const nested = root.payload ?? root.research
  if (nested && typeof nested === 'object' && !Array.isArray(nested)) {
    root = asRecord(nested, '研究状态响应')
    if (root.research && typeof root.research === 'object') {
      root = asRecord(root.research, '研究状态响应')
    }
  }

  const code = asString(root.code) ?? expectedCode ?? ''
  const date = asString(root.date)
  if (!date) throw parseError('研究状态响应缺少 date')

  const rawStages = root.stages
  if (!Array.isArray(rawStages)) throw parseError('研究状态响应缺少 stages 数组')

  const byName = new Map<ResearchStageName, unknown>()
  for (const entry of rawStages) {
    if (!entry || typeof entry !== 'object') continue
    const item = entry as Record<string, unknown>
    const name = item.name
    if (isStageName(name)) byName.set(name, item)
  }

  const stages: ResearchStage[] = RESEARCH_STAGES.map((name) => {
    const item = (byName.get(name) ?? {}) as Record<string, unknown>
    const artifactRaw = item.artifact
    let artifact: StageArtifact | null = null
    if (artifactRaw && typeof artifactRaw === 'object') {
      const record = artifactRaw as Record<string, unknown>
      const document = parseDocument(record.document ?? record.json, name)
      const markdown = asString(record.markdown) ?? asString(record.md) ?? ''
      if (document) artifact = { document, markdown }
    }
    return {
      name,
      title: asString(item.title) ?? STAGE_TITLE[name],
      status: isStageStatus(item.status)
        ? item.status
        : artifact?.document.status ?? 'pending',
      mode: asString(item.mode),
      degraded: item.degraded === true || artifact?.document.degraded === true,
      artifact,
    }
  })

  const failed = stages.filter((stage) => stage.status === 'failed').map((stage) => stage.name)
  const pending = stages.filter((stage) => stage.status === 'pending').map((stage) => stage.name)
  const available = root.available === true
  return {
    code,
    date,
    available,
    complete: root.complete === true,
    checkpoint: asString(root.checkpoint),
    updated_at: asString(root.updated_at),
    stages,
    pending: Array.isArray(root.pending)
      ? root.pending.filter(isStageName)
      : pending,
    failed: Array.isArray(root.failed) ? root.failed.filter(isStageName) : failed,
    source_status:
      root.source_status === 'ok' || root.source_status === 'degraded'
        ? root.source_status
        : available
          ? 'ok'
          : 'partial',
    reason: asString(root.reason),
    data_source_status: asString(root.data_source_status) ?? undefined,
  }
}

export function parseResearchJob(payload: unknown): ResearchJob {
  const record = asRecord(payload, '研究任务响应')
  const status = record.status
  const allowed: ResearchJobStatus[] = ['queued', 'running', 'ok', 'degraded', 'failed']
  return {
    id: String(record.id ?? record.job_id ?? ''),
    status: allowed.includes(status as ResearchJobStatus)
      ? (status as ResearchJobStatus)
      : 'running',
    stage: isStageName(record.stage) ? record.stage : null,
    progress: Math.min(1, Math.max(0, asNumber(record.progress))),
    step: asString(record.step),
    error: asString(record.error),
  }
}

// ---------------------------------------------------------------- 概览（QUOT-400）

export interface KlineBar {
  date: string
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  volume: number | null
}

export interface OverviewQuote {
  name?: string | null
  price: number | null
  prev_close: number | null
  change_pct: number | null
  volume: number | null
  amount: number | null
  timestamp: string | null
}

export interface OverviewFinancials {
  report_name: string | null
  report_date: string | null
  eps: number | null
  revenue: number | null
  net_profit: number | null
  roe: number | null
  revenue_yoy: number | null
  profit_yoy: number | null
}

export interface ResearchOverview {
  code: string
  name: string | null
  as_of: string | null
  /** `ok`（K 线有根）| `missing`（K 线一根都没有）；quote/financials 的有无看字段是否为 null */
  dataSourceStatus: 'ok' | 'missing'
  kline: { bars: KlineBar[]; count: number; requested: number; reason: string | null }
  quote: { quote: OverviewQuote | null; reason: string | null }
  financials: { financials: OverviewFinancials | null; reason: string | null }
}

function asNumberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function parseKlineBar(value: unknown): KlineBar | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const record = value as Record<string, unknown>
  const date = asString(record.date)
  if (!date) return null
  return {
    date,
    open: asNumberOrNull(record.open),
    high: asNumberOrNull(record.high),
    low: asNumberOrNull(record.low),
    close: asNumberOrNull(record.close),
    volume: asNumberOrNull(record.volume),
  }
}

/**
 * 解析 `GET /api/research/{code}/overview` 的四段信封
 * （`payload / data_source_status / data_as_of / meta`，与 data 面同款）。
 *
 * 兼容直接下钻：根上没有 `payload` 时按平铺处理。缺键不猜：quote / financials
 * 没提供就如实标 null + reason，页面据此出缺数态。
 */
export function parseResearchOverview(payload: unknown, expectedCode?: string): ResearchOverview {
  let root = asRecord(payload, '概览响应')
  if (root.payload && typeof root.payload === 'object' && !Array.isArray(root.payload)) {
    root = root.payload as Record<string, unknown>
  }

  const code = asString(root.code) ?? expectedCode ?? ''
  const klineRaw =
    root.kline && typeof root.kline === 'object' && !Array.isArray(root.kline)
      ? (root.kline as Record<string, unknown>)
      : {}
  const bars = Array.isArray(klineRaw.bars)
    ? klineRaw.bars.map(parseKlineBar).filter((bar): bar is KlineBar => bar !== null)
    : []

  const quoteBlock =
    root.quote && typeof root.quote === 'object' && !Array.isArray(root.quote)
      ? (root.quote as Record<string, unknown>)
      : null
  const quote: OverviewQuote | null = quoteBlock
    ? {
        name: asString(quoteBlock.name),
        price: asNumberOrNull(quoteBlock.price),
        prev_close: asNumberOrNull(quoteBlock.prev_close),
        change_pct: asNumberOrNull(quoteBlock.change_pct),
        volume: asNumberOrNull(quoteBlock.volume),
        amount: asNumberOrNull(quoteBlock.amount),
        timestamp: asString(quoteBlock.timestamp),
      }
    : null

  const finBlock =
    root.financials && typeof root.financials === 'object' && !Array.isArray(root.financials)
      ? (root.financials as Record<string, unknown>)
      : null
  const financials: OverviewFinancials | null = finBlock
    ? {
        report_name: asString(finBlock.report_name),
        report_date: asString(finBlock.report_date),
        eps: asNumberOrNull(finBlock.eps),
        revenue: asNumberOrNull(finBlock.revenue),
        net_profit: asNumberOrNull(finBlock.net_profit),
        roe: asNumberOrNull(finBlock.roe),
        revenue_yoy: asNumberOrNull(finBlock.revenue_yoy),
        profit_yoy: asNumberOrNull(finBlock.profit_yoy),
      }
    : null

  const status = root.data_source_status === 'missing' ? 'missing' : 'ok'
  return {
    code,
    name: asString(root.name),
    as_of: asString(root.as_of),
    dataSourceStatus: status,
    kline: {
      bars,
      count: bars.length,
      requested: asNumberOrNull(klineRaw.requested) ?? 120,
      reason: asString(klineRaw.reason),
    },
    quote: { quote, reason: asString(root.quote_reason) },
    financials: { financials, reason: asString(root.financials_reason) },
  }
}

// ---------------------------------------------------------------- K 线图（QUOT-410 / QUOT-429）

/**
 * 周期 / 复权是**后端枚举**：`daily|weekly|monthly`、`qfq|hfq|none`。
 * 与页面上的「日/周/月」「前/后/不复权」一一对应，原样透传，前端不做聚合与复权。
 */
export type KlinePeriod = 'daily' | 'weekly' | 'monthly'
export type KlineAdjust = 'qfq' | 'hfq' | 'none'

export interface KlineRequest {
  symbol: string
  period: KlinePeriod
  /** 只有 A 股要传；港美股传了只会换来上游一句看不懂的提示，所以不传 */
  adjust?: KlineAdjust
}

/**
 * K 线取数通道：默认走 HTTP，组件自证页与测试可注入替身。
 * 返回的是解析好的 `KlineResult`，调用方不再碰原始 JSON。
 */
export type KlineLoader = (
  request: KlineRequest,
  signal?: AbortSignal,
) => Promise<KlineResult>

const KLINE_TIMEOUT_MS = 20000

/** `GET /api/research/{code}/kline` → 解析后的视图（载荷契约见 SPEC-RESEARCH-PAGES §2.4）。 */
export async function loadKline(
  input: KlineRequest,
  signal?: AbortSignal,
): Promise<KlineResult> {
  const params = new URLSearchParams({ period: input.period })
  if (input.adjust) params.set('adjust', input.adjust)
  const payload = await request<unknown>(
    `/api/research/${encodeURIComponent(input.symbol)}/kline?${params.toString()}`,
    { signal, timeoutMs: KLINE_TIMEOUT_MS },
  )
  return parseKline(payload, input.symbol)
}

// ---------------------------------------------------------------- 事件回看（QUOT-451）

/**
 * `GET /api/research/{code}/event-study` 的消费形状（契约见 SPEC §2.6）。
 *
 * 🔴 每个收益数字都直接取自接口：均值只在 `sample.status === 'ok'` 时才有，
 *    前端不补算、不把「0 次」写成「0%」。
 * 🔴 增持 / 减持是同一份 `holder_trades` 数据集按 `detail.direction` 拆出来的两枚 chip；
 *    接口只按「增持减持」合计给样本，拆方向后没有单独均值，页面如实说明，不自己求平均。
 */

export type EventStudyDataset =
  | 'holder_trades'
  | 'dragon_tiger'
  | 'earnings_preview'
  | 'institution_survey'
  | 'share_buyback'
  | 'lockup_calendar'

export type EventStudyStatus = 'ok' | 'partial' | 'missing'

export interface EventWindow {
  start: string | null
  end: string | null
  /** 四个边界收盘原值（QUOT-451 接口新增）：只用于页面复算，窗口未对齐时全为 null */
  stockStartClose: number | null
  stockEndClose: number | null
  benchmarkStartClose: number | null
  benchmarkEndClose: number | null
  stockReturn: number | null
  benchmarkReturn: number | null
  excessReturn: number | null
  /** 窗口算不出来的原因（基准不足 / 个股缺收盘 / 停牌），原样展示 */
  reason: string | null
}

export interface EventStudyEvent {
  date: string
  /** 事件原始字段（方向、股东、类型…），键随数据集不同，页面只读存在的键 */
  detail: Record<string, unknown>
  window: EventWindow
}

export interface EventStudySample {
  required: number | null
  windowAvailable: number | null
  status: string | null
  meanStockReturn: number | null
  meanBenchmarkReturn: number | null
  meanExcessReturn: number | null
  reason: string | null
}

export interface EventStudyType {
  dataset: string
  label: string
  /** 条数；接口没给就是 null（`—`），0 是真实读数 */
  count: number | null
  dates: string[]
  events: EventStudyEvent[]
  sample: EventStudySample
}

export interface EventStudy {
  code: string
  name: string | null
  windowBefore: number | null
  windowAfter: number | null
  benchmark: string | null
  formula: string | null
  tradingDays: number | null
  benchmarkRows: number | null
  eventTypes: EventStudyType[]
  dataSourceStatus: EventStudyStatus
  dataAsOf: string | null
  fetchedAt: string | null
}

export interface EventStudyRequest {
  symbol: string
  /** 只取某几类（数据集键）；不给 = 全部 */
  sources?: string[]
  before?: number
  after?: number
}

export type EventStudyLoader = (
  request: EventStudyRequest,
  signal?: AbortSignal,
) => Promise<EventStudy>

const EVENT_STUDY_TIMEOUT_MS = 30000

function parseEventWindow(value: unknown): EventWindow {
  const record =
    value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {}
  return {
    start: asString(record.start),
    end: asString(record.end),
    stockStartClose: asNumberOrNull(record.stock_start_close),
    stockEndClose: asNumberOrNull(record.stock_end_close),
    benchmarkStartClose: asNumberOrNull(record.benchmark_start_close),
    benchmarkEndClose: asNumberOrNull(record.benchmark_end_close),
    stockReturn: asNumberOrNull(record.stock_return),
    benchmarkReturn: asNumberOrNull(record.benchmark_return),
    excessReturn: asNumberOrNull(record.excess_return),
    reason: asString(record.reason),
  }
}

function parseEventSample(value: unknown): EventStudySample {
  const record =
    value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {}
  return {
    required: asNumberOrNull(record.required),
    windowAvailable: asNumberOrNull(record.window_available),
    status: asString(record.status),
    meanStockReturn: asNumberOrNull(record.mean_stock_return),
    meanBenchmarkReturn: asNumberOrNull(record.mean_benchmark_return),
    meanExcessReturn: asNumberOrNull(record.mean_excess_return),
    reason: asString(record.reason),
  }
}

/**
 * 解析四段信封（`payload` + `data_source_status` + `data_as_of` + `meta`）。
 * `event_types` 缺失即解析失败——七枚 chip 是区块骨架，不猜。
 */
export function parseEventStudy(payload: unknown, expectedCode?: string): EventStudy {
  const envelope = asRecord(payload, '事件回看响应')
  // 信封四段的 data_as_of / meta 只在信封层，解包 payload 前先留住
  const root =
    envelope.payload && typeof envelope.payload === 'object' && !Array.isArray(envelope.payload)
      ? (envelope.payload as Record<string, unknown>)
      : envelope
  if (!Array.isArray(root.event_types)) throw parseError('事件回看响应缺少 event_types 数组')

  const eventTypes: EventStudyType[] = []
  for (const entry of root.event_types) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) continue
    const item = entry as Record<string, unknown>
    const dataset = asString(item.dataset)
    if (!dataset) continue
    const events: EventStudyEvent[] = []
    if (Array.isArray(item.events)) {
      for (const raw of item.events) {
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) continue
        const event = raw as Record<string, unknown>
        const date = asString(event.date)
        if (!date) continue
        events.push({
          date,
          detail:
            event.detail && typeof event.detail === 'object' && !Array.isArray(event.detail)
              ? (event.detail as Record<string, unknown>)
              : {},
          window: parseEventWindow(event.window),
        })
      }
    }
    eventTypes.push({
      dataset,
      label: asString(item.label) ?? dataset,
      count: asNumberOrNull(item.count),
      dates: asStringArray(item.dates),
      events,
      sample: parseEventSample(item.sample),
    })
  }

  const rawStatus = root.data_source_status ?? envelope.data_source_status
  const envelopeMeta =
    envelope.meta && typeof envelope.meta === 'object' && !Array.isArray(envelope.meta)
      ? (envelope.meta as Record<string, unknown>)
      : {}
  const meta =
    root.meta && typeof root.meta === 'object' && !Array.isArray(root.meta)
      ? (root.meta as Record<string, unknown>)
      : envelopeMeta
  return {
    code: asString(root.code) ?? expectedCode ?? '',
    name: asString(root.name),
    windowBefore: asNumberOrNull(root.window_before),
    windowAfter: asNumberOrNull(root.window_after),
    benchmark: asString(root.benchmark),
    formula: asString(root.formula),
    tradingDays: asNumberOrNull(root.trading_days),
    benchmarkRows: asNumberOrNull(root.benchmark_rows),
    eventTypes,
    dataSourceStatus:
      rawStatus === 'ok' || rawStatus === 'partial' || rawStatus === 'missing' ? rawStatus : 'ok',
    dataAsOf:
      asString(envelope.data_as_of) ?? asString(root.data_as_of) ?? asString(root.as_of),
    fetchedAt: asString(root.fetched_at) ?? asString(meta.fetched_at),
  }
}

/** `GET /api/research/{code}/event-study` → 解析后的视图。 */
export const loadEventStudy: EventStudyLoader = async (input, signal) => {
  const params = new URLSearchParams()
  if (input.sources && input.sources.length > 0) params.set('sources', input.sources.join(','))
  if (input.before !== undefined) params.set('window_before', String(input.before))
  if (input.after !== undefined) params.set('window_after', String(input.after))
  const query = params.toString()
  const payload = await request<unknown>(
    `/api/research/${encodeURIComponent(input.symbol)}/event-study${query ? `?${query}` : ''}`,
    { signal, timeoutMs: EVENT_STUDY_TIMEOUT_MS },
  )
  return parseEventStudy(payload, input.symbol)
}

// ---------------------------------------------------------------- 传输

const TIMEOUT_MS = 12000
const RUN_TIMEOUT_MS = 20000

interface RequestOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  signal?: AbortSignal
  timeoutMs?: number
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, timeoutMs = TIMEOUT_MS } = options
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  const abortFromCaller = () => controller.abort()
  signal?.addEventListener('abort', abortFromCaller)
  try {
    const response = await fetch(path, {
      method,
      signal: controller.signal,
      cache: 'no-store',
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
        `研究接口 ${path} 返回 HTTP ${response.status}${detail ? `：${detail}` : ''}`,
        response.status,
        detail,
      )
    }
    try {
      return (await response.json()) as T
    } catch {
      throw parseError(`研究接口 ${path} 返回的内容不是合法 JSON`)
    }
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (signal?.aborted) throw new ApiError('timeout', `请求已取消：${path}`)
      throw new ApiError('timeout', `研究接口 ${path} 超时（${timeoutMs}ms）`)
    }
    throw new ApiError('not-connected', `研究接口不可达：${path}`)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abortFromCaller)
  }
}

function statusPath(code: string, date: string): string {
  const query = new URLSearchParams({ date })
  return `/api/research/${encodeURIComponent(code)}?${query.toString()}`
}

export const httpResearchSource: ResearchSource = {
  status: async (code, date, signal) =>
    parseResearchStatus(await request<unknown>(statusPath(code, date), { signal }), code),
  overview: async (code, signal) =>
    parseResearchOverview(
      await request<unknown>(
        `/api/research/${encodeURIComponent(code)}/overview`,
        { signal, timeoutMs: 20000 },
      ),
      code,
    ),
  run: (code, date, only) =>
    request<ResearchRunHandle>(`/api/research/${encodeURIComponent(code)}/run`, {
      method: 'POST',
      body: { date, only },
      timeoutMs: RUN_TIMEOUT_MS,
    }),
  job: async (jobId, signal) =>
    parseResearchJob(
      await request<unknown>(`/api/research/jobs/${encodeURIComponent(jobId)}`, { signal }),
    ),
}

/** 与全站一致的「结论 + 原因 + 代码」文案。 */
export function describeResearchError(error: unknown): {
  title: string
  detail: string
  code: string | null
} {
  if (error instanceof ApiError) {
    if (error.kind === 'not-connected') {
      return {
        title: '研究接口未接入',
        detail: '本机 API 未连接（ldst run 未启动）；研究读取/续跑接口（/api/research/*）由后端提供。',
        code: null,
      }
    }
    if (error.kind === 'timeout') {
      return { title: '研究取数超时', detail: error.message, code: 'TIMEOUT' }
    }
    if (error.kind === 'parse') {
      return { title: '研究响应解析失败', detail: error.message, code: 'E_PARSE' }
    }
    if (error.status === 503) {
      return {
        title: '研究引擎不可用',
        detail: error.detail ?? '读取研究状态失败（盘面不可读或服务异常）；稍后重试。',
        code: 'HTTP 503',
      }
    }
    if (error.status === 404) {
      const kind = classify404(error)
      if (kind === 'unknown-code') {
        return {
          title: '未知证券代码',
          detail:
            error.detail ??
            '这个代码不是 A 股个股（沪市 60/68、深市 00/30、北交所 43/83/87/92 开头）。',
          code: 'HTTP 404',
        }
      }
      if (kind === 'no-record') {
        return {
          title: '这只还没跑过研究',
          detail: error.detail ?? '本机还没有这只的六段研究产物；生成后点「刷新」重新读取。',
          code: 'HTTP 404',
        }
      }
      return {
        title: '研究接口未接入',
        detail:
          error.detail ??
          '后端尚未提供 /api/research/* 只读接口（HTTP 404 Not Found）；接口上线后本页会自动读取落盘产物。',
        code: 'HTTP 404',
      }
    }
    return {
      title: '研究取数失败',
      detail: error.detail ?? error.message,
      code: error.status ? `HTTP ${error.status}` : null,
    }
  }
  return {
    title: '研究取数失败',
    detail: error instanceof Error ? error.message : String(error),
    code: null,
  }
}

/**
 * QUOT-400：404 不再一律当「接口没接」——后端用 `detail` 文案区分三种语义：
 *
 * - `unknown-code`：detail 含「未知证券代码」→ 这个代码不可能是 A 股个股；
 * - `no-record`：detail 含「还没有任何研究记录」/「没有研究产物」→ 业务空态，
 *   这只还没跑过研究（先 `ldst research <code>`）；
 * - `missing`：FastAPI 默认 `{"detail":"Not Found"}` 或无 detail → 路由本身没接，
 *   才是「接口未接入」。
 */
export type Research404Kind = 'missing' | 'no-record' | 'unknown-code'

const NO_RECORD_MARKERS = ['还没有任何研究记录', '没有研究产物'] as const

export function classify404(error: unknown): Research404Kind | null {
  if (!(error instanceof ApiError) || error.kind !== 'http' || error.status !== 404) return null
  const detail = error.detail ?? ''
  if (detail.includes('未知证券代码')) return 'unknown-code'
  if (NO_RECORD_MARKERS.some((marker) => detail.includes(marker))) return 'no-record'
  return 'missing'
}

/** 页面用于判断「接口没接」而不是「页面坏了」：只有路由本身没接（FastAPI 默认 404）才算。 */
export function isResearchEndpointMissing(error: unknown): boolean {
  return classify404(error) === 'missing'
}

export function stageArtifact(status: ResearchStatus, stage: ResearchStageName): StageArtifact | null {
  return status.stages.find((item) => item.name === stage)?.artifact ?? null
}

export function stageOf(status: ResearchStatus, stage: ResearchStageName): ResearchStage {
  return (
    status.stages.find((item) => item.name === stage) ?? {
      name: stage,
      title: STAGE_TITLE[stage],
      status: 'pending',
      artifact: null,
    }
  )
}
