/**
 * `validationDraft` 分片（IndexedDB key: `validation_draft`）。
 *
 * 明日验证条件草稿：未提交的条件、备注与勾选状态全部本地恢复；
 * 提交对账走 `ldst validate`（M2 之后接入），本分片不做任何上传。
 */

import { createPersistedStore } from '../persist/persistedStore'

export interface ValidationCondition {
  id: string
  code: string
  trigger: string
  note: string
  confirmed: boolean
}

export interface ValidationDraftState {
  tradeDate: string | null
  conditions: ValidationCondition[]
  updatedAt: string | null
  addCondition: (condition?: Partial<Pick<ValidationCondition, 'code' | 'trigger' | 'note'>>) => string
  updateCondition: (id: string, patch: Partial<Omit<ValidationCondition, 'id'>>) => void
  removeCondition: (id: string) => void
  toggleConfirmed: (id: string) => void
  reset: () => void
}

function newConditionId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `c-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
}

export const useValidationDraft = createPersistedStore<ValidationDraftState>(
  'validationDraft',
  (set) => ({
    tradeDate: null,
    conditions: [],
    updatedAt: null,
    addCondition: (condition) => {
      const id = newConditionId()
      set((state) => ({
        conditions: [
          ...state.conditions,
          {
            id,
            code: condition?.code ?? '',
            trigger: condition?.trigger ?? '',
            note: condition?.note ?? '',
            confirmed: false,
          },
        ],
        updatedAt: new Date().toISOString(),
      }))
      return id
    },
    updateCondition: (id, patch) =>
      set((state) => ({
        conditions: state.conditions.map((item) =>
          item.id === id ? { ...item, ...patch } : item,
        ),
        updatedAt: new Date().toISOString(),
      })),
    removeCondition: (id) =>
      set((state) => ({
        conditions: state.conditions.filter((item) => item.id !== id),
        updatedAt: new Date().toISOString(),
      })),
    toggleConfirmed: (id) =>
      set((state) => ({
        conditions: state.conditions.map((item) =>
          item.id === id ? { ...item, confirmed: !item.confirmed } : item,
        ),
        updatedAt: new Date().toISOString(),
      })),
    reset: () => set({ tradeDate: null, conditions: [], updatedAt: null }),
  }),
)

export function pendingConditionCount(state: ValidationDraftState): number {
  return state.conditions.filter((item) => !item.confirmed).length
}
