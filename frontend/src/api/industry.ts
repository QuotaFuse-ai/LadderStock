/**
 * 产业研究（R9）只读数据客户端。
 *
 * 数据源是 R5 `industry_snapshot`（`ldst industry list|show`）与 R9 的规则信号；
 * 浏览器读不到本机缓存，所以页面只认三个只读接口（契约见
 * `docs/specs/SPEC-INDUSTRY-PAGES.md` §2）：
 *
 *   GET /api/industry                    行业清单 + 每行业五维指标投影
 *   GET /api/industry/{name}             单行业快照 + 历史分位 / 成分股 / 相关资讯
 *   GET /api/signals?industry=&rule=     规则信号 + 逐条证据链
 *
 * 接口未合入时后端回 404：界面显示「接口未接入」并保留重试，**不伪造数据**；
 * `?demo=1` 的演示源是显式开关，且页面带水印。
 */

import { ApiError } from '../api'
import { envelopeMetaOf, type EnvelopeFields } from './envelope'

export const INDUSTRY_DIMENSIONS = [
  'prosperity',
  'capital',
  'valuation',
  'momentum',
  'crowding',
] as const

export type IndustryDimension = (typeof INDUSTRY_DIMENSIONS)[number]

export const DIMENSION_TITLE: Record<IndustryDimension, string> = {
  prosperity: '景气',
  capital: '资金',
  valuation: '估值',
  momentum: '动量',
  crowding: '拥挤度',
}

/** 维度名是否合法（路由 / 演示参数共用）。 */
export function isIndustryDimension(value: unknown): value is IndustryDimension {
  return typeof value === 'string' && (INDUSTRY_DIMENSIONS as readonly string[]).includes(value)
}

/** R5 五维指标路径（与 `industry/snapshot.py::metric_paths` 同名，不可增删）。 */
export const DIMENSION_METRIC_PATHS: Record<IndustryDimension, readonly string[]> = {
  prosperity: [
    'prosperity.revenue_yoy_median',
    'prosperity.profit_yoy_median',
    'prosperity.roe_median',
  ],
  capital: [
    'capital.constituents.main_net_inflow_nd',
    'capital.constituents.main_net_inflow_nd_sampled',
    'capital.board.main_net_inflow',
    'capital.board.main_net_pct',
    'capital.board.change_pct',
  ],
  valuation: [
    'valuation.pe_median',
    'valuation.pe_percentile',
    'valuation.pb_median',
    'valuation.pb_percentile',
  ],
  momentum: [
    'momentum.change_pct_5d',
    'momentum.change_pct_20d',
    'momentum.amount_share_pct',
  ],
  crowding: [
    'crowding.turnover_median',
    'crowding.turnover_percentile',
    'crowding.fund_concentration_pct',
  ],
}

export interface IndustryWindows {
  fund_days?: number | null
  valuation_lookback_days?: number | null
  turnover_lookback_days?: number | null
  momentum_windows?: number[]
}

export type DataSourceStatus = 'ok' | 'partial' | 'missing'

export interface IndustrySummaryRow {
  name: string
  l1_code: string | null
  bound: boolean
  enabled: boolean
  available: boolean
  constituent_count: number | null
  data_source_status: DataSourceStatus
  reason: string | null
  dimensions_available: Record<IndustryDimension, boolean>
  /** 键与 R5 `metric_paths()` 同名；`null` 是「没有这个数」，不是 0 */
  metrics: Record<string, number | null>
  /** 每个维度的数据时点（后端按数据集给，不冒充请求日期） */
  as_of: Partial<Record<IndustryDimension, string | null>>
}

export interface IndustryListPayload extends EnvelopeFields {
  available: boolean
  caliber: string | null
  windows: IndustryWindows
  count: number
  as_of: Partial<Record<IndustryDimension, string | null>>
  industries: IndustrySummaryRow[]
  datasets: Record<string, DatasetStatus>
  source_detail: string | null
  reason: string | null
  data_as_of: string | null
}

