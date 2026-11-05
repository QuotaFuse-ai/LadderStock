# DEC-063：回归 A 的验收口径锁定 `fund_flow_realtime`；日线 `fund_flow` 保持逐只

- 日期：2026-11-05
- 状态：已采纳（待验收方按 §1 修订 QUOT-375 回归 A 的命令与判据）
- 关联：QUOT-367（父）、QUOT-369、QUOT-371、QUOT-374、QUOT-375（验收）、QUOT-424（本决策）、
  DEC-053（双口径并存）、DEC-054（盘中批量槽）
- 编号：初稿误用 DEC-056，该号已被 PR #99 占用（`DEC-056-intraday-fast-polling.md`），
  按 DEC-045 顺延为 DEC-063；内容与初稿一致，仅编号与代码行号刷新。
- 结论：**选项 1 —— 验收命令改为 `--dataset fund_flow_realtime`。**
  日线 `fund_flow` 不批量化，也不另立改造任务。

## 1. 回归 A 的正式判据

| # | 命令 | 期望读数 | 性质 |
| --- | --- | --- | --- |
| A1 | `ldst sync --dataset fund_flow_realtime --codes <150 只>` | sync 台账 detail 含「上游往返 1 次」；trail 只有 1 条 `tickhub_fund_flow_batch/stock_fund_flow_batch:ok`，**不出现** `tickhub_fund_flow/stock_fund_flow:ok` | **判据本体**（QUOT-367「一次刷新 ≤2 个上游请求」） |
| A2 | `ldst sync --dataset fund_flow --codes <≤100 只>` | 每只 1 条 `tickhub_fund_flow/stock_fund_flow:ok`、上游往返 N 次 | **预期行为**，只作口径对照，不作为回归失败项 |

超过 150 只时 A1 按 150 只/请求自动分批（`stock_fund_flow_batch` 自报 `batches`），
单轮上限 450 只（`MAX_BATCH_POOL_CODES = BATCH_CODES_PER_CALL * 3`，`cli/sync.py:88-94`），
判据仍为「上游往返 = ceil(只数/150)」。

**QUOT-375 验收报告里的字面命令 `--dataset fund_flow --codes <150>` 不可执行**：
`fund_flow` 是逐只代码池数据集，单次上限 `MAX_POOL_CODES = 100`（`cli/sync.py:86`），
超过即 `EXIT_USAGE(3)`，在发出任何请求之前退出（`cli/sync.py:335-339`）。
该命令既不能用于证明「逐只」、也不能用于要求「批量」，应从验收文本中删去。

## 2. 为什么是选项 1

1. **口径已由 DEC-053 定死，验收对象本来就是实时链路。**
   打榜「资金」分项的第一顺位是 `fund_flow_realtime` 当日分区（`core/pool.py:268-286`，
   `fell_back=False`）；日线 `fund_flow` 只作第二、三顺位的历史回落。QUOT-367 的
   「一次刷新 ≤2 个上游请求」约束的是**盘中每分钟刷新**，对应批量数据集。
2. **上游没有可用的日线批量历史端点，「批量化」无法改变上游往返次数。**
   TickHub-CN 现有两个资金流工具（`src/tickhub_cn/datasources/funds.py`）：
   - `stock_fund_flow(code, days=60)`：单 `code` 必填，单 `secid` 打 `push2his` 日线；
   - `stock_fund_flow_batch(codes)`：`ulist` 当日快照，只回 `f62/f184` 等**当前值**，无日期序列。
   在上层把 150 次单只调用聚合成一次 MCP 调用，**上游往返仍是 150 次**——验收计的就是这个数
   （DEC-053「请求计数怎么算」）。要真正降到 ≤2，必须先有批量历史上游端点；目前没有。
   所以选项 2 不是「贵一点」，而是**在当前证据下不可达**。
3. **成本特征不同，不构成被 QUOT-367 针对的问题。**
   日线 `fund_flow` 只在 15:35 收盘同步里跑一次（`config/schedule.yaml:79-83` 的 `close_sync`），
   不是分钟级循环；盘后集中 100 次左右的单只请求不影响盘中刷新预算。
4. **两套口径混用是 DEC-053 明确禁止的静默错。**
   日线主力净额是盘后值，实时批量是当日累计；`fund_flow` 原地换成批量，等于让历史对比链路
   在不同时刻读到不同含义的同一列。

## 3. 后果

- 回归 A 报告按 §1 拆成 A1（通过/不通过）与 A2（口径说明），不再出现「字面命令未通过」这一项。
- 运维/验收若仍要求日线批量，需另立 TickHub-CN 任务新增批量历史工具，并先确认上游存在
  对应的批量历史端点；在确认之前不排期。
- 代码零改动：数据集注册（`core/boundary.py:609-611`）、批量集合（`data/adapters/tickhub.py:190`）、
  同步分支（`cli/sync.py:834-849`）保持现状。
