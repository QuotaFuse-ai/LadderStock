# DEC-065 持仓账本 schema v3、成本法与旧仓迁移

- 日期：2026-11-06
- 提出：产品经理（QUOT-440，父任务「按 vibe-astock 整体复刻我的股票三模块」）
- 状态：已采纳（对应实现 QUOT-426 / QUOT-430，代码已合入 `origin/main`）
- 编号：本决策任务书原定 `DEC-056`。开工扫描 `docs/decisions/` 时该号已被
  `DEC-056-intraday-fast-polling.md` 与 `DEC-056-peer-registry-import.md` 两个文件占用，
  按 DEC-045 §2「编号冲突由无反向引用的一方避让」顺延为 `DEC-065`。
  代码注释与 `DEC-064` 中现存的「DEC-056（持仓账本）」引用指的是本决策，
  后续统一改引用属独立文档收口项，本决策不修改既有文件。
- 关联：DEC-033（持仓写接口）、DEC-038（移动加权平均已实现盈亏）、DEC-052（自选只读面）、
  DEC-058（收件箱表，同属 schema v3）、DEC-064（收益率取数）、QUOT-426、QUOT-430
- 生效版本：`sqlite_store.SCHEMA_VERSION = 3`；`docs/UI-SPEC.md` v1.19；
  `docs/specs/SPEC-RESEARCH-PAGES.md` v0.5；`docs/specs/SPEC-MY-POSITION.md`（既有）

## 1. 背景

v2 的本机库只有两张与持仓相关的表：`trades`（交易纪律日志，只允许 `buy` / `sell`）与
`positions`（直接录持仓：代码、成本、数量、建仓日、行业、备注）。这带来三个问题：

1. **持仓是「当前快照」，不是「过程」**。买 / 卖 / 分红无法回放，已实现盈亏、分红收入、
   成本法切换都没有数据基础；
2. **改 `trades` 会污染历史语义**。它是纪律日志，不是持仓账本，两者不能合表；
3. **旧记录不能丢**。已经录进 `positions` 的数据必须在升级后可继续使用，且迁移失败时
   要能退回迁移前状态。

参考实现把持仓定义为「交易流水重放」的结果，并要求旧持仓以「期初流水」的形式迁入。
本决策只钉数据结构、成本法与迁移口径；收益率口径见 DEC-064，收件箱表见 DEC-058。

## 2. 决定

### 2.1 schema 升到 v3，新增两张账本表

`SCHEMA_VERSION = 3`；在 `_SCHEMA_V3` 中新增：

- `portfolio_trades`：`id / symbol / name / type / trade_date / shares / price / fee /
  amount / ratio / note / migrated_from / undated / created_at`。
  `type` 只允许 `opening / buy / sell / dividend / split`；
  `migrated_from` 指向 `positions.id`，并建**唯一部分索引**
  （`WHERE migrated_from IS NOT NULL`），保证一条旧仓最多只迁一次；
- `portfolio_legacy_marks`：`position_id`（主键） + `dismissed_at`，记录用户明确不要的旧记录。

`inbox` 表同属 v3，但归 DEC-058 管，本决策不重复定义。

### 2.2 旧 `positions` / `trades` 原样保留，不删行、不改语义

- `_SCHEMA_V1` / `_SCHEMA_V2` 一个字节都不改，历史库可以原样升上来；
- 迁移只**读** `positions`，不更新、不删除；
- 「不要这条旧记录」只写 `portfolio_legacy_marks`，`positions` 行仍在，可回滚；
- 旧仓转成一条 `opening` 流水：
  - `trade_date = entry_date`；`entry_date` 为空时取迁移当日并置 `undated = 1`，
    `note` 写明「原记录未写建仓日，按迁移日暂记」；
  - `opening.price` 允许为负（历史成本异常时如实保留，不修正、不截断）；
  - `migrated_from = positions.id`。

### 2.3 迁移必须带备份，且幂等

- `migrate()` 在 `BEGIN IMMEDIATE` 之前、检测到已有表或版本 > 0 时先备份；
  v2 → v3 留下 `journal.sqlite.bak-v2-<timestamp>`，备份失败则**中止迁移**；
- 旧仓转期初前再留一份 `journal.sqlite.bak-portfolio-<timestamp>`；
- 迁移语句逐条 `execute()`，不用 `executescript()`；重复执行不重复建表、不改数据；
- `GET /api/portfolio` 会触发一次自动迁移（这是唯一会写库的 GET），
  迁移错误折叠进响应的 `migration.error`，不把 500 抛给页面。

### 2.4 成本法：移动加权平均（默认）与先进先出

- `CostMethod = Literal["fifo", "average"]`；默认 `average`（标签「移动加权平均」），
  另一档 `fifo`（标签「先进先出」）；
- 买入费用计入成本；卖出费用冲减卖出所得；分红只计收益、不改成本；
  送转按比例放大股数、摊薄单位成本；
- 同日流水先处理送转，其余按 `created_at`、`id` 排序；
- 超卖、股数为负等非法流水进入 `issues[]` 并附原因，**跳过计算，不发明成本**；
- 计算全程用浮点，输出时统一四舍五入到 4 位小数。

