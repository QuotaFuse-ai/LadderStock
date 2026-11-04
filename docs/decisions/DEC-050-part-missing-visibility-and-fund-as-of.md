# DEC-050 分项缺数的可见性：回落要标口径日期、覆盖率按对外候选统计

- 日期：2026-11-03
- 提出：后端架构师（QUOT-361 / QUOT-362 验收退回，QUOT-364）
- 状态：生效
- 关联：SPEC-CANDIDATE-POOL.md §3.3/§3.4/§4/§5（P10–P11）、DEC-049（题材归属与按名 join，
  本条修订它的第 8 项「归属不进任何计数」）、DEC-018（口径逐字段标注）、DEC-006（不新增采集入口）、
  DEC-048（限流退避与陈旧回放）
- 编号依据：`git ls-tree -r --name-only origin/main docs/decisions/` 扫到的最大号 = 049，本条取 050
  （049 与 048 各有一份同号的前端条目，按 DEC-045 让号给先合并方，后端条目继续往上取）

## 1. 背景

QUOT-364 的验收在合并后的主干 `c2acaef` 上报了四条，其中三条是同一类病：**分数是对的，
但读者从响应里看不出这个分数是怎么来的**。

- **B-3（严重）**：`core/ladder.py::dataset_snapshot()` 只返回 `(records, backend)`，
  把 `archive.snapshot()` 已经算好的 `CacheSnapshot.as_of` 丢了。资金只读请求日分区，
  而日频 `stock_fund_flow` 的主力净额是**当日盘后**才发布的——当日 sync 没跑、
  或在盘后早段取证，`capital` 整列为空，而 `data_as_of` 仍然写着请求日。
  2026-11-03 手工只放 T-1（11-02）40 行、不留 11-03 分区：`capital` 0/30。
- **B-1（严重）**：`meta` 里的题材覆盖数按**归属表全量**统计（21 只），
  而响应里 `limit=30` 截断后只有 18 只候选 `theme` 非空。SPEC 与开发回报都写了 21，
  验收方复现不出来。「落库了几只」与「对外这几只里几只命中榜」被塞进了同一个数字。
- **B-2（一般）**：`concept_membership` 既不进 `POOL_DATASETS` 也不进 `counts`，
  于是归属整列取不到时 `missing=[]` + `data_source_status=ok`，
  消费方**没有任何机器可读的字段**能看出题材这一路是降级出来的。
- **B-4（记录）**：上游两条通道对**同名板块**的涨幅可以符号相反
  （`融资融券`：`stock_concept_blocks` −1.27 vs `concept_boards` +1.2782，后者 `price`/`change` 自洽），
  且 `board_code` 一侧 `gn_rzrq`、一侧 `BK0596` 不同构。实现恰好只用了榜单侧，
  但这是巧合而不是规定——下一棒若复用 `boards[].change_pct` 就会踩上。

## 2. 决定

1. **回落必须自带口径日期，且不用信封字段承载**（B-3）。
   `core/pool.py` 新增 `fund_flow_snapshot()`（内部走 `archive.snapshot()`，
   经新增的 `dataset_snapshot_as_of()` 拿回 `as_of`）：当日分区 → 按自然日往前找，
   最多 `FUND_LOOKBACK_DAYS = 6` 天；找到就按记录里的 `trade_date` 最大值报告口径日期。
   落地四处：`meta.fund_data_as_of`、`meta.fund_is_request_day`、
   `source_detail` 的 `fund_flow` 段（`口径日期 X ≠ 请求日 Y，回落读取`）、`degraded_inputs`。
   - **不改 `ladder.dataset_snapshot()` 的签名**：天梯与情绪都在用它的二元组，
     加返回值等于把两个不相干模块的调用点一起改一遍。新增兄弟函数比改旧函数便宜。
   - **不复用信封 `data_as_of`**：它是 `LadderEnvelope` 的必填标量 date，语义是
     「这份载荷依据的涨停池那天」，`cache_meta()` 只取首个数据集。把它换成资金日期
     会让天梯/情绪的同名字段变成两种意思。
   - **不逐候选挂日期**：30 只票的资金同源于一张分区，逐只挂日期等于把
     「一路输入的一个属性」伪装成「每只票各自的属性」。
   - **窗口外不捞旧数**：>6 天还没有就是 `capital=null` + 0.20 剔出分母。
     窗口再宽就会把「这条链路断了两周」读成「资金偏弱」，那是伪造而不是取数。
2. **覆盖率数字只准按对外候选统计，表级覆盖单独命名**（B-1）。
   `meta` 里凡是分母为 `limit` 之后那批候选的计数，一律带 `candidates`
   （`membership.candidates` / `membership.states` / `membership.not_fetched_codes` /
   `theme_source_counts` / `parts_non_null`）；整张归属表的口径只叫
   `membership.rows` / `table_codes` / `table_board_matched_codes`。
   旧字段 `theme_membership_rows` / `theme_membership_landed_codes` / `theme_membership_codes`
   由这三个 `table_*` 取代——它们之所以出事，正是因为名字看不出自己数的是哪张表。
