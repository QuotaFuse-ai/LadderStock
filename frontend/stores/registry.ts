/**
 * 分片清单（单一事实来源：`stores/manifest.json`）。
 *
 * manifest 同时被 Python 侧 `tests/unit/test_state_contract.py` 读取，
 * 用来校验「前端 store 清单 ↔ config/state.yaml」一一对应；
 * 因此这里的命名、TTL、schema 版本都不能只改一边。
 */

import manifest from './manifest.json'
import { SCHEMA_VERSION } from '../persist/migrate'

export type StoreKind = 'draft' | 'snapshot'

export interface StoreMeta {
  id: string
  /** IndexedDB 里的 key，与 `config/state.yaml` 的 `stores[].name` 完全一致 */
  persistKey: string
  module: string
  kind: StoreKind
  persist: boolean
  ttlSec?: number
  recoverable: boolean
  description: string
}

export const STATE_BACKEND = manifest.backend
export const STATE_SCHEMA_VERSION = manifest.schemaVersion
export const STATE_INDEX_FILE = manifest.indexFile
export const STATE_DATABASE = manifest.database

export const STORE_META_LIST: StoreMeta[] = manifest.stores as StoreMeta[]

export const STORE_META: Record<string, StoreMeta> = Object.fromEntries(
  STORE_META_LIST.map((entry) => [entry.id, entry]),
)

export function metaFor(storeId: string): StoreMeta {
  const meta = STORE_META[storeId]
  if (!meta) throw new Error(`未在 manifest.json 登记的分片：${storeId}`)
  return meta
}

if (manifest.schemaVersion !== SCHEMA_VERSION) {
  // 只提示不抛错：清单与迁移器版本脱节时，测试会先抓住它
  console.error(
    `[state] manifest schemaVersion=v${manifest.schemaVersion} 与迁移器 v${SCHEMA_VERSION} 不一致`,
  )
}
