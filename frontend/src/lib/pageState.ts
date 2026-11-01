import { useSearchParams } from 'react-router-dom'

/**
 * 5 种页面状态（DESIGN-SPEC §8）。
 *
 * 优先级：error > offline > partial > loading > empty > normal。
 * 演示方式统一走 URL：`?state=empty|loading|error|partial|offline`。
 * 强制态优先于真实请求结果，保证任何环境（含后端未启动）都能复现全部状态。
 */

export type PageState = 'normal' | 'empty' | 'loading' | 'error' | 'partial' | 'offline'

export const FORCED_STATES: readonly PageState[] = [
  'empty',
  'loading',
  'error',
  'partial',
  'offline',
]

const PRIORITY: PageState[] = ['error', 'offline', 'partial', 'loading', 'empty', 'normal']

export function isPageState(value: unknown): value is PageState {
  return typeof value === 'string' && (PRIORITY as string[]).includes(value)
}

/** `?state=` 的解析结果；没有合法值时返回 null（走真实数据） */
export function useForcedState(): PageState | null {
  const [params] = useSearchParams()
  const raw = params.get('state')
  return isPageState(raw) && raw !== 'normal' ? raw : null
}

/** §5.6 的唯一判定口径：`?demo=1` / `?demo=true` 才算演示源。 */
export function isDemoEnabled(value: string | null): boolean {
  return value === '1' || value === 'true'
}

/**
 * `?demo=1` 是否开启（UI-SPEC §5.6）。
 *
 * 真源只有这一处：各屏的 `useXxxDemoEnabled` 与 `PageShell` 的演示徽标必须同口径，
 * 否则会出现「数据走演示源但水印不亮」或反之的假阳性。
 */
export function useDemoEnabled(): boolean {
  const [params] = useSearchParams()
  return isDemoEnabled(params.get('demo'))
}

/** 多个候选里取优先级最高的一个 */
export function pickState(...candidates: PageState[]): PageState {
  for (const state of PRIORITY) {
    if (candidates.includes(state)) return state
  }
  return 'normal'
}

/** 请求态 → 页面态（不含强制覆盖） */
export function stateFromRequest(status: 'loading' | 'ready' | 'error', empty: boolean): PageState {
  if (status === 'loading') return 'loading'
  if (status === 'error') return 'error'
  return empty ? 'empty' : 'normal'
}

/** 最终态：强制演示参数优先 */
export function resolveState(forced: PageState | null, derived: PageState): PageState {
  return forced ?? derived
}
