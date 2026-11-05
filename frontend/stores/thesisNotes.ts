/**
 * `thesisNotes` 分片（IndexedDB key: `thesis_notes`，QUOT-452）。
 *
 * 用户的论点、建仓 / 观察理由与裁决点是**私有内容**：只落本机 IndexedDB，
 * 不进后端共享表、不进研究产物、不写日志或 AI 上下文（索引上报只报元数据，不报正文）。
 *
 * 三条不可退让的规则：
 * - 每条论点至少挂一个裁决点；裁决点的判定条件与到期日都不能为空 ——
 *   「到期必须判成立 / 不成立」是这一块存在的意义，空条件会让「到期清单」失效。
 * - 判定只追加不改写：`history` 保留每次结论与判定时间，回看时能看出改判过程。
 * - 多窗口一致沿用 DEC-037：IndexedDB 不产生 `storage` 事件，靠 BroadcastChannel 对齐。
 */

import { localDateIso } from '../src/lib/format'
import { createPersistedStore } from '../persist/persistedStore'

export const THESIS_NOTES_CHANNEL = 'ldst-thesis-notes'
export const THESIS_NOTES_MESSAGE = 'thesis_notes'

export type Verdict = 'pending' | 'met' | 'broken'

export interface VerdictEntry {
  id: string
  verdict: 'met' | 'broken'
  note: string
  /** 判定时间（ISO 8601 带时区），留痕用，不可回填成「今天」 */
  judgedAt: string
}

export interface DecisionPoint {
  id: string
  /** 到期日 `YYYY-MM-DD`，到期未判由界面高亮 */
  due: string
  /** 判定条件：什么算成立、什么算不成立 */
  condition: string
  verdict: Verdict
  judgedAt: string | null
  verdictNote: string
  /** 只追加的判定留痕，最近一次在最前 */
  history: VerdictEntry[]
}

export interface ThesisRecord {
  id: string
  /** 标的代码，本页按当前标的过滤 */
  code: string
  title: string
  body: string
  /** 建仓或观察的理由 */
  reason: string
  createdAt: string
  updatedAt: string
  points: DecisionPoint[]
}

export interface NewThesisInput {
  code: string
  title: string
  body: string
  reason: string
  condition: string
  due: string
}

export interface ThesisPatch {
  title?: string
  body?: string
  reason?: string
}

export interface NewPointInput {
  condition: string
  due: string
}

export interface JudgeInput {
  verdict: 'met' | 'broken'
  note: string
}

export type MutationResult = { ok: true; id: string } | { ok: false; error: string }

export interface ThesisNotesState {
  records: ThesisRecord[]
  updatedAt: string | null
  addThesis: (input: NewThesisInput) => MutationResult
  updateThesis: (id: string, patch: ThesisPatch) => MutationResult
  removeThesis: (id: string) => void
  addPoint: (thesisId: string, input: NewPointInput) => MutationResult
  judgePoint: (thesisId: string, pointId: string, input: JudgeInput) => MutationResult
}

/** 判定结论的展示文案：待判 / 成立 / 不成立（参考库把「不再跟踪」留给后端台账，本机不造）。 */
export const VERDICT_LABEL: Record<Verdict, string> = {
  pending: '待判',
  met: '成立',
  broken: '不成立',
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

/** 严格校验 `YYYY-MM-DD`：格式对但日期不存在（如 2026-02-30）也拒绝。 */
export function isValidDueDate(value: unknown): value is string {
  if (typeof value !== 'string' || !ISO_DATE.test(value)) return false
  const [year, month, day] = value.split('-').map(Number)
  const date = new Date(Date.UTC(year, month - 1, day))
  return (
    date.getUTCFullYear() === year &&
    date.getUTCMonth() === month - 1 &&
    date.getUTCDate() === day
  )
}

/** 距今天几天到期：负数=已逾期，0=今天。按本地日历日算，不判交易日。 */
export function daysPastDue(due: string, today: string = localDateIso()): number {
  const toUtc = (value: string): number => {
    const [year, month, day] = value.split('-').map(Number)
    return Date.UTC(year, month - 1, day)
  }
  return Math.round((toUtc(today) - toUtc(due)) / 86_400_000)
}

/** 到期未判：只统计 `pending`，已判过的不再计入清单。 */
export function overduePoints(
  points: readonly DecisionPoint[],
  today: string = localDateIso(),
): DecisionPoint[] {
  return points.filter((point) => point.verdict === 'pending' && point.due < today)
}

export function overdueCount(
  records: readonly ThesisRecord[],
  today: string = localDateIso(),
): number {
  return records.reduce((total, record) => total + overduePoints(record.points, today).length, 0)
}

function newId(prefix: string): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `${prefix}-${crypto.randomUUID()}`
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
}

