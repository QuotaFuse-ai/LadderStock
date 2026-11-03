/**
 * 统一构造「Zustand persist + IndexedDB + 迁移 + 索引上报」的分片 store。
 *
 * 分片 store 必须带 `updatedAt`，索引上报依赖它回答「最后写入时间」。
 * 采用 `skipHydration: true`：启动时先处理 `state reset` 登记，再统一 rehydrate，
 * 避免「刚恢复出旧数据、随后又被 reset 删掉」的竞态。
 */

import { create, type StateCreator } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

import { metaFor } from '../stores/registry'
import { createIdbJSONStorage } from './indexeddb'
import { registerStore, scheduleIndexReport } from './indexReporter'
import { SCHEMA_VERSION, migratePersistedState } from './migrate'

export interface PersistedShell {
  updatedAt: string | null
}

export interface PersistedStoreOptions<T extends PersistedShell> {
  /**
   * 恢复时校验落库内容：返回 null 表示整片不可信，按初始态启动。
   * 持久化不等于信任——越界值不能靠「上次写进去的」绕过入口校验。
   */
  sanitize?: (persisted: unknown) => Partial<T> | null
}

export function createPersistedStore<T extends PersistedShell>(
  storeId: string,
  createState: StateCreator<T>,
  options: PersistedStoreOptions<T> = {},
) {
  const meta = metaFor(storeId)

  // 分片被丢弃时用它兜底：建店后立刻填充，早于任何 rehydrate。
  let fallbackState: T | null = null

  const useStore = create<T>()(
    persist(createState, {
      name: meta.persistKey,
      version: SCHEMA_VERSION,
      storage: createJSONStorage(() => createIdbJSONStorage()),
      skipHydration: true,
      migrate: (persisted, version) =>
        migratePersistedState({
          storeId,
          persistKey: meta.persistKey,
          fromVersion: version,
          persisted,
          // 丢弃分片时的初始态：用当前内存里的初始值，避免 undefined 破坏 merge
          fallback: fallbackState,
        }) as T,
      merge: (persisted, current) => {
        if (!options.sanitize) return { ...current, ...(persisted as T) }
        const clean = options.sanitize(persisted)
        return (clean ? { ...current, ...clean } : current) as T
      },
    }),
  )

  fallbackState = useStore.getState()

  registerStore({
    meta,
    getState: () => useStore.getState() as unknown as Record<string, unknown>,
    rehydrate: () => useStore.persist.rehydrate(),
  })

  useStore.subscribe(() => scheduleIndexReport())

  return useStore
}
