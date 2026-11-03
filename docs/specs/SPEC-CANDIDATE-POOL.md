# SPEC-CANDIDATE-POOL 候选池评分口径

- 日期：2026-10-30
- 更新：2026-11-03（QUOT-362：题材接上逐只归属 `concept_membership`，并修掉板块名解析
  ——`parts.theme` 此前恒为 `null`。新增 §3.3 与夹具 P07–P09，口径见 DEC-049；
  其余分项、权重、算式不变）
- 归属：数据面 R7
- 实现：`src/ladderstock/core/pool.py`（打分）、`src/ladderstock/cli/pool.py`（`ldst pool`）、
  `src/ladderstock/mcp/server.py::_tool_candidate_pool`（MCP `candidate_pool`）
- 夹具：`tests/fixtures/pool/P01…P09.json` + `tests/unit/core/test_pool_golden.py`
  （题材链另有 `tests/unit/core/test_pool_theme_membership.py`）
- 相关：生态边界（不采集）、`available=false` → 退出码 6、本文两条命名与权重口径
- 口径不变项：五个分项、权重与算式不变；连板高度基准直接复用既有 6 板口径。
  QUOT-362 只改「题材的数从哪来」（§3.3），没改题材的算式本身。

## 0. 一句话

候选池 = 当日涨停池里每只票的可解释打分。五个分项（强度 / 换手 / 地位 / 资金 / 题材）
固定顺序、固定权重，**取不到数的分项给 `null` 并把它的权重从分母里剔除**，
不猜值、不补 0、不新增分项。

## 1. 输入（四路必取 + 一路可选，全部只读本机缓存）

| 输入 | 数据集 | 取用字段 | 用途 |
|---|---|---|---|
| 当日涨停池 | `limit_pool` | 代码、名称、连板数、换手率、题材标签 | 候选集本身 |
| 连板梯队 | 同上（复用 `core/ladder.compute_ladder`） | `height`（当日最高板） | 「地位」的相对位置 |
| 概念板块榜 | `concept_board` | 板块名、板块涨幅 | 「题材」强度 |
| 个股↔板块归属 | `concept_membership`（可选，QUOT-362） | 代码、板块名 | 「题材」成员：这只票挂在哪些概念板块上 |
| 资金流 | `fund_flow` | 代码、当日净额 | 「资金」集合内分位 |

三条硬约束：

1. **不新增采集入口**：五路数据集都是 `data/archive.py` 里已登记的缓存回放，
   本模块不发起任何网络请求；缓存没有就是没有 → `available=false` + 退出码 6。
   `concept_membership` 的取数在同步侧（`ldst sync`，代码池来自当日涨停池归档），
   不在这里（见 §3.3）。
2. **不读隐私库**：本模块不 import `ladderstock.journal` / `data.sqlite_store` / `sqlite3`
   （AST 级断言见 `tests/integration/test_position_privacy.py::test_pool_does_not_import_privacy_store`），
   所以「我的持仓」不可能经打分结果外溢到 MCP / AI。
   （代码池解析在 `cli/sync.py` 里读自选表，那是同步入口，与本模块无关。）
3. **梯队不另算一遍**：`height` 直接取 `compute_ladder` 的结果，公式只有一份（D-07 同源）。

## 2. 字段容忍表

上游 peer 的命名不统一，按优先级取**第一个非空值**；都没有就是 `null`（不猜、不用 0 顶替）。

| 语义 | 容忍键（顺序即优先级） |
|---|---|
| 代码 / 名称 / 连板数 | 复用 `core/ladder.py` 的 `CODE_KEYS` / `NAME_KEYS` / `BOARD_KEYS` |
| **板块名** | `board_name`、`concept_name`、`板块名称`、`板块名`、`name`、`名称`（`pool.py` 的 `BOARD_NAME_KEYS`，QUOT-362） |
| 换手率 | `turnover_rate`、`turnover`、`hb_rate`、`actual_turnover`、`换手率`、`换手` |
| 个股净额 | `net_inflow`、`main_net_inflow`、`net_amount`、`main_net`、`主力净流入`、`净流入`、`净额` |
| 题材标签 | `hot_topic`、`theme`、`topic`、`concept`、`labels`、`题材`、`概念`、`标签`、`热点`，末尾接 `core/limit_pool.py` 的 `REASON_KEYS`（`reason`、`limit_up_reason`、`涨停原因`、`解释`） |
| 板块涨幅 | `change_pct`、`pct_chg`、`pct_change`、`changePercent`、`change`、`涨跌幅`、`涨幅` |
| 归属行的代码 | `code`、`symbol`、`stock_code`（`MEMBERSHIP_CODE_KEYS`：落库行的代码列是**股票代码**，板块码在 `board_code` 里） |