3. **归属这一路分三种状态点名**（B-1 的可操作部分）：`hit` / `landed_off_board` /
   `not_fetched`。打分侧变不出归属行，但只有把「根本没取到」的代码列出来
   （`membership.not_fetched_codes`），下一棒才知道该去查同步扇出而不是查解析。
4. **`data_source_status` 不新增第四档**（B-2）。`ok|missing` 两档是 web / MCP / 界面共同的约定，
   `api/radar.ts` 按 `ok|partial|missing` 解析，多一个枚举值会让界面落到未知态。
   降级的可见性走新增的 `degraded_inputs` 数组 + `counts.concept_membership_rows`。
   **修订 DEC-049 第 8 项**：原决定「`concept_membership` 不进任何计数、状态字段不动」
   本意是「别让可选输入的缺失冒充候选池缺数据」，这个前半句保留（它仍然不进 `missing`），
   后半句作废——「不进 missing」不等于「静默」。
   `POOL_DATASETS` 把 `concept_membership` 加在**末尾**：`cache_meta()` 只取首位算信封日期，
   加在末尾不改变信封 `data_as_of` 的语义。
5. **同名板块涨幅只认 `concept_board`，归属侧 `boards[].change_pct` 不参与算分**（B-4）。
   不做「取绝对值 / 取平均 / 谁的更可信」这类调和——两条通道谁对无法在本模块裁定，
   但口径只能有一个。已写进 SPEC §3.3 第 1 条，并有夹具钉住（符号相反时分数仍取榜单侧）。

## 3. 备选方案与否决理由

| 方案 | 否决理由 |
|---|---|
| 资金取不到就补 0 / 用请求日顶 `data_as_of` | 破坏红线「不伪造」；补 0 会把 0.20 权重的分母留着，分数直接失真 |
| 回落不设窗口（取库里最新一天） | 会把两周前的分区读成「今天的资金」，比空着更危险 |
| 信封 `data_as_of` 改成资金口径日期 | 一个字段两种语义，天梯/情绪同名字段的读者全部被误导（`tests/contract/` 也锁着必填 date） |
| `meta` 里同时保留表级与窗口两套同名计数 | B-1 就是这么出的事；命名区分是这次唯一的防线 |
| `data_source_status` 加 `degraded` | 消费方按三档解析，加第四档让界面塌成未知态；能力可以用数组表达，就不动枚举 |
| 用 `board_code` join 两条通道 | 实测不同构（`gn_rzrq` vs `BK0596`），按码 join 恒空 |
| 打分侧自己补归属（调 `board_members` 反查） | 违反 DEC-006「不新增采集入口」，且 DEC-049 已否决过一次 |

## 4. 影响

- `src/ladderstock/core/pool.py`：新增 `dataset_snapshot_as_of()` / `fund_flow_snapshot()` /
  `_pool_meta()`，`POOL_DATASETS` 末位加 `concept_membership`，载荷新增 `degraded_inputs`，
  `counts` 新增 `concept_membership_rows`，`meta` 结构重组（旧 `theme_membership_*` 三个键退役）。
- 夹具：新增 `P10_fund_partition_uses_main_net.json`（只有 `main_net` 的日频形状）、
  `P11_fund_falls_back_to_t_minus_1.json`（回落 + 口径日期）；P01–P09 的 `counts` /
  `degraded_inputs` / `fund_data_as_of` 随之补齐。
- 测试：`tests/unit/core/test_pool_fund_as_of.py`（回落、最近一天、窗口外、签名）；
  `test_pool_live_fields.py` 的 `compute_pool` 用例改为走真缓存不打桩——
  QUOT-364 承认的漏测（夹具与容忍表一起缺 `main_net` 时两边一起绿）只有真读写路径能堵住。
- **复验入口（裁定）**：本条改动的验收取证必须从**干净检出**跑 `ldst pool --date <D> --output json`，
  环境变量 `LDST_DATA_DIR` 指向中心库 `D:\QuotaFuseModules\LadderStock\data`（只写变量名与路径，
  不写值）；**不能**读 `http://127.0.0.1:5950/api/pool`——5950 当前进程跑的是
  `D:\workplace\LadderStock` 那棵脏树的代码（落后 `origin/main` 198 提交，`docs/ops/pool-verification.md` §1），
  在它上面复验只会看到改动前的行为。`pool-verification.md` 的 §1/§2 把取证入口绑在这棵脏树进程上，
  需要按上面两句改正——那份文档是 QUOT-363（运维）的产出，本条只下裁定、不代改。
- 未解决、也不在本条范围内：盘中实时资金通路（`realtime_fund_flow` 在主干没有任何登记），
  所以 QUOT-364 第 5 条判据（盘中 vs 盘后两点取证）在当前主干上结构性无法取证，
  等 QUOT-367 / QUOT-368 落地后重跑。
