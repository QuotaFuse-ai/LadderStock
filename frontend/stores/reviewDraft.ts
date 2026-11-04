/**
 * `reviewDraft` 分片（IndexedDB key: `review_draft`）。
 *
 * 收盘复盘草稿：五段式正文全部本地可恢复，刷新 / 崩溃 / 版本升级后原样回填。
 * 指标（情绪温度、晋级率、梯队断层）由程序计算，这里只存用户撰写的叙述。
 *
 * **草稿按交易日分槽**（QUOT-354 BUG-C）：`slots[date]` 各存一份，切日期不覆盖另一天的笔记。
 * 分片里不再存「当前交易日」——那是 URL 的事（`/review?date=`），存进来会让深链被本机状态
 * 反向覆盖（QUOT-354 BUG-B：`?date=11-03` 打开却回填了 11-02 的草稿与日期）。
 */

import { createPersistedStore } from '../persist/persistedStore'

export const REVIEW_SECTIONS = [
  { key: 'sentiment', label: '情绪', hint: '情绪温度、晋级率、梯队断层（指标由程序直出，此处只写叙述）' },
  { key: 'capital', label: '资金', hint: '主力净额、龙虎榜席位与资金结构变化' },
  { key: 'theme', label: '题材', hint: '主线题材强度与轮动、次日延续性判断' },
  { key: 'dragonTiger', label: '龙虎榜', hint: '上榜个股与席位性质，注意与连板梯队交叉验证' },
  { key: 'leader', label: '龙头', hint: '空间板与补涨龙头的取舍，明确次日观察点' },
] as const

export type ReviewSectionKey = (typeof REVIEW_SECTIONS)[number]['key']
export type ReviewSections = Record<ReviewSectionKey, string>

export const EMPTY_SECTIONS: ReviewSections = {
  sentiment: '',
  capital: '',
  theme: '',
  dragonTiger: '',
  leader: '',
}

/** 一个交易日的草稿槽。 */
export interface ReviewDraftSlot {
  sections: ReviewSections
  updatedAt: string | null
}

const EMPTY_SLOT: ReviewDraftSlot = { sections: { ...EMPTY_SECTIONS }, updatedAt: null }

export interface ReviewDraftState {
  /** key = 交易日 `YYYY-MM-DD`。 */
  slots: Record<string, ReviewDraftSlot>
  activeSection: ReviewSectionKey
  /** 全片最后写入时间：`PersistedShell` 的索引上报只认这一个字段。 */
  updatedAt: string | null
  setSection: (date: string, key: ReviewSectionKey, text: string) => void
  setActiveSection: (key: ReviewSectionKey) => void
  /** 只清空某一天的草稿，其它交易日不动。 */
  clearDate: (date: string) => void
}

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/

function isDate(value: unknown): value is string {
  return typeof value === 'string' && DATE_PATTERN.test(value)
}

function isSections(value: unknown): value is ReviewSections {
  if (!value || typeof value !== 'object') return false
  const raw = value as Record<string, unknown>
  return REVIEW_SECTIONS.every((section) => typeof raw[section.key] === 'string')
}

function onlySections(value: ReviewSections): ReviewSections {
  return {
    sentiment: value.sentiment,
    capital: value.capital,
    theme: value.theme,
    dragonTiger: value.dragonTiger,
    leader: value.leader,
  }
}

function asIso(value: unknown): string | null {
  return typeof value === 'string' && !Number.isNaN(Date.parse(value)) ? value : null
}

function localDateOf(iso: string | null): string | null {
  if (!iso) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function normalizeSlot(value: unknown): ReviewDraftSlot | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  if (!isSections(raw.sections)) return null
  return { sections: onlySections(raw.sections), updatedAt: asIso(raw.updatedAt) }
}

/**
 * 旧分片只有一个槽（`tradeDate` + `sections`），跨交易日互相覆盖。
 * 恢复时把它归到自己记录的那个交易日下，不让人丢已有笔记；
 * 归不出日期（既没有 `tradeDate` 也没有可解析的写入时间）才丢弃。
 */
function normalizePersistedSlots(persisted: unknown): Partial<ReviewDraftState> | null {
  if (!persisted || typeof persisted !== 'object') return null
  const raw = persisted as Record<string, unknown>

  const slots: Record<string, ReviewDraftSlot> = {}
  const stored = raw.slots
  if (stored && typeof stored === 'object') {
    Object.entries(stored as Record<string, unknown>).forEach(([date, value]) => {
      const slot = normalizeSlot(value)
      if (isDate(date) && slot) slots[date] = slot
    })
  }

  const legacy = isSections(raw.sections) ? onlySections(raw.sections) : null
  const legacyDate = isDate(raw.tradeDate) ? raw.tradeDate : localDateOf(asIso(raw.updatedAt))
  if (legacy && legacyDate && !slots[legacyDate]) {
    slots[legacyDate] = { sections: legacy, updatedAt: asIso(raw.updatedAt) }
  }

  const keys = REVIEW_SECTIONS.map((section) => section.key)
  return {
    slots,
    activeSection: keys.includes(raw.activeSection as ReviewSectionKey)
      ? (raw.activeSection as ReviewSectionKey)
      : 'sentiment',
    updatedAt: asIso(raw.updatedAt),
  }
}

export const useReviewDraft = createPersistedStore<ReviewDraftState>(
  'reviewDraft',
  (set) => ({
    slots: {},
    activeSection: 'sentiment',
    updatedAt: null,
    setSection: (date, key, text) =>
      set((state) => {
        if (!isDate(date)) return {}
        const at = new Date().toISOString()
        const current = state.slots[date] ?? EMPTY_SLOT
        return {
          slots: {
            ...state.slots,
            [date]: { sections: { ...current.sections, [key]: text }, updatedAt: at },
          },
          updatedAt: at,
        }
      }),
    setActiveSection: (key) => set({ activeSection: key }),
    clearDate: (date) =>
      set((state) => {
        if (!state.slots[date]) return {}
        const next = { ...state.slots }
        delete next[date]
        return { slots: next }
      }),
  }),
  { sanitize: normalizePersistedSlots },
)

/** 某天的草稿槽；没写过就是空槽。 */
export function selectSlot(state: ReviewDraftState, date: string): ReviewDraftSlot {
  return state.slots[date] ?? EMPTY_SLOT
}

/** 已写字数（判定「草稿是否为空」，用于恢复提示）。 */
export function draftCharCount(sections: ReviewSections): number {
  return Object.values(sections).reduce((total, text) => total + text.trim().length, 0)
}
