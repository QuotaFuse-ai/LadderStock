import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  api,
  isApiError,
  isEndpointMissing,
  type JournalStatsPayload,
  type JournalStatsQuery,
} from '../../api'
import { KV_STORE, idbGet, idbSet } from '../../../persist/indexeddb'
import { resolveState, useDemoEnabled, useForcedState, type PageState } from '../../lib/pageState'
import { useAsync } from '../../lib/useAsync'
import { DEMO_JOURNAL_FETCHED_AT, demoJournalStatsSource, type JournalDemoVariant } from './demo'
/**
 * 交易日志的取数 / 状态推导（与页面 DOM 解耦，便于测试；模板同 `limitup/useLimitUpView.ts`）。
 *
 * - 演示源只有一个入口口径：`lib/pageState.ts` 的 `useDemoEnabled()`（URL `?demo=1` 或设置开关）。
 * - **真实请求 404 / 断连时进空态 / 离线态，绝不回落到 `demo.ts`**。
 * - 演示态**不碰用户的 IndexedDB**：既不读缓存回放，也不把演示数字写进缓存
 *   （否则关掉开关后，屏幕上留着的还是那份夹具）。
 */

const VARIANTS: readonly JournalDemoVariant[] = ['full', 'partial', 'empty']

function isDemoVariant(value: unknown): value is JournalDemoVariant {
  return typeof value === 'string' && (VARIANTS as readonly string[]).includes(value)
}

/** §5.6 的演示开关判定只有一处真源；这里保留屏侧惯用名。 */
export function useJournalDemoEnabled(): boolean {
  return useDemoEnabled()
}

/** 显式 `?variant=` 优先，其次由 `?state=` 推导（partial / empty 各演示一种降级）。 */
export function useJournalDemoVariant(): JournalDemoVariant {
  const [params] = useSearchParams()
  const explicit = params.get('variant')
  if (isDemoVariant(explicit)) return explicit
  const forced = useForcedState()
  if (forced === 'partial') return 'partial'
  if (forced === 'empty') return 'empty'
  return 'full'
}

/** 与 `api.journalStats` / `demoJournalStatsSource` 同构的取数口，屏侧只做一件事：`demo ? 演示 : 真实`。 */
export interface JournalStatsSource {
  load: (query: JournalStatsQuery, signal?: AbortSignal) => Promise<JournalStatsPayload>
}

export const httpJournalStatsSource: JournalStatsSource = {
  load: (query, signal) => api.journalStats(query, signal),
}

/** IndexedDB 缓存 key 与 schema 沿用原实现（只存聚合数字，不含任何自由文本）。 */
const CACHE_KEY = 'journal_stats_cache_v1'
const CACHE_SCHEMA = 1

export interface JournalCache {
  schema: typeof CACHE_SCHEMA
  fetchedAt: string
  payload: JournalStatsPayload
}

function isJournalCache(value: unknown): value is JournalCache {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<JournalCache>
  const payload = item.payload as Partial<JournalStatsPayload> | undefined
  return (
    item.schema === CACHE_SCHEMA &&
    typeof item.fetchedAt === 'string' &&
    !!payload &&
    typeof payload.total_trades === 'number'
  )
}

export interface JournalView {
  demo: boolean
  variant: JournalDemoVariant
  forced: PageState | null
  state: PageState
  /** 当前要渲染的聚合数字（含强制态与离线回放的选择），`null` = 没有可展示的数字 */
  stats: JournalStatsPayload | null
  /** 离线回放用的缓存；演示态恒为 `null`（不读用户库） */
  cached: JournalCache | null
  /** 演示态标注的取数时刻：真实通道没有这个外壳字段，只有缓存的本地时间 */
  demoFetchedAt: string | null
  error: unknown
  endpointMissing: boolean
  reload: () => void
}

