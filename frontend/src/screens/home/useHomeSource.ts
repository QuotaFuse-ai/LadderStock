import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api } from '../../api'
import { useDemoEnabled, useForcedState } from '../../lib/pageState'
import { demoHomeSource, type HomeDemoVariant, type HomeSource } from './demo'

/**
 * 首页四路取数口的选择（模板同 `limitup/useLimitUpView.ts:30-31,71-76`）。
 *
 * 演示开关判定只有一处真源（`lib/pageState.ts:104` 的 `useDemoEnabled`）；
 * `demo ? demoSource : httpSource` 之外不写第二条判定，**真实请求失败也不回落到演示数据**。
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
  sentiment: (date, signal) => api.sentiment(date, signal),
  ladder: (date, signal) => api.ladder(date, signal),
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
