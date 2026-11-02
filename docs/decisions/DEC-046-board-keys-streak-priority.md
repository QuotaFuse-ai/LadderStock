# DEC-046 连板数取值键：`streak` 必须排在 `limit_up_days` 之前，一处修覆盖四个消费面

- 日期：2026-11-02
- 提出：后端架构师（QUOT-290 真实整包验证交回的缺陷；缺陷由测试工程师 2026-11-02 09:36 实测抓出）
- 状态：生效（随本决策的代码与用例落地）
- 关联：DEC-006 闸门 5（天梯程序直出）、DEC-035（6 时点快照口径，其 §「昨日连板数读
  `y_streak`」的理由在本文更正）、DEC-039（盘中读面不重算，决定了历史快照无法被修复）、
  `SPEC-LIMIT-POOL` §4.1、`SPEC-INTRADAY-SNAPSHOT` 口径、`SPEC-CANDIDATE-POOL` §字段、QUOT-290

## 1. 背景

QUOT-290 用真实 TickHub-CN 在 2026-11-02 09:35 抓盘中快照，`sentiment.one_to_two` 报
`today_second_board=0`，而同一时刻真实涨停池里有 2 只二板。测试工程师逐行核出三处取错：

| 代码 | 名称 | 真实连板数 `streak` | `limit_up_days` | `BOARD_KEYS` 实际取到 |
| --- | --- | --- | --- | --- |
| 002086 | 东方海洋 | 3 | 9 | 9 |
| 600261 | 阳光照明 | 3 | 8 | 8 |
| 000995 | 皇台酒业 | 2 | 6 | 6 |

上游口径证据（`TickHub-CN/src/tickhub_cn/datasources/market.py:517` 涨停池适配器）：

- `streak` ← 东财 `lbc`，就是连板数；
- `limit_up_days` ← 东财 `zttj.days`，是「n天m板」里的 **n**（统计窗口天数），
  `limit_up_count` ← `zttj.ct` 才是窗口内涨停次数。

`ladder.BOARD_KEYS` 把 `limit_up_days` 排在 `streak` 前面（第 3 位对第 6 位），`pick()`
取第一个非空值，所以真实包里所有 `zttj` 有值的行都取到了窗口天数。

**范围比报上来的症状大。** `BOARD_KEYS` 是共享序列，同一份 `limit_pool` 记录有四个消费面
都在错：`core/ladder.py:173`（天梯层级、`height`、断层、龙头候选）、
`core/limit_pool.py:90`（涨停样本 `streak`）、`core/pool.py:229`（候选池 `strength` /
`standing` 打分）、`core/intraday.py:468`（1进2 今日侧）。1进2 只是最先被看见的那个。

**为什么 2083 条用例全绿也没拦住：**`tests/fixtures/peer_responses/limit_up_pool.json`
里三行的 `streak` 与 `limit_up_days` 恰好相等（纯连板时 `zttj.days` == 连板数），
两键冲突的形状在 fixture 里不存在；`tests/fixtures/intraday_stubs.py` 的今日涨停池
根本不带 `limit_up_days`。昨日侧早在 DEC-035 就用 `YESTERDAY_STREAK_KEYS` 防住了同一个坑，
但防的是「别拿 `limit_up_days` 顶替 `y_streak`」，没有防住今日侧。

## 2. 决定

### 2.1 一处改序列，不新立第二份键表

`BOARD_KEYS` 内 `limit_up_days` 移到**最后一位**，其余相对顺序不变：

```
consecutive_boards / consecutive_limit_up / board_count / boards / streak / 连板数 / limit_up_days
```

四个消费面共用这一份序列，改一处即全部覆盖；今日侧继续用 `BOARD_KEYS`，
`_one_to_two` 不引入专用键表。

### 2.2 保留 `limit_up_days` 作末位兜底别名，不删除

`tests/fixtures/pool/P04_tolerant_fields_duplicate.json` 与
`tests/fixtures/metrics/S09_tolerant_fields_duplicate.json` 已把「`limit_up_days` 值为
`"2板"` 这类文本」定为容忍口径，删除会让这两处契约变卦，且没有真实数据支持删除。

**已知残余风险（接受，不额外加逻辑）**：若某个上游只给整数 `limit_up_days` 而不给 `streak`，
末位兜底仍会把窗口天数当连板数。当前已知来源没有这种形状——东财涨停池两键都发，
同花顺涨停揭秘两键都不发（只有 `high_days` 文本，照旧落 `unknown_board`）。
出现该形状时按本节改末位别名的解析条件，不放开优先级。

### 2.3 用例必须携带两键不等的真实形状

- `tests/unit/core/test_ladder.py::test_streak_outranks_limit_up_days`：直接钉共享序列的优先级；
- `tests/unit/core/test_limit_pool.py::test_eastmoney_shape_streak_wins_over_limit_up_days`：
  真实东财行形状下样本 `streak` 取 `lbc`；
- `tests/fixtures/intraday_stubs.py::limit_pool_records`（今日涨停池桩）补上与之不等的
  `limit_up_days`，让既有断言（`today_second_board == 2`）从「恒真」变成真闸门；
- `tests/unit/core/test_ladder.py::test_limit_up_days_still_accepted_as_last_alias`
  钉住 §2.2 的末位兜底不回退。

负向验证：把序列改回旧顺序，`test_streak_outranks_limit_up_days` 与
`test_snapshot_payload_carries_market_readings` 两条失败（后者报 `today_second_board`
0 != 2，与 09:36 现场一致）。

## 3. 影响

- 天梯 `height` / 层级 / 断层 / 龙头、涨停样本 `streak`、候选池 `strength`+`standing`、
  1进2 四处的数从「窗口天数」回到连板数，**普遍变小**，看板与打分的绝对值会跳变；
- 已落盘的历史快照与 `GET /api/intraday/snapshot` 原文**不重算**（DEC-035 只创建不覆盖、
  DEC-039 读面不重算），2026-11-02 之前含真实 `zttj` 的快照里 1进2 与天梯数值不可信，
  按「不补跑」的既有裁定不回填，下一个交易日起自然为新值；
- `limit_up_days` 语义本身没变，变的只是取值优先级，接口字段与信封一律不动；
- DEC-035 §「昨日连板数读 `y_streak`」结论仍成立，但其括注「`limit_up_days` 含今日封板数」
  不准确，本文更正为「`zttj.days` = 统计窗口天数」，旧决策文件按只增不改保留原文。

## 4. 替代方案

| 方案 | 不采纳原因 |
| --- | --- |
| 只在 `_one_to_two` 今日侧换显式键序列 | 症状级修法。天梯、涨停样本、候选池三处仍取窗口天数，同一个坑留在共享序列里等下一个人踩 |
| 从 `BOARD_KEYS` 删除 `limit_up_days` | 会改 P04 / S09 两处已定的字段容忍契约，涉及他人负责的 fixture 与规格，收益只是防住当前不存在的形状 |
| 新建 `TODAY_STREAK_KEYS` 与 `BOARD_KEYS` 并存 | 两份连板数序列，四个消费面要逐个改且容易漏；优先级本该是序列自己的职责 |
| 加「只接受带 `板` 后缀的 `limit_up_days`」解析条件 | 为当前不存在的形状写防御代码，违背简单优先；留作 §2.2 残余风险成真时的下一步 |
