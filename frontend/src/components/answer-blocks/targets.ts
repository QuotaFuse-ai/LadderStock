import { ROUTE_PATHS } from '../../routes'

/**
 * `action.target` 的合法性（任务书 §7 红线 7「不得编造路由」）。
 *
 * 只认导航注册表 `ROUTE_PATHS` 里的路径，允许带查询串（`/research?code=600036`），
 * 动态段的真实值不参与匹配（`/research/:code` 收 `/research/600036`）。
 * 外链、协议相对地址、`javascript:` 一律不认——`open_screen` 是站内跳转。
 */

type Pattern = { segments: string[]; exact: boolean }

function patternOf(route: string): Pattern | null {
  if (route === '*') return null
  const normalized = route.replace(/\/+$/, '') || '/'
  return {
    segments: normalized === '/' ? [] : normalized.slice(1).split('/'),
    exact: !route.includes(':'),
  }
}

const PATTERNS = ROUTE_PATHS.map(patternOf).filter((item): item is Pattern => item !== null)

function segmentsOf(pathname: string): string[] {
  const trimmed = pathname.replace(/\/+$/, '')
  return trimmed === '' || trimmed === '/' ? [] : trimmed.slice(1).split('/')
}

export function isKnownTarget(target: string): boolean {
  if (!target.startsWith('/') || target.startsWith('//')) return false
  const pathname = target.split(/[?#]/)[0] || '/'
  const segments = segmentsOf(pathname)
  return PATTERNS.some((pattern) => {
    if (pattern.segments.length !== segments.length) return false
    // 静态路由整段比对；含动态段的路由按段数匹配（真实值在运行时由屏幕自己校验）
    return pattern.exact
      ? pattern.segments.every((segment, index) => segment === segments[index])
      : true
  })
}
