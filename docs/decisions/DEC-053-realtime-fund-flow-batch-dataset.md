# DEC-053：当日实时资金流单列数据集，与日线 `fund_flow` 双口径并存

- 日期：2026-11-04
- 状态：已采纳
- 关联：QUOT-367（父）、QUOT-369（TickHub 批量工具）、QUOT-371（本决策的落地）、
  QUOT-368（逐只量纲修复）、QUOT-374（对拍）、QUOT-364 B-3（资金口径日期回落）

## 背景

打榜（`core/pool.py`）的「资金」分项原来只有一个数据源：`fund_flow`，走
`stock_fund_flow`（`push2his…/fflow/daykline`）**逐只一次请求**。

两个问题：

1. **请求成本**：代码池 50–200 只，每分钟刷新就是 50–200 个请求/分钟，全打在同一个
   push2his 路径上。老板的目标是「一次刷新 ≤2 个上游请求」。
2. **时效**：日线主力净额是**盘后**才发布的。盘中打榜要「资金」有数，只有日线等于
   全天空白；QUOT-364 B-3 已经用「回落到最近可用交易日」绕过一次，但那是**昨天的数**，
   不是当日的。

东财有批量接口（`push2.eastmoney.com/api/qt/ulist.np/get`），一次约 150 只、带主力净额，
TickHub-CN 侧已封装为 `stock_fund_flow_batch`（QUOT-369），实测：

| 请求 | 上游往返 | 返回 | 丢失 | wall |
| --- | --- | --- | --- | --- |
| 150 只 | **1** | 150 | 0 | 0.24 s |
| 200 只 | **2**（150+50） | 200 | 0 | 25.65 s |
| 300 只 | **2**（150+150） | 300 | 0 | 26.64 s |

且与逐只同端点结果 **30/30 完全相等、差值 0 元**（QUOT-374）。

## 决策

**新增数据集 `fund_flow_realtime`，不原地改写 `fund_flow` 的语义。**

| 数据集 | 工具 | 口径 | 用途 | 水位 |
| --- | --- | --- | --- | --- |
| `fund_flow` | `stock_fund_flow`（逐只） | 日线，盘后发布 | **历史对比** | `trade_date` |
| `fund_flow_realtime` | `stock_fund_flow_batch`（批量） | 当日实时累计 | 打榜「资金」分项的当日值 | `timestamp` |

理由：

- 老板明确要求「两个口径不混用」。把 `fund_flow` 原地换掉，等于让历史对比链路
  悄悄读到当日实时数——同一列在不同时刻代表不同东西，是静默错。
- 两者的水位语义本来也不一样：日线按 `trade_date` 增量，实时按取数时刻整批替换
  （`timestamp`，与既有的 `spot_quote` 同一形状，不进 sync 台账）。
- 回放/降级路径也不同：日线走「最近可用交易日回落」，实时走「当日分区，没有就是没有」。

消费侧（`core/pool.py` 的 `fund_flow_snapshot`）取数顺序：

1. `fund_flow_realtime` 当日分区 → 命中即用，`fell_back=False`；
2. `fund_flow` 当日分区；
3. `fund_flow` 最近可用交易日（`FUND_LOOKBACK_DAYS` 窗口内），`fell_back=True`。

输出侧 `meta.fund_data_as_of` 与 `meta.fund_is_request_day` 沿用 QUOT-364 B-3 的
既有字段，不新增键（SPEC-CANDIDATE-POOL 的 `assert_matches` 拒绝未声明键）。

## 请求计数怎么算

不靠「代码看起来是批量」，用工具**自报**的数：`len(data.batches) + len(data.failures)`，
adapter 把它放进信封的 `payload.batch_requests`，`sync` 写进 detail
（「批量 1 次调用，上游往返 N 次」）。这是老板验收 #1 唯一的取数口。

## 部分失败不再整包丢弃

`stock_fund_flow_batch` 在某批失败时回 `ok=false` + `meta.partial=true`，但
`data.flows` 里仍带着另一批的结果。原 adapter 把 `ok=false` 一律当失败抛，
等于把取到的那半也丢了。本决策配一条 adapter 规则：

> `meta.partial is true` **且** `data` 里确实带回了记录（`records`/`flows`/`items`/`quotes`
> 任一非空）时放行，信封 `source_status` 标 `degraded`；其余 `ok=false` 仍然抛。

不满足这两个条件的失败**照旧抛**，不吞真失败。

## 后果

- 盘中「资金」分项有数，且一个请求覆盖整个代码池（150 只以内）；200 只以上跨一个
  25.5 秒闸门窗口（`_ULIST_BATCH_GAP`），这是 QUOT-345 调度预算的硬约束。
- `fund_flow_realtime` 只在当日有分区：跨日请求会落到日线回落链，不会拿昨天的实时数
  冒充今天。
- 单位统一为**元**（QUOT-368 一并修掉：`realtime_fund_flow` 原先走 `stock/get`，
  对 f62/f184 返回另一套刻度、符号都可能相反）。
