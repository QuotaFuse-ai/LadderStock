import { useSyncExternalStore } from 'react'

/**
 * 隐私模式（DEC-062 §4）：录屏、截图分享前一键把**自己的**金额与股数遮成 `••••`。
 *
 * 口径照参考实现 `lib/privacy.ts:1-11`：
 * - 只遮屏幕显示，计算、存盘都用原值；
 * - **百分比不遮**：看结构、比风险都要用，单凭比例推不出本金；
 * - 现价、PE、市值这类公开行情不遮；成本价、数量、金额、盈亏是自己的，遮；
 * - 「—」（没有数）照旧，遮了反而像有数。
 */

export const MASK = '••••'

export const PRIVACY_STORAGE_KEY = 'ldst.privacy'

function readStored(): boolean {
  try {
    return window.localStorage.getItem(PRIVACY_STORAGE_KEY) === 'on'
  } catch {
    // 隐私模式 / 浏览器策略禁用 localStorage：按关闭处理，不抛
    return false
  }
}

let on = typeof window === 'undefined' ? false : readStored()
const listeners = new Set<() => void>()

function emit(): void {
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** 结构化数值：隐私模式下显示成 `••••`；缺失值 `—` 不遮。 */
export function hideIf(privacyOn: boolean, text: string): string {
  return privacyOn && text !== '—' ? MASK : text
}

export function setPrivacyMode(next: boolean): void {
  on = next
  try {
    window.localStorage.setItem(PRIVACY_STORAGE_KEY, next ? 'on' : 'off')
  } catch {
    // 写不进去只影响「下次是否记得」，本次会话仍然生效
  }
  emit()
}

export function togglePrivacy(): void {
  setPrivacyMode(!on)
}

/** 全站共用一份开关：顶栏、持仓表同时跟着变（`useSyncExternalStore`，无需 Provider）。 */
export function usePrivacyMode(): { on: boolean; toggle: () => void } {
  const value = useSyncExternalStore(subscribe, () => on, () => false)
  return { on: value, toggle: togglePrivacy }
}
