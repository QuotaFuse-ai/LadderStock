# DEC-049 题材分项的取数口径：逐只归属优先，板块名对接即概念过滤

- 日期：2026-11-03
- 提出：后端架构师（QUOT-362，2026-11-03）
- 状态：生效
- 关联：SPEC-CANDIDATE-POOL.md §1/§2/§3.3/§5（P07–P09）、DEC-006（生态边界：不采集，
  只引用 peer）、DEC-018（口径逐字段标注）、DEC-035（盘中快照与台账分开）、DEC-043（Wire 赛道池：
  「一个元素一次调用」的先例）、DEC-048（限流退避）、`docs/ops/pool-verification.md`（§3 as-of 纪律）
- 编号依据：`git ls-tree -r --name-only origin/main docs/decisions/` 扫到的最大号 = 048，本条取 049

## 1. 背景

`parts.theme` 上线以来恒为 `null`，而当日 `concept_board` 有 100 行——不是没数据，是两件事分别坏了：

1. **板块索引一条都建不起来**：`concept_boards` 的行用 `board_name`，而解析走
   `ladder.NAME_KEYS`（`name/stock_name/sec_name/名称`，「股票名」语义），里面从来没有
   `board_name`。索引空 → 任何题材词都匹配不到 → `theme=null`。
2. **候选池行里没有题材字段**：现网 `limit_up_pool`（主工具）返回里既没有 `hot_topic` 也没有
   `reason`，`THEME_KEYS` 与 `REASON_KEYS` 各自为政，切词也无从切起。

上游另有一只现成的逐只工具 `stock_concept_blocks(code)`（layer=market，TTL 60 秒），
回 `data.boards[] = {name, code, change_pct, lead_stock}`。接它能真正解决「这只票挂在哪些概念上」，
但它的返回有三个坑（2026-11-03 实测）：

- boards 是 **industry / concept / region 混在一起**的，没有类型字段；
- 板块码是东财 `BKxxxx`，而 `concept_board` 的主键是 `gn_*` —— **不同构**，按码 join 恒空；
- `boards[].change_pct` 是**那次调用时刻**的板块涨幅，29 只票的取数时刻各不相同。

## 2. 决定

1. **板块名容忍表另立一份（`pool.BOARD_NAME_KEYS`），不动 `ladder.NAME_KEYS`**。
   后者被天梯与 sentiment 共用，语义是「股票名」；把 `board_name` 塞进去等于允许板块名
   冒充股票名。`name` 留在新表末尾只是为了兼容历史夹具。
2. **题材标签容忍表末尾接 `core/limit_pool.py` 的 `REASON_KEYS`**，不另立第二份清单；
   分隔符补 `+` / `＋`（实测涨停原因写作 `水产加工+鱼粉鱼油+“羊”字辈`）。
   同一句话在两个模块里出现两次，迟早变成两个口径。
3. **新数据集 `concept_membership`，四处联动登记**（`core/boundary.py` /
   `data/adapters/tickhub.py` 的 `RowShape` / `config/sources.yaml` / `config/sync.yaml`）。
   - 水位照 `_b("spot_quote", ...)` 先例用 `timestamp`：实时快照没有「一天一条」的水位，
     `timestamp` 只表取数时刻，**不作交易日增量起点**（该工具也没有 `since` 入参，每次整批替换）。
   - 行形状需要 `RowShape.rename`：`data.code`（股票）与 `boards[].code`（板块）**同名**，
     透传值会被记录行盖掉——盖掉之后行还是「有代码的行」（BK 码也是码），`require_code` 拦不住，
     但从此按板块代码入库，与候选池再也接不上。改名成 `board_code` / `board_name`。
   - provider `cooldown_sec: 90` ≥ 上游 60 秒 TTL：命中限流后立刻重试只是再撞一次
     还没刷新的窗口（DEC-048 同一件事）。
   - 代码池只取**当日候选池那批代码**：`sync.yaml` 新增 `codes_from: limit_pool`，
     读本机当日归档、不扩全市场、不发额外请求；优先级 `--codes` > `codes` > `codes_from` > 自选表。
     超过 `MAX_POOL_CODES` 按池子顺序截断**并把原数量写进来源说明**——静默丢尾部才是问题。
     归档池为空（休市 / 上一步失败）→ `optional` 判 `skipped`，一次调用都不发。
