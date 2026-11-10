/**
 * StockSenate 白话结论（`plain-v1`）的只读载荷解析（QUOT-537 / 上游 QUOT-536）。
 *
 * 契约来源：QUOT-536 契约评论 + StockSenate `roles/schemas.py:PlainConclusion`。
 * 前端**只消费、不重算**：文本、证据、置信度、风险标识、免责声明全部原样读载荷；
 * 这里做的只有形状校验与缺省补齐，缺字段就返回 `null`，页面据此显示降级态，
 * 绝不用前端文案顶替上游结论。
 *
 * 载荷出现位置（上游契约给的四种出口，逐个找，取第一个能解析的）：
 *   - `plain`                      MCP `stock_senate_analysis` / 研究状态透传
 *   - `decision.plain`            快照 `state.json`
 *   - `sections.plain`            MCP `stock_senate_report_query`
 *   - `summary.plain`             `stock_senate_report_query` 的摘要块
 * 免责声明同层读取：`disclaimer`（缺省再看 `sections.disclaimer`）。
 */

export interface PlainCitation {
  domain: string
  source: string
  data_as_of: string
  detail: string
}

export interface PlainClaim {
  text: string
  evidence: PlainCitation[]
  neutral: boolean
}

export interface PlainGlossaryEntry {
  term: string
  note: string
}

export interface PlainConclusion {
  version: string
  what: PlainClaim
  why: PlainClaim[]
  risks: PlainClaim[]
  confidence: number
  confidence_note: string
  glossary: PlainGlossaryEntry[]
  risk_flags: string[]
  degraded: boolean
  omitted_claims: number
  redline_blocked: number
}

export interface PlainBlock {
  plain: PlainConclusion | null
  disclaimer: string | null
}

const DEFAULT_VERSION = 'plain-v1'

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

function citationOf(value: unknown): PlainCitation | null {
  const record = asRecord(value)
  if (!record) return null
  const domain = text(record.domain)
  const source = text(record.source)
  const dataAsOf = text(record.data_as_of)
  if (!domain || !source || !dataAsOf) return null
  return {
    domain,
    source,
    data_as_of: dataAsOf,
    // `detail` 是可选定位（文件路径 / 接口路径），缺省保持空串而不是编一个
    detail: typeof record.detail === 'string' ? record.detail : '',
  }
}

/**
 * 单条论断。上游 `PlainClaim` 的结构约束是「非中性论断必须至少一条证据」，
 * 违反的条目当作非法数据丢弃——不展示无法溯源的结论。
 */
function claimOf(value: unknown): PlainClaim | null {
  const record = asRecord(value)
  if (!record) return null
  const claimText = text(record.text)
  if (!claimText) return null
  const evidence = Array.isArray(record.evidence)
    ? record.evidence.map(citationOf).filter((item): item is PlainCitation => item !== null)
    : []
  const neutral = record.neutral === true
  if (!neutral && evidence.length === 0) return null
  return { text: claimText, evidence, neutral }
}

function claimsOf(value: unknown): PlainClaim[] {
  if (!Array.isArray(value)) return []
  return value.map(claimOf).filter((item): item is PlainClaim => item !== null)
}

function glossaryOf(value: unknown): PlainGlossaryEntry[] {
  if (!Array.isArray(value)) return []
  const entries: PlainGlossaryEntry[] = []
  for (const item of value) {
    const record = asRecord(item)
    if (!record) continue
    const term = text(record.term)
    const note = text(record.note)
    if (term && note) entries.push({ term, note })
  }
  return entries
}

function countOf(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 0
  return Math.max(0, Math.trunc(value))
}

function confidenceOf(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 0
  return Math.min(1, Math.max(0, value))
}

/** `what` 是契约必填项：它不合法，整块白话结论就不能用。 */
export function parsePlainConclusion(value: unknown): PlainConclusion | null {
  const record = asRecord(value)
  if (!record) return null
  const what = claimOf(record.what)
  if (!what) return null
  const flags = Array.isArray(record.risk_flags)
    ? record.risk_flags.map(text).filter((item): item is string => item !== null)
    : []
  return {
    version: text(record.version) ?? DEFAULT_VERSION,
    what,
    why: claimsOf(record.why),
    risks: claimsOf(record.risks),
    confidence: confidenceOf(record.confidence),
    confidence_note: text(record.confidence_note) ?? '',
    glossary: glossaryOf(record.glossary),
    risk_flags: flags,
    degraded: record.degraded === true,
    omitted_claims: countOf(record.omitted_claims),
    redline_blocked: countOf(record.redline_blocked),
  }
}

function firstPlain(candidates: unknown[]): PlainConclusion | null {
  for (const candidate of candidates) {
    const parsed = parsePlainConclusion(candidate)
    if (parsed) return parsed
  }
  return null
}

/**
 * 从任意一层响应根上找白话块与免责声明。
 *
 * 兼容四段信封（`{payload: {...}}`）：先看根，再看 `payload`，字段位置与上游一致。
 */
export function parsePlainBlock(root: unknown): PlainBlock {
  const record = asRecord(root) ?? {}
  const payload = asRecord(record.payload)
  const layers = payload ? [record, payload] : [record]

  let plain: PlainConclusion | null = null
  for (const layer of layers) {
    plain = firstPlain([
      layer.plain,
      asRecord(layer.decision)?.plain,
      asRecord(layer.sections)?.plain,
      asRecord(layer.summary)?.plain,
    ])
    if (plain) break
  }

  let disclaimer: string | null = null
  for (const layer of layers) {
    disclaimer =
      text(layer.disclaimer) ?? text(asRecord(layer.sections)?.disclaimer) ?? null
    if (disclaimer) break
  }

  return { plain, disclaimer }
}