板块名单独一份、**不动 `ladder.NAME_KEYS`**：后者是天梯与 sentiment 都在用的「股票名」语义，
而现网 `concept_boards` / `stock_concept_blocks` 的行用的是 `board_name`——
把两种语义并到一张表里，等于要么板块索引建不起来（QUOT-362 之前的真实状态：
当日 `concept_board` 有 100 行、`parts.theme` 仍恒为 `null`），要么股票名被板块名污染。

题材标签常一格写多个概念，按 `，,、;；/|＋` 与空白切分、去重、保持出现顺序（结果可复算）。
`+` / `＋` 也在分隔符里（QUOT-362 实测：涨停原因的真实写法是
`水产加工+鱼粉鱼油+“羊”字辈`，`+` 才是主分隔符）。
概念板块同名重复时**取最大涨幅**；空白板块名丢弃。资金流同一代码重复保留首条
（与天梯 D-04 去重口径一致）。

## 3. 分项算式（`part_i ∈ [0, 100]`，1 位小数）

| 分项 | 对外键名 | 算式 | 基准与理由 |
|---|---|---|---|
| 强度 | `strength` | `min(1, boards / 6) × 100` | 6 板基准直接复用 `METRICS.md` §3.3 `TOP_BOARD_NORM`，不另立口径；6 板以上视为「打满」 |
| 换手 | `turnover` | `clamp(换手率, 0, 20) / 20 × 100` | 20% 是短线情绪票的常见满档换手；负值记 0，超过记 100 |
| 地位 | `standing` | `min(1, boards / height) × 100` | 「相对当日最高板的位置」，随每日梯队自动伸缩 |
| 资金 | `capital` | 集合内分位名次：`严格小于该净额的样本数 / (n − 1) × 100`；`n = 1` 给中位 `50.0` | **只用名次不用金额**：上游单位有「元 / 万元」两种口径，名次不受单位影响；`n = 1` 没有比较对象，如实给中位而不是 0 或 100 |
| 题材 | `theme` | `clamp(所挂概念板块最大涨幅, 0, 5) / 5 × 100` | 板块 5% 涨幅记满档；负涨幅记 0（题材走弱不给分）；「所挂板块」按 §3.3 取，取不到 → `null` |

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
两个概念刻意不同名（详见 §3）。

### 3.3 题材的两条取数路（QUOT-362 / DEC-049）

算式只有一条（§3 的 `theme`），**「所挂概念板块」怎么得到**有两条路，优先级固定：

| 路 | `meta.theme_source` / `raw.theme_source` | 成员来源 | 涨幅来源 |
|---|---|---|---|
| 主口径 | `stock_concept_blocks` | `concept_membership` 当日归档里该代码挂着的板块名 | **只取当日 `concept_board` 榜里的涨幅** |
| 降级口径 | `reason_keywords` | 涨停池行的题材标签 / `reason` 按 §2 分隔符切词 | 同上 |
| 都没有 | `null` | — | `theme = null`，0.15 权重从分母剔除（不补 0） |

四条口径细节，都是踩过的坑：

1. **涨幅只认 `concept_board` 的当日榜**。归属快照里 `boards[].change_pct` 是「那次调用时刻的
   板块涨幅」，逐只取数时每只票的时刻都不一样，用它就等于让分数依赖取数时刻
   （与 `docs/ops/pool-verification.md` §3 的 as-of 纪律同一件事）。
2. **概念过滤 = 按名 join 当日概念榜**。`stock_concept_blocks` 明写 industry / concept / region
   **混在一起**，而它的板块码是东财 `BKxxxx`，与 `concept_board` 的主键（实测 `gn_*`）不同构，
   两边只能按板块名对接；「对接得上」本身就是概念板块的判据。
   2026-11-03 实测当日归属表：行业段（`BK04xx`）与地域段（`BK01xx`，含「XX板块」结尾的地域名）
   命中当日概念榜 **0 行**，即过滤规则没有误杀概念归属。榜外的归属如实丢弃，不做任何补数。
3. **`theme_source` 必须逐候选标出来**，因为降级口径是**关键词**不是成分归属——
   没有标注时，`raw.theme_tags` 看起来和归属名单一模一样。
   `meta.theme_source` 是本轮的主口径（有归属命中就是 `stock_concept_blocks`），
   `meta.theme_source_counts` 给出两条路各覆盖几只，两者不能互相代替。
4. **命中多个板块取最大涨幅**（`max(...)`，沿用 §3 既有口径）。求和会让「挂在很多概念上」
   天然占便宜，而题材分回答的是「最强那条题材有多强」；分位名次则会把分数与候选池规模、
   归属同步进度耦合——池子越浅分位越没有意义（理由见 DEC-049）。

