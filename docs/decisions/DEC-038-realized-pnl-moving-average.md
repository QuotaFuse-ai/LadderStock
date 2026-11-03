# DEC-038 已实现盈亏：移动加权平均、只读复算、与 journal stats 同源不同口径

- 日期：2026-11-01
- 提出：项目领导人（QUOT-292）；口径由总经理 2026-10-31T23:02Z 裁决（Q1 = A 移动加权、
  `journal stats` 的 FIFO 不动；Q2 批准，只加 `unheld_pnl` + `unheld_codes` 两个只读字段）
- 状态：生效（随 QUOT-292 实现落地）
- 编号说明：038 是本单**预占**编号；030–036 一律不动、不重排，037 已由 QUOT-291
  （`DEC-037-watch-settings-persistence`）占用，本文不碰。编号在本仓已撞过两次，只增不改。
- 关联：DEC-006（数据只落本机）、DEC-015 Tier 1 / DEC-020（隐私库不进信封与 MCP）、
  DEC-033（持仓写面：`positions` 是唯一真相、不引入流水重放）、
  `SPEC-MY-POSITION` §3/§4/§5/§7、`SPEC-RADAR-PAGES` §6.1

## 1. 背景

`SPEC-MY-POSITION` §7 原来写着「不做已实现盈亏：持仓只算浮动盈亏，`journal stats` 管纪律统计」。
实际效果是：**卖出的钱落袋了就看不见**。持仓表里一条 `qty` 是「现在还剩多少」，
减仓那部分的收益在本产品里没有任何出口——用户只能自己去翻成交流水。

本机已经有全部原料：`journal` 的 `trades` 表（`code` / `side` / `price` / `qty` / `trade_date`），
`journal stats` 已经在用它做 FIFO 逐笔配对。缺的只是一个**读侧的另一种口径**，不是缺数据。

所以本决策的约束是三条硬的：

1. **只读**——不建表、不改 `positions` 结构、不新增写接口（那是 DEC-033 的边界）；
2. **不新增第二份真相**——已实现盈亏从流水复算，绝不写回 `positions`；
3. **同一份计算结果两个出口**——`ldst position list` 与 `GET /api/positions` 不许各算一遍。

## 2. 决定

### 2.1 成本法：移动加权平均（WAVG），不是 FIFO

卖出时结转的成本 = **卖出时点**的加权成本单价 × 成交数量：

```
买入：  qty += n；cost_total += n × price
        加权成本单价 u = cost_total / qty
卖出：  结转成本 = u × n；已实现 = n × price − u × n
        qty −= n；cost_total −= u × n        → 剩余持仓的成本单价 u 不变
```

加仓会摊薄单价（买 1000@10 + 买 1000@20 → u=15），卖出不吃掉「最早那一批」。
选它的唯一理由：**这是 A 股券商对账单的口径**，用户拿本机的数能和账户里的「清仓盈亏」对上。
FIFO 的逐笔闭环适合纪律复盘，但对账单上不存在「第一笔先卖」这件事。

`realized.method = "moving_weighted_average"` 写在出口字段里，不靠文档约束。

### 2.2 与 `journal stats` 的「同源」是什么，不是什么

两者吃同一张 `trades` 表，但**口径不同，不承诺任意场景同数**（这一点必须写明，
否则对账会被理解成 bug）。可复现的同源含义有三层，测试逐层钉住：

1. **同一条时间线**：同一个 `journal.log.list_trades()`，同一 `ORDER BY (trade_date, id)`，
   同一取数上限（`TRADE_SCAN_LIMIT` 与 `journal/stats.py` 一致）；
2. **全部清仓时逐分不差**：某 code 的仓位清光后
   `已实现 = 卖出总额 − 买入总额`，与结转法无关 → 与 `journal stats` 的 FIFO 数值相等；
3. **未平仓时差额有恒等式**，不是「大约」：

```
FIFO 已实现 − WAVG 已实现  ≡  FIFO 剩余成本基础 − WAVG 剩余成本基础
```

用例（本仓测试用的就是这组数）：买 1000@10 + 买 500@16，卖 600@14 →
FIFO 已实现 `8400−6000=2400`、WAVG `8400−7200=1200`，差 `1200`；
剩余基础 FIFO `400@10+500@16=12000`、WAVG `900@12=10800`，差正是 `1200`。

