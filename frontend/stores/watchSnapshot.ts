/**
 * `watchSnapshot` 分片（IndexedDB key: `watch_snapshot`，TTL 300s）。
 *
 * 行情类快照：可缓存但不可信旧值。超过 TTL、交易日变更或本机时间异常时，
 * `selectFreshness` 会标记 `needsRefetch`，界面据此提示重取并隐藏旧数据。
 */

import { metaFor } from './registry'
import { createPersistedStore } from '../persist/persistedStore'
import {
  evaluateSnapshotFreshness,
  localDateIso,
  type Freshness,
} from '../persist/freshness'

export interface SnapshotRow {
  code: string
  /** 与 `WatchSnapshotRow.name` 对齐：后端状态文件无名称来源，恒为 `null` */
  name: string | null
  limitUpCount: number | null
  changePct: number | null
  status: string | null
}

export interface WatchSnapshotState {
  tradeDate: string | null
  fetchedAt: string | null
  sourceDetail: string | null
  rows: SnapshotRow[]
  updatedAt: string | null
  setSnapshot: (payload: {
    tradeDate?: string | null
    fetchedAt?: string | null
    sourceDetail?: string | null
    rows: SnapshotRow[]
  }) => void
  clear: () => void
}

const META = metaFor('watchSnapshot')
export const WATCH_SNAPSHOT_TTL_SEC = META.ttlSec ?? 300

export const useWatchSnapshot = createPersistedStore<WatchSnapshotState>(
  'watchSnapshot',
  (set) => ({
    tradeDate: null,
    fetchedAt: null,
    sourceDetail: null,
    rows: [],
    updatedAt: null,
    setSnapshot: ({ tradeDate, fetchedAt, sourceDetail, rows }) => {
      const now = new Date().toISOString()
      set({
        tradeDate: tradeDate ?? localDateIso(),
        fetchedAt: fetchedAt ?? now,
        sourceDetail: sourceDetail ?? null,
        rows,
        updatedAt: now,
      })
    },
    clear: () =>
      set({ tradeDate: null, fetchedAt: null, sourceDetail: null, rows: [], updatedAt: null }),
  }),
)

export function selectFreshness(
  state: WatchSnapshotState,
  now: Date = new Date(),
): Freshness {
  return evaluateSnapshotFreshness(
    { fetchedAt: state.fetchedAt, tradeDate: state.tradeDate },
    WATCH_SNAPSHOT_TTL_SEC,
    now,
  )
}

/** 只有新鲜的快照才允许渲染行情数值；过期就当作没有数据。 */
export function selectTrustedRows(
  state: WatchSnapshotState,
  now: Date = new Date(),
): SnapshotRow[] {
  return selectFreshness(state, now).needsRefetch ? [] : state.rows
}
