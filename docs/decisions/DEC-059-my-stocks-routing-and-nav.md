# DEC-059 我的股票路由与导航：一页两标签、旧路径重定向

- 日期：2026-11-06
- 提出：产品经理（QUOT-440，父任务「按 vibe-astock 整体复刻我的股票三模块」）
- 状态：已采纳（外壳对应实现 QUOT-431，组内第三项 `/inbox` 随 QUOT-435、`hold` 标签的
  账本内容随 QUOT-434 先后合入 `origin/main`；本版按 `6db2cd7` 复核，只补事实、不改决定）
- 编号：`DEC-059`（任务书预定号，`docs/decisions/` 无同名文件）
- 关联：DEC-044（「我的股票」组创立）、DEC-046（复盘导航拆分）、
  DEC-052 / DEC-055（自选读写面）、DEC-058（收件箱与提醒入口）、
  DEC-061（只读页进入即取数）、DEC-064 / DEC-065（持仓账本与收益）、
  QUOT-431 / QUOT-434 / QUOT-435
- 生效版本：`docs/UI-SPEC.md` v1.19、`docs/specs/SPEC-RESEARCH-PAGES.md` v0.5

## 1. 背景

DEC-044 把「我的持仓 `/positions`」与「事件日历 `/calendar`」聚成侧栏「我的股票」组，
并明确「自选、提醒没有已交付页面，不建占位入口」。此后自选页、持仓账本、K 线卡陆续落地，
参考产品的「自选与持仓」又是一个页面里的两个标签。如果继续保留 `/watchlist` 与
`/positions` 两个独立页面，会出现：

- 同一组里两个近义入口，用户要先判断「我想看的东西属于哪个词」；
- 自选与持仓的 K 线、批量添加、隐私遮罩等能力重复实现；
- 旧深链（首页卡片的「管理自选 →」、历史书签）需要长期维护两套页面。

## 2. 决定

### 2.1 一页两标签

- 唯一页面路由：`/my-stocks`；
- 标签是查询参数 `?tab=watch|hold`，不是两条路由：
  - `tab=watch` → 自选（默认）；
  - `tab=hold` → 持仓；
- 解析顺序：URL `?tab=` → `localStorage['ldst.my-stocks.tab']` → `watch`；
  切标签时用 `replace` 写回 URL，并同步本机偏好；
- 页内标题「我的股票」，标签文案「自选 / 持仓」。

### 2.2 旧路径只重定向，不再挂屏

- `/watchlist` → `/my-stocks?tab=watch`（`replace`）；
- `/positions` → `/my-stocks?tab=hold`（`replace`）；
- 两条旧路径列入 `frontend/src/routes.ts` 的 `NON_PAGE_ROUTES`，不进入侧栏、
  不计入屏数；首页卡片至今仍链接旧地址（「管理自选 →」`/watchlist`，
  `HomeWatchlistCard.tsx:107,178`；「看明细 →」`/positions`，`HomePositionsCard.tsx:190`），
  靠重定向落地，两条入口至今没有直连 `/my-stocks`。

### 2.3 侧栏只留一个「我的股票」入口（组内三项）

「我的股票」组当前是三项（`frontend/src/lib/nav.ts:113-122`，QUOT-435 合入后）：

1. `我的股票` → `/my-stocks`
2. `事件日历` → `/calendar`
3. `提醒` → `/inbox`

- 不为 `watch` / `hold` 各建一个侧栏入口；
- 不建 `/watchlist`、`/positions` 的独立入口；
- **本条写作时定的是「`/inbox` 不建占位入口：没有页面就放入口会让用户点进 404」**。
  QUOT-435 交付页面后该前提消失，第三项按原计划补上，**不是**推翻本决策；
  「不建占位入口」的原则仍然生效，`remind` 这类没有页面的名字依旧不许进导航
  （`frontend/tests/nav-routes.test.tsx:171` 断言组内恰好这三项）。

### 2.4 三处路径清单必须同步

路由、导航、测试是同一件事的三个投影：