### 2.5 接口面

- `GET /api/portfolio?method=average|fifo`：账本重放结果。平面 JSON，
  顶层 `local_only / store / method / method_label / methods / today / price_lookup /
  daily_lookup / items / closed / realized / dividends / issues / ledger / legacy /
  migration_pending / migration / totals_cny`；
- `POST /api/portfolio/ledger`：新增一条流水；字段白名单
  `symbol / name / type / trade_date / shares / price / fee / amount / ratio / note`；
- `POST /api/portfolio/ledger/{id}/delete`：删除前先备份；
- `POST /api/portfolio/legacy/{position_id}/adopt` / `dismiss`：采纳或忽略旧记录；
- 所有接口回环 + `Host` 白名单，写面另要同源证据，响应 `Cache-Control: no-store`。

### 2.6 只支持 A 股、人民币

- 不建货币 / 汇率列；
- 账本路径拒绝 B 股（`900` / `200` 开头）：`valid_symbol()` 与 `trade_problem()`
  把它归入 `issues[]` / `skipped[]`，并给出「按美元、港元计价，并进人民币合计会算错」；
- 只做单组汇总：`market = "A股"`、`currency = "CNY"`、`label = "人民币 · A 股"`；
- 不做汇率换算、不跨币种相加。

## 3. 理由

- **过程可回放**：只有流水才能同时回答「现在持有多少」「已经实现多少」「分红收了多少」，
  且成本法可以切换对照；直接录持仓永远缺这三块。
- **不动历史表**：`trades` 是纪律日志，`positions` 是旧快照，二者语义不同。新表意味着
  升级可逆、回滚不丢数据。
- **迁移可审计**：`migrated_from` 唯一索引 + 两份备份 + 不删旧行，让「迁了几条、哪条没迁、
  用户明确不要哪条」都有据可查。
- **币种边界提前挡住**：本地看板没有汇率源，混入 B 股会直接把人民币合计算错；
  在入口拒绝比事后解释便宜。

## 4. 代价与风险

- **GET 带写**：`GET /api/portfolio` 会触发自动迁移。好处是用户打开页面即完成升级，
  代价是只读语义被打破；已用备份 + `migration.error` 兜底，但并发首开仍需串行事务保证。
- **旧仓可能重复出现**：迁移后 `positions` 仍在，若页面同时展示旧仓与新账本，用户会看到
  「同一只票两次」。当前用 `migration_pending` / `legacy[]` 状态区分，持仓页已随 QUOT-434
  改为账本驱动（`PortfolioPanel` + `MigrationCard` 承接采纳 / 忽略），旧 `PositionsScreen`
  不再挂路由。
- **未写建仓日的成本会失真**：`entry_date` 为空时按迁移日暂记，收益率起点会偏晚；
  已用 `undated=1` 标记，DEC-064 的「首笔流水晚于日线覆盖」规则会把它排除出收益率。
- **B 股只在账本路径被挡**：`POST /api/positions` 与自选写入口没有做 6 位 / B 股校验，
  这是既有缺口；本决策不扩大改动面，登记为后续收口项。
- **无数据库级金额精度**：SQLite `REAL` + 输出四舍五入，不做定点小数；本产品是本地看板，
  可接受，但跨年长账本的尾差需按 DEC-064 的口径解释。

## 5. 影响文件

- `src/ladderstock/data/sqlite_store.py`（`SCHEMA_VERSION`、`_SCHEMA_V3`、`TABLES`、备份）
- `src/ladderstock/core/portfolio.py`（重放、成本法、B 股边界）
- `src/ladderstock/core/portfolio_perf.py`（读取账本做收益率，DEC-064）
- `src/ladderstock/journal/portfolio.py`（流水校验、旧仓分类 / 迁移 / 采纳 / 忽略）
- `src/ladderstock/web/radar.py`（`/api/portfolio*` 路由与投影）
- `tests/unit/data/test_schema_v3.py`、`tests/unit/journal/test_portfolio_store.py`、
  `tests/unit/journal/test_portfolio_replay.py`、`tests/unit/web/test_portfolio_endpoints.py`
- `docs/specs/SPEC-MY-POSITION.md`、`docs/UI-SPEC.md` §3.11、`docs/specs/SPEC-RESEARCH-PAGES.md` §8

## 6. 生效版本与验收

- 生效：`SCHEMA_VERSION = 3` 起；文档随 `UI-SPEC` v1.19、`SPEC-RESEARCH-PAGES` v0.5 登记。
- 验收：
  - v2 库升级后 `PRAGMA user_version` 与 `schema_meta.schema_version` 都为 3，
    三张新表存在，`trades` / `positions` 行逐字节不变，备份文件存在；
  - 重复迁移不重复建表、不改数据；
  - 旧仓迁移只写 `opening`，`positions` 行数不变；`dismiss` 只写标记；
  - B 股流水进 `issues[]` / `skipped[]`，不进任何人民币合计。
