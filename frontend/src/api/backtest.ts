/**
 * 回测客户端（QUOT-534 / QUOT-500 + QUOT-535 契约）。
 *
 * 只读运行卡产物，页面**不重算任何指标 / 收益 / 曲线**：
 * - `GET /api/backtest/runs?limit=`：落盘运行卡摘要（新的在前）
 * - `GET /api/backtest/runs/{run_id}`：运行卡 JSON（契约本体）
 * - `GET /api/backtest/runs/{run_id}/export.{json,md}`：产物下载（页面直接给链接）
 *
 * 触发面（QUOT-535，PR #194 已合入 main）：
 * - `GET /api/backtest/strategies`：策略清单 + 参数 schema + 成本默认值（唯一来源）
 * - `POST /api/backtest/run`：提交参数 → `202 {job_id, status}`
 * - `GET /api/backtest/jobs/{job_id}`：轮询 `{status, run_id, gate_passed, exit_code, error}`
 *
 * 解析层只做类型收敛与结构校验：数值原样透传，缺的字段保持 `null` / 空数组，
 * 不补 0、不换算单位、不推导任何指标；成本字段按用户输入原样透传，前端不换算万分比。
 */

import { ApiError, isEndpointMissing } from '../api'

const READ_TIMEOUT_MS = 8000
const WRITE_TIMEOUT_MS = 20000
const RUN_ID_PATTERN = /^bt_[0-9a-z_]+$/
const JOB_ID_PATTERN = /^[0-9a-f]{8,64}$/i
/** 控制面写请求的来源证据（QUOT-178）：dev 代理会换 Host，端口白名单读这个显式头。 */
const CLIENT_HEADER = { 'X-LDST-Client': '1' }

export const BACKTEST_PREFIX = '/api/backtest'

export interface BacktestPeriod {
  start: string | null
  end: string | null
  trading_days: number | null
}

export interface BacktestRunSummary {
  run_id: string
  generated_at: string | null
  symbol: string | null
  strategy: string | null
  period: BacktestPeriod | null
  gate_passed: boolean | null
  cumulative_return: number | null
}

export interface BacktestRunsPayload {
  root: string
  count: number
  latest: string | null
  runs: BacktestRunSummary[]
}

export interface BacktestDataMeta {
  fingerprint: string | null
  first_date: string | null
  last_date: string | null
  bars: number | null
  source: string | null
  adjust: string | null
}

export type BacktestConfigValue = number | string | number[] | null

export interface BacktestGateCheck {
  key: string
  label: string
  status: string
  reason: string
  observed: Record<string, unknown>
}

export interface BacktestSegment {
  label: string
  start: string | null
  end: string | null
  bars: number | null
  metrics: Record<string, number>
  metrics_display: Record<string, string>
  metrics_calc_ids: Record<string, string>
  data_fp: string | null
}

export interface BacktestCostTier {
  commission: number | null
  cumulative_return: number | null
  annualized_return: number | null
  max_drawdown: number | null
  trade_count: number | null
  final_equity: number | null
}

export interface BacktestGate {
  passed: boolean
  status: string
  red_reasons: string[]
  warnings: string[]
  checks: BacktestGateCheck[]
  segments: BacktestSegment[]
  cost_tiers: BacktestCostTier[]
}

export interface BacktestEquityPoint {
  date: string
  equity: number
  drawdown: number | null
  shares: number | null
}

export interface BacktestBenchmark {
  name: string | null
  note: string | null
  curve: Array<{ date: string; equity: number }>
  cumulative_return: number | null
}

export interface BacktestRunCard {
  schema_version: string
  run_id: string
  generated_at: string | null
  symbol: string
  board: string | null
  limit_pct: number | null
  strategy: string
  strategy_params: Record<string, unknown>
  period: BacktestPeriod
  data: BacktestDataMeta
  assumptions: string[]
  config: Record<string, BacktestConfigValue>
  config_sources: Record<string, string>
  initial_cash: number | null
  final_equity: number | null
  open_position_pnl: number | null
  metrics: Record<string, number>
  metrics_display: Record<string, string>
  metrics_calc_ids: Record<string, string>
  equity_curve: BacktestEquityPoint[]
  benchmark: BacktestBenchmark
  gate: BacktestGate
  calc_ids: Record<string, string>
  artifacts: Record<string, string>
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

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value.trim() : null
}

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function numberRecord(value: unknown): Record<string, number> {
  const source = record(value)
  if (!source) return {}
  const out: Record<string, number> = {}
  for (const [key, item] of Object.entries(source)) {
    if (typeof item === 'number' && Number.isFinite(item)) out[key] = item
  }
  return out
}

