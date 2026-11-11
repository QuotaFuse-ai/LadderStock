# HOME-V2-DATA-SURFACE：首页 v2 数据面盘点（QUOT-379 S1）

- 日期：2026-11-03
- 作者：后端架构师
- 状态：**已采纳**（项目领导人 2026-11-03 拍板，落点 QUOT-380 / DEC-052）
- 变更记录（2026-11-04，QUOT-380 落地时）：
  1. **编号**：本文与实现里的 `DEC-049` 一律改为 **`DEC-052`**——草稿在 QUOT-379 工作目录里
     未合并，落地时 `git ls-tree origin/main` 扫到 main 上 `DEC-049` 已有两份实体
     （复盘拆屏 / 题材归属，且被 UI-SPEC 大量引用），按 DEC-045 §2/§3 由零反向引用的一方让号。
  2. **指数四格定档**：`sh000001` 上证 / `sh000300` 沪深300 / `sz399001` 深证成指 /
     `sz399006` 创业板指（本文 §2.1 的实测四条）。
     ⚠️ QUOT-380 派单消息里把四格写成「上证/深成/创业板/**科创50**」，与本文 §2.1、
     任务书正文「已拍板口径 3」、`INDEX_WHITELIST` 三处都不一致，**按本文与任务书实现**；
     `sh000688` 科创50 若要顶替 `sh000300` 沪深300，需回到本单重拍。
  3. **§2.1 那条「裸 `000001` → `isError`」的记录已过时**：2026-11-04 对真实 peer 复测，
     裸 `000001` 与点号 `000001.SH` 现在都**静默回平安银行**且照旧标 `is_index: true`
     ——比报错更危险。白名单只允许 `sh`/`sz` 前缀形式这条结论**不变，且更硬**。
  4. **B-3 选了方案 (a)**：当日盈亏 = `daily_k` 的 `close(T) − close(T−1)` 自算，
     **不改持仓现价口径**（`price_source` 仍是 `daily_k`、仍只读不联网）；停牌日显 `missing`，
     不退回到更早交易日。C-3 币种确认砍掉（只做 `currency: "CNY"` 单一汇总）。
  5. **B-1 按建议落地**：`index_quote` 的 `watermark_field=timestamp`，**不进 `config/sync.yaml`**。
  6. §2.3 那条「持仓现价走 `spot_quote` → `ProviderRegistry.fetch_live()`」的任务书误记已在
     原表中纠正，实现按纠正后的口径走。
  7. **（2026-11-11，QUOT-560 / DEC-081）§3 C-1「恒指 / 恒生科技无源，建议直接砍港股两格」
     已被推翻，不再有效**。2026-11-03 实测的「`HSI.HK` / `HSTECH.HK` 一律 `isError`」
     不是生态无源，而是 TickHub-US 港股支路只按数字个股代码解析的缺口：腾讯
     `r_hkHSI` / 新浪 `rt_hkHSI` 对港股指数免费公开、零 key 回完整行情。QUOT-560 在
     peer 侧补别名表（`HSI` / `HSTECH` → `HSI.HK` / `HSTECH.HK`），app 侧新增
     `index_quote_hk` 数据集逐 symbol 取数，首页港股两格已接真实读数（Tier C 非商用，
     `commercial_use=False`）。本文 §2.1 第 7/8 行、§3 C-1、§4 工作量表的「砍掉」
     结论均按历史快照保留，现行口径以 DEC-081 与 SPEC-HOME-V2 v1.5 为准。
     另：§3 C-2「美股两格砍掉」此前已被 QUOT-471 推翻（适配器 + 合规字段已落地），
     同属历史快照。
- 关联：QUOT-377（父任务）/ QUOT-379（本任务）/ DEC-033（持仓读写面）/ DEC-015（隐私库不进信封）
  / `docs/SOURCES.md` §2.1、§2.3 / `scripts/check_privacy.py` 第 3 组 / 本目录
  `DEC-052-watchlist-read-endpoint.md`（**草稿，未生效**）
- 范围：只盘点数据面与边界，不含实现。所有编号按 2026-11-03 checkout 分支
  `agent/agent/bb41d7700c64` 读取；peer 实测全部只读，未改任何 peer 配置、未装新依赖。

## 1. 结论摘要（一段话）

四个块里，**提问面板零数据依赖**、**我的持仓 3/4 字段现成**、**我的自选有表没读路径**、
**指数条只有一半**：A 股 4 个指数实测能取到点位与涨跌幅，但数据集**一条都没登记**，
要新增 `index_quote` 数据集；美股 2 个有点位但 `tickhub_us` 在本仓**根本没有取数适配器**，
恒指 / 恒生科技**无源**。本版本建议按「8 格改 4 格」落地。

## 2. 参考页四块 × 字段 × 来源 × 现状

### 2.1 大盘与全球指数条（参考页要 8 个指数）

| # | 指数 | 期望来源 peer / 工具 | 实测入参 | 实测结果 | 落点字段 | 现状 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 上证指数 | `tickhub` / `index_quote` | `{"codes":"sh000001"}` | 3608.10 / −0.82% | `price` / `change_pct` / `prev_close` | 有源，**未登记** |
| 2 | 沪深300 | `tickhub` / `index_quote` | `{"codes":"sh000300"}` | 4008.87 / −0.20% | 同上 | 有源，**未登记** |
| 3 | 深证成指 | `tickhub` / `index_quote` | `{"codes":"sz399001"}` | 10876.99 / −1.21% | 同上 | 有源，**未登记** |
| 4 | 创业板指 | `tickhub` / `index_quote` | `{"codes":"sz399006"}` | 2391.65 / −2.73% | 同上 | 有源，**未登记** |
| 5 | 道琼斯 | `tickhub_us` / `th_us_get_quote` | `{"symbol":"DJI","commercial_use":false}` | 51983.98 / **0.00%** | `payload.price` / `payload.change_pct` | 有点位；`source=sina` `compliance_tier=C`，**且取数管道不存在**（见 C-2） |
| 6 | 纳指 | `tickhub_us` / `th_us_get_quote` | `{"symbol":"IXIC","commercial_use":false}` | 25164.36 / **0.00%** | 同上 | 同上 |
| 7 | 恒指 | — | `hkHSI` / `hsi` / `DJI,IXIC` / `HSI.HK` / `HSTECH.HK` / `^DJI` | **全部 `isError=true`** | — | **无源** |
| 8 | 恒生科技 | — | 同上 | **无源** | — | **无源** |

实测快照（2026-11-03，北京时间约 22:15–22:35）：

- 4 个 A 股指数一次请求拿全：
  `index_quote {"codes":"sh000001,sh000300,sz399001,sz399006"}` → `data` 4 行，`meta.count=4`、
  `meta.source=tencent`、`meta.confidence=A`、`is_stale=false`。
- 行字段 28 个：`code name price prev_close open high low amount volume change change_pct
  amplitude turnover_rate pe pb float_market_cap total_market_cap limit_up limit_down bid1
  ask1 … timestamp market is_stale is_index`。**首页三件套（点位 / 涨跌额 / 涨跌幅）齐备，
  不需要换算**。
- 美股：`th_us_get_quote DJI` → `payload.price=51983.9805`、`payload.prev_close` **等于** `price`、
  `change=0.0` `change_pct=0.0`、`currency=USD`、`source=sina`、`compliance_tier=C`。
  盘后时段拿不到当日涨跌——这是上游口径，不是我们算错。`IXIC` 同形（25164.3631 / 0.00%）。
- 港股与恒生科技：`index_quote` 只覆盖 SH/SZ/BSE，`th_us_get_quote` 对
  `HSI.HK` / `HSTECH.HK` 一律 `isError`（`invalid literal for int() with base 10: 'HSI'`——
  港股支路按数字代码解析，指数名不是数字）。`th_us_get_market_data HSI` →
  `source=yahoo compliance_tier=D payload=[]` 空数组，K 线也没有。
- **`index_list` 不能当指数注册表用**（实测 `{"scope":"all"}`）：回的是
  `meta.source=eastmoney`、**按 `change_pct` 降序的 100 条榜**，不含 `000001` / `000300` /
  `399001` / `399006` 这四个首页要的指数，却混进 `400224 苏阳光5` 这类老三板个股
  （`400224 / 0.15 元`）。恒指 / 道指 / 纳指 / 标普命中数 **0**。
  **所以指数白名单必须由 LadderStock 侧写死（`sh000001` 等 4 条），不能向 peer 动态要清单**——
  榜单日单一，首页那 4 格会今天有、明天没。
- **`senate` 那条登记的工具名已修正**（QUOT-389）：实测 senate 共 5 个工具，
  `report_query` 不存在，实名为 `stock_senate_report_query`；`boundary.py` 已改名，
  `PROVISIONAL_TOOLS` 随之清空。**另有一处未修**：`fund_flow_realtime` 登记的
  `stock_fund_flow_batch` 也不在 tickhub 工具表里（最接近的 `realtime_fund_flow`
  是逐只 `code` 入参，不能按批替换），需单独定口径。
- `tickhub` peer 共 149 个工具，含 `index_quote` / `index_kline` / `index_list` / `index_weights` /
  `index_constituents` / `index_valuation`；`tickhub_us` 41 个（全 `th_us_*`）；`wire` 4 个；`senate` 5 个。
  **`wire` 与 `senate` 全无指数行情能力**。

### 2.2 我的自选

| 字段 | 需要的来源 | 现状 |
| --- | --- | --- |
| 自选代码列表 | 本机 `journal.sqlite` 表 `watchlist`（`data/sqlite_store.py:221`） | 表存在，**没有任何 HTTP 读路径**，全仓唯一读函数是 `cli/sync.py:182 watchlist_codes()`，只取 `code` 一列 |
| 现价 / 涨跌 | `tickhub` / `tencent_quote_batch`（已登记为 `spot_quote`） | 有源已登记，`core/intraday.py:466` 已在用 |
| 名称 / 币种 | — | `watchlist` 表只有 `code note created_at`，名称要靠 `stock_info`，**且没有币种列** |

### 2.3 我的持仓（`GET /api/positions`，DEC-033 已交付）

实测该接口本机返回（`position_count=0`，空态 200）顶层键：
`local_only store data_source_status as_of price_source position_count valuation realized items
concentration sector_distribution price_lookup`。

| 参考页卡片字段 | 现有返回 | 判定 |
| --- | --- | --- |
| 市值 | `valuation.market_value` | **够** |
| 持仓盈亏 | `valuation.unrealized_pnl` | **够** |
| 收益率 | `valuation.unrealized_pnl_pct` | **够** |
| 当日盈亏 | 无此字段 | **缺**，要新算 |
| 币种汇总 | 无 | **缺**，见下方 A-4 |

**必须纠正任务书的一处事实**：任务书写「现价走 `spot_quote` → `tencent_quote_batch`
（`ProviderRegistry.fetch_live()`）」——持仓卡片的现价**不是**这条链路。
`core/quotes.py:25` 写死 `PRICE_DATASET = "daily_k"`，返回里 `price_source: "daily_k"`，
文件头注释是「**只读、不联网**：缓存里没有就是没有」（`core/quotes.py:9`）。
`fetch_live()` 确实存在（`data/provider_registry.py:933`），但全仓唯一调用方是
`core/intraday.py:466` 的 `spot_quote`，服务于盘中 6 时点快照，与持仓卡片无关。

影响：持仓现价的新鲜度取决于 `daily_k` 上次同步日，**不是**实时报价。首页若把
「我的持仓」做成强实时卡片，要么接受这个水位并如实显示 `as_of`，要么新开一条
`spot_quote` 路径——后者是口径变更，得单独决定，不在本任务的盘点结论里默认。

「当日盈亏 =（现价 − 昨收）× 股数」的**昨收**从哪来：
`daily_k` 行字段实测只有 `date open close high low volume amount`，**没有 `prev_close`**。
所以「T 日 close − T−1 日 close」必须由 LadderStock 自己按两个交易日算（缓存里有前一天的行就行），
或改走 `tencent_quote_batch`（该工具**有** `prev_close`）。两种都可落地，本任务只登记差异。

### 2.4 提问面板「今天，想研究什么？」

纯前端 + 已有 `/api/research` 族，**本盘点无新增数据依赖**。

## 3. 三档结论

### A. 这一版能直接做的（不需要新决策，只需要写代码）

- **A-1 指数条 A 股 4 个 + `GET /api/indices`**：`index_quote` 已在 peer 上（149 个工具里就有），
  但**未在 `DATASET_CATALOG` 登记**——这是唯一硬缺口。登记之后 `GET /api/indices` 就是
  照 `core/intraday.py:466` 抄一遍的事，**约 6h**。
  ⚠️ **代码前缀是实测出来的坑，两个工具行为不一样，别混用**：
  - `index_quote {"codes":"000001"}` → `isError=true`；**必须带交易所前缀** `sh000001`。
  - `tencent_quote_batch {"codes":"sh000001,sh000300,sz399001,sz399006"}` →
    `succeeded=3`（不是 4！），`quotes` 里 **`000001` 映射成了「平安银行」而不是「上证指数」**——
    批量报价工具会把 `sh`/`sz` 前缀剥掉再按 6 位代码回键，指数与个股代码撞车时**静默给错票**。
    所以指数条**绝不能图省事复用 `spot_quote` / `tencent_quote_batch`**，必须走 `index_quote`，
    而且返回行要按 `(market, code)` 匹配、不能按 `code` 建字典。
    入参形状差异要在 `data/adapters/tickhub.py` 里单独处理。
- **A-2 `GET /api/watchlist` 只读**：表已有、只读、回环独占，照 DEC-033 口径开。
  **前提：DEC-052 被批准**（见 §5）。**约 4h**。
- **A-3 自选现价**：`spot_quote` 已登记（`boundary.py:326`），直接复用。**约 2h**。
- **A-4 我的持仓卡**：市值 / 持仓盈亏 / 收益率三格**字段齐了，不要重复实现**
  （DEC-030 纪律：接口只做投影，不重算）。**约 1h**。

### B. 要先登记数据集 / 要先做决定才能做的

- **B-1 `index_quote` 数据集登记**：`boundary.py` 加 `_b("index_quote", "tickhub", "index_quote",
  "replace", "trade_date")` + `sources.yaml` 加 primary 记录 + `config/sync.yaml` 决定要不要日更。
  门禁是 `core/boundary.py:owner_of()`——**没登记直接抛 `BoundaryViolation`**，写代码绕不过去。
  ⚠️ 实测 `index_quote` 的行**没有 `trade_date` 字段**（行情时间是 `timestamp=YYYYMMDDHHMMSS`，
  与 `spot_quote` 同形）。`spot_quote` 在 `boundary.py:326-327` 已经踩过这个坑并留了注释：
  硬套 `trade_date` 会让实时报价看起来像可按日增量同步的数据集，所以它的 `watermark_field`
  写的是 `timestamp` 且**不进 `sync.yaml`**。**建议 `index_quote` 照 `spot_quote` 同构处理**，
  不要进日更台账。
- **B-2 自选的写面**：`watchlist` 表只有 `code note created_at`，参考页要「带现价/涨跌」，
  读面够用；但**加删自选**要不要也走 HTTP？本任务建议**不走**（沿用 DEC-033 的思路：
  写面单独一次决策，不与读面捆绑），首页只做只读展示 + 空态文案指向 CLI。
- **B-3 持仓「当日盈亏」**：两个方案——(a) 从 `daily_k` 缓存自算 close(T) − close(T−1)；
  (b) 改走 `spot_quote` 拿 `prev_close`。(a) 不动接口口径但停牌日要额外处理；
  (b) 更新但引入「持仓现价从缓存改成实时」的口径变更（见 §2.3）。**需要项目领导人选一个**。
- **B-4 币种汇总**：`watchlist` 与持仓都没有币种列，参考页那张卡的「币种汇总的市值」
  要新加数据模型（代码 → 市场 → 币种 → 汇率），**本版本建议砍掉**，只做人民币单一汇总 + 空态说明。

### C. 无源，必须砍掉或降级

- **C-1 恒指 / 恒生科技：无源**。实测 `tickhub index_quote` 只覆盖 SH/SZ/BSE，
  `tickhub_us` 对 `HSI` / `HSTECH` 的各种写法全部报错。**不许自采**（DEC-006 闸门 4），
  所以首页只能：6 格指数条，或 A 股 4 + 美股 2 = 6 格。**建议直接砍港股两格**。
- **C-2 道琼斯 / 纳指：点位有，但取数管道根本不存在，本版本砍掉。**
  1. **硬阻断：`tickhub_us` 没有适配器分支。** `data/provider_registry.py:1176-1191` 的
     `_to_envelope()` 只认 `via == "tickhub" / "wire" / "senate"`，其余
     `raise SourceUnavailable(f"未知 peer：{spec.via}")`；`data/adapters/` 下也只有
     `tickhub.py` / `wire.py` / `senate.py` 三个文件。**结果：现在在 `sources.yaml` 里加一条
     `via: tickhub_us` 的记录，取数必然抛错**——`tickhub_us` 只是在 `core/boundary.py:64`
     的 `PEERS` 与 `config/sources.yaml` 里登记了地址，数据层从没接过它。
     要上美股指数，得先写第 4 个适配器 + 一条 dispatch 分支，这已经超出「首页改版」的范围。
  2. **涨跌永远是 0.00%**：`prev_close == price`（实测 `DJI` 与 `IXIC` 都如此），
     首页显示 `51983.98 +0.00%` 比不显示更糟，它看起来像数据坏了。
     ⚠️ 这条理由**待复核**：实测时段 eastmoney 支路 HTTP 502，点位是 sina 兜底给的；
     502 恢复后可能就不是 0。见 §6 假设 2。
  3. **合规口径由上游声明、我们不受约束**：回包带
     `warnings: ["新浪财经 属 Tier C：商用需事先书面许可，当前为非商用调用。"]`，
     `compliance_tier: "C"`。而本仓的 `tickhub` 适配器只把 peer 的 `meta.confidence`
     映射成 `confidence`（`data/adapters/tickhub.py:41` `CONFIDENCE_MAP = {"A":"high","B":"medium","C":"low"}`），
     **`compliance_tier` 与 `warnings` 一个字段都没有落地**。也就是说：一旦接入 `tickhub_us`，
     「非商用」这个前提是靠约定维持的，代码里没有任何地方阻止它被当成商用数据用。
     另外该工具必填 `commercial_use` 布尔——这个参数怎么填是一个**产品对外承诺**问题，
     不是后端能自己定的。
  **建议：本版本砍掉美股两格。** 要做就单独立项：写适配器 + 决定 `commercial_use` 口径 +
  把 `compliance_tier` / `warnings` 落进信封。
- **C-3 币种汇总**（同 B-4）：砍。

## 4. 工作量汇总

| 项 | 档 | 估计 |
| --- | --- | --- |
| A-1 指数条（A 股 4 格）+ `GET /api/indices` | 直接做 | 6h |
| B-1 `index_quote` 数据集登记 | 先登记 | 2h |
| A-2 `GET /api/watchlist`（依赖 DEC-052 批准） | 先决策 | 4h |
| A-3 自选现价 | 直接做 | 2h |
| A-4 持仓卡三格 | 直接做 | 1h |
| B-3 当日盈亏 | 先选方案 | 3h |
| **S2 合计（A 股 4 格，不含当日盈亏）** | | **约 13h** |
| **S2 合计（含 B-1 登记 + B-3 当日盈亏）** | | **约 18h** |
| C-1 港股两格 | 无源，砍掉 | 0h |
| C-2 美股两格 | 建议砍掉；若要做需先写 `tickhub_us` 适配器 | 单独 +10h（不在本改版范围） |
| C-3 币种汇总 | 砍掉 | 0h |

## 5. 与既有纪律的关系（本任务核心产出之一）

- **UI-SPEC §0** 把「交易日志 / 持仓 / 自选的网络读写」登记为**不覆盖**，
  `web/server.py:11-12` 也是同一句话。DEC-033 已经为持仓开过一次同样的口子
  （`GET /api/positions` + 全 POST 写面），本次是**同口径的第二次**。
- **不冲突的判据**：DEC-015 那条纪律约束的是隐私库**不进信封、不进 MCP、不进 AI**
  这三条出口。`GET /api/positions` 的做法是回环独占 + `Host` 白名单 + **平面 JSON**，
  因此天然到不了那三条出口；`GET /api/watchlist` 照抄即可。
- **⚠️ 会被闸门拦下的地方（必须先改再写代码）**：`scripts/check_privacy.py` 第 3 组
  `JOURNAL_IMPORT_ALLOWLIST`（`165-181` 行）目前是**逐文件 + 逐模块**的封闭清单，
  只有 `web/server.py → journal.stats` 和 `web/radar.py → journal.position` 两条。
  新端点若 `from ladderstock.journal.… import` 取自选，**判 FAIL**。
  落地时要按 DEC-033 的先例加第三条授权例外，且必须同时具备回环闸门 + `Host` 白名单 +
  `no-store`，并在 `_journal_rule_selftest()`（`219-283` 行）里补一条反例——
  这条规则的自检明确要求「白名单不能变成后门」。
- **`dataset` 名字不能叫 `watchlist`**：`core/privacy.py:23-31` 把 `watchlist` 和 `自选`
  都列进了 `PRIVATE_KEYS`，出口会被静默剥字段。新端点的 envelope key / 字段名要避开这两个词。

## 6. 未知与假设

1. 实测时段是盘后（北京时间 22:15–22:35），A 股数值是当日收盘价，`is_stale=false`。
   盘中行为未测。
2. `tickhub_us` peer 上 `push2.eastmoney.com` 当时 **HTTP 502**，美股点位来自 `sina` 兜底。
   502 恢复后 `DJI` 可能改由 eastmoney 提供、**涨跌可能就不是 0**——C-2 的第 1 条理由
   在 eastmoney 可用时可能不成立，需要在盘中/盘后各复测一次再定。
3. 本机 `journal.sqlite` 没有持仓数据（`position_count=0`），持仓字段清单来自
   `SPEC-RADAR-PAGES.md` §6.1 的契约 + 顶层键实测，未逐字段验证空值形态。
4. `index_quote` 是否需要在 `sync.yaml` 里日更，取决于首页要不要「非交易时段也显示上一日收盘」——
   产品问题，不是数据问题。本盘点默认 B-1 的「不进日更台账」建议。