`concept_membership` **不进** `missing`：它是可选的第四路输入，缺它只让题材降级到关键词，
不构成「候选池缺数据」（`missing` 仍然只有 `limit_pool` / `concept_board` / `fund_flow` 三个）。

**覆盖率的上界由两张表决定，不由解析代码决定**（2026-11-03 实测 21/30 只候选 `theme` 非空）：
当日概念榜只有 100 行（热度榜，不是概念全集），挂得再准、板块名不在榜里也只能 `null`；
而涨停池当日落库行没有原因字段时，降级路一只也接不上。这两种情况都按 `null` 报出并点名进
`absent_parts`，不补 0、不扩榜、不去调 `board_members` 反查（DEC-049 否决项）。

## 4. 输出契约

`compute_pool(day, *, root=None, records=None, limit=None)`（`records` 供测试注入）：

```jsonc
{
  "date": "2026-10-22",
  "available": true,
  "reason": null,
  "source_detail": "limit_pool: archive（3 条）; concept_board: …; fund_flow: …; "
                   "concept_membership: …（N 条，X 只落库，M 只命中当日概念榜）",
  "height": 5,
  "candidate_count": 3,
  "candidates": [
    {"code": "600001", "name": "甲", "score": 91.0,
     "parts": {"strength": 83.3, "turnover": 60.0, "standing": 100.0, "capital": 100.0, "theme": 100.0},
     "absent_parts": [],
     "raw": {"boards": 5, "turnover_rate": 12.0, "height": 5, "net_inflow": 30000000.0,
             "theme_tags": ["机器人"], "theme_board_change_pct": 8.0,
             "theme_source": "stock_concept_blocks"}}
  ],
  "weights": {"strength": 0.3, "turnover": 0.1, "standing": 0.25, "capital": 0.2, "theme": 0.15},
  "formula": "score = Σ(weight_i × part_i) / Σ(weight_i)…",
  "meta": {"theme_source": "stock_concept_blocks", "theme_membership_rows": 4,
           "theme_membership_landed_codes": 3, "theme_membership_codes": 2,
           "theme_source_counts": {"stock_concept_blocks": 2, "reason_keywords": 1}},
  "counts": {"limit_pool_rows": 3, "dropped_duplicates": 0, "unknown_code": 0,
             "theme_boards": 3, "fund_flow_rows": 3},
  "missing": [],
  "data_source_status": "ok"
}
```

- 排序：`score` 降序 → 同分按 `code` 升序 → `score=null` 的排最后；`--limit` 在排序**之后**截断。
- `raw` 里给出该候选用到的原始值，任何一条分数都能就地复算。
  `raw.theme_source` 是**这只票**的题材来源（§3.3），`null` 表示两条路都没数。
- `meta` 是题材链的账（本轮主口径 + 归属表的行/覆盖只数 + 两条路各几只候选），
  载荷级字段，不参与打分，也不进 `counts`。两个「只数」刻意分开：
  `theme_membership_landed_codes` = 归属表里有几只票（同步覆盖面），
  `theme_membership_codes` = 其中几只挂的板块名在当日概念榜里（题材分能用的）。
  2026-11-03 现网是 516 行 / 29 只落库 / 21 只命中榜——差额是榜单深度，不是解析丢了行。
- `counts` 是输入侧的账（原始行数、去重掉的重复 code、缺代码被丢的行、板块数、资金行数），
  用来解释 `candidate_count` 为什么比 `limit_pool_rows` 少。
- `data_source_status` 恒为 `ok` / `missing`，与 web、MCP 同一约定。

两个出口的外层形状（同一份 `compute_pool`，公式不复制）：

| 出口 | 外层 | 载荷位置 |
|---|---|---|
| `ldst pool --date --output json` | `LadderEnvelope`（`topic=candidate_pool`，`domain=sentiment`） | `payload` **就是**上面的对象 |
| MCP `candidate_pool` | `LadderEnvelope`（`_envelope()`，带 `summary`） | `payload.pool` = 上面的对象，`payload.summary` = 一行人类可读结论 |

这与既有工具一致（`ladder_get_sentiment` 也是 `payload.sentiment`），CLI 侧则与
`ldst sentiment show` 一样平铺。`available=false` 时 CLI 退出码 **6**，
stdout 仍是完整结构化结果，stderr 说明原因。

## 5. 手算对照（与夹具逐条一致）

