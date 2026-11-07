# DEC-058 本机收件箱与摘要任务：只走本机渠道，不接 webhook

- 日期：2026-11-06
- 提出：产品经理（QUOT-440，父任务「按 vibe-astock 整体复刻我的股票三模块」）
- 状态：已采纳（后端与 CLI 随 QUOT-432 合入 `origin/main`；`/inbox` 页面随 QUOT-435 合入，
  本版按合入后的代码复核）
- 编号：`DEC-058`（任务书预定号，`docs/decisions/` 无同名文件；代码注释
  `journal/inbox.py`、`web/inbox.py`、`watch/digest.py` 已引用本号）
- 关联：DEC-056（fast polling 的调度间隔，不同任务）、DEC-065（schema v3，`inbox` 表随 v3 建）、
  QUOT-432
- 生效版本：`SCHEMA_VERSION = 3`；`docs/UI-SPEC.md` v1.19；
  `docs/specs/SPEC-RESEARCH-PAGES.md` v0.5

## 1. 背景

参考产品的「提醒」是一条**可回看的收件箱**：定时任务把当天的盘前、收盘、自选变化整理成
一条摘要，先落本机，再按用户配置的渠道提醒。本仓在此之前只有「盯盘信号」，
没有跨日、可回看的提醒记录；`ldst notify test` 只能验证通知链路，不保存内容。

同时有两个边界必须先钉死：

1. 这是**本机私有数据**（持仓、自选、盈亏），不能进 `LadderEnvelope`、不进 MCP、不进 AI；
2. 通知只允许**本机渠道**，不引入 webhook、第三方推送或任何需要凭据的出网路径。

## 2. 决定

### 2.1 `inbox` 表与去重

- 表结构（随 schema v3）：
  `id TEXT PRIMARY KEY / job / job_label / kind / title / lines_json / link /
  dedupe_key / read_at / created_at`；
- `id` 为 `uuid4().hex`；`lines_json` 是字符串数组，单行最多 500 字、最多 40 行；
- **一条摘要只占一行**：`dedupe_key = f"{job}:{trade_day}"`，命中唯一部分索引时更新
  标题 / 正文 / 时间，`read_at` **原样保留**——同日补跑不刷第二条，也不把已读改回未读；
- `read_at` 用时间戳而非布尔值，既能显示「何时读的」，也避开 SQL 关键字。

### 2.2 三个摘要任务

| job id | 名称 | summary | 触发 | 渠道 | 内容 |
|---|---|---|---|---|---|
| `premarket_digest` | 09:05 盘前摘要 | `premarket` | 每日 09:05 | `console` | 盘前事实摘要；11:30 后跳过，周末跳过 |
| `close_digest` | 15:40 收盘摘要 | `close` | 每日 15:40 | `console` | 当日涨跌、自选表现、当日已实现与分红 |
| `watch_changes` | 16:15 自选变化 | `watch` | 每日 16:15 | `console` | 自选单日变化；只有一天数据时明确写「不可比」 |

- 任务类型 `kind = digest`；`summary` 只允许 `premarket / close / watch`；
- 生成失败折叠为 `status = failed`，不抛到调度器；上游缺数写「不可用」，不补 0；
- 先写收件箱、再发通知；通知失败只把任务标成 `degraded`，收件箱那行仍在。

### 2.3 只走本机渠道

- `CHANNELS = ("console", "desktop")`，默认 `console`；
- `console` 写标准错误（前缀「ldst 通知」）；`desktop` 走本机 toast
  （Windows `winotify` / `plyer.notification`），或用户显式配置的本机命令；
- **不接 webhook、不接第三方推送、不发网络请求**；
- **没有免打扰 / 静音 / 时段设置**：不提供空 UI，也不建占位开关。

### 2.4 只读 / 写面

- `GET /api/inbox?limit=1..200`（默认 50）：平面 JSON，顶层
  `local_only / store / items / unread / jobs`；
  `items[]` = `id / job / job_label / kind / title / lines / link / is_read / read_at /
  created_at`；`jobs[]` 只列 `kind in ("digest","notify")` 的任务，含 `id / label /
  trigger / enabled`，不返回 `last_status`；
