/**
 * 两次研究产物的逐段对比（QUOT-453）。
 *
 * 参考库是 `lib/reportDiff.ts`：按章节逐行比，并区分「改了 / 新增 / 删掉 / 没变」。
 * 这里只做**展示层**的语义归并，不改任何数字、不重算比率：
 * - 段落按 `## ` 标题切分，标题相同才算同一节；
 * - 行级差异用 LCS（最长公共子序列），每节超过 `MAX_SECTION_LINES` 行时只比前 N 行
 *   并如实标注截断，不假装比过整节；
 * - 关键数据由调用方先格式化成字符串（`formatFactValue`），本模块只比较展示值，
 *   所以「改了什么」与页面上的读数永远一致。
 */

export const MAX_SECTION_LINES = 600

export type DiffKind = 'same' | 'changed' | 'added' | 'removed'

export interface LineOp {
  kind: 'same' | 'add' | 'remove'
  text: string
}

export interface DocSection {
  title: string
  lines: string[]
}

export interface SectionDiff {
  title: string
  kind: DiffKind
  ops: LineOp[]
  added: number
  removed: number
  truncated: boolean
}

export interface ValueDiff {
  label: string
  before: string | null
  after: string | null
  kind: 'changed' | 'added' | 'removed'
}

export interface StageSnapshot {
  name: string
  title: string
  status: string
  markdown: string | null
  /** 已格式化的关键数据：标签 → 展示值 */
  values: Record<string, string>
}

export interface StageDiff {
  name: string
  title: string
  kind: DiffKind
  statusBefore: string | null
  statusAfter: string | null
  sections: SectionDiff[]
  values: ValueDiff[]
  /** 这一侧没有产物 / 状态变化等需要直说的事实 */
  notes: string[]
}

export interface DiffSummary {
  stages: number
  changed: number
  added: number
  removed: number
  same: number
  sectionChanged: number
  valueChanged: number
}

/** `## ` 标题切段；标题前的导语归到空标题段（参考库同款）。 */
export function splitSections(markdown: string | null | undefined): DocSection[] {
  const text = String(markdown ?? '').replace(/\r\n?/g, '\n')
  if (!text.trim()) return []
  const sections: DocSection[] = []
  let current: DocSection = { title: '', lines: [] }
  for (const raw of text.split('\n')) {
    const heading = /^##\s+(.+?)\s*$/.exec(raw)
    if (heading) {
      if (current.title || current.lines.some((line) => line.trim())) sections.push(current)
      current = { title: heading[1].trim(), lines: [] }
      continue
    }
    current.lines.push(raw.replace(/\s+$/, ''))
  }
  if (current.title || current.lines.some((line) => line.trim())) sections.push(current)
  return sections
}

function trimBlankEdges(lines: string[]): string[] {
  let start = 0
  let end = lines.length
  while (start < end && !lines[start].trim()) start += 1
  while (end > start && !lines[end - 1].trim()) end -= 1
  return lines.slice(start, end)
}

interface LineDiff {
  ops: LineOp[]
  added: number
  removed: number
  truncated: boolean
}

/** LCS 行差异：同一行文本才算「没变」，其余按增 / 删成对给出。 */
export function diffLines(
  before: string[],
  after: string[],
  limit = MAX_SECTION_LINES,
): LineDiff {
  const a = trimBlankEdges(before)
  const b = trimBlankEdges(after)
  const truncated = a.length > limit || b.length > limit
  const left = truncated ? a.slice(0, limit) : a
  const right = truncated ? b.slice(0, limit) : b

  const rows = left.length
  const cols = right.length
  if (rows === 0 && cols === 0) return { ops: [], added: 0, removed: 0, truncated: false }
  if (rows === 0) {
    return {
      ops: right.map((text) => ({ kind: 'add', text })),
      added: right.length,
      removed: 0,
      truncated,
    }
  }
  if (cols === 0) {
    return {
      ops: left.map((text) => ({ kind: 'remove', text })),
      added: 0,
      removed: left.length,
      truncated,
    }
  }

  // lcs[i][j] = left[i:] 与 right[j:] 的最长公共子序列长度
  const width = cols + 1
  const table = new Uint32Array((rows + 1) * width)
  for (let i = rows - 1; i >= 0; i -= 1) {
    for (let j = cols - 1; j >= 0; j -= 1) {
      table[i * width + j] =
        left[i] === right[j]
          ? table[(i + 1) * width + j + 1] + 1
          : Math.max(table[(i + 1) * width + j], table[i * width + j + 1])
    }
  }

  const ops: LineOp[] = []
  let added = 0
  let removed = 0
  let i = 0
  let j = 0
  while (i < rows && j < cols) {
    if (left[i] === right[j]) {
      ops.push({ kind: 'same', text: left[i] })
      i += 1
      j += 1
    } else if (table[(i + 1) * width + j] >= table[i * width + j + 1]) {
      ops.push({ kind: 'remove', text: left[i] })
      removed += 1
      i += 1
    } else {
      ops.push({ kind: 'add', text: right[j] })
      added += 1
      j += 1
    }
  }
  while (i < rows) {
    ops.push({ kind: 'remove', text: left[i] })
    removed += 1
    i += 1
  }
  while (j < cols) {
    ops.push({ kind: 'add', text: right[j] })
    added += 1
    j += 1
  }
  return { ops, added, removed, truncated }
}