夹具的断言视图（`pool_view`）只取「口径相关」的字段：`available` / `reason` / `height` /
`candidate_count` / 每条候选的 `code`、`score`、`parts`、`absent_parts` / `counts` / `missing`
（在夹具里换名写作 `missing_datasets`，避免与候选级的 `absent_parts` 混）。
视图里**没有**的字段（`source_detail`、`formula`、`weights`、`meta`、`data_source_status`、候选的 `raw`）
由另外两条测试负责：`test_weights_sum_to_one_and_sub_scores_are_fixed` 与
`test_parts_and_score_recompute_from_reported_inputs`（后者正是从 `raw` 出发重算）；
`meta` 与 `raw.theme_source` 的取值由 `tests/unit/core/test_pool_theme_membership.py` 钉。
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
（`unknown_code=1`，且不参与当日最高板，`height=4` 取有码行的最大值——既有 6 板口径）。

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

### P07 归属命中（`concept_membership`，主口径）

板块榜与归属表都用真实键 `board_name`；归属表里还挂了「农林牧渔」（行业段，不在当日概念榜里）。

| code | boards | 换手% | 净额 | 题材来源 | 题材板块涨幅 | strength | turnover | standing | capital | theme | score |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 600010 | 4 | 10.0 | 2e7 | 归属 | 固态电池 4.0 | 4/6→66.7 | 10/20→50.0 | 4/4→100.0 | 分位 100.0 | 4/5→80.0 | 20.01+5+25+20+12 = 82.01 → **82.0** |
| 600011 | 2 | 4.0 | −1e6 | 归属（关键词被弃用） | 光模块 1.0 | 2/6→33.3 | 4/20→20.0 | 2/4→50.0 | 分位 0.0 | 1/5→20.0 | 9.99+2+12.5+0+3 = 27.49 → **27.5** |

600011 的标签写着「光模块、妖股」，而「妖股」当日 +5.0%（走关键词会得 100.0）——
**归属命中时降级口径不参与**，所以题材是 20.0（`theme_source = stock_concept_blocks`）。

### P08 归属没覆盖到这只（降级到 `reason` 关键词）

归属表当日只覆盖 600099，本候选 600020 不在里面 → 退回关键词，
`raw.theme_source = reason_keywords`（`meta.theme_source` 仍是 `stock_concept_blocks`，
两者说的是不同的事，故都要标）。

| code | boards | 换手% | 净额 | `reason` 切词 | 命中板块 | strength | turnover | standing | capital | theme | score |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 600020 | 3 | 8.0 | 5e6（n=1） | `水产加工+饲料+跨境物流` → 三个词 | 饲料 5.0、跨境物流 1.5（水产加工不在榜） | 3/6→50.0 | 8/20→40.0 | 3/3→100.0 | 中位 50.0 | ≥5→100.0 | 15+4+25+10+15 = **69.0** |

### P09 两条路都没有（`theme = null`，权重剔出分母）

归属表里两只挂的都是行业/地域段（榜外）→ 过滤后 `members` 为空；600030 的涨停池行既无题材标签
也无 `reason` → 关键词也切不出词。板块索引本身建起来了（`theme_boards=2`）：
**「榜有没有」与「这只票挂哪」是两件事**。

| code | boards | 换手% | 净额 | 分母 | score |
|---|---|---|---|---|---|
| 600030 | 2 | 6.0 | 1.2e6（n=1） | 0.30+0.10+0.25+0.20 = 0.85 | (0.30×33.3 + 0.10×30.0 + 0.25×100.0 + 0.20×50.0)/0.85 = 47.99/0.85 = **56.5** |

`parts.theme = null`、`absent_parts = ["theme"]`、`raw.theme_source = null`、`missing = []`
（`concept_membership` 缺席不进 `missing`，见 §3.3）。

## 6. 可复算性与确定性

- `test_pool_matches_hand_calculation`：夹具里的 `expected` 由本文 §5 手算写成，程序必须逐字段命中
  （做法与 `METRICS.md` + `tests/fixtures/metrics/S*.json` 相同）；
- `test_parts_and_score_recompute_from_reported_inputs`：**另一份**按本文文字独立实现的算式
  （`documented_parts` / `documented_score`）从 `raw` 重算 `parts` 与 `score`，与程序输出比对——
  防止「实现和测试抄同一份代码」；
- `test_scoring_is_deterministic`：同一输入两次 `compute_pool` 的 JSON **逐字节相同**。
  范围要说清：这是**载荷级**确定性，外层信封的 `fetched_at` 每次调用都取当前时间，
  所以「两次 CLI 全量 stdout diff 为空」本来就不成立，比对要落在 `payload`；
- 打分不用随机、不用时间、不用环境变量，只依赖 §1 的五路数据集与本文常量。

## 7. 与「我的持仓」的关系

候选池与持仓**互不引用**：`core/pool.py` 不 import 隐私库，`journal/position.py` 不 import 候选池
（两侧都有 AST 级断言）。唯一允许的交集是「同一只票同时出现在候选池与持仓里」，
那是用户自己看的两件事，程序不做任何合并、不做任何「你已持有」标注——
避免把持仓经打分结果外溢到 MCP / AI 上下文。