export interface DatasetStatus {
  records: number
  as_of: string | null
  retrieved_at: string | null
  error: string | null
}

export interface IndustryMeta {
  name: string
  l1_code: string | null
  caliber: string | null
  in_catalog: boolean
  bound: boolean
  anchors: string[]
  board_name: string | null
  enabled: boolean
  note: string | null
}

export interface MembershipBlock {
  constituent_count: number | null
  classified_codes: number | null
  as_of: string | null
  reason: string | null
}

export interface ProsperityBlock {
  available: boolean
  reason: string | null
  caliber: string | null
  revenue_yoy_median: number | null
  profit_yoy_median: number | null
  roe_median: number | null
  stocks_with_report: number | null
  report_basis: string | null
}

export interface CapitalConstituents {
  main_net_inflow_nd: number | null
  main_net_inflow_nd_sampled: number | null
  window_days: number | null
  days: string[]
  sample_count: number | null
  coverage: { expected_rows: number | null; rows_used: number | null; complete: boolean | null }
  reason: string | null
}

export interface CapitalBoard {
  caliber: string | null
  board_code: string | null
  board_name: string | null
  trade_date: string | null
  main_net_inflow: number | null
  main_net_pct: number | null
  change_pct: number | null
  reason: string | null
}

export interface CapitalBlock {
  available: boolean
  reason: string | null
  caliber: { sw?: string | null; board?: string | null }
  constituents: CapitalConstituents
  board: CapitalBoard
  northbound: number | null
  northbound_reason: string | null
}

export interface ValuationBlock {
  available: boolean
  reason: string | null
  caliber: string | null
  pe_median: number | null
  pe_percentile: number | null
  pb_median: number | null
  pb_percentile: number | null
  window_days: number | null
  window_days_used: number | null
  percentile_definition: string | null
  stocks_with_valuation: number | null
}

export interface MomentumBlock {
  available: boolean
  reason: string | null
  index_change_pct: number | null
  index_change_pct_reason: string | null
  change_pct_5d: number | null
  change_pct_20d: number | null
  amount_share_pct: number | null
  amount_share_base: string | null
  amount_reason: string | null
  trade_date: string | null
  market_codes: number | null
}

export interface CrowdingBlock {
  available: boolean
  reason: string | null
  turnover_median: number | null
  turnover_percentile: number | null
  turnover_lookback_days: number | null
  fund_concentration_pct: number | null
  fund_concentration_base: string | null
  missing_flow_rows: number | null
}

export interface IndustryDimensions {
  prosperity: ProsperityBlock
  capital: CapitalBlock
  valuation: ValuationBlock
  momentum: MomentumBlock
  crowding: CrowdingBlock
}

export interface ProvenanceEntry {
  id: string
  dataset: string
  params: Record<string, unknown> | null
  retrieved_at: string | null
}

export interface IndustryProvenance {
  values: Record<string, { sources: string[]; unverified: boolean }>
  sources: ProvenanceEntry[]
  unverified: boolean
}

export interface HistoryPoint {
  date: string
  value: number
}

export interface IndustryHistory {
  pe_percentile: HistoryPoint[]
  pb_percentile: HistoryPoint[]
  turnover_percentile: HistoryPoint[]
  change_pct_20d: HistoryPoint[]
}

export interface ConstituentRow {
  code: string
  name: string | null
  pe_ttm: number | null
  pb: number | null
  turnover_rate: number | null
  revenue_yoy: number | null
  profit_yoy: number | null
  main_net_nd: number | null
  as_of: string | null
}

export type RelatedKind = 'report' | 'news'

export interface RelatedItem {
  kind: RelatedKind
  title: string
  source: string | null
  as_of: string | null
  url: string | null
  related_codes: string[]
  industries: string[]
}

