# SPEC-CANDIDATE-POOL 候选池评分口径

- 日期：2026-10-30
- 归属：数据面 R7（QUOT-145）
- 实现：`src/ladderstock/core/pool.py`（打分）、`src/ladderstock/cli/pool.py`（`ldst pool`）、
  `src/ladderstock/mcp/server.py::_tool_candidate_pool`（MCP `candidate_pool`）
- 夹具：`tests/fixtures/pool/P01…P06.json` + `tests/unit/core/test_pool_golden.py`
- 相关：`docs/specs/METRICS.md`（舍入与 6 板基准）、`DEC-006`（生态边界，不采集）、
  `DEC-012`（`available=false` → 退出码 6）、`DEC-017`（本文两条命名/权重决策的来历）
- 口径不变项：本文**不修改**任何既有指标口径；连板高度基准直接复用 `METRICS.md` §3.3 的 6 板。

## 0. 一句话

候选池 = 当日涨停池里每只票的可解释打分。五个分项（强度 / 换手 / 地位 / 资金 / 题材）
固定顺序、固定权重，**取不到数的分项给 `null` 并把它的权重从分母里剔除**，
不猜值、不补 0、不新增分项。

## 1. 输入（四个，全部只读本机缓存）

| 输入 | 数据集 | 取用字段 | 用途 |
|---|---|---|---|
| 当日涨停池 | `limit_pool` | 代码、名称、连板数、换手率、题材标签 | 候选集本身 |
| 连板梯队 | 同上（复用 `core/ladder.compute_ladder`） | `height`（当日最高板） | 「地位」的相对位置 |
| 题材成员 | `concept_board` | 板块名、板块涨幅 | 「题材」强度 |
| 资金流 | `fund_flow` | 代码、当日净额 | 「资金」集合内分位 |

三条硬约束：

1. **不新增采集入口**（DEC-006）：四个数据集都是 `data/archive.py` 里已登记的缓存回放，
   本模块不发起任何网络请求；缓存没有就是没有 → `available=false` + 退出码 6。
2. **不读隐私库**：本模块不 import `ladderstock.journal` / `data.sqlite_store` / `sqlite3`
   （AST 级断言见 `tests/integration/test_position_privacy.py::test_pool_does_not_import_privacy_store`），
   所以「我的持仓」不可能经打分结果外溢到 MCP / AI。
3. **梯队不另算一遍**：`height` 直接取 `compute_ladder` 的结果，公式只有一份（D-07 同源）。

## 2. 字段容忍表

上游 peer 的命名不统一，按优先级取**第一个非空值**；都没有就是 `null`（不猜、不用 0 顶替）。

| 语义 | 容忍键（顺序即优先级） |
|---|---|
| 代码 / 名称 / 连板数 | 复用 `core/ladder.py` 的 `CODE_KEYS` / `NAME_KEYS` / `BOARD_KEYS` |
| 换手率 | `turnover_rate`、`turnover`、`hb_rate`、`actual_turnover`、`换手率`、`换手` |
| 个股净额 | `net_inflow`、`main_net_inflow`、`net_amount`、`主力净流入`、`净流入`、`净额` |
| 题材标签 | `hot_topic`、`theme`、`topic`、`concept`、`labels`、`题材`、`概念`、`标签`、`热点` |
| 板块涨幅 | `change_pct`、`pct_chg`、`pct_change`、`changePercent`、`change`、`涨跌幅`、`涨幅` |

题材标签常一格写多个概念，按 `，,、;；/|` 与空白切分、去重、保持出现顺序（结果可复算）。
概念板块同名重复时**取最大涨幅**；空白板块名丢弃。资金流同一代码重复保留首条
（与天梯 D-04 去重口径一致）。

## 3. 分项算式（`part_i ∈ [0, 100]`，1 位小数）

