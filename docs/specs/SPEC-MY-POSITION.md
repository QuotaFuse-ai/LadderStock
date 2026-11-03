# SPEC-MY-POSITION 我的持仓（本地私有库）

- 日期：2026-10-30（2026-11-01 增补「已实现盈亏」：§3 import 规则、§4.1、§5.5、§7，DEC-038）
- 归属：数据面 R7
- 实现：`src/ladderstock/journal/position.py`（读写 + 本地计算）、`src/ladderstock/journal/realized.py`
  （已实现盈亏引擎：纯函数，只吃流水不进库）、`src/ladderstock/cli/position.py`（命令体）、
  `src/ladderstock/data/sqlite_store.py`（`positions` 表，schema v2）
- 测试：`tests/unit/journal/test_position.py`（字段口径 / CRUD / 手算对照）、
  `tests/unit/journal/test_realized.py`（移动加权手算对照）、
  `tests/integration/test_realized_reconciliation.py`（与 `journal stats` 同源对账）、
  `tests/integration/test_position_privacy.py`（四个出口的隐私断言）
- 相关：生态边界、隐私库只读数据面边界、本文的 schema v2 与边界决策、
  `docs/MCP_TOOLS.md` §2（禁止字段名）、DEC-033（持仓写面）、DEC-038（已实现盈亏口径）

## 0. 一句话

持仓是**只进不出本机**的私有数据：写 `data/journal.sqlite` 的 `positions` 表，
盈亏（浮动 + 已实现离线复算）/ 集中度 / 行业分布全部本地算，只在终端打印；
**不进 MCP 工具清单、不进 `LadderEnvelope`、不进 AI 上下文、不自动导出**。

## 1. 字段口径（schema v2）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `code` | TEXT NOT NULL UNIQUE | 证券代码，入库前 `strip().upper()`；一只票一条记录 |
| `cost` | REAL NOT NULL | 成本价（> 0）。v1 叫 `avg_price`，v2 改名与 CLI 对齐 |
| `qty` | REAL NOT NULL | 数量（> 0） |
| `entry_date` | TEXT | 建仓日 `YYYY-MM-DD`；`add` 缺省取今天，旧库升级来的行保持 `NULL` |
| `industry` | TEXT NOT NULL DEFAULT '' | 用户自己标注的行业，供行业分布统计；**空就是没标注，不猜** |
| `note` | TEXT NOT NULL DEFAULT '' | 备注 |
| `updated_at` | TEXT NOT NULL | ISO-8601 带时区 |

索引：`idx_positions_updated_at`（v1）、`idx_positions_entry_date (entry_date, code)`（v2）。
`list_positions` 就按 `(entry_date, code)` 排序，结果可复算。

升级与权限沿用 journal 现有策略，不新开库、不新开文件：
打开旧库自动 `migrate()`，**改结构前先备份** `journal.sqlite.bak-v<ver>-<时间戳>`，
`PRAGMA user_version` 与 `schema_meta` 两处版本号必须一致，
高于程序的版本直接拒绝（退出码 3），绝不猜未来 schema。WAL / `synchronous=NORMAL` / 0600 同 v1。

## 2. CLI 契约

| 命令 | 关键参数 | 退出码 |
|---|---|---|
| `ldst position add` | `--code` `--cost` `--qty` `--date` `--industry` `--note` `--output` | 0；参数不合法 2（含重复 code，提示改用 `update`） |
| `ldst position list` | `--code` `--date`（行情日期）`--export <path>` `--output` | 0；导出目标不可写 3；日期非法 2 |
| `ldst position update` | `--code` + 至少一个 `--cost/--qty/--date/--industry/--note` | 0；一个字段都没给 2；code 不在库里 **6** |
| `ldst position remove` | `--code` | 0；code 不在库里 **6** |

- 全部是**本地命令，可以打印**（`--output table` 直接在终端看），不经 MCP，也不构造 `LadderEnvelope`；
- `list` 的输出是 `compute_metrics()` 的载荷 + 一个 `price_lookup` 溯源块，`emit()` 直出；
  `GET /api/positions` 是**同一个函数的另一个出口**（§4.5），不是第二份实现；
- `add` / `update` / `remove` 的输出固定带 `local_only: true` 与 `store: "local_sqlite"`，
  脚本据此判断「这条数据没出本机」。

## 3. 现价与流水从哪来（为什么不联网）