**两个数都正确，不是一个是错的**（裁决原文要求写进 spec）：纪律复盘要逐笔配对，FIFO 是它的正确口径；
账户对账要券商口径，移动加权是它的正确口径。两条禁令同时成立——
**不许为了对齐数字去改 `journal stats`**（QUOT-196 的已交付契约，胜率与盈亏比的定义会跟着变），
**也不许拿 FIFO 偷算再标成移动加权**（`realized.method` 必须如实是 `moving_weighted_average`）。

### 2.3 计算只有一处，读流水只有一处

- 引擎：`journal/realized.py::compute_realized(trades)`——纯函数，不 import `sqlite3`、
  不读库、不联网、不重排输入；
- 读流水：`journal/position.py::list_journal_trades()`——薄封装 `list_trades(limit=…)`；
- 投影：`journal/position.py::compute_metrics(rows, prices, as_of=…, trades=…)`——
  **CLI 与 `GET /api/positions` 调的是同一个函数**，`realized` 块因此不可能分叉。

为什么不直接在 `cli/position.py` / `web/radar.py` 里 import `journal.log`：
`scripts/check_privacy.py` 的 `JOURNAL_IMPORT_ALLOWLIST` 对 `web/radar.py` 只放开
`journal.position` 一个模块（对 `web/server.py` 只放开 `journal.stats`）。
放开口子等于给数据面再加一条隐私库通路，所以流水读取收在 `journal/position.py` 后面，
接口层仍然只看见「持仓」这一个入口。**裁决要求的「隐私白名单不放开」就是这一条：
`scripts/check_privacy.py` 一行未改。**
唯一跟着动的是 `tests/integration/test_position_privacy.py` 里那条 AST 断言的允许前缀：
`("ladderstock.data", "ladderstock.errors")` → 再加 `ladderstock.journal.`，
放的是**同侧隐私库内部互引**（`position` 读 `log` / `realized`），
测试名 `test_position_does_not_import_market_or_ai` 不变，行情 / AI / MCP / 候选池依旧全禁。

### 2.4 空态：`null` 与 `0.0` 是两件事

| 情形 | 出口 |
|---|---|
| 本机没有任何成交记录 | `total_pnl = null`、每个 code `realized_pnl = null`、`status="missing"` + `reason` |
| 有流水、这只票没卖过 | `realized_pnl = 0.0`（实现了 0，与「没数据」不同） |
| 调用方没传流水（`trades=None`） | 整块不计算，`reason` 说明「调用方未传入」，与「本机没记过」区分 |

「没记过」不等于「不赚不亏」，沿用 `valuation.unrealized_pnl` 的 null 纪律。

### 2.5 手续费：毛额口径，不新增假设字段

`trades` 表**没有费用列**（列是 `trade_date` / `code` / `side` / `price` / `qty` /
`plan_price` / `note` / `created_at`）。
所以：

```
已实现 = 卖出金额 − 结转成本          （不含佣金/过户费/印花税）
```

不为此加字段、不猜费率、不拿 `note` 里的文字做解析。出口显式声明
`realized.fee_included = false`，页面与 CLI 都要显示这个前提，
免得用户把毛额当成对账单最终数。加费用列属于表结构与写面变更（DEC-033 的范围），不在本决策。

### 2.6 出口字段形状

```
realized = { method, fee_included, status, reason,
             buy_count, sell_count, matched_sell_qty, unmatched_sell_qty,
             total_pnl, held_pnl, unheld_pnl, unheld_codes }
items[i].realized_pnl                       # 该 code 的已实现，未卖过是 0.0，无流水是 null
```

`held_pnl` / `unheld_pnl` 是必须的，不是锦上添花：
`positions.code` 仍然 UNIQUE 且减仓要走 `update`，**全部清仓的票通常已经从持仓表里删掉了**。
只按持仓行展示 `realized_pnl`，落袋的收益就又消失了（回到 §1 的问题）；
而只报 `total_pnl` 又会出现「合计 ≠ Σ 明细」。所以合计拆成「在手 + 已不在持仓里」，
`unheld_codes` 给出是哪些票。

