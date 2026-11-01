import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  httpIntradaySource,
  isIntradayEndpointMissing,
  isIntradayOffline,
  type IntradaySnapshotPayload,
  type IntradaySource,
  type IntradaySummaryPayload,
} from '../../api/intraday'
import { resolveState, useDemoEnabled, useForcedState, type PageState } from '../../lib/pageState'
import { useAsync } from '../../lib/useAsync'
import { demoIntradaySource, type IntradayDemoVariant } from './demo'

/**
 * 「竞价与盘中」的取数 / 状态推导（与页面 DOM 解耦，便于测试）。
 *
 * 状态优先级沿用 `pageState.ts`：error > offline > partial > loading > empty > normal；
 * 404 是「数据源暂缺」→ 空态，未连接 → 离线态，都不当成整屏故障。
 *
 * 与既有屏的两点不同，都是这张屏特有的语义：
 * - **partial 的判定是时点级的**：`available` 为真但 `snapshot_count < slot_count`
 *   ——「这一天有快照，但有几个时点没取到」。不用 `data_source_status`，因为后端对
 *   汇总给的是「有没有至少一个时点落盘」（SPEC-INTRADAY-READ §3），粒度不对。
 * - **选中时点来自载荷**：缺省选第一个有快照的时点，都没有则选第一个；`?slot=` 深链
 *   优先。前端没有自己的时点表，所以后端给几个时点、给什么时刻，这里就只有那些值。
 */

const VARIANTS: readonly IntradayDemoVariant[] = ['full', 'partial', 'empty', 'mixed']

function isDemoVariant(value: unknown): value is IntradayDemoVariant {
  return typeof value === 'string' && (VARIANTS as readonly string[]).includes(value)
}

/** §5.6 的演示开关判定只有一处真源（`lib/pageState.ts`）；这里保留屏侧惯用名。 */
export function useIntradayDemoEnabled(): boolean {
  return useDemoEnabled()
}

/** 显式 `?variant=` 优先；否则由强制态推导（partial / empty 各自演示不同降级）。 */
function useDemoVariant(): IntradayDemoVariant {
  const [params] = useSearchParams()
  const forced = useForcedState()
  const explicit = params.get('variant')
  if (isDemoVariant(explicit)) return explicit
  if (forced === 'partial') return 'partial'
  if (forced === 'empty') return 'empty'
  return 'full'
}

function summaryState(
  status: 'loading' | 'ready' | 'error',
  error: unknown,
  payload: IntradaySummaryPayload | null,
): PageState {
  if (status === 'loading') return 'loading'
  if (status === 'error') {
    if (isIntradayEndpointMissing(error)) return 'empty'
    if (isIntradayOffline(error)) return 'offline'
    return 'error'
  }
  if (payload === null || !payload.available) return 'empty'
  return payload.snapshot_count < payload.slot_count ? 'partial' : 'normal'
}

export interface IntradayView {
  demo: boolean
  variant: IntradayDemoVariant
  forced: PageState | null
  state: PageState
  payload: IntradaySummaryPayload | null
  error: unknown
  reload: () => void
  /** 当前选中的时点名（后端给的原值）；载荷没到时 `null` */
  selected: string | null
  select: (slot: string) => void
  slotState: PageState
  snapshot: IntradaySnapshotPayload | null
  slotError: unknown
  reloadSlot: () => void
}

export function useIntradayView(query: { date: string }): IntradayView {
  const demo = useIntradayDemoEnabled()
  const variant = useDemoVariant()
  const forced = useForcedState()
  const [params] = useSearchParams()
  const source = useMemo<IntradaySource>(
    () => (demo ? demoIntradaySource(variant) : httpIntradaySource),
    [demo, variant],
  )

  const request = useAsync((signal) => source.summary({ date: query.date }, signal), [source, query.date])
  const payload = request.data
  const state = resolveState(forced, summaryState(request.status, request.error, payload))

  const deepLinkSlot = params.get('slot')
  const selected = useMemo(() => {
    const slots = payload?.slots ?? []
    if (slots.length === 0) return null
    const linked = slots.find((row) => row.slot === deepLinkSlot)
    if (linked) return linked.slot
    return (slots.find((row) => row.available) ?? slots[0]).slot
  }, [payload, deepLinkSlot])

  const [manual, setManual] = useState<string | null>(null)
  useEffect(() => {
    // 换天时把手动选择清空：上一天的时点名对这一天不一定有意义
    setManual(null)
  }, [query.date])

  const select = useCallback((slot: string) => setManual(slot), [])
  const active = manual !== null && (payload?.slots ?? []).some((row) => row.slot === manual) ? manual : selected

  const slotRequest = useAsync(
    (signal) => (active === null ? Promise.resolve(null) : source.snapshot({ date: query.date, slot: active }, signal)),
    [source, query.date, active],
  )
  const snapshot = slotRequest.data
  const slotState = resolveState(
    forced === 'loading' ? 'loading' : null,
    slotRequest.status === 'loading'
      ? 'loading'
      : slotRequest.status === 'error'
        ? isIntradayOffline(slotRequest.error)
          ? 'offline'
          : 'error'
        : snapshot === null || !snapshot.available
          ? 'empty'
          : 'normal',
  )

  return {
    demo,
    variant,
    forced,
    state,
    payload,
    error: request.error,
    reload: request.reload,
    selected: active,
    select,
    slotState,
    snapshot,
    slotError: slotRequest.error,
    reloadSlot: slotRequest.reload,
  }
}