| 分项 | 对外键名 | 算式 | 基准与理由 |
|---|---|---|---|
| 强度 | `strength` | `min(1, boards / 6) × 100` | 6 板基准直接复用 `METRICS.md` §3.3 `TOP_BOARD_NORM`，不另立口径；6 板以上视为「打满」 |
| 换手 | `turnover` | `clamp(换手率, 0, 20) / 20 × 100` | 20% 是短线情绪票的常见满档换手；负值记 0，超过记 100 |
| 地位 | `standing` | `min(1, boards / height) × 100` | 「相对当日最高板的位置」，随每日梯队自动伸缩 |
| 资金 | `capital` | 集合内分位名次：`严格小于该净额的样本数 / (n − 1) × 100`；`n = 1` 给中位 `50.0` | **只用名次不用金额**：上游单位有「元 / 万元」两种口径，名次不受单位影响；`n = 1` 没有比较对象，如实给中位而不是 0 或 100 |
| 题材 | `theme` | `clamp(所挂概念板块最大涨幅, 0, 5) / 5 × 100` | 板块 5% 涨幅记满档；负涨幅记 0（题材走弱不给分）；匹配不到板块 → `null` |

### 3.1 权重（和为 1.00）

| 分项 | 权重 | 理由 |
|---|---|---|
| 强度 `strength` | 0.30 | 连板高度是短线的第一排序量 |
| 地位 `standing` | 0.25 | 梯队里的相对位置决定「是不是核心」 |
| 资金 `capital` | 0.20 | 净额只在候选集内比较，抗单位歧义 |
| 题材 `theme` | 0.15 | 板块共振是加分项，不该压过个股本身 |
| 换手 `turnover` | 0.10 | 换手单独看不构成理由，只做修正项 |

权重与算式写在代码常量（`WEIGHTS` / `TURNOVER_NORM` / `THEME_NORM` / `TOP_BOARD_NORM`）里，
本文与常量必须一致，`test_weights_sum_to_one_and_sub_scores_are_fixed` 钉住「五个分项不可增删 + 权重和为 1」。

### 3.2 总分与舍入顺序

```
score = Σ(weight_i × part_i) / Σ(weight_i)   （i 只取 part_i 非 null 的分项）
```

- 分项先各自舍入到 1 位小数（`part_i` 就是对外报出的值，**用舍入后的值参与加权**，
  保证读者拿 `parts` 手算与程序 `score` 一致）；
- 总分只在最后舍入一次，1 位小数（Python `round` 半偶，与 `METRICS.md` §4.1 同口径）；
- 五个分项全为 `null` → `score = null`，该候选仍然出现，`absent_parts` 点名列满。

对外键名用 `absent_parts` 而不是第二个 `missing`：顶层 `missing` 已经是「缺哪个**数据集**」，
两个概念不同名（决策见 DEC-017 §3）。

## 4. 输出契约

`compute_pool(day, *, root=None, records=None, limit=None)`（`records` 供测试注入）：

```jsonc
{
  "date": "2026-10-22",
  "available": true,
  "reason": null,
  "source_detail": "limit_pool: archive（3 条）; concept_board: …; fund_flow: …",
  "height": 5,
  "candidate_count": 3,
  "candidates": [
    {"code": "600001", "name": "甲", "score": 91.0,
     "parts": {"strength": 83.3, "turnover": 60.0, "standing": 100.0, "capital": 100.0, "theme": 100.0},
     "absent_parts": [],
     "raw": {"boards": 5, "turnover_rate": 12.0, "height": 5, "net_inflow": 30000000.0,
             "theme_tags": ["机器人"], "theme_board_change_pct": 8.0}}
  ],
  "weights": {"strength": 0.3, "turnover": 0.1, "standing": 0.25, "capital": 0.2, "theme": 0.15},
  "formula": "score = Σ(weight_i × part_i) / Σ(weight_i)…",
  "counts": {"limit_pool_rows": 3, "dropped_duplicates": 0, "unknown_code": 0,
             "theme_boards": 3, "fund_flow_rows": 3},
  "missing": [],
  "data_source_status": "ok"
}
```

