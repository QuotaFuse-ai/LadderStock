# DEC-046 复盘组拆三独立入口：每日复盘 / AI复盘 / AI周报，路由与侧栏同源注册

- 日期：2026-11-03
- 提出：老板（QUOT-344 拍板「不合并，单独导航」，对齐参考侧 `vibe-astock` 的三屏信息架构）
- 状态：生效（导航与路由面已落地；`/review/ai` 正文结构归 QUOT-344-S2，不在本决策内）
- 关联：QUOT-344、QUOT-355（本决策的 S1 实现单）、QUOT-149（周度复盘原始交付）、
  QUOT-366（收口审计，本文件按其要求落地）、DEC-045（编号与变更记录规则）、DEC-044（导航分组先例）、
  `frontend/src/routes.ts`、`frontend/src/App.tsx`、`frontend/src/components/Layout.tsx`、
  `frontend/src/screens/AiReviewScreen.tsx`、`frontend/tests/nav-routes.test.tsx`、`docs/UI-SPEC.md` §0/§2/§3.7/§10

## 1. 背景

复盘组此前只有两条路径挂两个屏（`/review` 情绪复盘、`/weekly` 周度复盘）+ 候选池：当日五段数据与
AI 生成段落挤在同一屏里，周度叙述又与每日叙述共用文案口径。老板 2026-11-02 拍板不做屏幕合并，
拆成三个各自可达的入口。

QUOT-366 收口审计同时暴露一个流程事实：S1 首轮把分支推到远端却没开 PR、没合 `main`，
且 `Layout.tsx` 的注释仍写着「『每日复盘』是 P1 留位（🟡），本轮不建入口」——旧口径留在代码里，
main 上检不出三入口。本决策把「三入口 + 三条路由 + 旧口径删除」写成可验收的条文。

## 2. 决定

1. **复盘组是三个数据入口 + 候选池，共四项**，顺序与标签固定：

   | 入口 | 路由 | 屏 | 分工 |
   |---|---|---|---|
   | 每日复盘 | `/review` | `ReviewScreen` | 当日五段数据：情绪 / 资金 / 题材 / 龙虎榜 / 龙头，`?date=` |
   | AI复盘 | `/review/ai` | `AiReviewScreen` | AI 生成段落与逐段状态、降级与失败段 |
   | AI周报 | `/weekly` | `WeeklyScreen` | 按周聚合的事实包与叙述，`?week=YYYY-Www`，缺省本周 |
   | 候选池 | `/pool` | `PoolScreen` | 复盘的产出，位置与文案不变 |

2. **AI 复盘独立成屏，路由用同族前缀 `/review/ai`**，不用 `/ai-review`——后者破坏 `/review*` 的前缀语义，
   也让「复盘家族」在路由表里散开。
3. **路由与侧栏同源注册**：`routes.ts` 的 `ROUTE_PATHS` 是唯一路径清单，`App.tsx` 的
   `ROUTE_ELEMENTS: Record<RoutePath, ReactElement>` 因类型系统强制「加路径必写组件」，
   `Layout.tsx` 的 `NAV_SECTIONS` 是唯一入口清单，三者由 `nav-routes.test.tsx` 双向对齐。
   导航注册的落点是 `NAV_SECTIONS`，不新增第二个导航组件。
4. **不允许「两条路径指向同一个未拆分屏」成为终态**：S1 阶段 `/review/ai` 允许是占位壳
   （导航与深链可用、正文不伪造、不从 `/api/review` 猜渲染），但必须是独立组件；
   这条由测试读组件表断言，路径比对看不出同组件挂载。
5. **高亮按 pathname 精确匹配**：`/review` 亮时 `/review/ai` 不跟着亮。
6. **文案改动不连带改路由**：`/weekly` 只把侧栏标签从「周度复盘」改为「AI周报」，路由、深链、
   后端契约零变更；屏内 `h1` 的口径归 S2。
7. **旧口径删除**：`Layout.tsx` 中「『每日复盘』是 P1 留位（🟡），本轮不建入口」注释作废，
   替换为三入口分工说明；`docs/UI-SPEC.md` 的屏数与导航顺序同步。