function textRecord(value: unknown): Record<string, string> {
  const source = record(value)
  if (!source) return {}
  const out: Record<string, string> = {}
  for (const [key, item] of Object.entries(source)) {
    if (typeof item === 'string') out[key] = item
  }
  return out
}

function configRecord(value: unknown): Record<string, BacktestConfigValue> {
  const source = record(value)
  if (!source) return {}
  const out: Record<string, BacktestConfigValue> = {}
  for (const [key, item] of Object.entries(source)) {
    if (typeof item === 'number' || typeof item === 'string') {
      out[key] = item
    } else if (Array.isArray(item)) {
      out[key] = item.filter((entry): entry is number => typeof entry === 'number')
    } else if (item === null) {
      out[key] = null
    }
  }
  return out
}

function parsePeriod(value: unknown): BacktestPeriod {
  const source = record(value)
  return {
    start: text(source?.start),
    end: text(source?.end),
    trading_days: numberOrNull(source?.trading_days),
  }
}

function parseRunSummary(value: unknown): BacktestRunSummary | null {
  const source = record(value)
  const runId = text(source?.run_id)
  if (!source || !runId) return null
  return {
    run_id: runId,
    generated_at: text(source.generated_at),
    symbol: text(source.symbol),
    strategy: text(source.strategy),
    period: record(source.period) ? parsePeriod(source.period) : null,
    gate_passed: typeof source.gate_passed === 'boolean' ? source.gate_passed : null,
    cumulative_return: numberOrNull(source.cumulative_return),
  }
}

function parseCheck(value: unknown): BacktestGateCheck | null {
  const source = record(value)
  if (!source) return null
  return {
    key: text(source.key) ?? '',
    label: text(source.label) ?? text(source.key) ?? '',
    status: text(source.status) ?? '',
    reason: text(source.reason) ?? '',
    observed: record(source.observed) ?? {},
  }
}

function parseSegment(value: unknown): BacktestSegment | null {
  const source = record(value)
  if (!source) return null
  return {
    label: text(source.label) ?? '',
    start: text(source.start),
    end: text(source.end),
    bars: numberOrNull(source.bars),
    metrics: numberRecord(source.metrics),
    metrics_display: textRecord(source.metrics_display),
    metrics_calc_ids: textRecord(source.metrics_calc_ids),
    data_fp: text(source.data_fp),
  }
}

function parseCostTier(value: unknown): BacktestCostTier | null {
  const source = record(value)
  if (!source) return null
  return {
    commission: numberOrNull(source.commission),
    cumulative_return: numberOrNull(source.cumulative_return),
    annualized_return: numberOrNull(source.annualized_return),
    max_drawdown: numberOrNull(source.max_drawdown),
    trade_count: numberOrNull(source.trade_count),
    final_equity: numberOrNull(source.final_equity),
  }
}

function parseGate(value: unknown): BacktestGate | null {
  const source = record(value)
  if (!source || typeof source.passed !== 'boolean') return null
  return {
    passed: source.passed,
    status: text(source.status) ?? (source.passed ? 'pass' : 'fail'),
    red_reasons: list(source.red_reasons).filter((item): item is string => typeof item === 'string'),
    warnings: list(source.warnings).filter((item): item is string => typeof item === 'string'),
    checks: list(source.checks)
      .map(parseCheck)
      .filter((item): item is BacktestGateCheck => item !== null),
    segments: list(source.segments)
      .map(parseSegment)
      .filter((item): item is BacktestSegment => item !== null),
    cost_tiers: list(source.cost_tiers)
      .map(parseCostTier)
      .filter((item): item is BacktestCostTier => item !== null),
  }
}

function parseEquityPoint(value: unknown): BacktestEquityPoint | null {
  const source = record(value)
  const date = text(source?.date)
  const equity = numberOrNull(source?.equity)
  if (!source || !date || equity === null) return null
  return { date, equity, drawdown: numberOrNull(source.drawdown), shares: numberOrNull(source.shares) }
}

function parseBenchmark(value: unknown): BacktestBenchmark {
  const source = record(value)
  return {
    name: text(source?.name),
    note: text(source?.note),
    curve: list(source?.curve)
      .map((entry): { date: string; equity: number } | null => {
        const point = record(entry)
        const date = text(point?.date)
        const equity = numberOrNull(point?.equity)
        return date && equity !== null ? { date, equity } : null
      })
      .filter((point): point is { date: string; equity: number } => point !== null),
    cumulative_return: numberOrNull(source?.cumulative_return),
  }
}

