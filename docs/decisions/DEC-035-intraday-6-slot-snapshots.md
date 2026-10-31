# DEC-035 竞价与盘中 6 时点快照：当天当场采样、只创建不覆盖、绝不补跑

- 日期：2026-11-01
- 提出：项目领导人（QUOT-289，补 QUOT-286 的决策记录欠账）
- 状态：生效（追溯补记；口径随 QUOT-286 落地于 `main@6caab71` / 合入 `42d923d`）
- 编号：`DEC-035`。`DEC-034` 已由 QUOT-288 的 `/__state/index` 占用，本决策不占用 034（避免重演
  main 上出现两个 `DEC-031` 的撞号事故）
- 关联：DEC-006（数据只来自生态 peer、只落本机）、DEC-007（同步与缓存）、
  DEC-018 §R6（无源就如实标注，不摆占位）、DEC-028（判定在后端、`null` 不当 0）、
  QUOT-286（实现单）、QUOT-150 / QUOT-280（R12 对表与合并后复核）、
  `docs/specs/SPEC-INTRADAY-SNAPSHOT.md`（实现口径）、`docs/SOURCES.md` §2（数据源登记）

## 1. 背景

R12 对表（QUOT-150）第 6 项「盯盘·竞价与盘中」原判 **有意不做**，理由是「无 6 时点快照数据源」；
QUOT-280 合并后复核仍是「无数据源、无页面入口、需人类裁定」。这条在 `docs/decisions/` 里
**没有任何编号**，与 DEC-018 §R12「有意不做必须写理由 + 决策编号」的验收口径对不上——
它一直是 R1–R12 全量收口后唯一一条「代码已做、文档还挂着旧结论」的欠账。

随后参考仓库 `simonlin1212/vibe-astock`（`orchestrator/src/finance/shortline/intraday.ts`）的实测
推翻了「无源」结论：**6 个时点不需要新上游工具**，本机在当天当场定时采样、写成本地 JSON 即可，
上游只用已有的批量实时报价、昨日涨停池与情绪面。老板 2026-10-31 10:39 提出该缺口后，
QUOT-286 按此实现并合入 `main`（`42d923d`，PR #18）；代码与 `SPEC-INTRADAY-SNAPSHOT` 都进了主线，
但决策记录没写。本决策补的是「为什么这么定」，不是复述规格——尤其是几处最容易被「好心」改回去的边界。

## 2. 决定

### 2.1 数据来源：只复用已登记的 peer 数据，不新增采集通道

| 用途 | 数据集 | 工具 / 来源 |
| --- | --- | --- |
| 此刻批量报价 | `spot_quote` | TickHub-CN `tencent_quote_batch`（单批上限 50，分批请求） |
| 昨日涨停股名单 | `yesterday_limit_up` | TickHub-CN（含昨日连板数 `y_streak`） |
| 情绪面 | `sentiment` / `limit_pool` / `broken_board` | TickHub-CN 既有工具 |

不新增直连行情站点、不加新采集入口（DEC-006）。9:15–9:20 的集合竞价过程（撤单 / 撮合明细）
上游无源，**不做、也不摆占位字段**——空字段会被读成「今天竞价没有数据」，比明说没有更糟。

### 2.2 6 个时点与窗口：09:25 截止 09:30，其余 +8 分钟

窗口按 `scheduler.timezone`（默认 `Asia/Shanghai`）的本地时间判定，左闭右开：

| 时点 | 有效窗口 | 读数 |
| --- | --- | --- |
| `09:25` | `09:25:00` – `09:29:59` | 开盘价（集合竞价成交价），涨跌幅按开盘价重算 |
| `09:35` | `09:35:00` – `09:42:59` | 最新价；附情绪面 |
| `10:00` | `10:00:00` – `10:07:59` | 最新价；附情绪面 |
| `11:30` | `11:30:00` – `11:37:59` | 最新价（上午收盘）；附情绪面 |
| `14:00` | `14:00:00` – `14:07:59` | 最新价；附情绪面 |
| `15:00` | `15:00:00` – `15:07:59` | 最新价（收盘）；附情绪面 |

- **09:25 的窗口硬截止在 09:30**：9:30 起进入连续竞价，集合竞价成交价已不再可得；晚取到的
  都是连续竞价的数，混进同一份快照就是另一种口径。因此 09:25 读 `open` 并把 `reading_field`
  记为 `open`，涨跌幅用 `(开盘价 − 昨收) / 昨收` **重算**——报价里的 `change_pct` 是最新价口径，
  9:25 时最新价尚未产生，直接用它等于拿一个不存在的数。