4. **概念过滤 = 按板块名与当日 `concept_board` 榜对接**，对接不上的归属如实丢弃。
   板块码不同构，只有名字能对接；而「在当日概念榜里」正好就是「是概念板块」的判据，
   不需要再造一份板块类型表（那会变成第二个口径，且上游改了板块归类就悄悄失真）。
   实测当日归属表里行业段（`BK04xx`）与地域段（`BK01xx`）命中概念榜 0 行，规则不误杀。
   `board_members(board_code)` 明确不用：方向反过来（从板块找成分），
   且把「一只票挂在几个概念上」这件事变成 N 次全榜扫描。
5. **涨幅只认 `concept_board` 的当日榜，绝不复用归属行里的 `change_pct`**。
   后者随每只票的调用时刻漂移，用它打分就是让分数依赖「什么时候取的数」，
   与 `docs/ops/pool-verification.md` §3 的 as-of 纪律冲突。
6. **命中多个板块继续用 `max(...)`，不改求和、不改分位名次**。
   题材分回答的是「最强那条题材有多强」：求和让「挂在很多概念上」天然占便宜，
   那是广度不是强度；分位名次则把分数与候选池规模、归属同步进度耦合——
   池子浅的时候（当日只有几只票有归属）名次几乎没有意义，而归属覆盖率恰好在每天变。
   算式与权重都不变（§3 的 `theme` 一条没动），改的只有「数从哪来」。
7. **两条路逐候选标注 `raw.theme_source`，本轮主口径进 `meta.theme_source`**。
   降级口径是**关键词**不是成分归属，但 `raw.theme_tags` 与归属名单长得一模一样——
   不标注就会被读成「这只票确定挂在这些板块上」。`meta.theme_source_counts` 给出两条路各覆盖几只。
8. **`concept_membership` 不进 `missing`，也不进 `POOL_DATASETS`**。
   它是可选的第四路输入：缺它只让题材降级，不构成「候选池缺数据」（`available` /
   `data_source_status` 的语义保持原样，`ldst pool` 的退出码口径不动）。

## 3. 备选与否决

- **扩 `ladder.NAME_KEYS` 加 `board_name`**：一处改动影响天梯与 sentiment 的「股票名」解析，
  且把两种实体混进同一张表——否决。
- **`em_hot_concept(code)` 作备源**：它是热门概念榜，不是逐只归属，主备不同口径
  （DEC-006 要求同 peer 同口径降级）——不登记为 fallback。
- **全市场建归属表**：`stock_concept_blocks` 是逐只工具，全市场等于几千次调用打爆限流，
  而且候选池只有当日涨停池那几只票用得上——否决。
- **板块码 join**：实测不同构（`BKxxxx` vs `gn_*`），恒空——做不到。
- **题材分项按归属行的 `change_pct`**：见决定 5——否决。
- **`theme` 求和 / 分位**：见决定 6——否决。

## 4. 影响

- `parts.theme` 从恒 `null` 变成有数：主口径命中走归属，未覆盖的票走关键词，
  两条路都没有才 `null`（权重照旧从分母剔除，不补 0）。
- 2026-11-03 现网实测覆盖：**30 只候选里 21 只 `theme` 非空**（`counts.theme_boards` 从 0 → 100，
  `meta.theme_source_counts` = `{stock_concept_blocks: 21, reason_keywords: 0}`）。剩下 9 只的成因分别是：
  8 只有归属行（11–23 行不等）但板块名不在当日概念榜里——榜只有 100 行，是热度榜不是全集；
  1 只（`002696`）当日扇出失败、整批没有归属行；关键词降级路当日**一只都没接上**，
  因为当日落库的涨停池行 0/48 带原因或题材字段（`_source=tickhub_limit_pool`，行里只有 `industry`）。
  这三条都不是解析代码的问题，靠补数据解决，不靠补 0。
- 新增一次逐只扇出（当日候选池规模，上限 100 只），走既有 TTL 与限流退避；
  `ldst sync` 的数据集列表多一项，`sync.schema.json` 多一个 `codes_from` 键。
- 落库多一个 `concept_membership` 分区，水位字段是 `timestamp`：
  台账里它会显示「今天取过」，不代表「今天的数据」——note 与 sync.yaml 注释都写明了。
- 测试面：`tests/fixtures/pool/P07–P09.json`（命中 / 降级 / 两条路都缺）+
  `tests/unit/core/test_pool_theme_membership.py` + `tests/fixtures/peer_responses/stock_concept_blocks.json`
  真实响应锁形状 + `test_code_pool_datasets_are_the_ones_requiring_code` 点名。
- 不动 QUOT-361 的范围：`capital` 的量纲归一（元）、`fund_flow_as_of`、逐候选 `data_as_of`
  都不在这里；字段命名以 361 为准，冲突时改这边。