/**
 * 运行卡 JSON → 页面视图模型。
 *
 * `run_id` 与 `gate.passed` 是运行卡的证据锚点：缺任何一个都按解析失败处理，
 * 其余字段缺失时保持 `null` / 空数组，页面显示「—」而不是编一个数。
 */
export function parseRunCard(raw: unknown): BacktestRunCard {
  const source = record(raw)
  if (!source) throw new ApiError('parse', '运行卡响应不是对象')
  const runId = text(source.run_id)
  if (!runId) throw new ApiError('parse', '运行卡缺少 run_id')
  const gate = parseGate(source.gate)
  if (!gate) throw new ApiError('parse', `运行卡 ${runId} 的 gate 不完整（缺 passed）`)
  const strategy = text(source.strategy)
  if (!strategy) throw new ApiError('parse', `运行卡 ${runId} 缺少 strategy`)

  const data = record(source.data)
  const benchmark = parseBenchmark(source.benchmark)
  const period = parsePeriod(source.period)
  const artifacts = textRecord(source.artifacts)

  return {
    schema_version: text(source.schema_version) ?? '',
    run_id: runId,
    generated_at: text(source.generated_at),
    symbol: text(source.symbol) ?? '',
    board: text(source.board),
    limit_pct: numberOrNull(source.limit_pct),
    strategy,
    strategy_params: record(source.strategy_params) ?? {},
    period,
    data: {
      fingerprint: text(data?.fingerprint),
      first_date: text(data?.first_date),
      last_date: text(data?.last_date),
      bars: numberOrNull(data?.bars),
      source: text(data?.source),
      adjust: text(data?.adjust),
    },
    assumptions: list(source.assumptions).filter((item): item is string => typeof item === 'string'),
    config: configRecord(source.config),
    config_sources: textRecord(source.config_sources),
    initial_cash: numberOrNull(source.initial_cash),
    final_equity: numberOrNull(source.final_equity),
    open_position_pnl: numberOrNull(source.open_position_pnl),
    metrics: numberRecord(source.metrics),
    metrics_display: textRecord(source.metrics_display),
    metrics_calc_ids: textRecord(source.metrics_calc_ids),
    equity_curve: list(source.equity_curve)
      .map(parseEquityPoint)
      .filter((point): point is BacktestEquityPoint => point !== null),
    benchmark,
    gate,
    calc_ids: textRecord(source.calc_ids),
    artifacts,
  }
}

export function parseRunsPayload(raw: unknown): BacktestRunsPayload {
  const source = record(raw)
  if (!source) throw new ApiError('parse', '运行卡列表响应不是对象')
  const runs = list(source.runs)
    .map(parseRunSummary)
    .filter((item): item is BacktestRunSummary => item !== null)
  return {
    root: text(source.root) ?? '',
    count: numberOrNull(source.count) ?? runs.length,
    latest: text(source.latest) ?? (runs[0]?.run_id ?? null),
    runs,
  }
}

// ---------------------------------------------------------------- 触发面解析（QUOT-535）

export interface BacktestStrategyParam {
  key: string
  label: string
  /** 后端给 `int` / `float`；未知类型按文本展示，不猜数值域。 */
  type: string
  default: number | string | null
  min: number | null
  max: number | null
}

export interface BacktestStrategy {
  name: string
  label: string
  params: BacktestStrategyParam[]
}

export interface BacktestCostPreset {
  key: string
  label: string
  commission_rate_wan: number | null
  commission_floor_yuan: number | null
}

export interface BacktestConfigDefaults {
  commission: number | null
  slippage: number | null
  stamp_duty: number | null
  min_trades: number | null
  in_sample_ratio: number | null
  cost_tiers: number[]
  commission_rate_wan: number | null
  commission_floor_yuan: number | null
  transfer_fee_wan: number | null
  stamp_duty_wan: number | null
  presets: BacktestCostPreset[]
  config_sources: Record<string, string>
}

export interface BacktestStrategiesPayload {
  strategies: BacktestStrategy[]
  config_defaults: BacktestConfigDefaults
  symbol_default: string
}

/** `POST /api/backtest/run` 的请求体：成本字段按用户输入原样透传（万分之口径）。 */
export interface BacktestRunRequest {
  strategy: string
  strategy_params: Record<string, number | string>
  symbol: string
  start: string
  end: string
  commission_rate_wan: number
  commission_floor_yuan: number
  transfer_fee_wan: number
  stamp_duty_wan: number
  slippage: number
}

export interface BacktestRunAck {
  job_id: string
  status: string
}

export type BacktestJobStatus = 'queued' | 'running' | 'ok' | 'failed'

