/**
 * IndexedDB 存储实现（Zustand `persist` 的 StateStorage 后端）。
 *
 * 设计要点（DEC-005 / docs/STATE_PERSISTENCE.md）：
 * - 用 IndexedDB 而不是 localStorage：容量、事务与结构化存储都更可靠；
 * - 每个分片一个 key（`watch_snapshot` / `review_draft` / `validation_draft`），
 *   互不影响，单分片损坏可单独丢弃；
 * - 迁移日志与索引镜像放独立的 `meta` object store，不污染分片数据。
 */

import type { StateStorage } from 'zustand/middleware'

export const DB_NAME = 'ladderstock-state'
export const DB_VERSION = 1
export const KV_STORE = 'kv'
export const META_STORE = 'meta'

/** 单个分片允许的最大体积（字节）。超出即拒绝写入，避免把看板拖垮。 */
export const MAX_SHARD_BYTES = 1024 * 1024

let dbPromise: Promise<IDBDatabase> | null = null

export function openStateDatabase(): Promise<IDBDatabase> {
  if (typeof indexedDB === 'undefined') {
    return Promise.reject(new Error('当前环境没有 IndexedDB（无法持久化草稿）'))
  }
  if (!dbPromise) {
    dbPromise = new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION)
      request.onupgradeneeded = () => {
        const db = request.result
        if (!db.objectStoreNames.contains(KV_STORE)) db.createObjectStore(KV_STORE)
        if (!db.objectStoreNames.contains(META_STORE)) db.createObjectStore(META_STORE)
      }
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error ?? new Error('IndexedDB 打开失败'))
      request.onblocked = () => reject(new Error('IndexedDB 被其他标签页占用，请关闭重复打开的看板'))
    })
  }
  return dbPromise
}

/** 测试与「模拟刷新」用：关闭连接，下一次访问重新打开。 */
export async function closeStateDatabase(): Promise<void> {
  if (!dbPromise) return
  const db = await dbPromise.catch(() => null)
  db?.close()
  dbPromise = null
}

async function withStore<T>(
  storeName: string,
  mode: IDBTransactionMode,
  run: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const db = await openStateDatabase()
  return new Promise<T>((resolve, reject) => {
    const tx = db.transaction(storeName, mode)
    const request = run(tx.objectStore(storeName))
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error ?? new Error('IndexedDB 操作失败'))
    tx.onabort = () => reject(tx.error ?? new Error('IndexedDB 事务被中止'))
  })
}

export function idbGet<T>(storeName: string, key: string): Promise<T | undefined> {
  return withStore<T | undefined>(storeName, 'readonly', (store) => store.get(key) as IDBRequest<T | undefined>)
}

export function idbSet(storeName: string, key: string, value: unknown): Promise<IDBValidKey> {
  return withStore<IDBValidKey>(storeName, 'readwrite', (store) => store.put(value, key))
}

export function idbDelete(storeName: string, key: string): Promise<undefined> {
  return withStore<undefined>(storeName, 'readwrite', (store) => store.delete(key) as IDBRequest<undefined>)
}

/** 枚举 `kv` 白名单之外的全部 key（用于 `checkIntegrity` 与调试）。 */
export async function listShardKeys(): Promise<string[]> {
  const keys = await withStore<IDBValidKey[]>(KV_STORE, 'readonly', (store) => store.getAllKeys())
  return keys.map(String).sort()
}

/** 计数某个 object store 的条目，用于体检/测试断言。 */
export async function countEntries(storeName: string): Promise<number> {
  return withStore<number>(storeName, 'readonly', (store) => store.count())
}

/**
 * Zustand `createJSONStorage` 的后端：同步返回 Promise，读写都是字符串。
 * 写入失败（配额、隐私模式）时抛错，由 persist 的 onRehydrate/订阅侧兜底。
 */
export function createIdbJSONStorage(): StateStorage {
  return {
    getItem: async (name) => {
      const value = await idbGet<string>(KV_STORE, name)
      return typeof value === 'string' ? value : null
    },
    setItem: async (name, value) => {
      const bytes = new TextEncoder().encode(value).byteLength
      if (bytes > MAX_SHARD_BYTES) {
        throw new Error(`分片 ${name} 体积 ${bytes}B 超过上限 ${MAX_SHARD_BYTES}B，已拒绝写入`)
      }
      await idbSet(KV_STORE, name, value)
    },
    removeItem: async (name) => {
      await idbDelete(KV_STORE, name)
    },
  }
}

export const utf8Bytes = (value: string): number => new TextEncoder().encode(value).byteLength
