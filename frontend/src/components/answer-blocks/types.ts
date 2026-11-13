/**
 * 答案块协议（任务书 §6.3）在前端的类型与**形状校验**。
 *
 * 与 `api/plainConclusion.ts` 同一套原则：前端只消费、不重算。`value` 是数字就是
 * 数字，不替后端补 0、不把「38.62 元」拆回数值；载荷不合法的那一项**整条丢弃**，
 * 而不是渲染一个半成品格子。
 *
 * `blocks` 由服务端拼装（§7），顺序与数量也是服务端的事（§6.4）；这里只按
 * 「块类型」过一遍形状，把能渲染的挑出来。
 */

export type ConclusionTone = 'neutral' | 'positive' | 'negative' | 'warning'
export type MetricDirection = 'up' | 'down' | 'flat'
export type ChartKind = 'line' | 'bar' | 'area'

export interface ConclusionBlockPayload {
  text: string
  tone: ConclusionTone
  /** 0–1；载荷没给时为 `null`，如实显示「未提供」而不是猜一个 */
  confidence: number | null
}

export interface MetricItem {
  label: string
  value: number
  unit?: string
  direction?: MetricDirection
  as_of?: string
}

export interface MetricBlockPayload {
  items: MetricItem[]
}

export interface ChartSeries {
  name: string
  points: Array<[string, number]>
}

export interface ChartBlockPayload {
  kind: ChartKind
  x_axis: string
  y_unit: string
  series: ChartSeries[]
  caption: string
}

export interface TableBlockPayload {
  columns: string[]
  rows: Array<Array<string | number>>
  /** 真实总行数；载荷没给时按当前行数算 */
  rows_total: number
  /** 载荷说「已折叠」且确实还有没给的行，才允许展开 */
  collapsed: boolean
  caption: string
}

export interface CitationItem {
  name: string
  fetched_at: string
  /** 契约里可缺省（后端 `validate_citation` 只要求 name + fetched_at） */
  url: string | null
  /** 契约允许为空：缺失时来源行显示 `—` */
  symbol: string | null
  as_of: string | null
}

export interface CitationBlockPayload {
  items: CitationItem[]
}

export interface NoticeBlockPayload {
  /**
   * 取值域跟 `AiErrorNotice` 的 kind 一致，外加本阶段新增的 `missing_data`。
   * 故意用 `string` 而不是联合类型：服务端多一个未知 kind 时，前端应降级，
   * 而不是整块编译不过。
   */
  kind: string
  section?: string
  message?: string
}

export type ActionKey = 'open_screen'

export interface ActionItem {
  label: string
  action: ActionKey
  target: string
}

export interface ActionBlockPayload {
  items: ActionItem[]
}

interface BlockBase {
  /** 服务端给块 id 时用作 React key；没有则按位置 */
  id?: string
}

export type AnswerBlock = BlockBase &
  (
    | { type: 'conclusion'; payload: ConclusionBlockPayload }
    | { type: 'metric'; payload: MetricBlockPayload }
    | { type: 'chart'; payload: ChartBlockPayload }
    | { type: 'table'; payload: TableBlockPayload }
    | { type: 'citation'; payload: CitationBlockPayload }
    | { type: 'notice'; payload: NoticeBlockPayload }
    | { type: 'action'; payload: ActionBlockPayload }
  )

/** 载荷结构不合法时的兜底：块照样占位，但明确说「这块没画出来」，不静默消失。 */
export interface InvalidBlock {
  type: 'invalid'
  reason: string
}

export type RenderedBlock = AnswerBlock | InvalidBlock

export const BLOCK_TYPES = ['conclusion', 'metric', 'chart', 'table', 'citation', 'notice', 'action'] as const

export type BlockType = (typeof BLOCK_TYPES)[number]