export interface BacktestJob {
  job_id: string
  status: BacktestJobStatus
  run_id: string | null
  gate_passed: boolean | null
  exit_code: number | null
  error: string | null
  strategy: string | null
  symbol: string | null
  start: string | null
  end: string | null
  created_at: string | null
  started_at: string | null
  ended_at: string | null
}

function scalarOrNull(value: unknown): number | string | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') return value
  return null
}

function parseStrategyParam(value: unknown): BacktestStrategyParam | null {
  const source = record(value)
  const key = text(source?.key)
  if (!source || !key) return null
  return {
    key,
    label: text(source.label) ?? key,
    type: text(source.type) ?? 'str',
    default: scalarOrNull(source.default),
    min: numberOrNull(source.min),
    max: numberOrNull(source.max),
  }
}

function parseStrategy(value: unknown): BacktestStrategy | null {
  const source = record(value)
  const name = text(source?.name)
  if (!source || !name) return null
  return {
    name,
    label: text(source.label) ?? name,
    params: list(source.params)
      .map(parseStrategyParam)
      .filter((item): item is BacktestStrategyParam => item !== null),
  }
}

function parsePreset(value: unknown): BacktestCostPreset | null {
  const source = record(value)
  const key = text(source?.key)
  if (!source || !key) return null
  return {
    key,
    label: text(source.label) ?? key,
    commission_rate_wan: numberOrNull(source.commission_rate_wan),
    commission_floor_yuan: numberOrNull(source.commission_floor_yuan),
  }
}

/**
 * `GET /api/backtest/strategies` → 表单初始值。
 *
 * 清单与默认值**全部**来自后端：解析层不补策略、不填成本常量，缺什么就留空。
 */
export function parseStrategiesPayload(raw: unknown): BacktestStrategiesPayload {
  const source = record(raw)
  if (!source) throw new ApiError('parse', '策略清单响应不是对象')
  const rawStrategies = source.strategies
  if (!Array.isArray(rawStrategies)) throw new ApiError('parse', '策略清单响应缺少 strategies 数组')
  const defaults = record(source.config_defaults)
  if (!defaults) throw new ApiError('parse', '策略清单响应缺少 config_defaults')
  return {
    strategies: rawStrategies
      .map(parseStrategy)
      .filter((item): item is BacktestStrategy => item !== null),
    config_defaults: {
      commission: numberOrNull(defaults.commission),
      slippage: numberOrNull(defaults.slippage),
      stamp_duty: numberOrNull(defaults.stamp_duty),
      min_trades: numberOrNull(defaults.min_trades),
      in_sample_ratio: numberOrNull(defaults.in_sample_ratio),
      cost_tiers: list(defaults.cost_tiers).filter(
        (item): item is number => typeof item === 'number' && Number.isFinite(item),
      ),
      commission_rate_wan: numberOrNull(defaults.commission_rate_wan),
      commission_floor_yuan: numberOrNull(defaults.commission_floor_yuan),
      transfer_fee_wan: numberOrNull(defaults.transfer_fee_wan),
      stamp_duty_wan: numberOrNull(defaults.stamp_duty_wan),
      presets: list(defaults.presets)
        .map(parsePreset)
        .filter((item): item is BacktestCostPreset => item !== null),
      config_sources: textRecord(defaults.config_sources),
    },
    symbol_default: text(source.symbol_default) ?? '',
  }
}

export function parseRunAck(raw: unknown): BacktestRunAck {
  const source = record(raw)
  const jobId = text(source?.job_id)
  if (!source || !jobId) throw new ApiError('parse', '回测提交响应缺少 job_id')
  return { job_id: jobId, status: text(source.status) ?? 'queued' }
}

function jobStatus(value: unknown): BacktestJobStatus {
  return value === 'queued' || value === 'running' || value === 'ok' || value === 'failed'
    ? value
    : 'queued'
}

export function parseJob(raw: unknown): BacktestJob {
  const source = record(raw)
  const jobId = text(source?.job_id)
  if (!source || !jobId) throw new ApiError('parse', '回测任务响应缺少 job_id')
  return {
    job_id: jobId,
    status: jobStatus(source.status),
    run_id: text(source.run_id),
    gate_passed: typeof source.gate_passed === 'boolean' ? source.gate_passed : null,
    exit_code: numberOrNull(source.exit_code),
    error: text(source.error),
    strategy: text(source.strategy),
    symbol: text(source.symbol),
    start: text(source.start),
    end: text(source.end),
    created_at: text(source.created_at),
    started_at: text(source.started_at),
    ended_at: text(source.ended_at),
  }
}

