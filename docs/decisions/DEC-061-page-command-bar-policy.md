# DEC-061（已批准）只读页进入即取数：命令条只留有副作用或耗时长的操作

- 日期：2026-11-05
- 提出：项目领导人（QUOT-441，父任务 QUOT-425 追加需求第 4 条，原文标注「架构层，优先级最高」）
- 状态：已批准（QUOT-425 用户追加需求即批准口径）
- 编号：`DEC-061`。按 QUOT-441 任务书指定的号落盘；DEC-045 §2 的「最大号 + 1」在本单让位于并行分支占号——开工时本仓库 `docs/decisions/` 最大号为 055（DEC-055 已有两份并行文件），DEC-056–060 已被 QUOT-427 / QUOT-429 等并行任务预占，其中 `DEC-057-echarts-runtime-and-token-mapping.md` 已落在 `origin/agent/agent/41e808ead2af`。
- 关联：DEC-014（控制面白名单、argv 不接受请求参数）/ DEC-048（每页一键命令，本单是对它的裁剪而非推翻）/ DEC-049（数据面与模型面拆分）/ DEC-052、DEC-055（`/watchlist` 只读面与 `/positions` 去命令条，本单延续同一判据）/ DEC-029（产业 HTTP 三接口投影 R5 快照）/ DEC-006（数据集归属唯一真源）/ `src/ladderstock/core/privacy.py`（隐私边界）

## 1. 背景

`PageCommandBar`（`frontend/src/components/PageCommandBar.tsx:33`）由 `Layout.tsx:423` 全局挂载，按 `location.pathname` 精确命中后端 `PAGE_COMMANDS` 白名单（`src/ladderstock/web/control.py`）渲染按钮。白名单此前有 8 条：`market` / `review` / `review-ai` / `weekly` / `news` / `calendar` / `pool` / `validate`（`/positions` 已在 QUOT-419 / DEC-055 摘除）。

两类问题同时存在：

1. **只读页要求先跑命令**。`/calendar`、`/industry`、`/industry/:name` 的空态写着「先运行 `ldst events …`」「先运行 `ldst industry list|show` …」把终端命令当页面前置步骤，第一屏不可用。
2. **命令条混装了只读取数**。`calendar` 对应的 `ldst events` 是请求时现算（读本机库 + 调 peer），跑完不落盘——它既不是副作用，也不是必须的生成步骤，放在命令条上等于让用户误以为「必须先点一下才有数」。

三个只读 GET 早已存在且页面不需要前置命令：`positions_view()`、`watchlist_view()`、`events_view()`（`src/ladderstock/web/radar.py`，路由 `:1231-1285`）。`news/calendar.py:183` 的 `build_calendar()` 在每次请求里现算。行业三页不同：`/api/industry`、`/api/industry/{name}`、`/api/signals` 投影的是 R5 快照，快照由 `ldst industry list|show` 生成，属于「耗时长 + 有副作用」。

## 2. 决定

### 2.1 原则：命令条只放「有副作用或耗时长」的操作

判据只有两条，缺一不可：

- **有副作用**（写本机数据 / 抓网络入库 / 生成落盘产物），或**耗时长到不适合每次进页面都跑**；
- 该操作**没有一条等价的只读 GET** 能直接给页面取到同一份结果。

只读展示页（含「进入即渲染历史产物」的页）一律不放命令条：页面 mount 就调只读接口，缺数用空态说明「缺什么 + 为什么 + 页内重试」。

### 2.2 白名单分类表（本单落地后）

| key | 路由 | argv | 副作用 | 耗时 | 归类 |
| --- | --- | --- | --- | --- | --- |
| `market` | `/market` | `sync` | 抓取全量行情并落盘 | 分钟级 | **保留** |
| `review` | `/review` | `review --date {date}` | 生成当日复盘产物 | 分钟级 | **保留** |
| `review-ai` | `/review/ai` | `review --date {date} --force` | 调模型重生成 AI 正文 | 分钟级 | **保留** |
| `weekly` | `/weekly` | `weekly` | 生成周报产物 | 分钟级 | **保留** |
| `news` | `/news` | `news` | 网络抓取并入库 | 十秒级 | **保留** |
| `pool` | `/pool` | `pool` | 重算候选池并落盘 | 分钟级 | **保留** |
| `validate` | `/validate` | `validate reconcile --date {date}` | 写入收盘对账结果 | 十秒级 | **保留** |
| `industry` | `/industry` | `industry list` | 重算 R5 行业快照并落盘 | 分钟级 | **新增** |
| ~~`calendar`~~ | ~~`/calendar`~~ | ~~`events`~~ | 无（请求时现算，不落盘） | 秒级～十秒级 | **删除** |
| ~~`positions`~~ | ~~`/positions`~~ | ~~`position list`~~ | 无 | — | 已于 DEC-055 删除 |