export interface IndustryDetailPayload extends EnvelopeFields {
  available: boolean
  meta: IndustryMeta
  membership: MembershipBlock
  dimensions: IndustryDimensions
  windows: IndustryWindows
  formulas: Record<string, string>
  datasets: Record<string, DatasetStatus>
  provenance: IndustryProvenance
  metrics_present: string[]
  missing: string[]
  unavailable_dimensions: IndustryDimension[]
  data_source_status: DataSourceStatus
  reason: string | null
  source_detail: string | null
  /** R9 增补：当期分位所用序列；缺键 = 后端未提供，不画单点线 */
  history: IndustryHistory
  /** R9 增补：申万成分股逐股快照 */
  constituents: ConstituentRow[]
  /** R9 增补：R6 news_stream 关联资讯 / 研报 */
  related: RelatedItem[]
  /** 行业名不在清单时，后端给出候选（R5 §11） */
  candidates: string[]
  /** 快照没有请求日期口径：这里保留后端给的数据时点 */
  requested_industry: string | null
}

export type SignalDirection = 'bullish' | 'bearish'
export type SignalSeverity = 'info' | 'warn' | 'danger'

export interface SignalRule {
  id: string
  title: string
  direction: SignalDirection
  expression: string
}

export interface SignalEvidence {
  path: string
  label: string
  value: number
  unit: string | null
  as_of: string
  dataset: string
  comparator: string
  threshold: number
  passed: boolean
}

export interface IndustrySignal {
  id: string
  rule_id: string
  rule_title: string
  industry: string
  direction: SignalDirection
  severity: SignalSeverity
  as_of: string
  summary: string
  evidence: SignalEvidence[]
}

export interface SignalsPayload extends EnvelopeFields {
  available: boolean
  as_of: string | null
  rules: SignalRule[]
  signals: IndustrySignal[]
  counts: {
    total: number
    bullish: number
    bearish: number
    by_rule: Record<string, number>
  }
  reason: string | null
  /** 前端解析时因证据不完整丢弃的条目数（展示为 partial，不猜） */
  dropped: number
  data_as_of: string | null
}

/** 页面的取数通道：测试与演示可注入，真实通道只有 HTTP。 */
export interface IndustrySource {
  list: (signal?: AbortSignal) => Promise<IndustryListPayload>
  detail: (name: string, signal?: AbortSignal) => Promise<IndustryDetailPayload>
  signals: (
    query: { industry?: string | null; rule?: string | null },
    signal?: AbortSignal,
  ) => Promise<SignalsPayload>
}

// ---------------------------------------------------------------- 解析小工具

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

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function asInt(value: unknown): number | null {
  const number = asNumber(value)
  return number === null ? null : Math.round(number)
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function asRecordOf<T>(value: unknown, map: (item: unknown) => T): Record<string, T> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  const out: Record<string, T> = {}
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) out[key] = map(item)
  return out
}

/**
 * 兼容三种外壳：契约平铺、`{payload: {...}}`、MCP `{payload: {industry_metrics: {...}}}`。
 * 返回最内层的业务对象。
 * 外壳顶层的 `freshness` / `source_status` / `fetched_at` 不在这里取，
 * 由 `envelopeMetaOf()` 单独读出（QUOT-331）。
 */
function unwrap(value: unknown, what: string, nestedKey?: string): Record<string, unknown> {
  let root = asRecord(value, what)
  const payload = root.payload
  if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
    root = asRecord(payload, what)
    if (nestedKey && root[nestedKey] && typeof root[nestedKey] === 'object') {
      root = asRecord(root[nestedKey], what)
    }
  }
  return root
}

/** 数据时点表：只认字符串，缺的维度是 null（不拿今天冒充）。 */
function parseAsOf(value: unknown): Partial<Record<IndustryDimension, string | null>> {
  const record = value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
  const out: Partial<Record<IndustryDimension, string | null>> = {}
  for (const name of INDUSTRY_DIMENSIONS) out[name] = asString(record[name])
  return out
}