- 排序：`score` 降序 → 同分按 `code` 升序 → `score=null` 的排最后；`--limit` 在排序**之后**截断。
- `raw` 里给出该候选用到的原始值，任何一条分数都能就地复算。
- `counts` 是输入侧的账（原始行数、去重掉的重复 code、缺代码被丢的行、板块数、资金行数），
  用来解释 `candidate_count` 为什么比 `limit_pool_rows` 少。
- `data_source_status` 恒为 `ok` / `missing`，与 web、MCP 同一约定。

两个出口的外层形状（同一份 `compute_pool`，公式不复制）：

| 出口 | 外层 | 载荷位置 |
|---|---|---|
| `ldst pool --date --output json` | `LadderEnvelope`（`topic=candidate_pool`，`domain=sentiment`） | `payload` **就是**上面的对象 |
| MCP `candidate_pool` | `LadderEnvelope`（`_envelope()`，带 `summary`） | `payload.pool` = 上面的对象，`payload.summary` = 一行人类可读结论 |

这与既有工具一致（`ladder_get_sentiment` 也是 `payload.sentiment`），CLI 侧则与
`ldst sentiment show` 一样平铺。`available=false` 时 CLI 退出码 **6**（DEC-012），
stdout 仍是完整结构化结果，stderr 说明原因。

## 5. 手算对照（与夹具逐条一致）

夹具的断言视图（`pool_view`）只取「口径相关」的字段：`available` / `reason` / `height` /
`candidate_count` / 每条候选的 `code`、`score`、`parts`、`absent_parts` / `counts` / `missing`
（在夹具里换名写作 `missing_datasets`，避免与候选级的 `absent_parts` 混）。
视图里**没有**的字段（`source_detail`、`formula`、`weights`、`data_source_status`、候选的 `raw`）
由另外两条测试负责：`test_weights_sum_to_one_and_sub_scores_are_fixed` 与
`test_parts_and_score_recompute_from_reported_inputs`（后者正是从 `raw` 出发重算）。
`assert_matches` 会拒绝「视图里出现了预期没声明的键」，所以新增口径字段必须同时补夹具。

### P01 标准池（`height=5`）

| code | boards | 换手% | 净额 | 题材板块涨幅 | strength | turnover | standing | capital | theme | score |
|---|---|---|---|---|---|---|---|---|---|---|
| 600001 | 5 | 12.0 | 3e7 | 机器人 8.0 | 5/6→83.3 | 12/20→60.0 | 5/5→100.0 | 分位 100.0 | ≥5→100.0 | **91.0** |
| 600002 | 3 | 25.0 | −5e6 | 中药 −1.0 | 3/6→50.0 | ≥20→100.0 | 3/5→60.0 | 分位 0.0 | 负→0.0 | **40.0** |
| 600003 | 1 | 2.5 | 8e6 | 机器人 8.0 | 1/6→16.7 | 2.5/20→12.5 | 1/5→20.0 | 分位 50.0 | ≥5→100.0 | **36.3** |

```
600001 = 0.30×83.3 + 0.10×60.0 + 0.25×100.0 + 0.20×100.0 + 0.15×100.0 = 90.99 → 91.0
600002 = 0.30×50.0 + 0.10×100.0 + 0.25×60.0 + 0.20×0.0 + 0.15×0.0        = 40.00 → 40.0
600003 = 0.30×16.7 + 0.10×12.5 + 0.25×20.0 + 0.20×50.0 + 0.15×100.0        = 36.26 → 36.3
```

### P02 缺 `concept_board` 与 `fund_flow`（权重重归一）

| code | parts | 分母 | score |
|---|---|---|---|
| 600001 | 50.0 / 40.0 / 100.0 / **null** / **null** | 0.30+0.10+0.25 = 0.65 | (0.30×50 + 0.10×40 + 0.25×100)/0.65 = 44/0.65 = **67.7** |
| 600002 | 16.7 / 100.0 / 33.3 / null / null | 0.65 | (0.30×16.7 + 0.10×100 + 0.25×33.3)/0.65 = 23.335/0.65 = **35.9** |

`missing = ["concept_board", "fund_flow"]`，`absent_parts = ["capital", "theme"]`。

### P03 空池