export function useJournalView(query: JournalStatsQuery = {}): JournalView {
  const demo = useJournalDemoEnabled()
  const variant = useJournalDemoVariant()
  const forced = useForcedState()

  const source = useMemo<JournalStatsSource>(
    () => (demo ? demoJournalStatsSource(variant) : httpJournalStatsSource),
    [demo, variant],
  )

  const request = useAsync(
    (signal) => source.load(query, signal),
    [source, query.from, query.to, query.deviation],
  )

  const [cached, setCached] = useState<JournalCache | null>(null)

  // 离线回放：只读本机 IndexedDB 里的聚合缓存。演示态直接跳过——不碰用户库。
  useEffect(() => {
    if (demo) {
      setCached(null)
      return
    }
    let alive = true
    idbGet<unknown>(KV_STORE, CACHE_KEY)
      .then((value) => {
        if (alive && isJournalCache(value)) setCached(value)
      })
      .catch(() => {
        /* 无缓存 / IndexedDB 不可用：离线态如实显示「没有可回放的缓存」 */
      })
    return () => {
      alive = false
    }
  }, [demo])

  // 成功取到真实数字才写缓存；演示夹具一次都不落盘。
  useEffect(() => {
    if (demo) return
    if (request.status !== 'ready' || !request.data) return
    const entry: JournalCache = {
      schema: CACHE_SCHEMA,
      fetchedAt: new Date().toISOString(),
      payload: request.data,
    }
    setCached(entry)
    void idbSet(KV_STORE, CACHE_KEY, entry).catch(() => {
      /* 写缓存失败不影响当前展示，下次成功取数再写 */
    })
  }, [demo, request.status, request.data])

  const endpointMissing = isEndpointMissing(request.error)
  const payload = request.data

  const derived = useMemo<PageState>(() => {
    if (request.status === 'loading') return 'loading'
    if (request.status === 'error') {
      if (isApiError(request.error) && request.error.kind === 'not-connected') return 'offline'
      if (endpointMissing) return 'partial'
      return cached ? 'partial' : 'error'
    }
    if (!payload) return 'error'
    if (payload.total_trades === 0) return 'empty'
    // 真实契约里没有「部分缺失」这一档（扁平静态数字，`data_source_status` 只有 ok / missing），
    // 所以 `partial` 只可能来自演示变体本身。
    return demo && variant === 'partial' ? 'partial' : 'normal'
  }, [request.status, request.error, endpointMissing, cached, payload, demo, variant])

  const state = resolveState(forced, derived)

  // 强制态一律按「无数据」处理：normal / partial 用实时结果（partial 时若请求已失败，退回缓存），
  // offline 用缓存，empty 用零值结构，loading / error 不展示任何数字。
  const stats = useMemo<JournalStatsPayload | null>(() => {
    if (state === 'empty') return EMPTY_STATS
    if (state === 'normal') return payload
    if (state === 'partial') return payload ?? cached?.payload ?? null
    if (state === 'offline') return cached?.payload ?? null
    return null
  }, [state, payload, cached])

  return {
    demo,
    variant,
    forced,
    state,
    stats,
    cached: demo ? null : cached,
    demoFetchedAt: demo ? DEMO_JOURNAL_FETCHED_AT : null,
    error: request.error,
    endpointMissing,
    reload: request.reload,
  }
}

/** 空态下用于渲染的零值结构：无交易时后端返回同样的零值 + `data_source_status=missing`。 */
export const EMPTY_STATS: JournalStatsPayload = {
  local_only: true,
  store: 'local_sqlite',
  data_source_status: 'missing',
  range: { from: null, to: null },
  total_trades: 0,
  buy_count: 0,
  sell_count: 0,
  closed_rounds: 0,
  wins: 0,
  losses: 0,
  flats: 0,
  win_rate: null,
  avg_win: null,
  avg_loss: null,
  profit_loss_ratio: null,
  realized_pnl: 0,
  unmatched_sell_qty: 0,
  deviation_review: { threshold_pct: 1, count: 0 },
}