- **其余时点给 8 分钟余量**：调度 misfire、分批报价（每批 50 只）、Peer 重试都在窗口内消化。
  8 分钟仍不够说明链路已坏，此时宁可 `skipped` 也不要写一份成分不纯的快照。
- 窗口边界有单测锁死：`09:29:59` 收、`09:30:00` 拒（`test_slot_window_is_left_closed_right_open`
  / `test_auction_window_rejects_continuous_trading`）。

### 2.3 窗口外一律 `skipped`，绝不补跑；历史日绝不现抓

- 用今天（或任意一天）的价格给过去的日子写快照会**永久污染**数据，而且落盘之后与真实现场
  完全无法区分——文件里看不出异样（vibe-astock `intraday.ts:15-16` 的原话）。
- 因此 `run_slot()` 没有「补跑」入口：命令带日期也不补，窗口外 / 周末一律记 `skipped` 并写进
  `status.json`，让「这个时点为什么没有文件」可复盘。
- 非交易日：周末直接 `skipped`；**节假日没有单独日历**，靠行情行内时间戳
  （Tencent `timestamp` 的 `YYYYMMDDHHMMSS`）的日期必须等于今天来兜底——节假日腾讯会回
  节前收盘价，数字看着完全合理，只有日期能拆穿。一条 fresh 报价都没有 → `missing`，不写快照。

### 2.4 只创建不覆盖：`O_CREAT|O_EXCL`，已存在记 `already` 且不再请求 peer

- 快照是「现场取证」：同一时点只允许一份，覆盖等于篡改历史。
- 目标文件已存在 → 直接记 `already` 并返回，**连 peer 都不请求**：既避免重复外呼，也避免
  `status.json` 里出现一个「看着像刚抓的」时间。
- 写入用 `os.open(..., O_CREAT|O_EXCL)`（与 vibe-astock 的临时文件 + `linkSync` 等价）：
  两个并发进程也只有一个能写成功，另一个记 `already`。
- `status.json` 是唯一例外：每次原子替换（临时文件 + `os.replace`），因为它记录的是「调度发生了什么」，
  不是「盘面读数」；覆盖它不会污染数据，反而必须是最新状态。

### 2.5 `spot_quote` 登记进 `sources.yaml`，但不进 `ldst sync` 台账

- 实时报价**没有「一天一条」的交易日水位**。进 `sync.yaml` 会被 `ldst sync` 当成按日增量落库的
  数据集，而它每 5 分钟就该是不同的一份读数；硬套 `trade_date` 会让它看起来像可按日同步。
- 因此只登记在 `config/sources.yaml`（`via: tickhub`、`tool: tencent_quote_batch`、
  `timeout_sec: 10`、`retries: 2`、`cooldown_sec: 60`、`weight: 1.0`、`fallback: []`），
  链路走 `ProviderRegistry.fetch_live()`——只按 sources 组装，不读 sync 台账，主备同 peer 校验不变。
- `DATASET_CATALOG` 仍登记 `mode=replace`、`watermark_field=timestamp`，但那条水位只描述
  **单次响应内的行情时间**，不是交易日水位；快照的日期校验另走 `quote_day()`。
- 快照本身也不进 `ldst sync` 台账：机器存的盘面读数与用户自己记的台账分开
  （vibe-astock 的同一取舍——两件事混在一起，清台账时就会误删盘面证据）。
- 越界有测试兜底：`test_spot_quote_is_owned_by_tickhub_and_stays_out_of_the_ledger`、
  `test_spot_quote_rejects_cross_peer_source`。

### 2.6 09:25 用开盘价；09:35 起才附情绪面

- 09:25 只给行情读数，不给情绪面：竞价刚结束，涨停池 / 炸板 / 1进2 尚未稳定，此时算出来的比例
  下一分钟就会被自己推翻。
- 09:35 起附：涨停数、炸板数、跌停数、最高板、炸板率、1进2。
- 情绪面取不到**不阻断快照**：主读数（昨日涨停股此刻表现）成立，缺的是附加块 → 记 `degraded`，
  不是 `missing`。情绪面 / 池子的 `data_as_of` 必须等于今天，否则 `available=false` 并计降级。

### 2.7 昨日连板数读 `y_streak`，不读 `limit_up_days`

- `limit_up_days` 在该工具里**含今日封板**（是「截至今天」的连板数）。拿它给昨日涨停池分板位、
  算 1进2，会把昨日首板当成二板，**整体错一档**。
