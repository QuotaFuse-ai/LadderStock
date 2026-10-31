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

## 例外：`tencent_quote_batch.json`（QUOT-286）

盘中的 6 时点快照要的是**当场**报价，不能等到下一个交易日再补抓，所以这份不是
`th-cn tool call` 的整包原文，而是按 TickHub-CN 文档与源码拼出来的等价形状：

- 外层 `{total, succeeded, failed, quotes, failures}` 来自
  `datasources/quotes.py:tencent_quote_batch` 的实现（`quotes` 是 **`{code: row}`
  对象**，不是数组）；
- 行内字段集合与 `_parse_qt_line` / `tencent_quote` 的真实返回一致
  （`timestamp` 是 `YYYYMMDDHHMMSS`，`limit_up/limit_down` 可能为空）；
- 代码与昨日涨停池 fixture 对齐，便于快照用例端到端跑通。

拿到真实盘中整包后应原样替换本文件，形状不应变化。

契约测试：`tests/contract/test_tickhub_payload_mapping.py`。
新增/更换工具必须在这里补一份真实返回，并在 `adapters/tickhub.py:ROW_SHAPES` 登记形状。