function parseWindows(value: unknown): IndustryWindows {
  const record = value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
  return {
    fund_days: asInt(record.fund_days),
    valuation_lookback_days: asInt(record.valuation_lookback_days),
    turnover_lookback_days: asInt(record.turnover_lookback_days),
    momentum_windows: Array.isArray(record.momentum_windows)
      ? record.momentum_windows
          .map((item) => asInt(item))
          .filter((item): item is number => item !== null)
      : [],
  }
}

function parseDatasetStatus(value: unknown): DatasetStatus {
  const record = value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
  return {
    records: asInt(record.records) ?? 0,
    as_of: asString(record.as_of),
    retrieved_at: asString(record.retrieved_at),
    error: asString(record.error),
  }
}

function parseDatasets(value: unknown): Record<string, DatasetStatus> {
  return asRecordOf(value, parseDatasetStatus)
}

function parseStatus(value: unknown, available: boolean): DataSourceStatus {
  return value === 'ok' || value === 'partial' || value === 'missing'
    ? value
    : available
      ? 'ok'
      : 'missing'
}

function parseDimensionsAvailable(value: unknown): Record<IndustryDimension, boolean> {
  const record = value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
  const out = {} as Record<IndustryDimension, boolean>
  for (const name of INDUSTRY_DIMENSIONS) out[name] = record[name] === true
  return out
}

function parseMetrics(value: unknown): Record<string, number | null> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  const out: Record<string, number | null> = {}
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    out[key] = asNumber(item)
  }
  return out
}

// ---------------------------------------------------------------- 行业清单

function parseSummaryRow(value: unknown): IndustrySummaryRow | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const record = value as Record<string, unknown>
  const name = asString(record.name)
  if (!name) return null
  const available = record.available === true
  return {
    name,
    l1_code: asString(record.l1_code),
    bound: record.bound === true,
    enabled: record.enabled !== false,
    available,
    constituent_count: asInt(record.constituent_count),
    data_source_status: parseStatus(record.data_source_status, available),
    reason: asString(record.reason),
    dimensions_available: parseDimensionsAvailable(record.dimensions_available),
    metrics: parseMetrics(record.metrics),
    as_of: parseAsOf(record.as_of),
  }
}

export function parseIndustryList(payload: unknown): IndustryListPayload {
  const root = unwrap(payload, '行业清单响应')
  const rawRows = root.industries
  if (!Array.isArray(rawRows)) throw parseError('行业清单响应缺少 industries 数组')
  const rowList = rawRows
    .map(parseSummaryRow)
    .filter((row): row is IndustrySummaryRow => row !== null)
  if (rowList.length === 0 && rawRows.length > 0) {
    throw parseError('行业清单响应里没有可解析的行业行（缺 name）')
  }
  const available = root.available === true
  const meta = envelopeMetaOf(payload)
  return {
    // 外壳的溯源五键先铺，载荷里的同名键以载荷为准（QUOT-331）
    ...meta,
    available,
    caliber: asString(root.caliber),
    windows: parseWindows(root.windows),
    count: asInt(root.count) ?? rowList.length,
    as_of: parseAsOf(root.as_of),
    industries: rowList,
    datasets: parseDatasets(root.datasets),
    source_detail: asString(root.source_detail) ?? meta.source_detail,
    reason: asString(root.reason),
    data_as_of: asString(root.data_as_of) ?? meta.data_as_of,
  }
}

// ---------------------------------------------------------------- 单行业

function parseMeta(value: unknown, fallbackName: string): IndustryMeta {
  const record = value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
  return {
    name: asString(record.name) ?? fallbackName,
    l1_code: asString(record.l1_code),
    caliber: asString(record.caliber),
    in_catalog: record.in_catalog !== false,
    bound: record.bound === true,
    anchors: asStringArray(record.anchors),
    board_name: asString(record.board_name),
    enabled: record.enabled !== false,
    note: asString(record.note),
  }
}