/** 同一节的两种文本 → 一条段落差异；标题不同视为「删掉 + 新增」。 */
export function diffSections(
  beforeMarkdown: string | null,
  afterMarkdown: string | null,
): SectionDiff[] {
  const before = splitSections(beforeMarkdown)
  const after = splitSections(afterMarkdown)
  const byTitle = new Map<string, { before?: DocSection; after?: DocSection }>()
  const order: string[] = []
  for (const section of before) {
    const key = section.title
    if (!byTitle.has(key)) {
      byTitle.set(key, {})
      order.push(key)
    }
    byTitle.get(key)!.before = section
  }
  for (const section of after) {
    const key = section.title
    if (!byTitle.has(key)) {
      byTitle.set(key, {})
      order.push(key)
    }
    if (!byTitle.get(key)!.after) byTitle.get(key)!.after = section
  }

  const out: SectionDiff[] = []
  for (const title of order) {
    const pair = byTitle.get(title)!
    const kind: DiffKind = !pair.before ? 'added' : !pair.after ? 'removed' : 'changed'
    const lineDiff =
      kind === 'added'
        ? diffLines([], pair.after?.lines ?? [])
        : kind === 'removed'
          ? diffLines(pair.before?.lines ?? [], [])
          : diffLines(pair.before?.lines ?? [], pair.after?.lines ?? [])
    out.push({
      title: title || '（导语）',
      kind:
        kind === 'changed' && lineDiff.added === 0 && lineDiff.removed === 0 ? 'same' : kind,
      ops: lineDiff.ops,
      added: lineDiff.added,
      removed: lineDiff.removed,
      truncated: lineDiff.truncated,
    })
  }
  return out
}

/** 已格式化展示值的关键数据对比：缺一侧 = 新增 / 消失，值不同 = 改了。 */
export function diffValues(
  before: Record<string, string>,
  after: Record<string, string>,
): ValueDiff[] {
  const labels = Array.from(new Set([...Object.keys(before), ...Object.keys(after)]))
  const out: ValueDiff[] = []
  for (const label of labels) {
    const left = before[label] ?? null
    const right = after[label] ?? null
    if (left === null && right === null) continue
    if (left === right) continue
    out.push({
      label,
      before: left,
      after: right,
      kind: left === null ? 'added' : right === null ? 'removed' : 'changed',
    })
  }
  return out.sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))
}

const STAGE_KIND_LABEL: Record<Exclude<DiffKind, 'same'>, string> = {
  changed: '有变化',
  added: '本次新增',
  removed: '本次没有',
}

function stageMissingNote(side: 'before' | 'after'): string {
  const when = side === 'before' ? '旧的那次' : '新的那次'
  return `${when}没有这一段产物，整段按${side === 'before' ? '新增' : '消失'}处理。`
}

/** 一段（或缺失的一侧）→ 可渲染的差异；两段都缺时不产出。 */
export function diffStage(
  before: StageSnapshot | null,
  after: StageSnapshot | null,
): StageDiff | null {
  if (!before && !after) return null
  const name = (after ?? before)!.name
  const title = (after ?? before)!.title
  const notes: string[] = []
  if (!before) notes.push(stageMissingNote('before'))
  if (!after) notes.push(stageMissingNote('after'))

  const sections = diffSections(before?.markdown ?? null, after?.markdown ?? null)
  const values = diffValues(before?.values ?? {}, after?.values ?? {})
  const statusBefore = before?.status ?? null
  const statusAfter = after?.status ?? null
  if (statusBefore && statusAfter && statusBefore !== statusAfter) {
    notes.push(`状态从 ${statusBefore} 变为 ${statusAfter}。`)
  }

  let kind: DiffKind
  if (!before) kind = 'added'
  else if (!after) kind = 'removed'
  else if (
    sections.some((section) => section.kind !== 'same') ||
    values.length > 0 ||
    statusBefore !== statusAfter
  ) {
    kind = 'changed'
  } else {
    kind = 'same'
  }
  return { name, title, kind, statusBefore, statusAfter, sections, values, notes }
}

export function describeStageKind(kind: DiffKind): string {
  if (kind === 'same') return '没变'
  return STAGE_KIND_LABEL[kind]
}

export function summarizeStages(stages: StageDiff[]): DiffSummary {
  const summary: DiffSummary = {
    stages: stages.length,
    changed: 0,
    added: 0,
    removed: 0,
    same: 0,
    sectionChanged: 0,
    valueChanged: 0,
  }
  for (const stage of stages) {
    if (stage.kind === 'changed') summary.changed += 1
    else if (stage.kind === 'added') summary.added += 1
    else if (stage.kind === 'removed') summary.removed += 1
    else summary.same += 1
    summary.sectionChanged += stage.sections.filter((section) => section.kind !== 'same').length
    summary.valueChanged += stage.values.length
  }
  return summary
}