- `frontend/src/routes.ts` 的 `ROUTE_PATHS` / `NON_PAGE_ROUTES`；
- `frontend/src/App.tsx` 的 `ROUTE_ELEMENTS`（含两条 `Navigate`）；
- `frontend/src/lib/nav.ts` 的 `NAV_SECTIONS`；
- `frontend/tests/nav-routes.test.tsx` 双向断言：旧路径重定向到精确目标、
  组内是「我的股票 / 事件日历 / 提醒」三项、`/inbox` 在导航里且不在
  `NON_PAGE_ROUTES` 里（`:171,205-221`）。

## 3. 理由

- **用户心智是「我的股票」一件事**：自选的票和持有的票是同一批标的的两种关系，
  一页切换比两个页面更接近参考产品，也少一次导航决策；
- **URL 仍然是唯一深链**：标签进查询串，分享 `/my-stocks?tab=hold` 能直达持仓，
  与 `?tab=` 在本仓其它页面（`/market`）的既有用法一致；
- **旧链接不断**：首页、历史书签、外部文档里的 `/watchlist` / `/positions` 继续可用，
  迁移成本为零；
- **不提前建提醒入口**：写本决策时没有页面，放入口会让用户点进 404；DEC-044 的
  「不建占位入口」原则至今有效——QUOT-435 交付页面后入口才出现，顺序也是先页面后入口。

## 4. 代价与风险

- **两个旧路径长期存在**：重定向逻辑要一直保留，直到确认没有外部引用；这是可接受的
  小成本，删除会造成书签失效。
- **标签状态有本机记忆**：URL 没带 `?tab=` 时，用户可能被上次的标签「带走」；
  这是有意为之（刷新不丢上下文），但分享裸 `/my-stocks` 时接收方看到的是自己的记忆。
- **屏数口径**：`/my-stocks` 替换原「我的持仓」一屏，屏幕总数不变；
  UI-SPEC 的 §0 / §10 必须同步，否则会出现「20 屏里有一屏不存在」。
  （同批 QUOT-435 把 `/inbox` 记为第 21 屏，UI-SPEC v1.19 已按 21 屏重算 §0 / §4.5 / §10 / §11。）
- **持仓内容由 QUOT-434 换掉，外壳不变**：本决策只定路由与导航；`hold` 标签现在渲染
  `PortfolioPanel`（流水重放 + 成本法 + 收益 / 流水两子页，`MyStocksScreen.tsx:69`），
  旧 `radar/PositionsScreen.tsx` 不再挂在任何路由上。标签、重定向、`?tab=` 口径零变更，
  这正说明外壳与内容是解耦的。

## 5. 影响文件

- `frontend/src/routes.ts`（`ROUTE_PATHS`、`NON_PAGE_ROUTES`）
- `frontend/src/App.tsx`（`/my-stocks:85`、`/inbox:87`、`/watchlist:89`、`/positions:90` 两条 `Navigate`）
- `frontend/src/lib/nav.ts`（`my-stocks` 组三项，`:113-122`）
- `frontend/src/screens/my-stocks/MyStocksScreen.tsx`（标签解析与记忆）
- `frontend/src/screens/my-stocks/WatchlistPanel.tsx`（自选标签内容）
- `frontend/src/screens/my-stocks/PortfolioPanel.tsx`（持仓标签内容，QUOT-434）
- `frontend/src/screens/my-stocks/InboxScreen.tsx`（`/inbox`，QUOT-435）
- `frontend/tests/nav-routes.test.tsx`、`frontend/tests/my-stocks-screen.test.tsx`、
  `frontend/tests/portfolio-panel.test.tsx`、`frontend/tests/inbox-screen.test.tsx`、
  `frontend/tests/readonly-mount-fetch.test.tsx`
- `docs/UI-SPEC.md` §0 / §2 / §3.11 / §3.21 / §4.5 / §10 / §11.11 / §11.21

## 6. 生效版本与验收

- 生效：QUOT-431 合入起；文档随 `UI-SPEC` v1.19 登记。
- 验收：
  - `/watchlist` 与 `/positions` 分别重定向到 `?tab=watch` / `?tab=hold`，且 `replace`；
  - 侧栏「我的股票」组是「我的股票」「事件日历」「提醒」三项，且没有 `remind` 这类
    无页面的入口（`tests/nav-routes.test.tsx:171`）；
  - 切换标签写回 URL 与本机偏好，刷新后保持；
  - `routes.ts` / `App.tsx` / `nav.ts` 与测试四处一致。
