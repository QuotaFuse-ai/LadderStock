/**
 * `reviewDraft` 分片（IndexedDB key: `review_draft`）。
 *
 * 收盘复盘草稿：五段式正文全部本地可恢复，刷新 / 崩溃 / 版本升级后原样回填。
 * 指标（情绪温度、晋级率、梯队断层）由程序计算，这里只存用户撰写的叙述。
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

export interface ReviewDraftState {
  tradeDate: string | null
  sections: ReviewSections
  activeSection: ReviewSectionKey
  updatedAt: string | null
  setTradeDate: (date: string | null) => void
  setSection: (key: ReviewSectionKey, text: string) => void
  setActiveSection: (key: ReviewSectionKey) => void
  reset: () => void
}

export const useReviewDraft = createPersistedStore<ReviewDraftState>('reviewDraft', (set) => ({
  tradeDate: null,
  sections: { ...EMPTY_SECTIONS },
  activeSection: 'sentiment',
  updatedAt: null,
  setTradeDate: (date) => set({ tradeDate: date, updatedAt: new Date().toISOString() }),
  setSection: (key, text) =>
    set((state) => ({
      sections: { ...state.sections, [key]: text },
      updatedAt: new Date().toISOString(),
    })),
  setActiveSection: (key) => set({ activeSection: key }),
  reset: () =>
    set({
      tradeDate: null,
      sections: { ...EMPTY_SECTIONS },
      activeSection: 'sentiment',
      updatedAt: null,
    }),
}))

/** 已写字数（判定「草稿是否为空」，用于恢复提示）。 */
export function draftCharCount(state: ReviewDraftState): number {
  return Object.values(state.sections).reduce((total, text) => total + text.trim().length, 0)
}
