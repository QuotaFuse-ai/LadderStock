# DEC-054: 盘中批量结果用分钟级槽文件存储，不进 archive（QUOT-373）

日期：2026-11-04 ｜ 状态：已采纳 ｜ 关联：QUOT-373（本单）、QUOT-367（父单）、QUOT-345/347/352（调度与收盘同步，边界外）

## 1. 问题与前提纠正

每 60 秒一轮的批量资金刷新（`fund_flow_realtime`，QUOT-371 引入）此前走
`write_dataset()` → DuckDB 写入 + `_export_partition()`。老板原话是「不要每秒落
parquet，一天 240 个小文件」——**实测前提不成立**：分区路径是
`data/archive/<Y>/<M>/<D>/<dataset>.parquet`，一个数据集一天一个文件，不存在
240 个小文件。

真实代价是两条：

1. **写放大**：每分钟把当天那个 parquet 文件整体 COPY 重写一遍（150 只 × 全天
   240 轮 ≈ 每天重写 240 次同一个文件，IO 量随只数线性放大）；
2. **锁竞争**：DuckDB 跨进程写锁（QUOT-234 为此加的有界重试 + sync 入口文件锁）
   在每分钟一轮的频率下，与看板读侧、`ldst review` 等进程互相顶。

## 2. 备选与取舍

- **(A) 盘中槽文件**（照 `spot_quote` 先例，`core/intraday.py`）：每轮写
  `data/intraday/<date>/<dataset>/<HHMM>.json`，`write_new_json` 只创建不覆盖，
  完全不进 archive。天然带分钟级时间槽（父任务 QUOT-367 要求「回放能拿到上一
  分钟」），无跨进程写锁。
- **(B) DuckDB 当日表**：查询方便，但要防跨进程写锁——恰恰是本单要消掉的代价，
  且「上一分钟」要靠日期列自查询，形状上没有 (A) 直白。

**选 (A)**。读侧代价（`archive.snapshot()` 先查最新槽）一次封装，调用方零改动。

## 3. 语义

- **写**：`write_batch_slot()` 每轮一条槽；同一分钟第二轮**不覆盖**（先到先得，
  槽的价值是「那一分钟盘面上是什么」，事后改写等于伪造历史读数）；空记录不写槽。
- **读**：`archive.snapshot(dataset, day)` 先取该天最新一条槽（`backend=
  "intraday-slot"`），没有槽目录才走原有 Parquet/DuckDB 链路。某天的值 = 那天
  最后一条槽；**T-1 及更早仍走日线口径（fflow/daykline），两个口径不混用**。
- **水位**：`sync_batch_pool` 的水位推进逻辑不变，只是落库目标从 write_dataset
  换成槽；`record_run` 形状不变（rows/days/total_rows 字段保持兼容）。
- **保留策略**：默认保留最近 3 天（240 槽 × 150 只 ≈ 单日 10–50MB，3 天上界
  ~150MB），每次成功写槽后 `purge_batch_slots()` 清理过期日；只删
  `<dataset>/` 形状的自有目录，spot_quote 的 `<HHMM>.json` 不受影响。

## 4. 边界

- 不改 `write_dataset()` / `_export_partition()` 既有行为（QUOT-361/349 同链在动）；
- 60 秒触发器、15:35 收盘导出（QUOT-347）不在本单；
- 日线 `fund_flow` 数据集照旧走 archive，本单只管批量实时数据集。