硬约束（裁决 Q2）：

- `realized.total_pnl == Σ items[].realized_pnl + unheld_pnl`，由单测
  `test_realized_total_is_items_plus_unheld` 用**两种形状**钉住：「已清仓的票」
  （持仓存在，另一只 code 已 remove）与「未持仓但有流水」（持仓表为空）；
- `unheld_codes` **只是 code 列表**（按 code 升序），不带成本、不带明细——
  本单不把它扩成第二套账；
- Q2 批准的只有这两个只读字段，其余出口字段都服务于 §2.4/§2.5 已定的口径说明。

### 2.7 卖出多于在手：如实计数，不编造成本

流水可能从持仓中途开始记（先记了卖、没记买）。只结转在手的部分，
多出来的数量进 `unmatched_sell_qty`，**不假设一个成本价**。
结转后 `qty ≤ 0` 时把 `qty` / `cost_total` 归零，清掉浮点尾巴（否则下一次买入会接着 `−1e-16` 的成本额）。

### 2.8 舍入只在出口做一次

引擎返回未舍入原值；`compute_metrics()` 出口对金额舍入到 2 位、数量到 4 位。
逐条舍入再相加会凑出多余的分（同 `SPEC-MY-POSITION` §4 的 `top3_pct` 纪律）。

## 3. 明确不做

- 不引入 `trade` 流水重放来生成持仓（DEC-033 §1 的差异不变：`positions` 表仍是唯一持仓真相，
  流水只用来**复算已实现盈亏**，不覆盖 `qty` / `cost`）；
- 不做 FIFO / WAVG 可切换（两个口径各归各的出口：`journal stats` 纪律复盘、`position list` 对账单）；
- 不加手续费 / 分红 / 送转字段，不做除权除息调整；
- 不改 `positions` 表结构、不新增写接口、不把已实现盈亏写回任何表；
- 不进 `LadderEnvelope`、不进 MCP 工具清单、不进 AI 上下文（DEC-006 / DEC-020）。

## 4. 影响

- 新增：`src/ladderstock/journal/realized.py`（纯引擎）；
  `tests/unit/journal/test_realized.py`（手算对照）、
  `tests/integration/test_realized_reconciliation.py`（§2.2 三层同源）。
- 改动：`journal/position.py`（`list_journal_trades()` + `compute_metrics(trades=…)` 投影 `realized`
  与 `items[].realized_pnl`）、`cli/position.py`（读流水并注入同一个 `compute_metrics`）、
  `web/radar.py`（`positions_view` 同一路径）、`tests/unit/journal/test_position.py`、
  `tests/unit/web/test_radar_endpoints.py`（CLI 与 API 同形断言）、
  `tests/integration/test_position_privacy.py`（§2.3 的 import 断言前缀）、
  `docs/specs/SPEC-MY-POSITION.md` §0/§2/§3/§4.1/§5.5/§6/§7、`docs/specs/SPEC-RADAR-PAGES.md` §6.1、
  `docs/CLI.md`（第 39 行与对账命令）、`docs/MCP_TOOLS.md` §3–§4（隐私侧内部互引的准确表述）。
- `GET /api/positions` 的既有字段与实现不动，只是载荷多出 `realized` 块；
  前端 `frontend/src/api/radar.ts::parsePositions()` 是白名单投影，未知顶层键被忽略，
  因此本轮不需要前端改动（要展示已实现盈亏是下一页的事）。

## 5. 对账怎么做（人工可复现）

```bash
ldst journal add --code 600000 --side buy  --price 10.0 --qty 1000 --date 2026-10-03
ldst journal add --code 600000 --side buy  --price 16.0 --qty  500 --date 2026-10-03
ldst journal add --code 600000 --side sell --price 14.0 --qty 1500 --date 2026-10-03
ldst journal stats --from 2026-10-03 --to 2026-10-03 --output json   # FIFO realized_pnl = 3000.0
ldst position list --output json                                     # realized.unheld_pnl  = 3000.0
```

全清仓的那只票逐分不差（§2.2 第 2 层）；未平仓的票按 §2.2 第 3 层的恒等式解释差额，
测试 `tests/integration/test_realized_reconciliation.py` 把这两条都算成了断言。
