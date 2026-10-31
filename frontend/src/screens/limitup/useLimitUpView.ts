import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  httpLimitPoolSource,
  isLimitPoolEndpointMissing,
  isLimitPoolOffline,
  type LimitPoolPayload,
  type LimitPoolSource,
} from '../../api/limitPool'
import { resolveState, useForcedState, type PageState } from '../../lib/pageState'
import { useAsync } from '../../lib/useAsync'
import { demoLimitPoolSource, type LimitPoolDemoVariant } from './demo'

/**
 * 涨停样本页的取数 / 状态推导（与页面 DOM 解耦，便于测试）。
 *
 * 状态优先级沿用 `pageState.ts`：error > offline > partial > loading > empty > normal。
 * 404 是「数据源暂缺」→ 空态；未连接 → 离线态；都不当成整屏故障。
 * `?demo=1` 是唯一演示入口，真实通道绝不回落到演示数据。
 */

const VARIANTS: readonly LimitPoolDemoVariant[] = ['full', 'partial', 'empty']

function isDemoVariant(value: unknown): value is LimitPoolDemoVariant {
  return typeof value === 'string' && (VARIANTS as readonly string[]).includes(value)
}

export function useLimitPoolDemoEnabled(): boolean {
  const [params] = useSearchParams()
  const raw = params.get('demo')
  return raw === '1' || raw === 'true'
}

function useDemoVariant(): LimitPoolDemoVariant {
  const [params] = useSearchParams()
  const forced = useForcedState()
  const explicit = params.get('variant')
  if (isDemoVariant(explicit)) return explicit
  if (forced === 'partial') return 'partial'
  if (forced === 'empty') return 'empty'
  return 'full'
}

export interface LimitUpView {
  demo: boolean
  variant: LimitPoolDemoVariant
  forced: PageState | null
  state: PageState
  payload: LimitPoolPayload | null
  error: unknown
  reload: () => void
}

function stateFromRequest(
  status: 'loading' | 'ready' | 'error',
  error: unknown,
  empty: boolean,
  partial: boolean,
): PageState {
  if (status === 'loading') return 'loading'
  if (status === 'error') {
    if (isLimitPoolEndpointMissing(error)) return 'empty'
    if (isLimitPoolOffline(error)) return 'offline'
    return 'error'
  }
  if (empty) return 'empty'
  return partial ? 'partial' : 'normal'
}

export function useLimitUpView(query: { date: string }): LimitUpView {
  const demo = useLimitPoolDemoEnabled()
  const variant = useDemoVariant()
  const forced = useForcedState()
  const source = useMemo<LimitPoolSource>(
    () => (demo ? demoLimitPoolSource(variant) : httpLimitPoolSource),
    [demo, variant],
  )
  const request = useAsync((signal) => source.load({ date: query.date }, signal), [source, query.date])
  const payload = request.data

  const derived = stateFromRequest(
    request.status,
    request.error,
    payload === null || !payload.available || payload.rows.length === 0,
    payload !== null && payload.available && payload.data_source_status === 'partial',
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
