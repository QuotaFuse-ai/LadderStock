/**
 * 路由路径清单（QUOT-300）。
 *
 * 单独放一个不含组件的模块，`tests/nav-routes.test.ts` 才能在不加载 uPlot
 * （模块加载期就要 `matchMedia`）的前提下，把侧栏 `NAV_SECTIONS` 和真实路由表对齐。
 *
 * 组件表在 `App.tsx` 里按 `Record<RoutePath, ReactElement>` 声明：
 * 这里加一条路径而 `App.tsx` 没写组件，或反过来，都编译不过——一致性由类型系统先兜一层，
 * 测试再兜「侧栏有没有入口」这一层。
 *
 * 顺序即 `Routes` 里的书写顺序；react-router 按路径具体性排名匹配，不看书写顺序。
 */
export const ROUTE_PATHS = [
  '/',
  '/market',
  '/watch',
  // 盯盘第二页：竞价与盘中 6 时点快照（QUOT-302 / DEC-035 §5），`?date=` + `?slot=` 深链
  '/intraday',
  '/review',
  '/weekly',
  '/journal',
  '/validate',
  '/limit-up',
  '/news',
  '/calendar',
  '/pool',
  '/positions',
  '/industry',
  '/industry/:name',
  '/signals',
  '/settings',
  '/research',
  '/research/:code',
  '/research/:code/report',
  '/index.html',
  '*',
] as const

export type RoutePath = (typeof ROUTE_PATHS)[number]

/** 不是「页面」的路径：404 兜底 + 旧地址重定向，本来就不该有侧栏入口。 */
export const NON_PAGE_ROUTES: readonly RoutePath[] = ['*', '/index.html']
