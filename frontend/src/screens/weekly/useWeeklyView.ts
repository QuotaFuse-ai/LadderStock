import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api, type LadderEnvelope, type WeeklyPayload } from '../../api'
import { useDemoEnabled, useForcedState, type PageState } from '../../lib/pageState'
import { useAsync, type AsyncResult } from '../../lib/useAsync'
import { demoWeeklySource, type WeeklyDemoVariant } from './demo'

/**
 * 周度复盘页的取数 / 状态推导（与页面 DOM 解耦，便于测试）。
 *
 * 演示源的唯一入口是 `useDemoEnabled()`；真实请求 404 / 断连不回落到 `demo.ts`，
 * 页面按 `/api/weekly` 的原语义进空态（200 + missing）或离线态。
 */

export interface WeeklyQuery {
  week: string
}

export interface WeeklySource {
  load: (query: WeeklyQuery, signal?: AbortSignal) => Promise<LadderEnvelope<WeeklyPayload>>
}

export const httpWeeklySource: WeeklySource = {
  load: (query, signal) => api.weekly(query.week, signal),
}

const VARIANTS: readonly WeeklyDemoVariant[] = ['full', 'partial', 'empty']

function isDemoVariant(value: unknown): value is WeeklyDemoVariant {
  return typeof value === 'string' && (VARIANTS as readonly string[]).includes(value)
}

/** §5.6 的演示开关判定只有一处真源（`lib/pageState.ts`）；这里保留屏侧惯用名。 */
export function useWeeklyDemoEnabled(): boolean {
  return useDemoEnabled()
}

function useDemoVariant(): WeeklyDemoVariant {
  const [params] = useSearchParams()
  const forced = useForcedState()
  const explicit = params.get('variant')
  if (isDemoVariant(explicit)) return explicit
  if (forced === 'partial') return 'partial'
  if (forced === 'empty') return 'empty'
  return 'full'
}

export interface WeeklyView {
  demo: boolean
  variant: WeeklyDemoVariant
  forced: PageState | null
  request: AsyncResult<LadderEnvelope<WeeklyPayload>>
}

export function useWeeklyView(query: WeeklyQuery): WeeklyView {
  const demo = useWeeklyDemoEnabled()
  const variant = useDemoVariant()
  const forced = useForcedState()
  const source = useMemo<WeeklySource>(
    () => (demo ? demoWeeklySource(variant) : httpWeeklySource),
    [demo, variant],
  )
  const request = useAsync((signal) => source.load({ week: query.week }, signal), [source, query.week])
  return { demo, variant, forced, request }
}