// ---------------------------------------------------------------- 恢复校验

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function asIsoOrNull(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function parseHistory(raw: unknown): VerdictEntry[] | null {
  if (raw === undefined) return []
  if (!Array.isArray(raw)) return null
  const entries: VerdictEntry[] = []
  for (const item of raw) {
    if (!isRecord(item)) return null
    const verdict = item.verdict
    if (verdict !== 'met' && verdict !== 'broken') return null
    const judgedAt = asIsoOrNull(item.judgedAt)
    if (!judgedAt) return null
    entries.push({
      id: asText(item.id) || newId('j'),
      verdict,
      note: asText(item.note),
      judgedAt,
    })
  }
  return entries
}

function parsePoint(raw: unknown): DecisionPoint | null {
  if (!isRecord(raw)) return null
  const condition = asText(raw.condition).trim()
  const due = raw.due
  if (!condition || !isValidDueDate(due)) return null
  const verdict: Verdict =
    raw.verdict === 'met' || raw.verdict === 'broken' ? raw.verdict : 'pending'
  const history = parseHistory(raw.history)
  if (history === null) return null
  const judgedAt = asIsoOrNull(raw.judgedAt)
  // 已判但缺判定时间 = 留痕不可信，整片拒绝而不是补一个「今天」
  if (verdict !== 'pending' && !judgedAt) return null
  return {
    id: asText(raw.id) || newId('p'),
    due,
    condition,
    verdict,
    judgedAt: verdict === 'pending' ? null : judgedAt,
    verdictNote: asText(raw.verdictNote),
    history,
  }
}

function parseThesis(raw: unknown): ThesisRecord | null {
  if (!isRecord(raw)) return null
  const code = asText(raw.code).trim()
  const title = asText(raw.title).trim()
  if (!code || !title) return null
  if (!Array.isArray(raw.points) || raw.points.length === 0) return null
  const points: DecisionPoint[] = []
  for (const item of raw.points) {
    const point = parsePoint(item)
    if (!point) return null
    points.push(point)
  }
  const createdAt = asIsoOrNull(raw.createdAt)
  const updatedAt = asIsoOrNull(raw.updatedAt)
  if (!createdAt || !updatedAt) return null
  return {
    id: asText(raw.id) || newId('t'),
    code,
    title,
    body: asText(raw.body),
    reason: asText(raw.reason),
    createdAt,
    updatedAt,
    points,
  }
}

/**
 * 严格解析落库内容（rehydrate 与跨窗口消息共用）。
 * 返回 null = 整片不可信 → 按初始态启动；绝不逐字段补默认值造出「半真」的记录。
 */
export function parseThesisNotesState(
  input: unknown,
): { records: ThesisRecord[]; updatedAt: string | null } | null {
  if (!isRecord(input)) return null
  const records: ThesisRecord[] = []
  if (!Array.isArray(input.records)) return null
  for (const item of input.records) {
    const record = parseThesis(item)
    if (!record) return null
    records.push(record)
  }
  return { records, updatedAt: asIsoOrNull(input.updatedAt) }
}

// ---------------------------------------------------------------- 分片

export const useThesisNotes = createPersistedStore<ThesisNotesState>(
  'thesisNotes',
  (set, get) => ({
    records: [],
    updatedAt: null,
    addThesis: (input) => {
      const code = input.code.trim()
      const title = input.title.trim()
      const condition = input.condition.trim()
      if (!code) return { ok: false, error: '没有标的代码，无法保存论点' }
      if (!title) return { ok: false, error: '论点需要一个标题' }
      if (!condition) {
        return { ok: false, error: '裁决点必须写清判定条件：什么算成立、什么算不成立' }
      }
      if (!isValidDueDate(input.due)) {
        return { ok: false, error: '裁决点必须填有效的到期日（YYYY-MM-DD）' }
      }
      const now = new Date().toISOString()
      const id = newId('t')
      const record: ThesisRecord = {
        id,
        code,
        title,
        body: input.body.trim(),
        reason: input.reason.trim(),
        createdAt: now,
        updatedAt: now,
        points: [
          {
            id: newId('p'),
            due: input.due,
            condition,
            verdict: 'pending',
            judgedAt: null,
            verdictNote: '',
            history: [],
          },
        ],
      }
      set((state) => ({ records: [record, ...state.records], updatedAt: now }))
      return { ok: true, id }
    },
    updateThesis: (id, patch) => {
      const current = get().records.find((record) => record.id === id)
      if (!current) return { ok: false, error: '这条论点已不存在（可能在其他窗口被删除）' }
      const title = patch.title === undefined ? current.title : patch.title.trim()
      if (!title) return { ok: false, error: '论点需要一个标题' }
      const now = new Date().toISOString()
      set((state) => ({
        records: state.records.map((record) =>
          record.id === id
            ? {
                ...record,
                title,
                body: patch.body === undefined ? record.body : patch.body.trim(),
                reason: patch.reason === undefined ? record.reason : patch.reason.trim(),
                updatedAt: now,
              }
            : record,
        ),
        updatedAt: now,
      }))
      return { ok: true, id }
    },
    removeThesis: (id) => {
      const now = new Date().toISOString()
      set((state) => ({
        records: state.records.filter((record) => record.id !== id),
        updatedAt: now,
      }))
    },
    addPoint: (thesisId, input) => {
      const condition = input.condition.trim()
      if (!condition) {
        return { ok: false, error: '裁决点必须写清判定条件：什么算成立、什么算不成立' }
      }
      if (!isValidDueDate(input.due)) {
        return { ok: false, error: '裁决点必须填有效的到期日（YYYY-MM-DD）' }
      }
      if (!get().records.some((record) => record.id === thesisId)) {
        return { ok: false, error: '这条论点已不存在（可能在其他窗口被删除）' }
      }
      const now = new Date().toISOString()
      const pointId = newId('p')
      set((state) => ({
        records: state.records.map((record) =>
          record.id === thesisId
            ? {
                ...record,
                points: [
                  ...record.points,
                  {
                    id: pointId,
                    due: input.due,
                    condition,
                    verdict: 'pending' as const,
                    judgedAt: null,
                    verdictNote: '',
                    history: [],
                  },
                ],
                updatedAt: now,
              }
            : record,
        ),
        updatedAt: now,
      }))
      return { ok: true, id: pointId }
    },
    judgePoint: (thesisId, pointId, input) => {
      if (input.verdict !== 'met' && input.verdict !== 'broken') {
        return { ok: false, error: '先选判定结论：成立或不成立' }
      }
      const thesis = get().records.find((record) => record.id === thesisId)
      if (!thesis) return { ok: false, error: '这条论点已不存在（可能在其他窗口被删除）' }
      if (!thesis.points.some((point) => point.id === pointId)) {
        return { ok: false, error: '这个裁决点已不存在' }
      }
      const now = new Date().toISOString()
      const entry: VerdictEntry = {
        id: newId('j'),
        verdict: input.verdict,
        note: input.note.trim(),
        judgedAt: now,
      }
      set((state) => ({
        records: state.records.map((record) =>
          record.id === thesisId
            ? {
                ...record,
                points: record.points.map((point) =>
                  point.id === pointId
                    ? {
                        ...point,
                        verdict: entry.verdict,
                        judgedAt: entry.judgedAt,
                        verdictNote: entry.note,
                        history: [entry, ...point.history],
                      }
                    : point,
                ),
                updatedAt: now,
              }
            : record,
        ),
        updatedAt: now,
      }))
      return { ok: true, id: entry.id }
    },
  }),
  {
    sanitize: (persisted) => parseThesisNotesState(persisted),
  },
)

// ---------------------------------------------------------------- 多窗口一致

/** 防止「应用远端内容」再被订阅器广播回去（回声）。 */
let applyingRemote = false

/** 应用另一个窗口广播的论点；内容非法或与当前相同则忽略。 */
export function applyRemoteThesisNotes(payload: unknown): boolean {
  if (!isRecord(payload) || payload.type !== THESIS_NOTES_MESSAGE) return false
  const next = parseThesisNotesState(payload)
  if (!next) return false
  const current = useThesisNotes.getState().records
  if (JSON.stringify(current) === JSON.stringify(next.records)) return false
  applyingRemote = true
  try {
    useThesisNotes.setState(next)
  } finally {
    applyingRemote = false
  }
  return true
}

function openSyncChannel(): BroadcastChannel | null {
  if (typeof BroadcastChannel === 'undefined') return null
  try {
    return new BroadcastChannel(THESIS_NOTES_CHANNEL)
  } catch {
    // 宿主没有 BroadcastChannel 时只是失去跨窗口实时联动，数据仍落同一份 IndexedDB
    return null
  }
}

const syncChannel = openSyncChannel()

syncChannel?.addEventListener('message', (event) => {
  applyRemoteThesisNotes(event.data)
})

useThesisNotes.subscribe((state, previous) => {
  if (!syncChannel || applyingRemote) return
  if (state.records === previous.records) return
  syncChannel.postMessage({
    type: THESIS_NOTES_MESSAGE,
    records: state.records,
    updatedAt: state.updatedAt,
  })
})