`journal/position.py` **不读行情、不 import 接口层**——它的 import 只允许
`ladderstock.data.*`、`ladderstock.errors` 与同侧的 `ladderstock.journal.*`
（隐私库内部互相引用不算越界；AST 级断言 `test_position_does_not_import_market_or_ai`）。
现价由 `cli/position.py::_price_map()` 从**本机 `daily_k` 缓存**读出来再注入
`compute_metrics(rows, prices, as_of=…)`；成交流水由同模块的
`list_journal_trades()`（薄封装 `journal.log.list_trades`）读出后注入同一个函数的 `trades` 参数：

- 缺省取缓存里可用的最后一天（`archive.latest_day("daily_k")`），`--date` 可指定；
- 收盘价容忍键：`close` / `close_price` / `latest_price` / `price` / `收盘`；
- 取不到就是取不到：`latest_price = null`，`price_lookup.reason` 说清原因，stderr 提示
  「持仓现价不可用」，**不联网补数、不拿成本价冒充现价**。

`price_lookup` 形如 `{"dataset": "daily_k", "date": "2026-10-22", "backend": "archive", "reason": null}`。

## 4. 本地计算口径

```
每条：market_value = 现价 × 数量      unrealized_pnl = (现价 − 成本) × 数量
      unrealized_pnl_pct = (现价 / 成本 − 1) × 100
合计：total_cost = Σ(成本 × 数量)     priced_cost = Σ(有现价的成本 × 数量)
      unpriced_cost = Σ(取不到价的成本 × 数量)
      coverage_pct = priced_cost / total_cost × 100
      unrealized_pnl = Σ(有现价那条的浮盈)   unrealized_pnl_pct = Σ浮盈 / priced_cost × 100
```

**口径基准（`basis`）**：只有**全部**持仓都取到现价，才用市值（`market_value`）算权重；
只要有一条取不到，就整体退回成本口径（`cost`）并把 `valuation.market_value` 给 `null`——
「一半市值一半成本」的加权是没有意义的混合数，不给。

**集中度**：权重 `w_i`（成本或市值）→ `fraction_i = w_i / Σw`

```
top1_pct      = 最大那一条的 fraction × 100
top3_pct      = 按 fraction 降序取前三、**先相加再一次**舍入到 1 位
hhi           = Σ fraction_i² （4 位小数）
holdings[]    = {code, weight_pct, cost}，按 (权重降序, code 升序)
```

两条舍入纪律：

1. `top3_pct` 必须按原始比例相加后只舍入一次。逐行已舍入的百分比再相加会凑出 `100.1%`
   （P=45.5/45.5/9.1 那组用例就是这条的回归锚）；
2. `top3` 取的是**最重的三条**，不是记录顺序的前三条——`list_positions` 按建仓日排序，
   直接切片会把最轻的算进前三。

**行业分布**：只统计用户标注过的 `industry`；未标注的归进 `未标注` 桶（仅当已有标注时才出现）。
一条都没标注 → `available=false` + 原因，**绝不拿题材或概念去猜行业**：
本机缓存只有板块层数据集（`industry_board` / `concept_board`），没有「个股 → 行业」的成分映射
（`config/sources.yaml` 未登记），所以行业只能由用户 `--industry` 标注。

空态：没有持仓 → `data_source_status="missing"`、`position_count=0`、
`concentration.available=false`（`reason="本机没有持仓"`）、`sector_distribution.available=false`，
退出码仍是 **0**（「你还没记」不是「对象不存在」，与 `journal list` 同口径）。
成交流水为空时 `realized.status="missing"`、`total_pnl=null`，**持仓有但没卖过是 `0.0`**，两种空态不同。

### 4.1 已实现盈亏（`realized` 块，DEC-038）

只读复算，不写回任何表：**成本法 = 移动加权平均**（券商对账单口径，不是 FIFO），
按 `journal.log.list_trades()` 的顺序（`(trade_date, id)` 升序、上限与 `journal stats` 一致）逐笔回放。

```
买入：  qty += n；cost_total += n × price     → 加权成本单价 u = cost_total / qty
卖出：  已实现 += n × price − u × n；qty −= n；cost_total −= u × n
        （剩余持仓的成本单价 u 不变 —— 这就是与 FIFO 的区别）
```

出口字段：

```
realized = { method: "moving_weighted_average", fee_included: false,
             status, reason, buy_count, sell_count,
             matched_sell_qty, unmatched_sell_qty,
             total_pnl, held_pnl, unheld_pnl, unheld_codes }
items[i].realized_pnl     # 没卖过 0.0；无流水 null；只按 code 匹配，与该行现价/成本无关
```