三个数据集都没有缓存 → `available=false`、`candidate_count=0`、`height=null`、
`reason="涨停池没有可解析的候选（可能尚未同步）"`、`missing` 点列三个数据集，CLI 退出码 6。

### P04 字段容忍 + 重复代码 + 无代码行

4 行原始数据里：`600010` 重复（保留首条，`dropped_duplicates=1`）、一行没有 code
（`unknown_code=1`，且不参与当日最高板，`height=4` 取有码行的最大值——QUOT-231 L6 口径）。

| code | 取到的字段 | strength | turnover | standing | capital | theme | score |
|---|---|---|---|---|---|---|---|
| 600011 | `连板数="4"`、`换手率="30"`、概念「半导体；光模块」 | 4/6→66.7 | ≥20→100.0 | 4/4→100.0 | −3e6 → 0.0 | max(3.0,5.0)=5.0→100.0 | 0.30×66.7+0.10×100+0.25×100+0.20×0.0+0.15×100 = 70.01 → **70.0** |
| 600010 | `consecutive_boards=2`、`turnover=5.0`、题材「半导体,存储」 | 2/6→33.3 | 5/20→25.0 | 2/4→50.0 | 12e6 → 100.0 | max(3.0,−2.0)=3.0→60.0 | 9.99+2.5+12.5+20+9 = 53.99 → **54.0** |

`counts = {limit_pool_rows: 4, dropped_duplicates: 1, unknown_code: 1, theme_boards: 3, fund_flow_rows: 2}`。

### P05 单一候选

`capital` 的 `n=1` 分支给中位 `50.0`（不是 0、不是 100）：
`0.30×100 + 0.10×3.0 + 0.25×100 + 0.20×50 + 0.15×20 = 68.3`（换手 0.6/20→3.0，题材 1.0/5→20.0）。

### P06 缺连板数 / 题材匹配不到

| code | parts（强度/换手/地位/资金/题材） | 分母 | score |
|---|---|---|---|
| 600600 | **null** / 50.0 / **null** / 100.0 / 50.0 | 0.45 | (0.10×50 + 0.20×100 + 0.15×50)/0.45 = 32.5/0.45 = **72.2** |
| 600601 | 33.3 / 20.0 / 100.0 / 0.0 / **null** | 0.85 | (0.30×33.3 + 0.10×20 + 0.25×100 + 0.20×0)/0.85 = 36.99/0.85 = **43.5** |

600601 的题材「中药」在 `concept_board` 里没有对应板块 → `theme=null`（**不是 0**），
`absent_parts=["theme"]`；600600 没有连板数 → `absent_parts=["strength", "standing"]`。

## 6. 可复算性与确定性

- `test_pool_matches_hand_calculation`：夹具里的 `expected` 由本文 §5 手算写成，程序必须逐字段命中
  （做法与 `METRICS.md` + `tests/fixtures/metrics/S*.json` 相同）；
- `test_parts_and_score_recompute_from_reported_inputs`：**另一份**按本文文字独立实现的算式
  （`documented_parts` / `documented_score`）从 `raw` 重算 `parts` 与 `score`，与程序输出比对——
  防止「实现和测试抄同一份代码」；
- `test_scoring_is_deterministic`：同一输入两次 `compute_pool` 的 JSON **逐字节相同**。
  范围要说清：这是**载荷级**确定性，外层信封的 `fetched_at` 每次调用都取当前时间，
  所以「两次 CLI 全量 stdout diff 为空」本来就不成立，比对要落在 `payload`；
- 打分不用随机、不用时间、不用环境变量，只依赖 §1 的四个数据集与本文常量。

## 7. 与「我的持仓」的关系

候选池与持仓**互不引用**：`core/pool.py` 不 import 隐私库，`journal/position.py` 不 import 候选池
（两侧都有 AST 级断言）。唯一允许的交集是「同一只票同时出现在候选池与持仓里」，
那是用户自己看的两件事，程序不做任何合并、不做任何「你已持有」标注——
避免把持仓经打分结果外溢到 MCP / AI 上下文。
