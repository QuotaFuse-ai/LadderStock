import { navTitleFor } from '../nav'

/**
 * 路由 → 页面技能绑定（QUOT-475）。**唯一维护点**：任何页面都不许在别处再写一份
 * 「这个页面用哪个 skill」。
 *
 * 匹配口径沿用 `navTitleFor()`：查询串不参与匹配，子路径归到**最长**前缀
 * （`/review/ai` 命中 `/review` 而不是 `/`，`/research/600519` 命中 `/research`）；
 * `/` 只认首页本身，不做 catch-all，否则每条路由都会命中开放域。
 *
 * `pkg: null` = 开放域（不绑定任何包）：首页、设置、以及由 QUOT-474 页内自行决定的 `/skills`。
 */
export interface PageSkillBinding {
  prefix: string
  pkg: string | null
}

export const PAGE_SKILL_BINDINGS: PageSkillBinding[] = [
  { prefix: '/', pkg: null },
  { prefix: '/market', pkg: 'a-share-tape' },
  { prefix: '/watch', pkg: 'a-share-tape' },
  { prefix: '/intraday', pkg: 'a-share-tape' },
  { prefix: '/review', pkg: 'review-journal' },
  { prefix: '/weekly', pkg: 'review-journal' },
  { prefix: '/pool', pkg: 'review-journal' },
  { prefix: '/journal', pkg: 'review-journal' },
  { prefix: '/validate', pkg: 'review-journal' },
  { prefix: '/limit-up', pkg: 'review-journal' },
  { prefix: '/my-stocks', pkg: 'portfolio-check' },
  { prefix: '/calendar', pkg: 'event-calendar' },
  { prefix: '/inbox', pkg: 'event-calendar' },
  { prefix: '/industry', pkg: 'sector-rotation' },
  { prefix: '/signals', pkg: 'sector-rotation' },
  { prefix: '/research', pkg: 'a-share-dossier' },
  { prefix: '/news', pkg: 'wire-desk' },
  { prefix: '/skills', pkg: null },
  { prefix: '/settings', pkg: null },
]

export interface PageBinding {
  prefix: string
  pkg: string | null
  /** 页面名，口径与顶栏面包屑同一个 `navTitleFor()` */
  label: string
}

function matches(path: string, prefix: string): boolean {
  if (prefix === '/') return path === '/'
  return path === prefix || path.startsWith(`${prefix}/`)
}

/**
 * 当前路径绑定哪个包。没命中任何前缀时返回 `null`（例如 `/positions` 这类旧深链），
 * 调用方按开放域处理。
 */
export function bindingFor(pathname: string): PageBinding | null {
  const path = pathname.split('?')[0] || '/'
  let best: PageSkillBinding | null = null

  for (const binding of PAGE_SKILL_BINDINGS) {
    if (!matches(path, binding.prefix)) continue
    if (!best || binding.prefix.length > best.prefix.length) best = binding
  }

  if (!best) return null
  return { prefix: best.prefix, pkg: best.pkg, label: navTitleFor(path) }
}
