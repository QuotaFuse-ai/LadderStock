import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api } from '../../api'
import { useDemoEnabled, useForcedState } from '../../lib/pageState'
import { demoWatchSource, type WatchDemoVariant, type WatchSource } from './demo'

/**
 * 盯盘取数口的选择（模板同 `limitup/useLimitUpView.ts:30-31,71-76`）。
 *
 * 演示开关判定只有一处真源（`lib/pageState.ts:104` 的 `useDemoEnabled`）；
 * `demo ? demoSource : httpSource` 之外不写第二条判定，也不做失败回落。
 */

const VARIANTS: readonly WatchDemoVariant[] = ['full', 'partial', 'empty']

function isDemoVariant(value: unknown): value is WatchDemoVariant {
  return typeof value === 'string' && (VARIANTS as readonly string[]).includes(value)
}

/** §5.6 的演示开关判定；这里只保留屏侧惯用名。 */
export function useWatchDemoEnabled(): boolean {
  return useDemoEnabled()
}

function useWatchDemoVariant(): WatchDemoVariant {
  const [params] = useSearchParams()
  const forced = useForcedState()
  const explicit = params.get('variant')
  if (isDemoVariant(explicit)) return explicit
  if (forced === 'partial') return 'partial'
  if (forced === 'empty') return 'empty'
  return 'full'
}

export const httpWatchSource: WatchSource = {
  snapshot: (signal) => api.watchSnapshot(signal),
}

export function useWatchSource(): { demo: boolean; variant: WatchDemoVariant; source: WatchSource } {
  const demo = useWatchDemoEnabled()
  const variant = useWatchDemoVariant()
  const source = useMemo<WatchSource>(
    () => (demo ? demoWatchSource(variant) : httpWatchSource),
    [demo, variant],
  )
  return { demo, variant, source }
}