无命令条的路由（本单逐条核对，空态只写缺数原因 + 页内重试）：`/`、`/watch`、`/intraday`、`/journal`、`/limit-up`、`/calendar`、`/watchlist`、`/positions`、`/industry`（仅保留命令条按钮，页面本身不再出终端文案）、`/industry/:name`、`/signals`、`/research`、`/research/:code`、`/settings`。

### 2.3 为什么 `events` 不算副作用

- `GET /api/events` → `events_view()` → `build_calendar()`：每次请求重新读本机库并调 peer，**不写任何文件**，同样参数重复调用得到同一结果（幂等读）。
- 因此页面 mount 直接 GET 就拿到全部结果；再要求先跑 `ldst events` 只是让同一份计算多跑一遍，且多写一条终端指令。命令条上的按钮会产生「必须点一下才出数」的错误心智模型。
- 对照 `industry`：`ldst industry list` 生成 R5 快照产物，`/api/industry` 只投影该产物；不生成就没有数据，生成耗时且写盘——符合命令条判据。
- 对照 `news`：`ldst news` 抓取入库写 `news` 数据集，`/api/news` 只读缓存，生成与否结果不同——保留。

### 2.4 `/industry` 的页内入口与 `/industry/:name` 的边界

- `industry` 条目固定为 `("/industry", "重算行业快照", ("industry", "list"))`，`PageCommandBar` 的 `route === pathname` 精确匹配使它在 `/industry/:name` **不命中**（这是有意的：详情页不该触发全量重算）。
- 详情页空态改为「行业快照由总览页生成；回行业总览页点「重算行业快照」后重试」，不提供第二条命令入口。
- `argv` 仍是常量元组，不接收请求参数（DEC-014 不变）；`industry list` 不接受任何前端可改写的参数。

### 2.5 空态文案规则

- 禁止「先运行 `ldst …`」式终端指令；改为**缺什么 + 为什么 + 页内动作**（重试按钮，或指向命令条按钮名）。
- 唯一例外是**服务未启动**：`PageShell.tsx:123-146` 与 `SearchPanel.tsx:546` 的「`ldst run` 未启动」，由顶栏健康灯任务统一处理，本单不动。
- 缺数一律 `available=false` + `reason`，前端显示 `—`（`frontend/src/lib/format.ts` 的 `MISSING`），不补 0、不插值、不用骨架屏冒充。

### 2.6 隐私边界与 CLI 不变

- `journal` / `positions` / `watchlist` 仍只读本机 `data/journal.sqlite`，平面 JSON + `Cache-Control: no-store` + 回环；不进 `LadderEnvelope`、不被 MCP 读、不注入 AI 上下文。
- `src/ladderstock/core/privacy.py` 的 `PRIVATE_KEYS` 不动；`_positions_guard` / `_watchlist_guard` 保持。
- **CLI 一条不删**：`tests/fixtures/command_matrix.py` 既有条目不动。本单只删 Web 白名单里的一行 `calendar` 并加一行 `industry`，CLI 仍是权威接口。

## 3. 后果与风险

- **`/calendar` 首屏变慢**：每次进入都会触发一次 peer 取数（此前用户点「刷新」才取）。日历本身有 `limit` 上限，且页面保留「刷新」；若后续 peer 抖动明显，应在 `events_view` 的缓存层解决，而不是恢复命令前置。
- **精确匹配的副作用**：`/industry/:name` 拿不到按钮属预期；若用户从详情页进入，需要先回总览。文案已明确指向。
- **新页面准入**：今后新增只读页不得加入 `PAGE_COMMANDS`；只有同时满足 2.1 两条判据的写/生成操作才可加入，避免命令条重新膨胀。

## 4. 备选方案（否决）

1. **保留 `calendar` 命令「保险起见」**：等于承认页面首屏不可用，并让同一份现算逻辑跑两遍。否决。
2. **`industry` 同时挂 `/industry/:name`**：命令条按 pathname 精确匹配；在详情页触发全量重算既贵又不是用户意图。否决。
3. **只改文案、白名单不动**：命令条上仍会出现「刷新事件日历」，与「进入即取数」并存，心智模型仍然混乱。否决。

## 5. 落点

- `src/ladderstock/web/control.py`：删 `calendar`、加 `industry`。
- `frontend/src/screens/radar/CalendarScreen.tsx`、`frontend/src/screens/industry/IndustryMatrixScreen.tsx`、`IndustryDetailScreen.tsx`：空态改为缺数原因 + 页内动作。
- 其余只读页（`/`、`/watch`、`/limit-up`、`/research*`、`/settings` 等）逐条清理终端指令。
- 测试：`frontend/tests/readonly-mount-fetch.test.tsx`（三屏 mount 即取数 + 空态文案）、`tests/unit/web/test_control_plane.py`（白名单条目与 404 断言）。
- 规范：`docs/UI-SPEC.md` 版本行与变更记录各追加一行。
