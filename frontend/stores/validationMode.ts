/**
 * 验证屏「条件放哪」偏好：`local`（本机草稿） / `backend`（后端 validation 缓存）。
 *
 * 三条硬约束（QUOT-186 追加需求 C/D）：
 * 1. **只存偏好**：localStorage key `ldst.validation.mode`，不碰 IndexedDB、不进
 *    `stores/manifest.json`（它不是持久化分片，`config/state.yaml` 里没有对应条目）。
 * 2. **默认值跟环境走**：本机（`127.0.0.1` / `localhost` / `[::1]`）→ `local`；
 *    其它主机名 → `backend`。
 * 3. **手动改过就以用户为准**：只有「没存过选择」时才回落到环境默认值，
 *    之后不再被环境覆盖（否则每次打开都会被打回默认）。
 *
 * 与 `src/lib/prefs.ts` 同构：模块级缓存 + `useSyncExternalStore`，跨标签页靠
 * `storage` 事件同步，保证同一份选择在多个入口读到同一个值。
 */

import { useSyncExternalStore } from 'react'

export type ValidationSource = 'local' | 'backend'
export type ValidationEnvironment = 'local' | 'server'

export const VALIDATION_SOURCE_KEY = 'ldst.validation.mode'

const LOCAL_HOSTNAMES = new Set(['127.0.0.1', 'localhost', '::1', '[::1]'])

/** 只看主机名，不看端口：本机端口换了仍是「本机」。 */
export function detectEnvironment(hostname: string | null | undefined): ValidationEnvironment {
  if (typeof hostname !== 'string' || hostname === '') return 'server'
  return LOCAL_HOSTNAMES.has(hostname.toLowerCase()) ? 'local' : 'server'
}

export function defaultSourceFor(environment: ValidationEnvironment): ValidationSource {
  return environment === 'local' ? 'local' : 'backend'
}

function isSource(value: unknown): value is ValidationSource {
  return value === 'local' || value === 'backend'
}

/** 读「用户明确选过的值」；没选过返回 `null`（交给环境默认值） */
export function readStoredSource(): ValidationSource | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(VALIDATION_SOURCE_KEY)
    return isSource(raw) ? raw : null
  } catch {
    // 隐私模式 / 站点数据被禁用：退回环境默认值，不抛错
    return null
  }
}

/** 当前环境（`location` 缺失时按远程处理，宁严不宽） */
export function currentEnvironment(): ValidationEnvironment {
  if (typeof window === 'undefined') return 'server'
  return detectEnvironment(window.location.hostname)
}

// ---------------------------------------------------------------- 进程内状态

let choice: ValidationSource | null = readStoredSource()

const listeners = new Set<() => void>()

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function emit(): void {
  for (const listener of listeners) listener()
}

export function setValidationSource(next: ValidationSource): void {
  choice = next
  if (typeof window !== 'undefined') {
    try {
      window.localStorage.setItem(VALIDATION_SOURCE_KEY, next)
    } catch {
      // 写不进去也不影响本次会话；刷新后会回到环境默认值，属可接受降级
    }
  }
  emit()
}

/** 清掉用户选择，回到「跟环境走」 */
export function resetValidationSource(): void {
  choice = null
  if (typeof window !== 'undefined') {
    try {
      window.localStorage.removeItem(VALIDATION_SOURCE_KEY)
    } catch {
      // 同上：忽略
    }
  }
  emit()
}

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key === VALIDATION_SOURCE_KEY) {
      choice = readStoredSource()
      emit()
    }
  })
}

export interface ValidationSourceState {
  /** 当前实际生效的来源（已套用环境默认值） */
  source: ValidationSource
  /** 当前访问环境：本机 / 服务器 */
  environment: ValidationEnvironment
  /** 用户是否手动选过（`false` 时 `source` 完全由环境决定） */
  explicit: boolean
  setSource: (next: ValidationSource) => void
  resetToEnvironment: () => void
}

export function useValidationSource(): ValidationSourceState {
  const stored = useSyncExternalStore(
    subscribe,
    () => choice,
    () => null,
  )
  const environment = currentEnvironment()
  return {
    source: stored ?? defaultSourceFor(environment),
    environment,
    explicit: stored !== null,
    setSource: setValidationSource,
    resetToEnvironment: resetValidationSource,
  }
}