function block(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function parseDimensions(value: unknown): IndustryDimensions {
  const record = block(value)
  const prosperity = block(record.prosperity)
  const capital = block(record.capital)
  const constituents = block(capital.constituents)
  const coverage = block(constituents.coverage)
  const board = block(capital.board)
  const caliber = block(capital.caliber)
  const valuation = block(record.valuation)
  const momentum = block(record.momentum)
  const crowding = block(record.crowding)
  return {
    prosperity: {
      available: prosperity.available === true,
      reason: asString(prosperity.reason),
      caliber: asString(prosperity.caliber),
      revenue_yoy_median: asNumber(prosperity.revenue_yoy_median),
      profit_yoy_median: asNumber(prosperity.profit_yoy_median),
      roe_median: asNumber(prosperity.roe_median),
      stocks_with_report: asInt(prosperity.stocks_with_report),
      report_basis: asString(prosperity.report_basis),
    },
    capital: {
      available: capital.available === true,
      reason: asString(capital.reason),
      caliber: { sw: asString(caliber.sw), board: asString(caliber.board) },
      constituents: {
        main_net_inflow_nd: asNumber(constituents.main_net_inflow_nd),
        main_net_inflow_nd_sampled: asNumber(constituents.main_net_inflow_nd_sampled),
        window_days: asInt(constituents.window_days),
        days: asStringArray(constituents.days),
        sample_count: asInt(constituents.sample_count),
        coverage: {
          expected_rows: asInt(coverage.expected_rows),
          rows_used: asInt(coverage.rows_used),
          complete: typeof coverage.complete === 'boolean' ? coverage.complete : null,
        },
        reason: asString(constituents.reason),
      },
      board: {
        caliber: asString(board.caliber),
        board_code: asString(board.board_code),
        board_name: asString(board.board_name),
        trade_date: asString(board.trade_date),
        main_net_inflow: asNumber(board.main_net_inflow),
        main_net_pct: asNumber(board.main_net_pct),
        change_pct: asNumber(board.change_pct),
        reason: asString(board.reason),
      },
      northbound: asNumber(capital.northbound),
      northbound_reason: asString(capital.northbound_reason),
    },
    valuation: {
      available: valuation.available === true,
      reason: asString(valuation.reason),
      caliber: asString(valuation.caliber),
      pe_median: asNumber(valuation.pe_median),
      pe_percentile: asNumber(valuation.pe_percentile),
      pb_median: asNumber(valuation.pb_median),
      pb_percentile: asNumber(valuation.pb_percentile),
      window_days: asInt(valuation.window_days),
      window_days_used: asInt(valuation.window_days_used),
      percentile_definition: asString(valuation.percentile_definition),
      stocks_with_valuation: asInt(valuation.stocks_with_valuation),
    },
    momentum: {
      available: momentum.available === true,
      reason: asString(momentum.reason),
      index_change_pct: asNumber(momentum.index_change_pct),
      index_change_pct_reason: asString(momentum.index_change_pct_reason),
      change_pct_5d: asNumber(momentum.change_pct_5d),
      change_pct_20d: asNumber(momentum.change_pct_20d),
      amount_share_pct: asNumber(momentum.amount_share_pct),
      amount_share_base: asString(momentum.amount_share_base),
      amount_reason: asString(momentum.amount_reason),
      trade_date: asString(momentum.trade_date),
      market_codes: asInt(momentum.market_codes),
    },
    crowding: {
      available: crowding.available === true,
      reason: asString(crowding.reason),
      turnover_median: asNumber(crowding.turnover_median),
      turnover_percentile: asNumber(crowding.turnover_percentile),
      turnover_lookback_days: asInt(crowding.turnover_lookback_days),
      fund_concentration_pct: asNumber(crowding.fund_concentration_pct),
      fund_concentration_base: asString(crowding.fund_concentration_base),
      missing_flow_rows: asInt(crowding.missing_flow_rows),
    },
  }
}

function parseProvenance(value: unknown): IndustryProvenance {
  const record = block(value)
  const values: IndustryProvenance['values'] = {}
  for (const [key, entry] of Object.entries(block(record.values))) {
    const item = block(entry)
    values[key] = {
      sources: asStringArray(item.sources),
      unverified: item.unverified !== false,
    }
  }
  const sources: ProvenanceEntry[] = []
  if (Array.isArray(record.sources)) {
    for (const entry of record.sources) {
      const item = block(entry)
      const id = asString(item.id)
      const dataset = asString(item.dataset)
      if (!id || !dataset) continue
      sources.push({
        id,
        dataset,
        params:
          item.params && typeof item.params === 'object' && !Array.isArray(item.params)
            ? (item.params as Record<string, unknown>)
            : null,
        retrieved_at: asString(item.retrieved_at),
      })
    }
  }
  return { values, sources, unverified: record.unverified !== false }
}

function parseSeries(value: unknown): HistoryPoint[] {
  if (!Array.isArray(value)) return []
  const points: HistoryPoint[] = []
  for (const entry of value) {
    const item = block(entry)
    const date = asString(item.date)
    const point = asNumber(item.value)
    if (date && point !== null) points.push({ date, value: point })
  }
  // 后端按日期升序给；前端再排一次，避免依赖顺序
  return points.sort((a, b) => a.date.localeCompare(b.date))
}

function parseHistory(value: unknown): IndustryHistory {
  const record = block(value)
  return {
    pe_percentile: parseSeries(record.pe_percentile),
    pb_percentile: parseSeries(record.pb_percentile),
    turnover_percentile: parseSeries(record.turnover_percentile),
    change_pct_20d: parseSeries(record.change_pct_20d),
  }
}

function parseConstituent(value: unknown): ConstituentRow | null {
  const record = block(value)
  const code = asString(record.code)
  if (!code) return null
  return {
    code,
    name: asString(record.name),
    pe_ttm: asNumber(record.pe_ttm),
    pb: asNumber(record.pb),
    turnover_rate: asNumber(record.turnover_rate),
    revenue_yoy: asNumber(record.revenue_yoy),
    profit_yoy: asNumber(record.profit_yoy),
    main_net_nd: asNumber(record.main_net_nd),
    as_of: asString(record.as_of),
  }
}

function isHttpUrl(value: string | null): value is string {
  if (!value) return false
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

function parseRelatedItem(value: unknown): RelatedItem | null {
  const record = block(value)
  const title = asString(record.title)
  if (!title) return null
  const kind = record.kind === 'news' ? 'news' : 'report'
  const rawUrl = asString(record.url)
  return {
    kind,
    title,
    source: asString(record.source),
    as_of: asString(record.as_of),
    url: isHttpUrl(rawUrl) ? rawUrl : null,
    related_codes: asStringArray(record.related_codes),
    industries: asStringArray(record.industries),
  }
}

export function parseIndustryDetail(payload: unknown, expectedName?: string): IndustryDetailPayload {
  const root = unwrap(payload, '行业快照响应', 'industry_metrics')
  const industry = block(root.industry)
  const membership = block(root.membership)
  const available = root.available === true
  const name = asString(industry.name) ?? expectedName ?? ''
  const meta = envelopeMetaOf(payload)
  return {
    // 外壳的溯源五键先铺，载荷里的同名键以载荷为准（QUOT-331）
    ...meta,
    available,
    meta: parseMeta(industry, name),
    membership: {
      constituent_count: asInt(membership.constituent_count),
      classified_codes: asInt(membership.classified_codes),
      as_of: asString(membership.as_of),
      reason: asString(membership.reason),
    },
    dimensions: parseDimensions(root.dimensions),
    windows: parseWindows(root.windows),
    formulas: Object.fromEntries(
      Object.entries(block(root.formulas)).flatMap(([key, value]) =>
        typeof value === 'string' ? [[key, value] as const] : [],
      ),
    ),
    datasets: parseDatasets(root.datasets),
    provenance: parseProvenance(root.provenance),
    metrics_present: asStringArray(root.metrics_present),
    missing: asStringArray(root.missing),
    unavailable_dimensions: INDUSTRY_DIMENSIONS.filter((dimension) =>
      asStringArray(root.unavailable_dimensions).includes(dimension),
    ),
    data_source_status: parseStatus(root.data_source_status, available),
    reason: asString(root.reason),
    source_detail: asString(root.source_detail) ?? meta.source_detail,
    history: parseHistory(root.history),
    constituents: Array.isArray(root.constituents)
      ? root.constituents
          .map(parseConstituent)
          .filter((row): row is ConstituentRow => row !== null)
      : [],
    related: Array.isArray(root.related)
      ? root.related
          .map(parseRelatedItem)
          .filter((row): row is RelatedItem => row !== null)
      : [],
    candidates: asStringArray(root.candidates),
    requested_industry: asString(block(root.requested).industry),
  }
}

// ---------------------------------------------------------------- 产业信号

function parseDirection(value: unknown): SignalDirection {
  return value === 'bearish' ? 'bearish' : 'bullish'
}

function parseRule(value: unknown): SignalRule | null {
  const record = block(value)
  const id = asString(record.id)
  const title = asString(record.title)
  if (!id || !title) return null
  return {
    id,
    title,
    direction: parseDirection(record.direction),
    expression: asString(record.expression) ?? '',
  }
}

function parseEvidence(value: unknown): SignalEvidence | null {
  const record = block(value)
  const path = asString(record.path)
  const label = asString(record.label)
  const as_of = asString(record.as_of)
  const dataset = asString(record.dataset)
  const comparator = asString(record.comparator)
  const numeric = asNumber(record.value)
  const threshold = asNumber(record.threshold)
  // 证据链必须能回溯到字段 + 时点；缺任何一项都不展示半条证据
  if (!path || !label || !as_of || !dataset || !comparator || numeric === null || threshold === null) {
    return null
  }
  return {
    path,
    label,
    value: numeric,
    unit: asString(record.unit),
    as_of,
    dataset,
    comparator,
    threshold,
    passed: record.passed !== false,
  }
}

function parseSignal(value: unknown): IndustrySignal | null {
  const record = block(value)
  const industry = asString(record.industry)
  const rule_id = asString(record.rule_id)
  const as_of = asString(record.as_of)
  if (!industry || !rule_id || !as_of) return null
  const rawEvidence = Array.isArray(record.evidence) ? record.evidence : []
  const evidence = rawEvidence
    .map(parseEvidence)
    .filter((item): item is SignalEvidence => item !== null)
  if (rawEvidence.length === 0 || evidence.length !== rawEvidence.length) return null
  return {
    id: asString(record.id) ?? `${rule_id}-${industry}`,
    rule_id,
    rule_title: asString(record.rule_title) ?? rule_id,
    industry,
    direction: parseDirection(record.direction),
    severity:
      record.severity === 'warn' || record.severity === 'danger'
        ? record.severity
        : 'info',
    as_of,
    summary: asString(record.summary) ?? '',
    evidence,
  }
}

export function parseSignals(payload: unknown): SignalsPayload {
  const root = unwrap(payload, '产业信号响应')
  const rawSignals = Array.isArray(root.signals) ? root.signals : []
  const parsed = rawSignals.map(parseSignal)
  const signals = parsed.filter((item): item is IndustrySignal => item !== null)
  const rules = Array.isArray(root.rules)
    ? root.rules.map(parseRule).filter((item): item is SignalRule => item !== null)
    : []
  const counts = block(root.counts)
  const available = root.available === true || signals.length > 0
  const meta = envelopeMetaOf(payload)
  return {
    // 外壳的溯源五键先铺，载荷里的同名键以载荷为准（QUOT-331）
    ...meta,
    available,
    as_of: asString(root.as_of),
    rules,
    signals,
    counts: {
      total: asInt(counts.total) ?? signals.length,
      bullish: asInt(counts.bullish) ?? signals.filter((item) => item.direction === 'bullish').length,
      bearish: asInt(counts.bearish) ?? signals.filter((item) => item.direction === 'bearish').length,
      by_rule: asRecordOf(counts.by_rule, (item) => asInt(item) ?? 0),
    },
    reason: asString(root.reason),
    dropped: rawSignals.length - signals.length,
    data_as_of: asString(root.data_as_of) ?? meta.data_as_of,
  }
}

// ---------------------------------------------------------------- 传输

const TIMEOUT_MS = 12000

interface RequestOptions {
  signal?: AbortSignal
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { signal } = options
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
  const abortFromCaller = () => controller.abort()
  signal?.addEventListener('abort', abortFromCaller)
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
        const parsed = (await response.json()) as { detail?: unknown }
        detail = typeof parsed.detail === 'string' ? parsed.detail : null
      } catch {
        detail = null
      }
      throw new ApiError(
        'http',
        `产业接口 ${path} 返回 HTTP ${response.status}${detail ? `：${detail}` : ''}`,
        response.status,
        detail,
      )
    }
    try {
      return (await response.json()) as T
    } catch {
      throw parseError(`产业接口 ${path} 返回的内容不是合法 JSON`)
    }
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (signal?.aborted) throw new ApiError('timeout', `请求已取消：${path}`)
      throw new ApiError('timeout', `产业接口 ${path} 超时（${TIMEOUT_MS}ms）`)
    }
    throw new ApiError('not-connected', `产业接口不可达：${path}`)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abortFromCaller)
  }
}