8. **本轮不新增后端接口与字段**：AI 段落与数据段落同源，父单 QUOT-344 已判定无需后端改动。

## 3. 备选与取舍

| 方案 | 取舍 |
|---|---|
| `/review?tab=ai` 同屏切页签 | 与老板「单独导航」拍板相反，且 `/review` 会承载两屏内容，否决 |
| `/ai-review` 平铺新路径 | 破坏 `/review*` 前缀语义，家族路径散开，否决 |
| 两条路径挂同一个 `ReviewScreen`（仅导航拆） | 「拆了导航没拆屏」，用户点进去看到同一屏——正是本决策要防的退路，写成测试断言，否决 |
| 新建 `ReviewNav.tsx` 承接三入口 | 导航唯一事实源已是 `Layout.tsx` 的 `NAV_SECTIONS`，第二处注册必然漂移（QUOT-366 检出的未跟踪文件就是这种漂移的产物），否决 |
| 屏内内容重排一并放进 S1 | 一个 PR 改两屏，与 S2 冲突，否决（内容重排归 QUOT-344-S2） |

## 4. 影响

- 侧栏「复盘」组 3 项变 4 项，看板总屏数 19 → 20（`docs/UI-SPEC.md` §0 表新增第 20 行）。
- `rail:`（1024–1279）档展平后图标行由 19 变 20；AI复盘取 `Bot`，不复用底部「接入 AI」的
  `Sparkles`——该档只剩图标可辨，两个 Sparkles 分不开。
- 深链新增一条：`/review/ai`。既有 `/review`、`/weekly`、`/pool` 的 URL、IndexedDB 键、后端端点零变更。
- `docs/UI-SPEC.md` 变更记录按 DEC-045 §4 合并成 **v1.6 一条**覆盖整条 PR 链（S1+S2），不逐 PR 刷行。
- **编号占用**：按 DEC-045 §1，本链开单时 `git ls-tree -r --name-only origin/main docs/decisions/`
  扫到的最大号 = 045，取 046。本文件落地时 `main` 仍是 045；另有三条未合并分支
  （QUOT-349 rate-limit、QUOT-347 schedule job、QUOT-290 连板键）各自也预占了 DEC-046。
  按 DEC-045 §3，未合并分支上的 DEC 不算已占用，让号成本由零反向引用一方承担：
  本文件与 `docs/UI-SPEC.md` v1.6、`nav-routes.test.tsx`、`AiReviewScreen.tsx` 四处互相引用，
  合并时以**先合入 `main` 的一方保留 046**，其余按扫描时实号让号并在合并说明里标注引用方。
- 回滚方式：还原 `Layout.tsx` 的 `NAV_SECTIONS` 单项、删 `routes.ts` 的 `/review/ai` 与
  `App.tsx` 对应组件项、删除本文件与 v1.6 变更记录行；类型系统会拦住只删一半的中间态。

## 5. 证据

- `frontend/src/routes.ts`（`/review` `/review/ai` `/weekly` 三条路径与分工注释）
- `frontend/src/App.tsx`（`ROUTE_ELEMENTS` 注册，`/review/ai` → `AiReviewScreen`）
- `frontend/src/components/Layout.tsx`（`NAV_SECTIONS` review 组四项，旧「P1 留位」注释已删）
- `frontend/src/screens/AiReviewScreen.tsx`（占位壳，正文待接入徽标 + 去每日复盘的出口）
- `frontend/tests/nav-routes.test.tsx`（三入口顺序与文案、路径互不相同且各自深链命中、
  `/review` 与 `/review/ai` 非同一组件）
- `docs/UI-SPEC.md` §0（20 屏）、§2（外壳图与导航顺序）、§3.7（AI周报入口行）、§10（映射行）、变更记录 v1.6
- 截图：`docs/screenshots/review-after-344-dark.png`、`review-ai-after-344-dark.png`、
  `review-ai-after-344-1024-dark.png`、`weekly-after-344-dark.png`、`pool-after-344-dark.png`
