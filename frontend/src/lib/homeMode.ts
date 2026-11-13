/**
 * 首页双模式（对话 / 研究台）的持久化（QUOT-582 / F5，D5）。
 *
 * - 选择值 `chat | desk` 存 `localStorage['home_mode']`，默认 `chat`（D5「默认对话态」）；
 * - 只影响 `/` 的落点：F4 跳到 `/research/{code}` 再返回 / 刷新 `/`，读回的都是研究台；
 * - 隐私模式下 localStorage 读写抛错时，写入退到内存（本次会话仍生效、只是不持久化），
 *   读取降级为默认值，**不抛**。
 */

import { useSyncExternalStore } from 'react'

export type HomeMode = 'chat' | 'desk'

export const HOME_MODE_STORAGE_KEY = 'home_mode'
export const DEFAULT_HOME_MODE: HomeMode = 'chat'

function normalize(value: unknown): HomeMode | null {
  return value === 'chat' || value === 'desk' ? value : null
}

let memoryMode: HomeMode | null = null

export function readHomeMode(): HomeMode {
  try {
    const saved = window.localStorage.getItem(HOME_MODE_STORAGE_KEY)
    return normalize(saved) ?? memoryMode ?? DEFAULT_HOME_MODE
  } catch {
    return memoryMode ?? DEFAULT_HOME_MODE
  }
}

export function writeHomeMode(mode: HomeMode): void {
  memoryMode = mode
  try {
    window.localStorage.setItem(HOME_MODE_STORAGE_KEY, mode)
  } catch {
    /* 隐私模式：本次会话仍然生效，只是不持久化 */
  }
}

const listeners = new Set<() => void>()

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function setHomeMode(mode: HomeMode): void {
  const next = normalize(mode) ?? DEFAULT_HOME_MODE
  writeHomeMode(next)
  for (const listener of listeners) listener()
}

/** 跨标签页同步：`storage` 事件只在本文件里挂一次 */
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key !== null && event.key !== HOME_MODE_STORAGE_KEY) return
    for (const listener of listeners) listener()
  })
}

/** 读当前模式并订阅变化 */
export function useHomeMode(): [HomeMode, (mode: HomeMode) => void] {
  const mode = useSyncExternalStore(subscribe, readHomeMode, () => DEFAULT_HOME_MODE)
  return [mode, setHomeMode]
}
