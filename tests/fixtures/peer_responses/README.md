# 真实 peer 响应 fixture（QUOT-233）

`th-cn tool call <tool>` 在 2026-10-30 抓的**原始返回**，文件名 = 工具名 + `.json`。
R5 产业研究新增的四只（`sw_industry_history` / `eastmoney_finance_indicators` /
`baostock_valuation_history` / `board_fund_flow`）抓于 2026-10-31，同一套裁剪规则。
形状由这些样本锁定，不靠记忆——`tencent_kline` 的 `data` 是对象
（`{code, period, adjust, klines: [...]}`）而不是记录列表，这正是 QUOT-233 的根因。

脱敏与裁剪（只动这几处，字段语义保持原样）：

- 删 `meta.source_url`（源站域名）、`meta.note`、`meta.fallback_from`；
- 任何值为 `http(s)://...` 的字符串整体删除；
- `meta.trace_id` → `trace-fixture-<序号>`；
- 记录数组最多留 3 行；`meta.count` 保留上游原值，所以可能与数组长度不等。

## `tencent_quote_batch.json`（QUOT-286 建，QUOT-290 换成真实整包）

**变更记录（2026-11-03，QUOT-290 收口）**：本文件是**最近一个完整交易日（2026-11-03）**
的真实整包原文——17:54:03 由 `th-cn tool call tencent_quote_batch` 对当日 11:30 快照的
124 只昨日涨停股整包请求，`ok` / `data` / `meta` 三层结构与字段值一律照抄，只按上面的
脱敏规则把 `meta.trace_id` 换成 `trace-fixture-1`（真实返回里没有 `source_url` /
`note` / `fallback_from`，也没有任何 `http(s)` 值）。

上一版是 2026-11-02（周一）09:36:36 盘中抓的 47 只整包，那份原文仍在仓里可取：
`git show bf8d0df:tests/fixtures/peer_responses/tencent_quote_batch.json`。本单原本要的就是
盘中整包，但 11-03 的六个时点窗口都已过、按 DEC-035「只创建不补跑」不能回补，
所以主 fixture 换成了当日可获得的最新真实整包；一个工具只留一份主样本，两份都验过形状。

两版真实整包（11-02 盘中 47 行 / 11-03 收盘 124 行）逐字段比对结果：**28 个行内字段
无改名、无层级变化、无单位变化、无缺失字段**，`quotes` 键集合完全一致。
最初那版（QUOT-286 按文档与源码拼的 3 行形状）与真实包的差异只有一个：真实返回多
6 个一档盘口字段 `bid1` / `bid1_volume` / `ask1` / `ask1_volume` / `inside_volume` /
`outside_volume`。

- 外层 `{total, succeeded, failed, quotes, failures}`（`quotes` 是 **`{code: row}`
  对象**，不是数组）；
- 行内 `timestamp` 是 `YYYYMMDDHHMMSS`；
- 代码集合与当日昨日涨停池一致，快照用例可端到端跑通。
- 124 行整包 **0 个 null**：`ask1` / `ask1_volume` 各有 15 行为 `0`、`amplitude` /
  `outside_volume` 各有 5 行为 `0`，逐行核过全部是市场状态本身（15 行 `price == limit_up`
  封死涨停没有卖一；5 行 `high == low` 一字板振幅 0、无外盘），不是上游缺字段也不是解析问题。

`quotes` 是对象不是记录数组，所以「最多留 3 行」的裁剪规则不适用，整包原样保留。

契约测试：`tests/contract/test_tickhub_payload_mapping.py`。
新增/更换工具必须在这里补一份真实返回，并在 `adapters/tickhub.py:ROW_SHAPES` 登记形状。