/** 后端 400 的 `detail` 是字符串；FastAPI 422 的 `detail` 是 `[{loc, msg}]` 数组。 */
function errorDetailText(parsed: unknown): string | null {
  const detail = record(parsed)?.detail
  if (typeof detail === 'string' && detail.trim() !== '') return detail.trim()
  if (!Array.isArray(detail)) return null
  const parts: string[] = []
  for (const entry of detail) {
    const item = record(entry)
    if (!item) continue
    const where = list(item.loc)
      .map((segment) => String(segment))
      .filter((segment) => segment !== 'body')
      .join('.')
    const message = text(item.msg) ?? text(item.type) ?? '参数不合法'
    parts.push(where ? `${where}：${message}` : message)
  }
  return parts.length > 0 ? parts.join('；') : null
}

// ---------------------------------------------------------------- 传输

interface RequestOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  signal?: AbortSignal
  timeoutMs?: number
}

async function requestJson(path: string, options: RequestOptions = {}): Promise<unknown> {
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
        ...(method === 'POST' ? CLIENT_HEADER : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
    if (!response.ok) {
      let detail: string | null = null
      try {
        detail = errorDetailText((await response.json()) as unknown)
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
      return (await response.json()) as unknown
    } catch {
      throw new ApiError('parse', `${path} 返回的内容不是合法 JSON`)
    }
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (signal?.aborted) throw new ApiError('timeout', `请求已取消：${path}`)
      throw new ApiError('timeout', `本地接口 ${path} 超时（${timeoutMs}ms）`)
    }
    throw new ApiError('not-connected', `本地接口 ${path} 未接入：需先启动 ldst run`)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abortFromCaller)
  }
}

export function assertRunId(runId: string): string {
  if (!RUN_ID_PATTERN.test(runId)) {
    throw new ApiError('http', `run_id 不合法：${runId}`, 400)
  }
  return runId
}

function assertJobId(jobId: string): string {
  if (!JOB_ID_PATTERN.test(jobId)) {
    throw new ApiError('http', `job_id 不合法：${jobId}`, 400)
  }
  return jobId
}

export function backtestRunsPath(limit = 50): string {
  const clamped = Math.max(1, Math.min(200, Math.trunc(limit)))
  return `${BACKTEST_PREFIX}/runs?limit=${clamped}`
}

export function backtestCardPath(runId: string): string {
  return `${BACKTEST_PREFIX}/runs/${encodeURIComponent(assertRunId(runId))}`
}

/** 证据区下载链接：后端 `attachment` 响应，页面只给 href，不经前端转换。 */
export function backtestExportPath(runId: string, format: 'json' | 'md'): string {
  return `${backtestCardPath(runId)}/export.${format}`
}

export function backtestStrategiesPath(): string {
  return `${BACKTEST_PREFIX}/strategies`
}

export function backtestSubmitPath(): string {
  return `${BACKTEST_PREFIX}/run`
}

export function backtestJobPath(jobId: string): string {
  return `${BACKTEST_PREFIX}/jobs/${encodeURIComponent(assertJobId(jobId))}`
}

export async function fetchBacktestRuns(
  signal?: AbortSignal,
  limit = 50,
): Promise<BacktestRunsPayload> {
  return parseRunsPayload(await requestJson(backtestRunsPath(limit), { signal }))
}

export async function fetchBacktestCard(
  runId: string,
  signal?: AbortSignal,
): Promise<BacktestRunCard> {
  return parseRunCard(await requestJson(backtestCardPath(runId), { signal }))
}

/** 策略清单 + 成本默认值；参数区的唯一数据来源（前端不硬编码策略/成本）。 */
export async function fetchBacktestStrategies(
  signal?: AbortSignal,
): Promise<BacktestStrategiesPayload> {
  return parseStrategiesPayload(await requestJson(backtestStrategiesPath(), { signal }))
}

/** 提交回测：后端同步校验后返回 `202 {job_id, status}`，执行结果走任务轮询。 */
export async function submitBacktestRun(
  request: BacktestRunRequest,
  signal?: AbortSignal,
): Promise<BacktestRunAck> {
  return parseRunAck(
    await requestJson(backtestSubmitPath(), {
      method: 'POST',
      body: request,
      signal,
      timeoutMs: WRITE_TIMEOUT_MS,
    }),
  )
}

export async function fetchBacktestJob(
  jobId: string,
  signal?: AbortSignal,
): Promise<BacktestJob> {
  return parseJob(await requestJson(backtestJobPath(jobId), { signal }))
}

/** 端点尚未上线（旧版后端 404）：页面按「回测接口暂缺」呈现，不编数据。 */
export function isBacktestEndpointMissing(error: unknown): boolean {
  return isEndpointMissing(error)
}