三条口径纪律：

1. **`total_pnl == Σ items[].realized_pnl + unheld_pnl`**：全部清仓的票通常已从 `positions` 里删掉，
   只按持仓行展示会把落袋的收益变没；只报合计又会出现「合计 ≠ Σ 明细」。
   `unheld_codes` 说明差额来自哪几只（按 code 升序，**只是 code 列表，不带成本或明细**）；
   单测 `test_realized_total_is_items_plus_unheld` 用「已清仓的票」与「未持仓但有流水」两种形状钉住这条；
2. **卖出多于在手**：只结转在手部分，多出来的进 `unmatched_sell_qty`，**不编造成本价**；
3. **不含手续费**：`trades` 表没有费用列，所以是毛额口径，`fee_included=false` 写在出口上，
   不猜费率、不解析 `note`。

与 `journal stats`（FIFO）「同源」的准确含义、以及未平仓时差额的恒等式，见 DEC-038 §2.2；
两种口径不承诺同数，但全清仓的那只票必须逐分不差（集成测试钉着）。
舍入依旧只在出口做一次（金额 2 位、数量 4 位），引擎给原值。

## 5. 手算对照（与 `tests/unit/journal/test_position.py` 一致）

### 5.1 部分取到价（`basis = cost`）

| code | 成本 | 数量 | 现价 | 成本额 | 行业 |
|---|---|---|---|---|---|
| 600000 | 10.0 | 1000 | 11.0 | 10000 | 银行 |
| 600519 | 100.0 | 100 | 90.0 | 10000 | 白酒 |
| 000001 | 400.0 | 5 | **无** | 2000 | 银行 |

```
total_cost  = 22000      priced_cost = 20000      unpriced_cost = 2000
coverage    = 20000/22000 = 90.909… → 90.9        market_value = null（有一条没价）
unrealized  = (11−10)×1000 + (90−100)×100 = +1000 − 1000 = 0.0 → 0.0/20000 = 0.0%

集中度（10000 / 10000 / 2000，合计 22000）
  top1  = 10000/22000 = 45.45… → 45.5
  top3  = 22000/22000 = 100.0（逐行相加 45.5+45.5+9.1 = 100.1 是错的）
  hhi   = 2×(5/11)² + (1/11)² = 51/121 = 0.4215
  holdings = [600000 45.5/10000, 600519 45.5/10000, 000001 9.1/2000]（同权重按 code 升序）

行业（成本口径）：银行 (10000+2000)/22000 = 54.5、白酒 10000/22000 = 45.5
```

### 5.2 全部取到价（`basis = market_value`）

| code | 成本 | 数量 | 现价 | 成本额 | 市值 |
|---|---|---|---|---|---|
| 600000 | 10.0 | 100 | 12.0 | 1000 | 1200 |
| 600519 | 20.0 | 100 | 21.0 | 2000 | 2100 |

```
total_cost = 3000   market_value = 3300   coverage = 100.0
unrealized = (12−10)×100 + (21−20)×100 = 300 → 300/3000 = 10.0%
top1 = 2100/3300 = 7/11 = 63.6   top3 = 100.0   hhi = (7/11)² + (4/11)² = 65/121 = 0.5372
holdings = [600519 63.6/2000, 600000 36.4/1000]
```

### 5.3 前三取「最重的三条」

成本额 100 / 1000 / 1000 / 1000（合计 3100，最轻的一条排在记录第一行）：

```
top1 = 1000/3100 = 32.3     top3 = 3000/3100 = 96.8（若按记录顺序切片会错报 100.0）
hhi  = 3×(10/31)² + (1/31)² = 301/961 = 0.3132
holdings 前 3 = 300750 → 600000 → 600519（同权重按 code 升序）
```

### 5.4 只有一条标注行业

成本额 2000（银行）/ 1000（未标注）：`银行 66.7` / `未标注 33.3`，`labeled_count=1`、`unlabeled_count=1`。

### 5.5 已实现盈亏（`tests/unit/journal/test_realized.py` 与 `test_position.py` §5 一致）

持仓 `600000`（成本 10 × 1000）、`600519`（成本 100 × 100），流水：

```
600000：买 1000@10 → 卖 400@12          (12−10)×400 = +800，剩余单价仍是 10
300750：买 100@30 → 卖 100@36（已清仓）  (36−30)×100 = +600

total_pnl  = 1400      held_pnl = 800（600000 还在持仓表）
unheld_pnl = 600       unheld_codes = ["300750"]
items[600000].realized_pnl = 800.0；items[600519].realized_pnl = null（一笔流水都没有）
```

