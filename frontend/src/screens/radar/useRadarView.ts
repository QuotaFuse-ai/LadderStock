import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import { isApiError } from '../../api'
import {
  httpRadarSource,
  isRadarEndpointMissing,
  type CandidatePoolPayload,
  type EventCalendarPayload,
  type NewsStreamPayload,
  type PositionInput,
  type PositionMutation,
  type PositionsPayload,
  type RadarSource,
} from '../../api/radar'
import { resolveState, useDemoEnabled, useForcedState, type PageState } from '../../lib/pageState'
import { useAsync } from '../../lib/useAsync'
import { demoSource, type DemoVariant } from './demo'

/**
 * 四页的取数 / 状态推导（与页面 DOM 解耦，便于测试）。
 *
 * 与 R9 产业页同一套规则：`?demo=1` 是显式开关，演示源与 HTTP 源同构；
 * 页面只认 `state`。状态优先级 error > offline > partial > loading > empty > normal。
 * 404 是「数据源暂缺」→ 空态「接口未接入」；未连接 → 离线态；都不当成整屏故障。
 */

const VARIANTS: readonly DemoVariant[] = ['full', 'partial', 'empty']

function isDemoVariant(value: unknown): value is DemoVariant {
  return typeof value === 'string' && (VARIANTS as readonly string[]).includes(value)
}

/** §5.6 的演示开关判定只有一处真源（`lib/pageState.ts`），这里转发给各屏导入。 */
export { useDemoEnabled }

/** 显式 `?variant=` 优先；否则由强制态推导（partial/empty 各自演示不同降级）。 */
export function useDemoVariant(): DemoVariant {
  const [params] = useSearchParams()
  const forced = useForcedState()
  const explicit = params.get('variant')
  if (isDemoVariant(explicit)) return explicit
  if (forced === 'partial') return 'partial'
  if (forced === 'empty') return 'empty'
  return 'full'
}

interface RequestView<T> {
  demo: boolean
  variant: DemoVariant
  forced: PageState | null
  state: PageState
  payload: T | null
  error: unknown
  reload: () => void
}

function stateFromRequest(
  status: 'loading' | 'ready' | 'error',
  error: unknown,
  empty: boolean,
  partial = false,
): PageState {
  if (status === 'loading') return 'loading'
  if (status === 'error') {
    if (isRadarEndpointMissing(error)) return 'empty'
    if (isApiError(error) && error.kind === 'not-connected') return 'offline'
    return 'error'
  }
  if (empty) return 'empty'
  return partial ? 'partial' : 'normal'
}

function useRadarSource(): { demo: boolean; variant: DemoVariant; source: RadarSource } {
  const demo = useDemoEnabled()
  const variant = useDemoVariant()
  const source = useMemo(() => (demo ? demoSource(variant) : httpRadarSource), [demo, variant])
  return { demo, variant, source }
}

export function useNewsView(query: { date: string; until: string | null; codes: string[] }): RequestView<NewsStreamPayload> {
  const { demo, variant, source } = useRadarSource()
  const forced = useForcedState()
  const codeKey = query.codes.join(',')
  const request = useAsync(
    (signal) =>
      source.news(
        { date: query.date, until: query.until, codes: query.codes.length > 0 ? query.codes : null, limit: 60 },
        signal,
      ),
    [source, query.date, query.until, codeKey],
  )
  const payload = request.data

  const derived = stateFromRequest(
    request.status,
    request.error,
    payload === null || payload.items.length === 0,
    payload !== null && payload.data_source_status === 'partial',
  )

  return {
    demo,
    variant,
    forced,
    state: resolveState(forced, derived),
    payload,
    error: request.error,
    reload: request.reload,
  }
}

export function useCalendarView(query: { month: string; codes: string[] }): RequestView<EventCalendarPayload> {
  const { demo, variant, source } = useRadarSource()
  const forced = useForcedState()
  const codeKey = query.codes.join(',')
  const request = useAsync(
    (signal) =>
      source.calendar(
        { month: query.month, codes: query.codes.length > 0 ? query.codes : null, limit: 200 },
        signal,
      ),
    [source, query.month, codeKey],
  )
  const payload = request.data

  const allNoSource =
    payload !== null &&
    payload.sections.every((section) => section.status === 'no_source') &&
    payload.extracted_events.length === 0

  const derived = stateFromRequest(
    request.status,
    request.error,
    payload === null || allNoSource,
    payload !== null && payload.data_source_status === 'partial',
  )

  return {
    demo,
    variant,
    forced,
    state: resolveState(forced, derived),
    payload,
    error: request.error,
    reload: request.reload,
  }
}

export function usePoolView(query: { date: string }): RequestView<CandidatePoolPayload> {
  const { demo, variant, source } = useRadarSource()
  const forced = useForcedState()
  const request = useAsync(
    (signal) => source.pool({ date: query.date, limit: 60 }, signal),
    [source, query.date],
  )
  const payload = request.data

  const derived = stateFromRequest(
    request.status,
    request.error,
    payload === null || !payload.available || payload.candidates.length === 0,
    payload !== null && payload.data_source_status === 'partial',
  )

  return {
    demo,
    variant,
    forced,
    state: resolveState(forced, derived),
    payload,
    error: request.error,
    reload: request.reload,
  }
}

export interface PositionsView extends RequestView<PositionsPayload> {
  addPosition: (input: PositionInput) => Promise<PositionMutation>
  removePosition: (code: string) => Promise<PositionMutation>
}

export function usePositionsView(): PositionsView {
  const { demo, variant, source } = useRadarSource()
  const forced = useForcedState()
  const request = useAsync((signal) => source.positions({ date: null }, signal), [source])
  const payload = request.data
  const reload = request.reload

  const derived = stateFromRequest(
    request.status,
    request.error,
    payload === null || payload.position_count === 0,
    payload !== null && payload.data_source_status === 'partial',
  )

  const addPosition = useCallback(
    async (input: PositionInput) => {
      const mutation = await source.addPosition(input)
      reload()
      return mutation
    },
    [source, reload],
  )

  const removePosition = useCallback(
    async (code: string) => {
      const mutation = await source.removePosition(code)
      reload()
      return mutation
    },
    [source, reload],
  )

  return {
    demo,
    variant,
    forced,
    state: resolveState(forced, derived),
    payload,
    error: request.error,
    reload,
    addPosition,
    removePosition,
  }
}
