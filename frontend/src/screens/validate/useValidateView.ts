import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api, type LadderEnvelope, type ValidationPayload } from '../../api'
import { useDemoEnabled, useForcedState } from '../../lib/pageState'
import { demoValidationSource, type ValidationDemoVariant } from './demo'

/**
 * 明日验证的取数入口（模板同 `limitup/useLimitUpView.ts`、`journal/useJournalView.ts`）。
 *
 * - 演示源只有一个开关口径：`lib/pageState.ts:useDemoEnabled()`（URL `?demo=1` 或设置页「虚拟数据」）。
 * - **真实请求 404 / 断连时进空态 / 离线态，绝不回落到 `demo.ts`**：这一屏写的是用户自己
 *   落盘的对账条件，把夹具当真实条件比多几行数字危险得多。
 * - 演示态不做两件事：不探测控制面写通道（`GET /api/control/validation/conditions`），
 *   不探测 `GET /api/health`——两个探针都是「读环境」，但演示环境读到的东西会指回用户真实的数据目录；
 *   屏侧改用 `demo ? 演示说明 : 探针结果`。
 */

const VARIANTS: readonly ValidationDemoVariant[] = ['full', 'partial', 'empty']

function isDemoVariant(value: unknown): value is ValidationDemoVariant {
  return typeof value === 'string' && (VARIANTS as readonly string[]).includes(value)
}

export function useValidateDemoEnabled(): boolean {
  return useDemoEnabled()
}

/** 显式 `?variant=` 优先，其次由 `?state=` 推导（partial / empty 各演示一种降级）。 */
export function useValidateDemoVariant(): ValidationDemoVariant {
  const [params] = useSearchParams()
  const explicit = params.get('variant')
  if (isDemoVariant(explicit)) return explicit
  const forced = useForcedState()
  if (forced === 'partial') return 'partial'
  if (forced === 'empty') return 'empty'
  return 'full'
}

/** 与 `api.validation` 同构的取数口：`GET /api/validation?date=` → `LadderEnvelope<ValidationPayload>`。 */
export interface ValidationLoader {
  load: (date: string, signal?: AbortSignal) => Promise<LadderEnvelope<ValidationPayload>>
}

export const httpValidationSource: ValidationLoader = {
  load: (date, signal) => api.validation(date, signal),
}

export interface ValidateView {
  demo: boolean
  variant: ValidationDemoVariant
  source: ValidationLoader
}

/**
 * 「明日条件」与「收盘对账」两个视图读的是**同一个响应**（`/api/validation` 一次给全），
 * 所以这里只出一个 source，屏侧两个 `useAsync` 各自决定何时启用。
 */
export function useValidateView(): ValidateView {
  const demo = useValidateDemoEnabled()
  const variant = useValidateDemoVariant()
  const source = useMemo<ValidationLoader>(
    () => (demo ? demoValidationSource(variant) : httpValidationSource),
    [demo, variant],
  )
  return { demo, variant, source }
}
