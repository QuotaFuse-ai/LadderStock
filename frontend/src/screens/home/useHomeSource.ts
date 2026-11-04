import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api, isApiError, isEndpointMissing } from '../../api'
import {
  fetchIndices,
  fetchWatchlist,
  httpRadarSource,
  type IndicesPayload,
  type PositionsPayload,
  type WatchlistPayload,
} from '../../api/radar'
import { useDemoEnabled, useForcedState } from '../../lib/pageState'
import { useAsync, type AsyncResult } from '../../lib/useAsync'
import { demoHomeSource, type HomeDemoVariant, type HomeSource } from './demo'

/**
 * 首页五路取数口的选择（模板同 `limitup/useLimitUpView.ts:30-31,71-76`）。
 *
 * 演示开关判定只有一处真源（`lib/pageState.ts:104` 的 `useDemoEnabled`）；
 * `demo ? demoSource : httpSource` 之外不写第二条判定，**真实请求失败也不回落到演示数据**。
 *
 * 五路对应 SPEC-HOME-V2 §1.1 的四块 + 两块保留：
 * indices(A) / watchlist(C) / positions(D) / watch(E 之外，异动 F) / validation(E 待办)。
 */

const VARIANTS: readonly HomeDemoVariant[] = ['full', 'partial', 'empty']

function isDemoVariant(value: unknown): value is HomeDemoVariant {
  return typeof value === 'string' && (VARIANTS as readonly string[]).includes(value)
}

/** §5.6 的演示开关判定；这里只保留屏侧惯用名。 */
export function useHomeDemoEnabled(): boolean {
  return useDemoEnabled()
}

function useHomeDemoVariant(): HomeDemoVariant {
  const [params] = useSearchParams()
  const forced = useForcedState()
  const explicit = params.get('variant')
  if (isDemoVariant(explicit)) return explicit
  if (forced === 'partial') return 'partial'
  if (forced === 'empty') return 'empty'
  return 'full'
}

export const httpHomeSource: HomeSource = {
  indices: (signal) => fetchIndices(signal),
  watchlist: (signal) => fetchWatchlist(signal),
  // 持仓走雷达四屏同一个 `RadarSource`，不另开一条只读通道（同一份 `request()` 超时 / 404 口径）
  positions: (signal) => httpRadarSource.positions({ date: null }, signal),
  watchSnapshot: (signal) => api.watchSnapshot(signal),
  validation: (date, signal) => api.validation(date, signal),
}

export function useHomeSource(date: string): {
  demo: boolean
  variant: HomeDemoVariant
  source: HomeSource
} {
  const demo = useHomeDemoEnabled()
  const variant = useHomeDemoVariant()
  const source = useMemo<HomeSource>(
    () => (demo ? demoHomeSource(variant, date) : httpHomeSource),
    [demo, variant, date],
  )
  return { demo, variant, source }
}

// ---------------------------------------------------------------- 块级视图

/**
 * 块级六态（SPEC-HOME-V2 §7 末段）：整屏态决定「要不要渲染骨架 / 离线条」，
 * 块级态决定「这一块自己缺了怎么说话」。判据在屏侧算好，呈现收在块件里。
 *
 * - `empty` + `endpointMissing` 是两种不同的空：前者「这个交易日确实没数」，后者「端点还没上线」，
 *   文案与出口都不一样，所以不是一个布尔值能表达的。
 * - `partial` 只给「有数但缺格/缺行」的块（指数条缺某几格）；整块失败一律 `error`。
 */
export type BlockState = 'loading' | 'normal' | 'partial' | 'empty' | 'error' | 'offline'

export interface BlockView<T> {
  state: BlockState
  data: T | null
  error: unknown
  offline: boolean
  /** 端点 404：`empty` 状态下用来区分「接口未接入」与「本日无数据」。 */
  endpointMissing: boolean
  onRetry: () => void
}

function asBlock<T>(
  request: AsyncResult<T>,
  isEmpty: (data: T) => boolean,
  isPartial?: (data: T) => boolean,
): BlockView<T> {
  const base = { onRetry: request.reload, data: request.data, error: request.error }

  if (request.status === 'loading') {
    return { ...base, state: 'loading', offline: false, endpointMissing: false }
  }

  if (request.status === 'error') {
    const offline = isApiError(request.error) && request.error.kind === 'not-connected'
    const endpointMissing = isEndpointMissing(request.error)
    return {
      ...base,
      state: offline ? 'offline' : endpointMissing ? 'empty' : 'error',
      offline,
      endpointMissing,
    }
  }

  const data = request.data as T
  if (isEmpty(data)) {
    return { ...base, state: 'empty', offline: false, endpointMissing: false }
  }
  return {
    ...base,
    state: isPartial?.(data) ? 'partial' : 'normal',
    offline: false,
    endpointMissing: false,
  }
}

/**
 * 指数条：整块无可读点位 = empty（含「四格都在但一格都没取到」，§2.3 的 empty 判据是端点 404 / 未接入，
 * 一格都没点位时 `available=false` 且 `reason` 给了原因，按空态说话比摆 8 个 `—` 诚实）；
 * 有值但缺格 = partial（§2.3「部分格缺」）。
 */
function indicesEmpty(data: IndicesPayload): boolean {
  return !data.available || data.cells.length === 0
}

function indicesPartial(data: IndicesPayload): boolean {
  return data.cells.some((cell) => cell.price === null)
}

export function useHomeBlocks(tradeDate: string) {
  const { demo, variant, source } = useHomeSource(tradeDate)

  const indices = useAsync((signal) => source.indices(signal), [source])
  const watchlist = useAsync((signal) => source.watchlist(signal), [source])
  const positions = useAsync((signal) => source.positions(signal), [source])
  const watch = useAsync((signal) => source.watchSnapshot(signal), [source])
  const validation = useAsync((signal) => source.validation(tradeDate, signal), [source, tradeDate])

  const views = useMemo(() => {
    const i = asBlock<IndicesPayload>(indices, indicesEmpty, indicesPartial)
    const w = asBlock<WatchlistPayload>(watchlist, (data) => data.items.length === 0)
    const p = asBlock<PositionsPayload>(positions, (data) => data.position_count === 0)
    const s = asBlock(watch, (data) => (data.payload.rows ?? []).length === 0)
    const v = asBlock(validation, (data) => (data.payload.conditions ?? []).length === 0)
    return { indices: i, watchlist: w, positions: p, watch: s, validation: v }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [indices, watchlist, positions, watch, validation])

  const requests = [indices, watchlist, positions, watch, validation]
  const reloadAll = () => requests.forEach((request) => request.reload())

  return { demo, variant, views, requests, reloadAll }
}
