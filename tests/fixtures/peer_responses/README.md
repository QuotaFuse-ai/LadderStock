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

**变更记录（2026-11-02，QUOT-290 盘中真实整包验证）**：本文件已是真实整包原文
——2026-11-02（周一）09:36:36 由 `th-cn tool call tencent_quote_batch` 在盘中抓的
47 只昨日涨停股批量报价，`ok` / `data` / `meta` 三层结构、字段值一律照抄，
只按上面的脱敏规则把 `meta.trace_id` 换成 `trace-fixture-1`（真实返回里没有
`source_url` / `note` / `fallback_from`，也没有任何 `http(s)` 值）。
上一版（QUOT-286）是按文档与源码拼的等价形状，只留 3 行。

真实整包与拼装形状逐字段比对的结果：**无改名、无层级变化、无单位变化、无缺失字段**；
真实返回比拼装形状多 6 个一档盘口字段 —— `bid1` / `bid1_volume` / `ask1` /
`ask1_volume` / `inside_volume` / `outside_volume`。47 行字段集合完全一致，
`limit_up` / `limit_down` / `open` 在这份里都非空（`limit_up/limit_down` 为空的情况
仍按上游实现保留为可能出现）。

- 外层 `{total, succeeded, failed, quotes, failures}`（`quotes` 是 **`{code: row}`
  对象**，不是数组）；
- 行内 `timestamp` 是 `YYYYMMDDHHMMSS`；
- 代码集合与当日昨日涨停池一致，快照用例可端到端跑通。

`quotes` 是对象不是记录数组，所以「最多留 3 行」的裁剪规则不适用，整包原样保留。

契约测试：`tests/contract/test_tickhub_payload_mapping.py`。
新增/更换工具必须在这里补一份真实返回，并在 `adapters/tickhub.py:ROW_SHAPES` 登记形状。
