/**
 * 命中 → 跳转目标的映射（QUOT-356）。
 *
 * 路由模板从 `../routes` 的 `ROUTE_PATHS` 取：`routes.ts` 里改了一条路径，
 * 这里三个常量就会编译不过，避免出现「搜索面板跳到一个不存在的页面」。
 *
 * 拼接只允许发生在这一个文件里——组件里散着写 `/research/${code}`
 * 是这次评审要明确干掉的东西（DEC：URL 编码、参数名、兜底形态都得有唯一真源）。
 */

import { type RoutePath } from '../routes'

/** 个股深挖。 */
export const RESEARCH_ROUTE: RoutePath = '/research/:code'
/** 单行业页。 */
export const INDUSTRY_ROUTE: RoutePath = '/industry/:name'
/** 资讯雷达（带过滤参数）。 */
export const NEWS_ROUTE: RoutePath = '/news'

/** `/news` 支持的跳转参数：`code` 是 NewsScreen 今天就读得懂的；`q` 是本期约定的关键词位。 */
export interface NewsRouteParams {
  q?: string | null
  code?: string | null
}

function encodeSegment(value: string): string {
  return encodeURIComponent(value.trim())
}

export function researchHref(code: string): string {
  return `/research/${encodeSegment(code)}`
}

export function industryHref(name: string): string {
  return `/industry/${encodeSegment(name)}`
}

/**
 * `/news?q=<关键词>`；给定 `code` 时改出 `/news?code=<代码>`——
 * 后者是 `NewsScreen` 现在唯一真能消费的标的参数（`radar/NewsScreen.tsx:407`），
 * 代码意图走它可以让跳转今天就生效。
 * 手工拼而不用 `URLSearchParams`：它把空格编成 `+`，验收口径是 `encodeURIComponent`。
 */
export function newsHref(params: NewsRouteParams): string {
  const code = (params.code ?? '').trim()
  if (code) return `/news?code=${encodeURIComponent(code)}`
  const q = (params.q ?? '').trim()
  return q ? `/news?q=${encodeURIComponent(q)}` : NEWS_ROUTE
}

const ROUTE_PATTERNS: ReadonlyArray<readonly [RoutePath, RegExp]> = [
  [RESEARCH_ROUTE, /^\/research\/[^/]+$/],
  [INDUSTRY_ROUTE, /^\/industry\/[^/]+$/],
  [NEWS_ROUTE, /^\/news(\?.*)?$/],
]

/** 产出的 href 必须落在声明过的路由上（含查询串）；`tests/search-core.test.ts` 用它兜底。 */
export function isKnownRouteHref(href: string): boolean {
  return ROUTE_PATTERNS.some(([, pattern]) => pattern.test(href))
}
