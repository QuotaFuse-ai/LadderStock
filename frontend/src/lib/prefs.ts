/**
 * 主题 / 密度偏好的进程内广播（DESIGN-SPEC §7.5 / §7.6）。
 *
 * 同一份选择在侧栏底部与设置页「关于」两处都要可切换，两处必须同状态。
 * localStorage 已由 `theme.ts` 负责；这里只做 React 侧的订阅与广播。
 * 用 `useSyncExternalStore` 而不是自建 Context：两处入口互不嵌套，没必要加 Provider。
 */

import { useSyncExternalStore } from 'react'

import {
  DENSITY_STORAGE_KEY,
  THEME_STORAGE_KEY,
  readDensity,
  readThemeChoice,
  setDensity as persistDensity,
  setThemeChoice,
  type Density,
  type ThemeChoice,
} from './theme'

let themeChoice: ThemeChoice = readThemeChoice()
let density: Density = readDensity()

const listeners = new Set<() => void>()

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function emit(): void {
  for (const listener of listeners) listener()
}

export function setTheme(choice: ThemeChoice): void {
  themeChoice = choice
  setThemeChoice(choice)
  emit()
}

/** 系统配色变化时，若当前是 system 需要广播一次（设置页的分段控件不显示解析值，但图表要重绘） */
export function refreshTheme(): void {
  emit()
}

export function setDensityPref(next: Density): void {
  density = next
  persistDensity(next)
  emit()
}

/** 其他标签页改了偏好：同步过来（storage 事件只会在别的标签页触发） */
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key === THEME_STORAGE_KEY) {
      themeChoice = readThemeChoice()
      emit()
    }
    if (event.key === DENSITY_STORAGE_KEY) {
      density = readDensity()
      emit()
    }
  })
}

export function useThemeChoice(): ThemeChoice {
  return useSyncExternalStore(subscribe, () => themeChoice, () => 'system')
}

export function useDensity(): Density {
  return useSyncExternalStore(subscribe, () => density, () => 'default')
}
