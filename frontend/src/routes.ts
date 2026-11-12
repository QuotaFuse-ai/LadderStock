/**
 * 路由路径清单（QUOT-300）。
 *
 * 单独放一个不含组件的模块，`tests/nav-routes.test.ts` 才能在不加载 uPlot
 * （模块加载期就要 `matchMedia`）的前提下，把侧栏 `NAV_SECTIONS` 和真实路由表对齐。
 * 唯一例外是 DEC-046 那条「两条路径不能挂同一个屏」，它动态 import 一次 `App.tsx`。
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
  // 复盘三屏（QUOT-344 / DEC-046）：同一组导航的三个独立入口，三条路径各自成屏，不共享屏
  // 每日复盘 = 当日五段数据（情绪 / 资金 / 题材 / 龙虎榜 / 龙头），`?date=`
  '/review',
  // AI 复盘 = AI 生成段落与降级/失败段（S1 占位壳，`?date=` 与正文由 S2 接）；
  // 用同族前缀，不用 `/ai-review` 破坏 `/review*` 语义
  '/review/ai',
  // AI 周报 = 按周聚合的叙述与事实包，`?week=YYYY-Www`
  '/weekly',
  '/journal',
  '/validate',
  // QUOT-534：策略回测（只读运行卡）挂在「验证」组，`?run=bt_xxxxxxxx` 深链
  '/backtest',
  '/limit-up',
  '/news',
  '/calendar',
  '/pool',
  // QUOT-431：自选与持仓合成一页两标签（`?tab=watch|hold`），旧地址只做重定向
  '/my-stocks',
  // QUOT-435：提醒（本机收件箱 + 定时任务 + 本机投递渠道），「我的股票」组第三项
  '/inbox',
  '/watchlist',
  '/positions',
  '/industry',
  '/industry/:name',
  '/signals',
  '/settings',
  '/research',
  '/research/:code',
  '/research/:code/report',
  // QUOT-474 / DEC-067：「研究工具」组的三条页路由。
  // `/skills` 是方向条目总览（70 条目录），`/skills/:id` 是单条方向详情（`id` = catalog 条目 id），
  // `/skills/pkg/:name` 是整包详情（`name` = index.json 里的包名）。8 个可执行包不各占一条页路由。
  '/skills',
  '/skills/:id',
  '/skills/pkg/:name',
  // QUOT-567：新增一级「知识库」。`/knowledge` 收代码后跳 `/knowledge/:market/:symbol`，
  // 档案页直接读后端 `GET /api/knowledge/:market/:symbol`；深链形如 `/knowledge/SH/600000`。
  '/knowledge',
  '/knowledge/:market/:symbol',
  '/index.html',
  '*',
] as const

export type RoutePath = (typeof ROUTE_PATHS)[number]

/** 不是「页面」的路径：404 兜底 + 旧地址重定向 + 由有入口页面链入的深链，本来就不该有侧栏入口。 */
export const NON_PAGE_ROUTES: readonly RoutePath[] = [
  '*',
  '/index.html',
  // QUOT-431：旧深链保留可用，但落点是 `/my-stocks?tab=watch|hold`，不再单独挂屏
  '/watchlist',
  '/positions',
  // QUOT-474 / DEC-067：`/skills/:id` 与 `/skills/pkg/:name` 由 `/skills` 列表页逐条链入，
  // 静态前缀（`/skills/pkg/`）落不进侧栏，按「有入口页面链入」处理。
  '/skills/:id',
  '/skills/pkg/:name',
]
