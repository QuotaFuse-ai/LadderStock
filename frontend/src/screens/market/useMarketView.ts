import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import { useDemoEnabled, useForcedState, type PageState } from '../../lib/pageState'
import { demoMarketSource, type MarketDemoVariant } from './demo'
import { httpMarketSource, type MarketSource } from './source'

/**
 * 市场屏的取数通道选择（与页面 DOM 解耦，便于测试）。
 *
 * 口径与 `limitup/useLimitUpView.ts` 一致：`demo ? demoMarketSource(variant) : httpMarketSource`，
 * 演示入口只有 `useDemoEnabled()`（URL `?demo=1` 或「设置 → 虚拟数据」）这一处真源；
 * 真实请求 404 / 断连时页面进空态 / 离线态，**不回落到 `demo.ts`**。
 */

const VARIANTS: readonly MarketDemoVariant[] = ['full', 'partial', 'empty']

function isDemoVariant(value: unknown): value is MarketDemoVariant {
  return typeof value === 'string' && (VARIANTS as readonly string[]).includes(value)
}

/** §5.6 的演示开关判定只有一处真源（`lib/pageState.ts`）；这里保留屏侧惯用名。 */
export function useMarketDemoEnabled(): boolean {
  return useDemoEnabled()
}

function useDemoVariant(): MarketDemoVariant {
  const [params] = useSearchParams()
  const forced = useForcedState()
  const explicit = params.get('variant')
  if (isDemoVariant(explicit)) return explicit
  if (forced === 'partial') return 'partial'
  if (forced === 'empty') return 'empty'
  return 'full'
}

export interface MarketView {
  demo: boolean
  variant: MarketDemoVariant
  forced: PageState | null
  source: MarketSource
}

export function useMarketView(): MarketView {
  const demo = useMarketDemoEnabled()
  const variant = useDemoVariant()
  const forced = useForcedState()
  const source = useMemo<MarketSource>(
    () => (demo ? demoMarketSource(variant) : httpMarketSource),
    [demo, variant],
  )
  return { demo, variant, forced, source }
}