另外三条锚点：

- **不拿 `positions.cost` 当基础**：持仓填 12、流水是 买 1000@10 → 卖 500@14，
  已实现 `+2000`（按流水单价 10），不是 `(14−12)×500 = +1000`；
- **两种空态分开**：`trades=[]` → `total_pnl=null` + 「流水为空」；
  `trades=None`（调用方没载入）→ `total_pnl=null` + 原因里写明 `trades=None`；
  有买有卖但只买未卖 → `0.0`；
- **卖出多于在手**：卖 200（前面没买）→ 不结转、进 `unmatched_sell_qty`；
  买 100@10 后卖 150@12 → 结转 100 得 `+200`。合计 `matched_sell_qty=100`、`unmatched_sell_qty=250`。

## 6. 隐私边界（四条出口都要干净）

| 出口 | 结论 | 护栏测试 |
|---|---|---|
| MCP 工具清单 | 没有任何 position 工具；`FORBIDDEN_KEYWORDS` 一个都不出现 | `test_mcp_listing_has_no_position_tool` |
| MCP 工具结果 | 13 个工具里所有只读工具逐个 `dispatch`，信封不含哨兵、不含 `entry_date` 等持仓字段名 | `test_dispatched_envelopes_are_position_free` |
| AI 上下文 | `strip_private()` → `build_messages()` 序列化后不含持仓字段名（并反向证明哨兵在**未剥离**的载荷里确实存在） | `test_ai_context_serialization_excludes_position_fields` |
| 只读 API | 沿用 `_strip_private`，`positions` / `holdings` / `持仓` 任意层级剥掉 | `tests/integration/test_journal_privacy.py` |
| 磁盘 | 不加 `--export` 不产生任何文件（数据目录里只有 `journal.sqlite*`）；加了才落盘并在 stderr 提醒「隐私数据，请自行保管，勿上传」 | `test_no_export_file_without_flag` / `test_explicit_export_writes_file_and_warns` |

`ldst position` 与 `ldst pool` 互不引用：`core/pool.py` / `cli/pool.py` 不 import 隐私库
（也不 import `sqlite3`），`journal/position.py` / `cli/position.py` 不 import 候选池，
两侧都有 AST 级断言。**「地位」这个分项对外叫 `standing`**，因为 `position` 是 MCP 隐私关键字。

`--export` 写出的文件与 stdout 内容完全一致（`exported` 字段在序列化前写入），
路径由用户指定，程序不选目录、不加时间戳、不覆盖同名文件的提醒之外的任何自作主张。

已实现盈亏（§4.1）**不开新的隐私通路**：流水读取收在 `journal/position.py::list_journal_trades()`
后面，接口层（`cli/position.py` / `web/radar.py`）不 import `journal.log` ——
`scripts/check_privacy.py` 的 `JOURNAL_IMPORT_ALLOWLIST` 对 `web/radar.py` 只放开 `journal.position`。
`realized` 块因此与 `items` 同进同出：进 `--export` 的文件，不进 MCP、不进信封、不进 AI 上下文。

## 7. 已知取舍

- **`positions.code` 仍然 UNIQUE**：一条票只有一条记录，加仓走 `update` 改 `qty`/`cost`。
  要做「同一票多笔建仓」需要把主键改成 (code, entry_date) 并引入批次概念，属于新的表结构决策，不在 R7 范围。
- **已实现盈亏只读复算，不写回**（DEC-038 取代原来的「不做已实现盈亏」）：`positions` 仍是持仓唯一真相，
  `trades` 流水只用来算 `realized`，两边不重复实现同一件事。
  **两种结转法回答的是两个不同的问题，两个数都正确**：纪律复盘要逐笔配对，所以 `journal stats` 用 FIFO
  （胜率 / 盈亏比按它算，契约不动）；账户对账要券商口径，所以持仓侧用移动加权平均。
  两者**不承诺同数**，差额由恒等式解释（DEC-038 §2.2），不许为了对齐数字去改 stats。
  **不含手续费**——`trades` 表没有费用列，出口用 `fee_included=false` 写明，不猜费率、不新增字段。
- **`entry_date` 允许 NULL**：旧库升级来的行没有建仓日，如实给 `NULL`，
  不拿 `updated_at` 或今天冒充。
