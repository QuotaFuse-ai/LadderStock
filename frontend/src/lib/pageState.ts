import { useSyncExternalStore } from 'react'
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
 * 设置页「虚拟数据」开关的持久化偏好（`localStorage['ldst.demo-data']`）。
 *
 * 和 `?demo=1` 是同一件事的两种入口：URL 用于一次性深链，设置开关用于整机默认。
 * 两者任一开启都走演示源；关掉开关时设置页会把 URL 里的 `demo` 一并移除。
 */
export const DEMO_DATA_STORAGE_KEY = 'ldst.demo-data'

function readDemoPref(): boolean {
  if (typeof window === 'undefined') return false
  try {
    return window.localStorage.getItem(DEMO_DATA_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

let demoPref = readDemoPref()
const demoListeners = new Set<() => void>()

function subscribeDemo(listener: () => void): () => void {
  demoListeners.add(listener)
  return () => demoListeners.delete(listener)
}

function emitDemo(): void {
  for (const listener of demoListeners) listener()
}

export function setDemoPref(next: boolean): void {
  demoPref = next
  try {
    window.localStorage.setItem(DEMO_DATA_STORAGE_KEY, next ? '1' : '0')
  } catch {
    /* 隐私模式禁用 localStorage：本次会话仍然生效，只是不持久化 */
  }
  emitDemo()
}

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key === DEMO_DATA_STORAGE_KEY) {
      demoPref = readDemoPref()
      emitDemo()
    }
  })
}

export function useDemoPref(): boolean {
  return useSyncExternalStore(subscribeDemo, () => demoPref, () => false)
}

/** 仅 `?demo=1` / `?demo=true` 的 URL 口径（水印文案据此区分来源） */
export function useDemoUrlEnabled(): boolean {
  const [params] = useSearchParams()
  return isDemoEnabled(params.get('demo'))
}

/**
 * 演示源是否开启（UI-SPEC §5.6）：URL `?demo=1` 或设置页开关，任一为真。
 *
 * 真源只有这一处：各屏的 `useXxxDemoEnabled` 与 `PageShell` 的演示徽标必须同口径，
 * 否则会出现「数据走演示源但水印不亮」或反之的假阳性。
 */
export function useDemoEnabled(): boolean {
  const urlDemo = useDemoUrlEnabled()
  const prefDemo = useDemoPref()
  return urlDemo || prefDemo
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