- `POST /api/inbox/{id}/read` 与 `POST /api/inbox/read-all` 标记已读；
- 全部回环 + `Host` 白名单，写面另要同源证据，响应 `no-store`；
- 读接口在库不存在时返回空结果且**不建库**。

## 3. 理由

- **先落盘再通知**：通知渠道可能没装、可能失败，收件箱是唯一可靠的回看入口；
- **同键更新而不是追加**：盘前摘要可能因补跑执行多次，用户只需要当天一条；
- **只走本机**：提醒内容包含持仓与自选，出网即越过本产品的隐私边界；
- **不做免打扰**：没有可靠的跨平台系统级静音实现，做一个假开关比不做更糟。

## 4. 代价与风险

- **页面只呈现后端给得出的部分**：`/inbox` 随 QUOT-435 落地（`frontend/src/screens/my-stocks/InboxScreen.tsx`
  + 独立取数层 `frontend/src/api/inbox.ts`），版面见 UI-SPEC §3.21 / §11.21。参考产品里的
  「免打扰 / 渠道开关 / 发送测试 / 第三方推送」在参考库存在、在本仓**故意不做**，页面因此
  只有只读的任务清单与两条本机渠道，不摆任何推不开的空控件。
- **默认渠道是 stderr**：不开终端就看不到；`desktop` 依赖可选的系统通知库，
  未安装时只降级、不报错。用户需要知道「收件箱才是主入口」。
- **摘要质量受上游限制**：盘前 11:30 后不生成、周末不生成；某类事实缺源时摘要会变短，
  这是如实呈现，不是 bug。
- **无推送意味着无实时性**：本决策不承诺「到点弹窗必达」，只承诺「当天可回看」。

## 5. 影响文件

- `src/ladderstock/data/sqlite_store.py`（`inbox` 表与索引，随 schema v3）
- `src/ladderstock/journal/inbox.py`（读写、去重、已读）
- `src/ladderstock/web/inbox.py`（`GET /api/inbox`、两条标已读）
- `src/ladderstock/watch/digest.py`（三类摘要生成）
- `src/ladderstock/watch/notifier.py`（`console` / `desktop` 本机渠道）
- `src/ladderstock/cli/schedule.py`、`config/schedule.yaml`（三个任务）
- `tests/unit/journal/test_inbox.py`、`tests/unit/web/test_inbox_endpoints.py`、
  `tests/unit/cli/test_schedule_digest.py`
- `frontend/src/screens/my-stocks/InboxScreen.tsx`、`frontend/src/api/inbox.ts`（QUOT-435）
- `frontend/src/routes.ts`、`frontend/src/App.tsx`、`frontend/src/lib/nav.ts`（组内第三项）、
  `frontend/tests/inbox-screen.test.tsx`、`frontend/tests/nav-routes.test.tsx:171,205-221`
- `docs/UI-SPEC.md` §3.21 / §10 / §11.21（v1.19 登记版面）、`docs/specs/SPEC-RESEARCH-PAGES.md` §8

## 6. 生效版本与验收

- 生效：schema v3；后端与 CLI 随 QUOT-432 合入；页面随 QUOT-435 合入。
- 验收：
  - 同一 `dedupe_key` 重跑只保留一行，`read_at` 不变；
  - 三个任务 id 与触发时间在默认配置和仓库 `schedule.yaml` 中一致；
  - 通知失败时收件箱仍有记录且任务为 `degraded`；
  - `GET /api/inbox` 顶层只有五个键，`jobs` 只列三个摘要任务，无 `last_status`；
  - 全仓无 webhook / 免打扰实现（`grep` 为零命中）；
  - 页面侧：渠道卡只有 console / desktop 两条且无「发送测试」按钮，任务卡没有任何开关，
    外链不可点，未读数来自后端 COUNT（`tests/inbox-screen.test.tsx:202,215,227`）。
