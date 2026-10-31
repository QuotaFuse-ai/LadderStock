# DEC-031 市场屏两段数据源：交易日窗口取自本机缓存，题材强度只信 Wire，参数名两侧对齐

- 日期：2026-10-31
- 提出：后端架构师（QUOT-199 / Q172-8）
- 状态：生效（随 QUOT-199 实现落地）
- 关联：DEC-004（回环 5950，不自动换端口）、DEC-006（程序直出，AI 不参与数字）、
  DEC-007 §2.6（不新增采集入口）、DEC-008 §4（资讯只做叙事，不产数值指标）、
  DEC-015 Tier 1（回环独占闸门）、DEC-028（判定在后端、`null` 不当 0）、
  DEC-030（只读接口只做投影，不重算）、
  `SPEC-MARKET-SCREEN-API`（两接口契约）、`SPEC-LIMIT-POOL` / `SPEC-RADAR-PAGES`（同族只读接口）

## 1. 背景

「市场」屏（`/market`，Q172-7）要三段本机数据，其中两段现有后端给不出来：

- **CH-01 情绪温度折线**：`GET /api/sentiment` 只算单日（`compute_sentiment(day)` 返回一个
  `temperature`）。折线要的是「近 N 日序列 + 40–60 区间带 + 较昨日 +4 + 近 N 日分位」。
- **CH-03 题材轮动**：全库没有「题材强度」这件事。题材的唯一数据源是 QuotaFuse Wire 的
  `theme_news`（`core/boundary.py` 登记为可选消费），本机当前**没有接入**，也没有当日落盘。

同时，前端在 `frontend/src/api.ts` 已按 QUOT-198 上线了一份契约草案（字段名
`MarketHistoryPayload` / `MarketThemePayload`，请求参数 `?date=&window=`），并注明
「后端上线时以本文件为准对齐」；而 QUOT-199 的接口契约写的是 `?to=&days=`。两侧都以为
对方会改名字——这是接口最先出事的地方，必须在后端这一侧一次定死。

## 2. 决定

### 2.1 交易日窗口：只认本机缓存里有涨停池的日子，不猜节假日

窗口 = `to`（含）往前最近 `days` 个**本地已有涨停池分区**的日期。两级来源，顺序有意义：

1. `data/archive.py::has_partition("limit_pool", day)` 逐日回看（只做一次 `stat`，不读数据），
   跳过周末，回看步数上界 `2 × days + 30`（够跨周末与长假，也不做无界扫描）；
2. 一条分区都没有时，才退回 `core/validation.py::session_days()`（DuckDB 全表扫描）。

Parquet 日分区是 `archive.snapshot()` 的第一优先后端，所以它必须是窗口的第一优先来源：
反过来只信 DuckDB 会出现「`/api/sentiment` 有数、`/api/market/history` 空」的自相矛盾。

**被否方案**：按自然日/工作日硬算再逐日试。法定节假日不是交易日，硬算会把十一、五一整段
报成「缺数据」，等于用一个假日历去解释真数据——本机有没有那天的涨停池，本身就是最可信的
交易日历。代价是「缓存断一天」和「那天放假」在 `missing_days` 里长得一样，因此
`source_detail` 必须同时给出窗口覆盖（`limit_pool 缓存: M/N 个交易日（起~止）`），
让读的人自己判断是缺数据还是本来不开市。

### 2.2 单点不算趋势；缺的日如实列出来

只有 `compute_sentiment` 返回 `available=true` 且温度可解析为数的日子才成点。
**可用点少于 2 个 → `available=false`**：单点画不出折线，也就谈不上「较昨日」和「近 N 日分位」，
这两个字段此时是 `null` 而不是 0（DEC-028）。缺的日子进 `missing_days`，并在
`source_detail` 与 `payload.reason` 里点名。

`quantile` 的口径钉在这里，因为它前端只看到数：**窗口内**温度 ≤ 最后一个可用日的比例 × 100，
保留 1 位小数；`delta_vs_prev` = 最后一个可用日 − 倒数第二个可用日。两者都以**可用点**为样本，
不是以日历日为样本——放假的日子不参与分位，否则分位会被空日拉偏。

本接口**不重算**任何分项：数字与 `compute_sentiment` 同源同值（测试逐点比对），
只做「取窗口 + 排序 + 两个派生指标」。同 DEC-030 的「HTTP 层只投影」。

### 2.3 名字冲突：本名 `to` / `days`，别名 `date` / `window`

