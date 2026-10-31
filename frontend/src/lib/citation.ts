/**
 * 研究报告 Markdown 的轻量解析（R3 渲染格式见 SPEC-RESEARCH-PROVENANCE §3）。
 *
 * 只认后端会写的几种形态：`#`/`##`/`###` 标题、`-` 列表、`>` 引用、空行分段。
 * 行内只认三类标记：`[n]` 角标、`〔未核实〕`、`**加粗**` / `` `代码` ``。
 *
 * 为什么自己解析而不引 markdown 库：正文由 `citation.annotate()` 程序生成，
 * 语法面窄且稳定；引一个库只会多 20KB，还会把 `[n]` 渲染成普通链接，
 * 丢掉「角标必须可点开 provenance」这条验收。
 */

import type { ProvenanceSlot, ProvenanceSource } from '../api/research'

export type InlineToken =
  | { type: 'text'; text: string }
  | { type: 'citation'; n: number; raw: string }
  | { type: 'unverified' }
  | { type: 'strong'; text: string }
  | { type: 'code'; text: string }

export type MarkdownBlock =
  | { kind: 'heading'; level: 1 | 2 | 3; text: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'list'; items: string[] }
  | { kind: 'quote'; text: string }
  | { kind: 'rule' }

const INLINE_PATTERN = /(\[\d+\]|〔未核实〕|\*\*[^*]+\*\*|`[^`]+`)/g

export function parseInline(text: string): InlineToken[] {
  const tokens: InlineToken[] = []
  let cursor = 0
  for (const match of text.matchAll(INLINE_PATTERN)) {
    const index = match.index ?? 0
    if (index > cursor) tokens.push({ type: 'text', text: text.slice(cursor, index) })
    const raw = match[0]
    if (/^\[\d+\]$/.test(raw)) {
      tokens.push({ type: 'citation', n: Number(raw.slice(1, -1)), raw })
    } else if (raw === '〔未核实〕') {
      tokens.push({ type: 'unverified' })
    } else if (raw.startsWith('**')) {
      tokens.push({ type: 'strong', text: raw.slice(2, -2) })
    } else {
      tokens.push({ type: 'code', text: raw.slice(1, -1) })
    }
    cursor = index + raw.length
  }
  if (cursor < text.length) tokens.push({ type: 'text', text: text.slice(cursor) })
  return tokens
}

export function parseMarkdown(markdown: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = []
  let paragraph: string[] = []
  let list: string[] = []

  const flushParagraph = () => {
    if (paragraph.length === 0) return
    blocks.push({ kind: 'paragraph', text: paragraph.join(' ') })
    paragraph = []
  }
  const flushList = () => {
    if (list.length === 0) return
    blocks.push({ kind: 'list', items: list })
    list = []
  }
  const flushAll = () => {
    flushParagraph()
    flushList()
  }

  for (const rawLine of markdown.split(/\r?\n/)) {
    const line = rawLine.trimEnd()
    if (line.trim() === '') {
      flushAll()
      continue
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(line)
    if (heading) {
      flushAll()
      blocks.push({
        kind: 'heading',
        level: heading[1].length as 1 | 2 | 3,
        text: heading[2].trim(),
      })
      continue
    }
    if (/^\s*[-*]\s+/.test(line)) {
      flushParagraph()
      list.push(line.replace(/^\s*[-*]\s+/, '').trim())
      continue
    }
    if (/^\s*>\s?/.test(line)) {
      flushAll()
      blocks.push({ kind: 'quote', text: line.replace(/^\s*>\s?/, '').trim() })
      continue
    }
    if (/^\s*(---|\*\*\*)\s*$/.test(line)) {
      flushAll()
      blocks.push({ kind: 'rule' })
      continue
    }
    flushList()
    paragraph.push(line.trim())
  }
  flushAll()
  return blocks
}

export interface CitationIndex {
  /** 角标序号 → 来源；只有 `verified=true` 的来源才允许被角标引用。 */
  byNumber: Map<number, ProvenanceSource>
  /** 角标序号 → 原始 id（`S3`），抽屉高亮用。 */
  idByNumber: Map<number, string>
  sources: ProvenanceSource[]
  unverifiedSources: ProvenanceSource[]
  /** 整段兜底：没有任何可引用来源。 */
  empty: boolean
}

/**
 * `sources[]` 的列表序号就是角标 `[n]`（n 从 1 起）。
 * 未核实来源不进入角标索引——正文里该位置由 `〔未核实〕` 兜底。
 */
export function buildCitationIndex(slot: ProvenanceSlot | null | undefined): CitationIndex {
  const sources = slot?.sources ?? []
  const byNumber = new Map<number, ProvenanceSource>()
  const idByNumber = new Map<number, string>()
  const unverifiedSources: ProvenanceSource[] = []
  sources.forEach((source, index) => {
    const number = index + 1
    idByNumber.set(number, source.id)
    if (source.verified) byNumber.set(number, source)
    else unverifiedSources.push(source)
  })
  return {
    byNumber,
    idByNumber,
    sources,
    unverifiedSources,
    empty: byNumber.size === 0,
  }
}

/** 抽屉里展示一条来源的两个落地字段；缺失就是 `—`，不拼假地址。 */
export function sourceLocator(source: ProvenanceSource): string | null {
  return source.endpoint ?? source.url ?? null
}

/** 未核实条目的说明：为什么它不能当依据。 */
export function unverifiedReason(source: ProvenanceSource): string {
  if (source.dataset === 'web') return 'web 来源永远未核实（DEC-027 §2）'
  return '该条目 verified=false，不得作为数字依据'
}