/** §6.3 `points ≤ 120`：超过说明服务端没降采样，前端截断并如实说明，不静默画一条不完整的线 */
export const MAX_CHART_POINTS = 120
/** §6.3 `metric.items` 2–4 个 */
export const MAX_METRIC_ITEMS = 4
/** §8.3 表格超过 5 行折叠 */
export const TABLE_COLLAPSE_ROWS = 5
/** 二次传播要求支撑 100 行表格 */
export const MAX_TABLE_ROWS = 100
/** §6.4 一次回答最多 6 块；与后端 `src/ladderstock/ai/answer_blocks.py` 的 MAX_BLOCKS（PI 拍板 6）保持一致，不许两套数 */
export const MAX_BLOCKS = 6
/** 后端 QUOT-581 提交 34f708e3 拍板的同一上限；测试用它断言前端没有漂移 */
export const BACKEND_MAX_BLOCKS = 6

const CONCLUSION_TONES: readonly ConclusionTone[] = ['neutral', 'positive', 'negative', 'warning']
const METRIC_DIRECTIONS: readonly MetricDirection[] = ['up', 'down', 'flat']
const CHART_KINDS: readonly ChartKind[] = ['line', 'bar', 'area']

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function text(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed === '' ? null : trimmed
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function conclusionPayload(raw: unknown): ConclusionBlockPayload | InvalidBlock {
  const record = asRecord(raw)
  const body = record && text(record.text)
  if (!record || !body) return { type: 'invalid', reason: '结论块缺少正文' }
  const tone = CONCLUSION_TONES.includes(record.tone as ConclusionTone)
    ? (record.tone as ConclusionTone)
    : 'neutral'
  const confidence = finite(record.confidence)
  return {
    text: body,
    tone,
    confidence: confidence === null ? null : Math.min(1, Math.max(0, confidence)),
  }
}

function metricPayload(raw: unknown): MetricBlockPayload | InvalidBlock {
  const record = asRecord(raw)
  const list = record?.items
  if (!Array.isArray(list)) return { type: 'invalid', reason: '指标块缺少 items' }
  const items: MetricItem[] = []
  for (const entry of list) {
    const item = asRecord(entry)
    const label = item && text(item.label)
    const value = item && finite(item.value)
    if (!item || !label || value === null) continue
    items.push({
      label,
      value,
      unit: text(item.unit) ?? undefined,
      direction: METRIC_DIRECTIONS.includes(item.direction as MetricDirection)
        ? (item.direction as MetricDirection)
        : undefined,
      as_of: text(item.as_of) ?? undefined,
    })
  }
  if (items.length === 0) return { type: 'invalid', reason: '指标块没有可展示的数值' }
  return { items: items.slice(0, MAX_METRIC_ITEMS) }
}

function chartPayload(raw: unknown): ChartBlockPayload | InvalidBlock {
  const record = asRecord(raw)
  if (!record) return { type: 'invalid', reason: '图表块载荷不是对象' }
  const series: ChartSeries[] = []
  for (const entry of Array.isArray(record.series) ? record.series : []) {
    const item = asRecord(entry)
    const name = item && text(item.name)
    if (!item || !name || !Array.isArray(item.points)) continue
    const points: Array<[string, number]> = []
    for (const point of item.points) {
      if (!Array.isArray(point) || point.length < 2) continue
      const label = text(point[0])
      const value = finite(point[1])
      if (label === null || value === null) continue
      points.push([label, value])
    }
    if (points.length === 0) continue
    series.push({ name, points: points.slice(0, MAX_CHART_POINTS) })
  }
  if (series.length === 0) return { type: 'invalid', reason: '图表块没有可画的序列' }
  return {
    kind: CHART_KINDS.includes(record.kind as ChartKind) ? (record.kind as ChartKind) : 'line',
    x_axis: text(record.x_axis) ?? '',
    y_unit: text(record.y_unit) ?? '',
    series,
    caption: text(record.caption) ?? '',
  }
}

function tablePayload(raw: unknown): TableBlockPayload | InvalidBlock {
  const record = asRecord(raw)
  const columns = Array.isArray(record?.columns)
    ? record.columns.map(text).filter((item): item is string => item !== null).slice(0, 12)
    : []
  if (columns.length === 0) return { type: 'invalid', reason: '表格块缺少列定义' }
  const rows: Array<Array<string | number>> = []
  for (const entry of Array.isArray(record?.rows) ? record.rows : []) {
    if (!Array.isArray(entry)) continue
    const cells = entry
      .slice(0, columns.length)
      .map((cell) => (typeof cell === 'number' && Number.isFinite(cell) ? cell : text(cell) ?? ''))
    if (cells.length === 0) continue
    rows.push(cells)
  }
  if (rows.length === 0) return { type: 'invalid', reason: '表格块没有数据行' }
  const total = finite(record?.rows_total)
  return {
    columns,
    rows: rows.slice(0, MAX_TABLE_ROWS),
    rows_total: total === null ? rows.length : Math.max(Math.trunc(total), rows.length),
    collapsed: record?.collapsed === true,
    caption: text(record?.caption) ?? '',
  }
}

function citationPayload(raw: unknown): CitationBlockPayload | InvalidBlock {
  const record = asRecord(raw)
  const list = record?.items
  if (!Array.isArray(list)) return { type: 'invalid', reason: '来源块缺少 items' }
  const items: CitationItem[] = []
  for (const entry of list) {
    const item = asRecord(entry)
    const name = item && text(item.name)
    const fetchedAt = item && text(item.fetched_at)
    if (!item || !name || !fetchedAt) continue
    // url / symbol / as_of 后端都可缺省；缺了照常展示（不可点 / 显示 `—`），不整行丢弃
    items.push({
      name,
      fetched_at: fetchedAt,
      url: text(item.url),
      symbol: text(item.symbol),
      as_of: text(item.as_of),
    })
  }
  if (items.length === 0) return { type: 'invalid', reason: '来源块没有可跳转的来源' }
  return { items }
}

function noticePayload(raw: unknown): NoticeBlockPayload | InvalidBlock {
  const record = asRecord(raw)
  const kind = record && text(record.kind)
  if (!record || !kind) return { type: 'invalid', reason: '提示块缺少 kind' }
  return {
    kind,
    section: text(record.section) ?? undefined,
    message: text(record.message) ?? undefined,
  }
}

function actionPayload(raw: unknown): ActionBlockPayload | InvalidBlock {
  const record = asRecord(raw)
  const list = record?.items
  if (!Array.isArray(list)) return { type: 'invalid', reason: '动作块缺少 items' }
  const items: ActionItem[] = []
  for (const entry of list) {
    const item = asRecord(entry)
    const label = item && text(item.label)
    const target = item && text(item.target)
    // §7 红线 7：只认固定的 open_screen 动作；target 的合法性由 `BlockRenderer` 的导航口把关
    if (!item || !label || !target || item.action !== 'open_screen') continue
    items.push({ label, action: 'open_screen', target })
  }
  if (items.length === 0) return { type: 'invalid', reason: '动作块没有可用的跳转' }
  return { items }
}

const PARSERS = {
  conclusion: conclusionPayload,
  metric: metricPayload,
  chart: chartPayload,
  table: tablePayload,
  citation: citationPayload,
  notice: noticePayload,
  action: actionPayload,
} as const

/** 单个块的「类型 → 载荷」校验；`type` 不认识时返回 `invalid`，页面据此显示未识别块。 */
export function parseBlock(raw: unknown): RenderedBlock {
  const record = asRecord(raw)
  if (!record) return { type: 'invalid', reason: '答案块不是对象' }
  const type = record.type
  if (typeof type !== 'string' || !(type in PARSERS)) {
    return { type: 'invalid', reason: typeof type === 'string' ? `未识别的答案块类型「${type}」` : '答案块缺少 type' }
  }
  const parsed = PARSERS[type as BlockType](record.payload)
  if ((parsed as InvalidBlock).type === 'invalid') return parsed as InvalidBlock
  return mapBlock(type as BlockType, parsed, text(record.id) ?? undefined)
}

function mapBlock(type: BlockType, payload: object, id: string | undefined): AnswerBlock {
  return { type, payload, id } as AnswerBlock
}

/** 服务端已排好序（§6.4），这里只截到 8 块，不重排。 */
export function parseBlocks(raw: unknown): RenderedBlock[] {
  if (!Array.isArray(raw)) return []
  return raw.slice(0, MAX_BLOCKS).map(parseBlock)
}