/** 行业名校验：非空、≤ 40 字；合法名原样返回，非法返回 null。 */
export function normalizeIndustryName(raw: string | undefined | null): string | null {
  const text = String(raw ?? '').trim()
  if (!text || text.length > 40) return null
  return text
}

export const httpIndustrySource: IndustrySource = {
  list: async (signal) => parseIndustryList(await request<unknown>('/api/industry', { signal })),
  detail: async (name, signal) => {
    const path = `/api/industry/${encodeURIComponent(name)}`
    return parseIndustryDetail(await request<unknown>(path, { signal }), name)
  },
  signals: async (query, signal) => {
    const params = new URLSearchParams()
    if (query.industry) params.set('industry', query.industry)
    if (query.rule) params.set('rule', query.rule)
    const suffix = params.toString()
    const path = suffix ? `/api/signals?${suffix}` : '/api/signals'
    return parseSignals(await request<unknown>(path, { signal }))
  },
}

/** 与全站一致的「结论 + 原因 + 代码」文案。 */
export function describeIndustryError(error: unknown): {
  title: string
  detail: string
  code: string | null
} {
  if (error instanceof ApiError) {
    if (error.kind === 'not-connected') {
      return {
        title: '产业接口未接入',
        detail: '先运行 `ldst run` 启动本机 API；产业读取接口（/api/industry/*、/api/signals）由后端提供。',
        code: null,
      }
    }
    if (error.kind === 'timeout') {
      return { title: '产业取数超时', detail: error.message, code: 'TIMEOUT' }
    }
    if (error.kind === 'parse') {
      return { title: '产业响应解析失败', detail: error.message, code: 'E_PARSE' }
    }
    if (error.status === 404) {
      return {
        title: '产业接口未接入',
        detail: error.detail ?? '后端尚未提供该只读接口（HTTP 404）。',
        code: 'HTTP 404',
      }
    }
    return {
      title: '产业取数失败',
      detail: error.detail ?? error.message,
      code: error.status ? `HTTP ${error.status}` : null,
    }
  }
  return {
    title: '产业取数失败',
    detail: error instanceof Error ? error.message : String(error),
    code: null,
  }
}

/** 页面用于判断「接口没接」而不是「页面坏了」。 */
export function isIndustryEndpointMissing(error: unknown): boolean {
  return error instanceof ApiError && error.kind === 'http' && error.status === 404
}

/** 指标路径取值；路径不存在与显式 null 都返回 null（两者在展示上同为「无数据」）。 */
export function metricOf(metrics: Record<string, number | null>, path: string): number | null {
  const value = metrics[path]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}
