/**
 * 状态索引上报：让 Python 侧 `ldst state inspect` 能看到浏览器里的分片概况。
 *
 * 进程边界（任务要求）：Python 不读 IndexedDB。前端在每次持久化后把
 * 「分片清单 + 版本 + 最后写入时间 + 体积 + 是否需重取」写到
 * `data/state/frontend-index.json`；开发态由 Vite 中间件落盘
 * （见 `vite.config.ts` 的 `ladderstock-state-index` 插件）。
 *
 * 反向通道（`ldst state reset`）：命令只登记 `pending_reset`，
 * 前端下次启动读取该登记、删除对应 IndexedDB key，再回执清除结果。
 */

import { KV_STORE, idbDelete, utf8Bytes } from './indexeddb'
import { SCHEMA_VERSION } from './migrate'
import { evaluateSnapshotFreshness, type RefetchReason } from './freshness'
import { usePersistenceStatus } from './status'
import {
  STATE_BACKEND,
  STORE_META_LIST,
  type StoreMeta,
} from '../stores/registry'

export const INDEX_ENDPOINT = '/__state/index'

export interface RegisteredStore {
  meta: StoreMeta
  getState: () => Record<string, unknown>
  rehydrate: () => Promise<void> | void
}

export interface IndexEntry {
  store_id: string
  kind: StoreMeta['kind']
  schema_version: number
  updated_at: string | null
  bytes: number
  recoverable: boolean
  needs_refetch: boolean
  needs_refetch_reason: RefetchReason | null
  ttl_sec: number | null
}

export interface IndexSnapshot {
  backend: string
  schema_version: number
  generated_at: string
  stores: Record<string, IndexEntry>
}

const registry = new Map<string, RegisteredStore>()

export function registerStore(handle: RegisteredStore): void {
  registry.set(handle.meta.id, handle)
}

export function registeredStores(): RegisteredStore[] {
  return [...registry.values()]
}

function asIsoOrNull(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function indexEntry(meta: StoreMeta, state: Record<string, unknown>, now: Date): IndexEntry {
  const updatedAt = asIsoOrNull(state.updatedAt) ?? asIsoOrNull(state.fetchedAt)
  const persistedShape = { state, version: SCHEMA_VERSION }
  const bytes = utf8Bytes(JSON.stringify(persistedShape))

  let needsRefetch = false
  let reason: RefetchReason | null = null
  if (meta.kind === 'snapshot') {
    const freshness = evaluateSnapshotFreshness(
      { fetchedAt: asIsoOrNull(state.fetchedAt), tradeDate: asIsoOrNull(state.tradeDate) },
      meta.ttlSec ?? 300,
      now,
    )
    needsRefetch = freshness.needsRefetch
    reason = freshness.reason
  }

  return {
    store_id: meta.id,
    kind: meta.kind,
    schema_version: SCHEMA_VERSION,
    updated_at: updatedAt,
    bytes,
    recoverable: meta.recoverable,
    needs_refetch: needsRefetch,
    needs_refetch_reason: reason,
    ttl_sec: meta.ttlSec ?? null,
  }
}

export function buildIndexSnapshot(now: Date = new Date()): IndexSnapshot {
  const stores: Record<string, IndexEntry> = {}
  for (const handle of registeredStores()) {
    stores[handle.meta.persistKey] = indexEntry(handle.meta, handle.getState(), now)
  }
  // 尚未加载的（理论上不存在）分片也如实列出，避免清单漂移被掩盖
  for (const meta of STORE_META_LIST) {
    if (!stores[meta.persistKey]) {
      stores[meta.persistKey] = indexEntry(meta, {}, now)
    }
  }
  return {
    backend: STATE_BACKEND,
    schema_version: SCHEMA_VERSION,
    generated_at: now.toISOString(),
    stores,
  }
}

function canReachDevServer(): boolean {
  return typeof window !== 'undefined' && typeof fetch === 'function'
}

async function postIndex(payload: IndexSnapshot, ackReset: string[] = []): Promise<boolean> {
  if (!canReachDevServer()) return false
  try {
    const response = await fetch(INDEX_ENDPOINT, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ...payload, ack_reset: ackReset }),
    })
    return response.ok
  } catch {
    return false
  }
}

export async function reportIndexNow(): Promise<boolean> {
  const snapshot = buildIndexSnapshot()
  const ok = await postIndex(snapshot)
  const status = usePersistenceStatus.getState()
  status.markIndexReported(ok ? snapshot.generated_at : null)
  if (canReachDevServer()) status.setIndexEndpoint(ok)
  return ok
}

let timer: ReturnType<typeof setTimeout> | null = null

/** 持久化写入后合并上报，避免每次按键都写盘。 */
export function scheduleIndexReport(delayMs = 400): void {
  if (!canReachDevServer()) return
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => {
    timer = null
    void reportIndexNow()
  }, delayMs)
}

interface PendingReset {
  store?: string
  store_id?: string
  requested_at?: string
}

/**
 * 读取 `pending_reset` 登记并清除对应分片，返回已清除的 key。
 * 任何一步失败都只是「本次没清掉」，不阻塞启动，下次启动会重试。
 */
export async function syncPendingResets(): Promise<string[]> {
  if (!canReachDevServer()) return []
  let payload: { pending_reset?: PendingReset[] } | null = null
  try {
    const response = await fetch(INDEX_ENDPOINT, { headers: { accept: 'application/json' } })
    if (!response.ok) {
      usePersistenceStatus.getState().setIndexEndpoint(false)
      return []
    }
    payload = (await response.json()) as { pending_reset?: PendingReset[] }
    usePersistenceStatus.getState().setIndexEndpoint(true)
  } catch {
    usePersistenceStatus.getState().setIndexEndpoint(false)
    return []
  }

  const known = new Set(STORE_META_LIST.map((meta) => meta.persistKey))
  const pending = Array.isArray(payload?.pending_reset) ? payload.pending_reset : []
  const acked: string[] = []
  for (const item of pending) {
    const persistKey = typeof item?.store === 'string' ? item.store : null
    // 白名单：只允许清除 manifest 登记过的分片
    if (!persistKey || !known.has(persistKey)) continue
    try {
      await idbDelete(KV_STORE, persistKey)
      acked.push(persistKey)
    } catch {
      /* 下次启动重试 */
    }
  }
  if (acked.length > 0) {
    await postIndex(buildIndexSnapshot(), acked)
  } else {
    await reportIndexNow()
  }
  return acked
}