- 字段取值顺序固定为 `y_streak` → `yesterday_streak` → `yesterday_consecutive_boards`；
  上游一个都没给就记未知（`by_board` 进「未知」组），**不拿别的字段顶替**——与 DEC-028
  「`null` 不当 0」同一条原则：宁可少一组，不可错一档。

### 2.8 单点异常折成状态字段，不向上抛、不拖垮调度器

- `run_slot()` 只返回结果字段 `ok / degraded / already / skipped / missing / failed`，不抛异常；
  调度器 `run_job` 外面还有一层兜底。
- 理由：6 个时点是一条长跑链路，一个时点上游抖一下不能让调度器倒下，更不能让后面的时点全部不跑；
  失败也必须在 `status.json` 留下证据（`failed` + 一行原因，不带堆栈、不带绝对路径）。
- 单只股票取不到报价 ≠ 整只丢弃：记 `fresh=false` + `reason`（`no_quote` / `stale` / `no_reading`），
  聚合只用 fresh 行，避免用陈旧值把市场强弱算歪。
- `at_limit_up` 只在报价同时给出 `limit_up` 时才是布尔值，否则 `null`——不拿涨幅阈值猜封板。

## 3. 替代方案（及否决理由）

| 方案 | 否决理由 |
| --- | --- |
| 向上游申请「竞价快照工具」（QUOT-150 原判方向） | vibe-astock 已证明本机定时采样就够。为 6 个时点新增一个上游工具是把采集责任外推，且上游也不可能替我们「当天当场」存盘——补抓的责任最终仍在本机 |
| 用分钟 K 线回补 6 个时点 | 历史日现抓会污染（§2.3）；分钟线不是集合竞价成交价，09:25 尤其不可替代 |
| 每次触发覆盖写 | 同一时点会被重试 / 重启改写，「当时的读数」变成「最后一次成功抓到的读数」；文件本身还看不出被改过 |
| 把 `spot_quote` 放进 `sync.yaml` 按日增量 | 实时报价没有交易日水位（§2.5），会被 `ldst sync` 当成可按日增量同步的数据集 |
| 6 条独立 cron，不做窗口判定 | 机器休眠 / misfire 后的补触发无法区分「晚了几秒」与「晚了半小时」；现在由每个 tick 判断窗口，超窗即 `skipped` |
| 失败一直重试到成功 | 窗口内重试有意义（`retries: 2`）；窗口外重试就是补跑，直接违反 §2.3 |
| 快照走 MCP 信封 / 进 AI 上下文 | 盘面读数只在数据面消费；进信封会让 6 份快照变成对外通道的载荷，没有消费方需要 |

## 4. 明确不做

- 历史补跑（哪怕命令带 `--date`）：窗口外只记 `skipped`；
- 9:15–9:20 集合竞价过程：上游无源，不摆占位字段；
- 新增直连行情站点或采集通道（DEC-006）；
- 对个股排序 / 评分 / 推荐：快照只存客观读数；
- 前端「盯盘·竞价与盘中」入口：本决策只落数据面，页面另行立项（见 §5）。

## 5. 影响

- 新增：`src/ladderstock/core/intraday.py`（687 行）、`docs/specs/SPEC-INTRADAY-SNAPSHOT.md`、
  `tests/unit/core/test_intraday.py`、`tests/unit/test_schedule_snapshot.py`、
  `tests/fixtures/intraday_stubs.py`、`tests/fixtures/peer_responses/tencent_quote_batch.json`。
- 改动：`config/sources.yaml`（`spot_quote`）、`config/schedule.yaml`（6 条 `kind: snapshot`）、
  `config/schema/schedule.schema.json`（`kind` 枚举）、`core/boundary.py`（`DATASET_CATALOG`）、
  `data/provider_registry.py`（`fetch_live` / `source_chain`）、`cli/schedule.py`（kind 白名单 +
  `snapshot` 分支）、`docs/SOURCES.md` §2、`tests/contract/test_tickhub_payload_mapping.py`。
- 运维面：调度器需长驻（`ldst schedule`）。**机器休眠错过窗口 → 该时点 `skipped` 是设计行为，
  不是故障**；排查入口是 `data/intraday/<date>/status.json`，不是去补文件。
- 数据面：`data/intraday/<YYYY-MM-DD>/<HHMM>.json` + `status.json`；只创建不覆盖，不进 sync 台账。
- 文档面：R12 报告第 6 项「有意不做且缺决策编号」的欠账由本决策关闭；页面入口仍为
  「本轮不建入口」，如需上线另立前端单（不在本决策范围内）。
