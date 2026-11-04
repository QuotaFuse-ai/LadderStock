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

**变更记录（2026-11-04，QUOT-290 盘中整包）**：本文件是 **2026-11-04 盘中**的真实整包原文
——10:42:25 由 `th-cn tool call tencent_quote_batch` 对 2026-11-03 昨日涨停池 124 只整包请求
（`total`/`succeeded`/`failed` = 124/124/0，`failures` 为空，行内 `timestamp`
20261104104219–20261104104224），`ok` / `data` / `meta` 三层结构与字段值一律照抄，只按上面的
脱敏规则把 `meta.trace_id` 换成 `trace-fixture-1`（真实返回里没有 `source_url` /
`note` / `fallback_from`，也没有任何 `http(s)` 值）。

更早两版原文仍在仓里可取：
- 2026-11-03 收盘整包（124 行，17:54:03）：`4e81fae` 提交的那版；
- 2026-11-02 盘中 09:36:36 整包（47 行）：`git show bf8d0df:tests/fixtures/peer_responses/tencent_quote_batch.json`。

三版真实整包（11-02 盘中 47 行 / 11-03 收盘 124 行 / 11-04 盘中 124 行）逐字段比对结果：
**124×28 个单元格零类型差异、零 null**，`quotes` 键集合、字段集合与字段顺序完全一致。
最初那版（QUOT-286 按文档与源码拼的 3 行形状）与真实包的差异只有一个：真实返回多
6 个一档盘口字段 `bid1` / `bid1_volume` / `ask1` / `ask1_volume` / `inside_volume` /
`outside_volume`。

- 外层 `{total, succeeded, failed, quotes, failures}`（`quotes` 是 **`{code: row}`
  对象**，不是数组）；
- 行内 `timestamp` 是 `YYYYMMDDHHMMSS`；
- 代码集合与当日昨日涨停池一致，快照用例可端到端跑通。
- 124 行整包 **0 个 null**，行内 `0` 全部核过是市场状态本身：10 行 `ask1 == 0` 且
  `price == limit_up`（封死涨停没有卖一）、4 行 `bid1 == 0` 且 `price == limit_down`
  （封死跌停没有买一）、3 行 `amplitude == 0` 且 `high == low`（一字板）、2 行
  `outside_volume == 0` 与 `volume == inside_volume` 对得上、2 行 `change == 0` 平盘。
  不是上游缺字段也不是解析问题。

`quotes` 是对象不是记录数组，所以「最多留 3 行」的裁剪规则不适用，整包原样保留。

## 例外：`tencent_kline_rate_limited.json`（QUOT-349）

限流是**碰运气的返回**：要么刚好被 pacing 挡住，要么把上游打到限流窗口里（不该为了
拿样本去压上游），所以这份同样不是抓包原文，而是按 TickHub-CN 源码拼出的等价形状，
2026-11-03 逐字段核对过：

- `error` 的键集合来自 `core/errors.py:TickHubError.to_dict`
  （`code/name/message/action/detail/retryable`），`name` / `action` 取自
  `ERROR_CODES["QFSD-7001"]` = `rate_limited` / 「返回重试建议」；
- `detail.{url, endpoint, reason, retry_after_s}` 与 `core/http.py` 里
  `endpoint_paced` 那条抛出一致（`retry_after_s` 是**整秒**，`endpoint_breaker`
  那条没有 `reason`，另有熔断恢复时长）；
- `detail.url` 按本目录规则抹去。

拿到真实限流返回后应原样替换本文件，形状不应变化。

契约测试：`tests/contract/test_tickhub_payload_mapping.py`。
新增/更换工具必须在这里补一份真实返回，并在 `adapters/tickhub.py:ROW_SHAPES` 登记形状。
