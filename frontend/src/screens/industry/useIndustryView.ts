import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import { isApiError } from '../../api'
import {
  httpIndustrySource,
  isIndustryEndpointMissing,
  type IndustryDetailPayload,
  type IndustryListPayload,
  type IndustrySource,
  type SignalsPayload,
} from '../../api/industry'
import { resolveState, useForcedState, type PageState } from '../../lib/pageState'
import { useAsync } from '../../lib/useAsync'
import { demoIndustryDetail, demoIndustryList, demoSignals, type DemoVariant } from './demo'

/**
 * 产业三页的取数 / 状态推导（与页面 DOM 解耦，便于测试）。
 *
 * `?demo=1` 是显式开关：演示源与 HTTP 源同构（`IndustrySource`），
 * 页面只认 `state`，不认数据从哪来。状态优先级沿用全站 §8：
 * error > offline > partial > loading > empty > normal。
 *
 * 404 / 未连接**不当作页面故障**：404 → 空态「接口未接入」，未连接 → 离线态。
 */

const VARIANTS: readonly DemoVariant[] = ['full', 'partial', 'empty', 'all-null']

function isDemoVariant(value: unknown): value is DemoVariant {
  return typeof value === 'string' && (VARIANTS as readonly string[]).includes(value)
}

export function useDemoEnabled(): boolean {
  const [params] = useSearchParams()
  const raw = params.get('demo')
  return raw === '1' || raw === 'true'
}

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

function filterDemoSignals(payload: SignalsPayload, query: { industry?: string | null; rule?: string | null }): SignalsPayload {
  const signals = payload.signals.filter(
    (signal) =>
      (!query.industry || signal.industry === query.industry) &&
      (!query.rule || signal.rule_id === query.rule),
  )
  return {
    ...payload,
    signals,
    counts: {
      total: signals.length + payload.dropped,
      bullish: signals.filter((signal) => signal.direction === 'bullish').length,
      bearish: signals.filter((signal) => signal.direction === 'bearish').length,
      by_rule: Object.fromEntries(
        payload.rules.map((rule) => [rule.id, signals.filter((signal) => signal.rule_id === rule.id).length]),
      ),
    },
  }
}

export function demoSource(variant: DemoVariant): IndustrySource {
  return {
    list: async () => demoIndustryList(variant),
    detail: async (name) => demoIndustryDetail(name, variant),
    signals: async (query) => filterDemoSignals(demoSignals(variant), query),
  }
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
    if (isIndustryEndpointMissing(error)) return 'empty'
    if (isApiError(error) && error.kind === 'not-connected') return 'offline'
    return 'error'
  }
  if (empty) return 'empty'
  return partial ? 'partial' : 'normal'
}

function useDemoSource(): { demo: boolean; variant: DemoVariant; source: IndustrySource } {
  const demo = useDemoEnabled()
  const variant = useDemoVariant()
  const source = useMemo(() => (demo ? demoSource(variant) : httpIndustrySource), [demo, variant])
  return { demo, variant, source }
}

export function useIndustryListView(): RequestView<IndustryListPayload> {
  const { demo, variant, source } = useDemoSource()
  const forced = useForcedState()
  const request = useAsync((signal) => source.list(signal), [source])
  const payload = request.data

  const derived = stateFromRequest(
    request.status,
    request.error,
    payload === null || !payload.available || payload.industries.length === 0,
    payload !== null && payload.industries.some((row) => row.data_source_status !== 'ok'),
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

export function useIndustryDetailView(name: string | null): RequestView<IndustryDetailPayload> {
  const { demo, variant, source } = useDemoSource()
  const forced = useForcedState()
  const request = useAsync(
    (signal) =>
      name
        ? source.detail(name, signal)
        : Promise.reject(new Error('缺少行业名')),
    [source, name],
    { enabled: Boolean(name) },
  )
  const payload = request.data

  const derived = !name
    ? 'empty'
    : stateFromRequest(
        request.status,
        request.error,
        payload === null || !payload.available,
        payload !== null && payload.unavailable_dimensions.length > 0,
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

export function useSignalsView(query: {
  industry: string | null
  rule: string | null
}): RequestView<SignalsPayload> {
  const { demo, variant, source } = useDemoSource()
  const forced = useForcedState()
  const request = useAsync(
    (signal) => source.signals(query, signal),
    [source, query.industry, query.rule],
  )
  const payload = request.data

  const derived = stateFromRequest(
    request.status,
    request.error,
    payload === null || !payload.available || payload.signals.length === 0,
    payload !== null && payload.dropped > 0,
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
