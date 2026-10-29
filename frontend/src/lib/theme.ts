/**
 * 主题与密度的唯一入口（§7.5 / §7.6）。
 *
 * 规则：
 * - 选择值 `system | light | dark` 存 `localStorage['ldst.theme']`；`system` 时跟随
 *   `matchMedia('(prefers-color-scheme: dark)')`，变化即时应用。
 * - 实际主题写到 `<html data-ldst-theme>`（dark 为默认值，light 触发 tokens.css 的覆盖块）。
 * - 切换不重载页面：只改属性 + `color-scheme`，CSS 变量即刻生效。
 * - 首次访问不做过渡动画（避免整页闪一下）：见 `applyTheme(choice, {animate:false})`。
 */

export type ThemeChoice = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'
export type Density = 'default' | 'compact' | 'comfortable'

export const THEME_STORAGE_KEY = 'ldst.theme'
export const DENSITY_STORAGE_KEY = 'ldst.density'

const DARK_QUERY = '(prefers-color-scheme: dark)'

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    /* 隐私模式禁用 localStorage：本次会话仍然生效，只是不持久化 */
  }
}

export function prefersDark(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return true
  return window.matchMedia(DARK_QUERY).matches
}

export function readThemeChoice(): ThemeChoice {
  const saved = readStorage(THEME_STORAGE_KEY)
  return saved === 'light' || saved === 'dark' || saved === 'system' ? saved : 'system'
}

export function resolveTheme(choice: ThemeChoice): ResolvedTheme {
  if (choice === 'system') return prefersDark() ? 'dark' : 'light'
  return choice
}

export function readDensity(): Density {
  const saved = readStorage(DENSITY_STORAGE_KEY)
  return saved === 'compact' || saved === 'comfortable' ? saved : 'default'
}

export function applyTheme(choice: ThemeChoice): ResolvedTheme {
  const resolved = resolveTheme(choice)
  const root = document.documentElement
  root.setAttribute('data-ldst-theme', resolved)
  root.setAttribute('data-ldst-scheme', choice)
  root.style.colorScheme = resolved
  return resolved
}

export function setThemeChoice(choice: ThemeChoice): ResolvedTheme {
  writeStorage(THEME_STORAGE_KEY, choice)
  return applyTheme(choice)
}

export function setDensity(density: Density): void {
  writeStorage(DENSITY_STORAGE_KEY, density)
  document.documentElement.setAttribute('data-ldst-density', density)
}

/** 监听系统配色变化（只影响 `system` 选择） */
export function watchSystemTheme(callback: () => void): () => void {
  if (typeof window === 'undefined' || !window.matchMedia) return () => {}
  const media = window.matchMedia(DARK_QUERY)
  const handler = () => callback()
  media.addEventListener('change', handler)
  return () => media.removeEventListener('change', handler)
}
