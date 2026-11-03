# DEC-046 收盘全量同步任务：新增 `sync_close` job kind、复用 sync 链路落 Parquet 日分区

- 日期：2026-11-03
- 提出：后端架构师（agent `dda26417`），QUOT-347（父 QUOT-345 范围 1「收盘后全量自动拉取」）
- 状态：生效（实现随 PR 进 `main`）
- 编号：`DEC-046`。按 `DEC-045` 规则取值：`git ls-tree -r --name-only origin/main docs/decisions/`
  已扫到的最大号 = 045，本决策取 046
- 关联：DEC-006（数据只来自生态 peer、只落本机、不直打上游）、DEC-007（同步与缓存链路）、
  DEC-035（盘中 6 时点快照：当天当场、仅交易日动作）、DEC-036（DuckDB 跨进程写：进程级写锁）、
  QUOT-286（快照实现）、QUOT-345（近乎实时 + 自持久化）、`docs/specs/`、
  `config/schedule.yaml` + `config/schema/schedule.schema.json`（实现口径）

## 1. 背景

QUOT-345 要求逼近参考产品的「常驻近实时 + 自持久化」。调度器已有 8 个定点任务，但
**收盘位 15:05 只做对账（`reconcile`），不做全量拉取落库**——收盘后的候选池数据要靠人
手动 `ldst sync`，不满足「自持久化」。范围 1「15:35 收盘全量同步」价值最高、风险最低，
优先交付。

约束来自现有架构，本决策不新造机制，只做接线：

- `kind` 此前只有 `notify` / `snapshot` / `reconcile` 三种（`schedule.py` 的 `DEFAULT_JOBS`
  与 `run_job` 分发表、`schedule.schema.json` 的 enum、`_structural_problems` 兜底三处一致）；
- 落库与 Parquet 日分区已由 `sync.py → duckdb_store.write_dataset` 承担
  （`archive/YYYY/MM/DD/<dataset>.parquet`，`replace` 整日覆盖、`append` 按 `(code, trade_date)`
  upsert，分区原子重写）；
- 仅交易日判定沿用 `core/intraday.local_moment` + 周末口径，不在 job 里自己拼时间比较。

## 2. 决定

### 2.1 新增 job kind：`sync_close`

不复用 `snapshot`（快照是「当天当场存一个时点读数、绝不补跑」的语义，收盘同步恰恰要
「落整日分区、可补跑」），也不复用 `reconcile`（对账只读本地已落盘数据、不取数）。
单开 `sync_close`，四处注册面同步改，缺一处即「用户手写配置解析失败退出码 3」：

| 位置 | 改动 |
| --- | --- |
| `run_job` 分发 | `elif kind == "sync_close"` → `_run_sync_close(...)` |
| `DEFAULT_JOBS` | 追加 `15:35 收盘全量同步`（第 9 条） |
| `config/schema/schedule.schema.json` | `kind` enum 加 `"sync_close"` |
| `_structural_problems` | 兜底 allowed kinds 加 `"sync_close"`（schema 缺失时仍认） |

命名取 `sync_close`（动词+场景），与既有 `reconcile`（对账）区分开：一个是「拉数落库」，
一个是「读库判定」。

### 2.2 落库顺序与数据集：`limit_pool → fund_flow → concept_board`

固定顺序写进 `CLOSE_SYNC_DATASETS`（模块常量，不入配置，避免为一个顺序开配置面）：

- 先 `limit_pool`（涨停池，当日盘面主线）→ 再 `fund_flow`（候选池资金流，`code` 必填走代码池
  分支）→ 最后 `concept_board`（概念板块，供题材视角）；
- 三者都是 `core/boundary` 登记在册的 `tickhub` `replace` 数据集，**一切取数走 `sync.run_sync`
  的既有链路（主源 → 同 peer 备源 → 本地缓存回放），不新建采集、不直打上游**（DEC-006）；
- `fund_flow` 的代码池由 `sync.py` 按 `--codes > config/sync.yaml > 本机自选` 解析，任务侧不重复
  实现；收盘刷新时同一池给出更多行，`replace` 整日覆盖 → 条数增长、不膨胀。
- 顺序拉完再触发当日 `ldst review` 生成（`review.pipeline.generate`），复盘自身的问题
  只把任务状态从 `ok` 降到 `degraded`，**不把已成功的同步带崩**。

### 2.3 幂等：交给 `duckdb_store`，不在 job 里加防重标记

「同一交易日重复触发不产生重复分区、不写坏已落盘数据」这件事，复用层已经保证：

- `replace` 模式整日按 `trade_date` 覆盖，重跑行数不增长；`append` 按 `(code, trade_date)` upsert；
- 分区导出走临时文件 + 原子替换，同日只重写那一个 `<dataset>.parquet`，目录不新增；
- `ldst schedule run --once` 手动补跑、或机器 wake 后 `misfire_grace_seconds`（300s）窗口内
  补触发，都命中同一天的覆盖路径，安全。

因此**不引入「当日已同步则跳过」的状态位**——那会挡住合法的收盘数据刷新，与「自持久化」
的意图相反。

### 2.4 仅交易日动作：沿用 intraday 的日历口径

`_run_sync_close` 先 `intraday.local_moment(now, timezone)` 取调度时区下的本地日期，
`day.weekday() >= 5` 直接 `skipped`、**不建空目录、不碰 registry**。与 DEC-035 快照同源，
不在 job 里自己拼时间比较。节假日若上游无数据，`run_sync` 会把该数据集记 `skipped/failed`
并折进本任务状态，不空写分区。

### 2.5 与 15:05 `close_reconcile` 解耦

两者是**两个独立 cron 触发点**（`15:05` / `15:35`），daemon 模式下互不在同一轮；`--once`
模式下 `run_job` 逐条捕获异常折成状态字段，`sync_close` 失败不会中断 `reconcile`，也不
改变调度器「单任务失败不拖垮整轮」的既有契约（DEC-004）。

## 3. 权衡

| 选项 | 取舍 |
| --- | --- |
| 新开配置项放数据集列表 / 顺序 | 否。为一个固定顺序开配置面属过度设计；常量够用，改数据集是架构决策、应走 PR |
| 复用 `snapshot` 语义 | 否。快照「不补跑」与收盘「可补跑落整日分区」冲突 |
| job 里加当日防重标记 | 否。会挡掉合法的收盘数据刷新；幂等交给 `duckdb_store` |
| 独立进程/服务做收盘拉取 | 否。调度器已有定点任务与进程级写锁（DEC-036），再起一套是重复 |

## 4. 影响

- `src/ladderstock/cli/schedule.py`：新增 `CLOSE_SYNC_DATASETS`、`_run_sync_close` 与两个 helper、
  分发分支、默认表第 9 条、docstring；
- `config/schedule.yaml` / `config/schema/schedule.schema.json`：追加 `close_sync`（15:35）任务与 enum；
- 测试：`tests/unit/test_schedule_sync_close.py` 覆盖注册面 / 分发 / 仅交易日 / 幂等 /
  `fund_flow` 增长；`test_schedule_snapshot.py` 与 `test_watch_signals.py` 的默认表条数由 8 改 9。