`GET /api/market/history` 同时接受 `to|date`（窗口末日）与 `days|window`（天数），
两个本名各给一个别名，语义完全相同；**同名冲突时本名优先**。理由：

- 前端已经按 `?date=&window=` 上线（`api.ts:748`），别名让它**今天就能点亮**，
  不需要一轮「后端上线 → 前端改名 → 再验收」的往返；
- 契约文档（QUOT-199）与 MCP 侧说的是 `to` / `days`，本名不能被别名顶掉；
- 别名只有两个参数、一条优先级规则，不是一套兼容层，代价可控。

payload 里同时给 `date` / `available` / `reason` / `window`（前端在读）与
`points` / `missing_days` / `quantile` / `delta_vs_prev`（契约在读），顶层再补一个
`available`（`LadderEnvelope` 是 `extra="forbid"`，没有「有没有数」这个字段，
既有接口靠 `payload.data_source_status` 表达；市场屏的空态判定要直接读 `available`）。
**两个 `available` 同值**，前端任选——但这也意味着它们是同一个结论的两份写法，
将来若要收敛，先收前端再收后端，不要只留一处。

题材侧的已知差异：前端类型 `MarketThemeRow.strength: number`（非空），后端在 Wire 没给
强度时如实输出 `null`。前端已用 `isFiniteNumber(row.strength)` 过滤（`MarketScreen.tsx:385`），
所以**不需要改前端就能正确表现**；但类型应改成 `number | null` 才如实——这属于前端目录，
本决策只记录，不代改。

### 2.4 题材强度只信 `theme_news`，取不到就是 `null`

题材行的五个字段（`theme` / `strength` / `limit_up_count` / `leader` / `delta`）全部来自
记录本身，按别名表容忍取值（Wire 的摘要工具没有固定 schema）；取不到一律 `null`。

**被否方案**（都试过一轮再否）：

- 用 `industry_board` / `concept_board` 的 `change_pct` 折算「题材强度」：口径完全不同
  （板块涨跌幅 ≠ 题材强度），且会把 DEC-008 §4「资讯只做叙事，不产数值指标」变成
  「后端替资讯编数值」。
- 用当日 `theme_news` 的条数当热度：行数只反映 Wire 那天抓了几条，不反映市场。
  宁可让前端画不出柱状条，也不给一个编出来的数。

同一题材多条记录：`strength` / `limit_up_count` 取**最大**，`leader` / `delta` 取第一条
能取到的。排序按 `strength` 降序，**无强度的排最后**，最多 20 条。

资讯正文与链接（`summary` / `content` / `title` / `url` / 正文 / 摘要 / 链接 …）
**一律不进响应**：投影层只拼白名单，再加一遍递归剔除兜底（同 DEC-015 §3.4 的做法），
最后 `strip_private()` 恒等由测试钉住。

### 2.5 闸门：复用 `_journal_guard`，`no-store`，不进信封之外的出口

两个接口都挂 `/api/journal/stats` 那道回环闸门（`is_loopback_host` + `host_header_allowed`），
非回环或 `Host` 不在白名单 → 403；`LDST_CONTROL_ENABLED=0` 不影响它们（只读，属数据面）；
只注册 `GET`，其它方法 405；响应带 `Cache-Control: no-store`；**不触发 `sync` / `reconcile`**，
无副作用。

为什么公开行情的聚合也要回环独占：单日涨停数是公开数据，但「近 60 个交易日的情绪序列 +
题材轮动」合起来就是这台机器在看什么、看到什么程度。同 DEC-015 的判据——
**Token 管「谁连得进来」，不管「数据该不该出去」**。

## 3. 影响

- 新增：`GET /api/market/history`、`GET /api/market/theme`（`web/server.py`，落在同一模块的
  只读 API 段，紧跟 `/api/journal/stats`，注册在 SPA 兜底之前）。
- 未改：`web/control.py`（QUOT-197 正在改，只 import 它的 helper）、
  `core/sentiment.py`、`core/validation.py`、`data/archive.py`、`frontend/**`。
- 上线即为的状态：CH-01 取决于本机涨停池缓存宽度（缓存几天就几天，最多 60）；
  CH-03 恒为 `available=false`，直到有人接入 Wire（配置动作，需人类批准，不在本单范围）。
- 验证：`tests/unit/web/test_market_history_endpoint.py`、
  `tests/unit/web/test_market_theme_endpoint.py`（45 例，含空态结构、`days` 钳制、
  非法日期 400、非回环 403、别名等价、`strip_private` 恒等、正文/链接不出境）。
