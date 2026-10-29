/**
 * 持久化运行时状态（不落库）：hydration 进度、迁移日志、索引上报时间。
 * 与业务 store 分开，避免把「运行时」字段写进分片。
 */

import { create } from 'zustand'
import type { MigrationLogEntry } from './migrate'

export type HydrationState = 'pending' | 'ready' | 'error'

interface PersistenceStatus {
  hydration: Record<string, HydrationState>
  errors: Record<string, string>
  migrationLog: MigrationLogEntry[]
  indexReportedAt: string | null
  indexEndpointAvailable: boolean | null
  markHydration: (storeId: string, state: HydrationState, message?: string) => void
  setMigrationLog: (entries: MigrationLogEntry[]) => void
  markIndexReported: (at: string | null) => void
  setIndexEndpoint: (available: boolean | null) => void
}

export const usePersistenceStatus = create<PersistenceStatus>()((set) => ({
  hydration: {},
  errors: {},
  migrationLog: [],
  indexReportedAt: null,
  indexEndpointAvailable: null,
  markHydration: (storeId, state, message) =>
    set((current) => ({
      hydration: { ...current.hydration, [storeId]: state },
      errors:
        state === 'error'
          ? { ...current.errors, [storeId]: message ?? '恢复失败' }
          : current.errors,
    })),
  setMigrationLog: (entries) => set({ migrationLog: entries }),
  markIndexReported: (at) => set({ indexReportedAt: at }),
  setIndexEndpoint: (available) => set({ indexEndpointAvailable: available }),
}))
