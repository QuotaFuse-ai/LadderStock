# SPEC-INDUSTRY-METRICS 行业五维画像口径

- 日期：2026-10-31
- 归属：数据面 R5（QUOT-143）
- 实现：`src/ladderstock/industry/catalog.py`（清单加载与校验）、
  `src/ladderstock/industry/metrics.py`（公式层，纯函数）、
  `src/ladderstock/industry/snapshot.py`（装配层，只读缓存回放）、
  `src/ladderstock/cli/industry.py`（`ldst industry list|show`）、
  `src/ladderstock/mcp/server.py::_tool_industry_snapshot`（MCP `industry_snapshot`）
- 配置：`config/industry.yaml`（行业清单 + 窗口参数，**数据**不是口径）
- 夹具：`tests/fixtures/industry/I001…I005.json` + `tests/unit/industry/`
- 相关：`docs/specs/METRICS.md`（既有指标口径，本文**不修改**任何一条）、
  `docs/specs/SPEC-RESEARCH-PROVENANCE.md`（§10 的溯源形状即 R3）、
  `docs/specs/SPEC-CANDIDATE-POOL.md`（同一套信封装配与 DEC-012 退出码习惯）、
  `DEC-006`（生态边界，不采集）、`DEC-012`（`available=false` → 退出码 6）、
  `DEC-018`（申万 / 板块两套分类不混算）、`D-07`（口径只有一份）

## 0. 一句话

一个申万一级行业 = 五个维度（景气 / 资金 / 估值 / 动量 / 拥挤度）的**可解释画像**，
全部由本机缓存里的既有数据集按固定公式聚合而来：**算得出就给值，算不出就给 `null` + 原因**。
不估算、不插值、不用模型补，也不拿部分样本冒充全样本。

## 1. 输入与口径边界

### 1.1 六个数据集 + 一份配置（全部只读，零外呼）

| 输入 | 归属数据集 | 上游工具（生态 peer） | 取用字段 | 用途 | 缓存为空时 |
|---|---|---|---|---|---|
| 申万一级归属 | `sw_industry_map` | TickHub-CN `sw_industry_history` | `code`、`l1_code`、`trade_date`（= 分类计入日 `start_date`） | 成分集合（五维的共同分母） | 五维全 `null`，`membership.reason` 指向该数据集 |
| 财务指标 | `finance_indicators` | TickHub-CN `eastmoney_finance_indicators` | `revenue_yoy`、`profit_yoy`、`roe`、`trade_date`（= `report_date`） | 景气 | 景气 `null` + `NO_FINANCE_CACHE` |
| 估值历史 | `valuation_history` | TickHub-CN `baostock_valuation_history` | `pe_ttm`、`pb`、`turnover_rate`、`trade_date` | 估值 + 拥挤度（换手） | 估值 / 换手段 `null` |
| 个股资金流 | `fund_flow` | TickHub-CN `stock_fund_flow` | `main_net`、`trade_date` | 资金（成分口径）+ 拥挤度（集中度） | 两路口径分别 `null` |
| 板块资金流 | `industry_fund_flow` | TickHub-CN `board_fund_flow`（`board_type=industry`） | `board_name`/`name`、`main_net_inflow`/`main_net`、`main_net_ratio`/`main_net_pct`、`change_pct`、`_source` | 资金（板块口径段，独立标注） | 板块段 `null` + `NO_BOARD_CACHE` |
| 日 K | `daily_k` | TickHub-CN `tencent_kline` / `tdx_daily_package` | `close`、`amount`、`trade_date` | 动量 | 动量 `null` + `NO_KLINE_CACHE` |
| 行业清单 | `config/industry.yaml` | — | `name`、`l1_code`、`anchors`、`board_name`、`enabled`、`window` | 行业集合 + 窗口参数 | **退出码 3**（清单配错时给出的快照比一片 `null` 更危险） |

三条硬约束：

1. **不新增采集入口**（DEC-006）：读取路径与 `core/pool.py` 完全一致，走
   `data/archive.py`（Parquet 优先 → DuckDB 兜底），本模块不 import `data/providers` /
   `httpx` / `sqlite3`，单测用 `provider_registry.build_transport` 直接抛异常来证明零外呼。
2. **不读隐私库**：交易日志与持仓（`journal.sqlite`）不出现在任何字段里，
   护栏见 `tests/contract/test_mcp_tools.py` 的 `FORBIDDEN_KEYWORDS` 断言。
3. **不改既有口径**（D-07）：本文只**新增**行业维度的字段。`METRICS.md` 里的
   情绪 / 天梯 / 候选池口径一字未动，成交额占比这类「分母」问题按本地缓存代码池单独标注（§5）。

### 1.2 没有 `--date`：口径是「本机缓存里的最新一批」

行业快照**不接受请求日期**（CLI 与 MCP 都没有 `--date` / `date` 入参）。理由：

- 五个维度读的是各自数据集的**全量可用历史**，每个维度自己在输出里给出它属于哪一天
  （`momentum.trade_date`、`capital.board.trade_date`、`valuation.window_days_used`、
  `membership.as_of`）；
- 拿请求日期冒充数据日期会**改口径**——那等于要求「按日重放」，而行业归属、报表期、
  估值窗口三种时间轴本来就不对齐；
- 信封的 `data_as_of` 取 `sw_industry_map` 的最新日期（`META_PRIMARY_DATASET`，
  CLI 与 MCP 共用同一个常量），归属数据一条都没有时如实给 `null`。

契约测试反向钉住这一点：`tests/contract/test_mcp_tools.py::test_industry_scoped_tools_require_industry_and_carry_no_date`。

### 1.3 全层通用的取值规则

**字段容忍表**（按顺序取**第一个非空值**，都没有就是 `null`；这是上游命名不统一的现实，
不是兜底猜测）：

| 语义 | 容忍键 |
|---|---|
| 代码 | 复用 `core/ladder.py` 的 `CODE_KEYS` |
| 交易日 | `trade_date`、`date`（落库时由 `adapters/tickhub.py` 统一成 `trade_date`） |
| 营收同比 | `revenue_yoy`、`revenue_growth_yoy`、`营收同比` |
| 净利润同比 | `profit_yoy`、`net_profit_yoy`、`净利润同比` |
| ROE | `roe`、`roe_weighted`、`净资产收益率` |
| 主力净流入 | `main_net`、`main_net_inflow`、`主力净流入` |
| 主力净流入占比 | `main_net_pct`、`main_net_ratio`、`主力净流入占比` |
| 涨跌幅 | `change_pct`、`pct_chg`、`涨跌幅` |
| PE / PB / 换手 | `pe_ttm`+`pe` / `pb` / `turnover_rate`+`turnover`+`换手率` |
| 成交额 / 收盘 | `amount`+`成交额` / `close`+`收盘` |
| 板块名 / 板块代码 | `board_name`+`name`+`名称` / `board_code`+`code` |

**数值与取整**（`industry/metrics.py`）：

1. 非数一律剔除：`None`、`bool`、`NaN`、±`inf`、不可解析字符串都不算数（`"3.21%"` 容忍 `%`）；
2. **空集回 `None`，不回 0**——0 是一个数值，`None` 才是「没有这个数」；
3. **任一分量缺失 → 整条指标 `None`**（`sum_values` / `mean` / `ratio_share` / 分位类），
   分母 ≤ 0 同样给 `None`（不除零、不取绝对值）；
4. **取整只在最后一步**，且是十进制四舍五入（远离 0：`0.125 → 0.13`、`-2.5 → -3.0`），
   不是 Python 内置 `round()` 的银行家舍入；
5. 保留位数：比率与分位 **4 位**，百分数与涨跌幅 **2 位**，金额 **2 位**。

**中位数**：升序后取中间，偶数个取中间两数的算术平均（`[12.5, 8.5] → 10.5`）。

**经验分位**（不做插值，这是任务硬约束）：
`窗口内每日序列中 <= 当前值的天数 / 窗口天数`，值域 `(0, 1]`。含 `<=` 意味着
「序列全是同一个值」时分位是 1.0，「当前值最小」时是 `1/窗口天数`——**两点的序列
里最小值给 0.5，不因为「左边没有值」而给 0**。

**覆盖度门槛**（资金维度，§3）：`代码 × 交易日` 的行必须齐，缺一天缺一只 →
行业合计给 `null`，已取到部分的和单独放在 `main_net_inflow_nd_sampled`，
`coverage` 三键（`expected_rows` / `rows_used` / `complete`）一起输出。

**`available` 只看承诺的指标字段**（`METRIC_FIELDS`），不看 `reason` / `caliber` 这类
说明性文字：说明字段永远有值，拿它们判断「有没有数」会让空缓存也报 `available=true`。

## 2. 景气（prosperity）

- 公式：成分股**各自最近一期报表**（按 `trade_date` = `report_date` 取最大，同一代码多期只取最新）
  的 `revenue_yoy` / `profit_yoy` / `roe` **中位数**（%，2 位）。
- 字段：`revenue_yoy_median`、`profit_yoy_median`、`roe_median`；
  说明字段 `stocks_with_report`（有几只票出了报表）、`sample_counts`（每个中位数各由几只样本算出）。
- caliber：`sw_constituents`；`report_basis` 固定说明「东财财务指标原值：营收 / 净利润同比为
  单季或累计口径以字段名为准，ROE 为累计值非年化」。
- 缺失策略：无报表行 → `NO_FINANCE_CACHE`（提示先 `ldst sync --dataset finance_indicators`）；
  有行但三个字段全空 → `FINANCE_EMPTY`；某一只票某个字段缺 → 该字段的样本数少 1，
  中位数按**实际样本**算（这是中位数定义本身，不是补值），`sample_counts` 把样本口径摊开。

## 3. 资金（capital）

两路口径**并列输出、互不换算**（DEC-018）：

| 段 | 公式 | caliber |
|---|---|---|
| `constituents` | 最近 `window.fund_days` 个交易日（**按缓存里实际出现的交易日截取**，不是日历日）成分股 `main_net` 合计（元） | `sw_constituents` |
| `board` | 缓存里最新一日的**同名板块**资金流：`main_net_inflow` / `main_net_ratio` / `change_pct` | `board:<缓存里的实际 _source>`（如 `board:eastmoney`） |
| `northbound` | 结构性缺口，永远 `null`（§9） | — |

板块段能出数，必须同时满足三条，任何一条不满足就整段 `null` + 原因：

1. 该行业在 `config/industry.yaml` 里配了 `board_name`（**同名≠同成分**，没配就不给，
   不做跨分类近似匹配）；
2. 最新一日的板块榜只有**一个上游口径**（混有东财 BK 与新浪行业两套榜时不挑数）；
3. 同名板块**恰好命中 1 条**（命中 0 条或 2 条以上都不给，原因里写明命中几条）。

覆盖度门槛见 §1.3。`main_net_inflow_nd` 是「齐了才算」的行业合计，
`main_net_inflow_nd_sampled` 是「已取到部分的和」，两者在覆盖完整时相等——
下游想引用部分样本必须显式引用后者，不能拿合计字段当部分样本用。

## 4. 估值（valuation）

- 两步：① 每个交易日对成分股取 `pe_ttm` / `pb` 的**中位数**（4 位），得到日频序列；
  ② 取最近 `window.valuation_lookback_days` 个交易日（默认 250，交易日不是日历日）
  的序列，当前值 = 序列最后一天，分位 = 经验分位（§1.3）。
- 字段：`pe_median`、`pe_percentile`、`pb_median`、`pb_percentile`；
  说明字段 `percentile_definition`（把分位定义写进载荷，下游不用回来问）、
  `window_days`（配置值）与 `window_days_used`（实际用了几天的序列）、
  `stocks_with_valuation`。
- caliber：`sw_constituents`。
- 缺失策略：无估值缓存 → `NO_VALUATION_CACHE`（文案里带「北交所标的上游无源，实测缺」，
  见 §8 探针记录）；序列为空 → 四个字段全 `null`，不给 0 分位。

## 5. 动量（momentum）

- 等权涨跌幅：对每只成分股
  `(收盘价[-1] / 收盘价[-1-N] - 1) × 100`，再对**参与计算的股票取算术平均**（%，2 位）。
  N 来自 `window.momentum_windows`（默认 `[5, 20]`），字段名随窗口变化：`change_pct_5d`、
  `change_pct_20d`。K 线不足 `N+1` 根、或基期为 0 的股票**不计入也不补 0**，
  样本数放在 `change_pct_sample_counts` 里。
- 成交额占比：`行业成分当日 amount 合计 / 本地缓存代码池当日 amount 合计 × 100`，
  caliber 是 `cache_pool` 并在 `amount_share_base` 里写明分母是几只票、
  「全市场口径需先同步盘后全市场包」。**这不是全市场占比**，字段名与 caliber 都不许被误读。
- 指数口径涨跌幅 `index_change_pct`：结构性缺口，永远 `null`（§9）——
  成分股等权是**另一个字段**，不是它的降级替身。
- 缺失策略：无日 K → `NO_KLINE_CACHE`；涨跌幅有值但 `amount_share_pct` 为 `null`
  时，`amount_reason` 单独说明是成交额缺（`tencent_kline` 实测 `amount` 为空）还是没有成分。

## 6. 拥挤度（crowding）

- 换手分位：与 §4 同一条路径，指标换成 `turnover_rate` 的每日中位数，
  窗口是 `window.turnover_lookback_days`（默认 60）→ `turnover_median`（当前值）
  + `turnover_percentile`（经验分位），caliber `sw_constituents`。
- 资金集中度：`sum(|成分股 main_net|) / sum(|缓存代码池 main_net|) × 100`
  （4 位），窗口同 `fund_days`；caliber `cache_pool`，
  分母口径写在 `fund_concentration_base`，缺失行数放在 `missing_flow_rows`。
  **用绝对值**是故意的：集中度问的是「这只票的钱在本机样本里占多大活动量」，
  净流入的正负会在合计里互相抵消。
- 缺失策略：换手与集中度两路都没数 → `NO_CROWDING_CACHE`；任一路有数就 `available=true`，
  另一路留 `null`（`missing` 列表会点名到底是哪个字段缺）。

## 7. 手算复核（五份夹具）

夹具 = 输入数据集记录 + 一份可落盘的 `industry.yaml` + 手算期望值。
`tests/unit/industry/test_industry_snapshot_golden.py` 逐路径比对，
并断言「承诺的指标路径集合 == 期望值路径集合」（一个都不能少、也不能多）。
夹具的窗口是**故意缩小**的（`fund_days=3`、估值 4 天、换手 3 天、动量 `[2]`），
好让手算在纸上完成；默认窗口（5 / 250 / 60 / [5,20]）由
`tests/unit/industry/test_industry_catalog.py` 钉住。

### I001 五维全出数（食品饮料）

成分：`sw_industry_map` 里 `600001/600002/600004 → 340000`（2025-12-01），
`600004` 在 2026-01-05 改归 `270000` ⇒ **最新归属优先**，成分是 `600001, 600002`（2 只），
`classified_codes=4`，`as_of="2025-12-01"`（340000 名下最新的一天）。

| 字段 | 手算 | 值 |
|---|---|---|
| `prosperity.revenue_yoy_median` | 600001 最新期 12.5、600002 8.5 → (12.5+8.5)/2 | `10.5` |
| `prosperity.profit_yoy_median` | 只有 600001 的 -3.0（600002 无该字段） | `-3.0`（样本 1） |
| `prosperity.roe_median` | (8.0+4.0)/2 | `6.0` |
| `capital.constituents.main_net_inflow_nd` | 窗口 01-05/06/07：1000−500+2500+2000+1000−1500 | `4500.0`（`coverage` 6/6 完整） |
| `capital.board.main_net_inflow` | 同名板块「食品饮料」最新一日恰好 1 条 | `123456789.0`，`caliber="board:eastmoney"` |
| `valuation.pe_median` / `pe_percentile` | 每日中位数：12-31 26.0、01-02 29.0、01-05 22.0、01-06 30.0、01-07 20.0；窗口取最近 4 天 `[29,22,30,20]`，当前 20，`<=20` 占 1/4 | `20.0` / `0.25` |
| `valuation.pb_median` / `pb_percentile` | 同窗口 `[4,4,4.5,3.5]`，当前 3.5 → 1/4 | `3.5` / `0.25` |
| `momentum.change_pct_2d` | 600001 `12/10−1=+20%`、600002 `18/20−1=−10%` → 等权均值 | `5.0`（样本 2） |
| `momentum.amount_share_pct` | 01-07：行业 1000+500=1500，代码池 1500+1500(600003)=3000 | `50.0` |
| `crowding.turnover_median` / `turnover_percentile` | 每日中位数 01-05 7.0、01-06 5.0、01-07 3.0；窗口 3 天，当前 3.0，`<=3.0` 占 1/3 | `3.0` / `0.3333` |
| `crowding.fund_concentration_pct` | 成分 |main_net| = 4000+4500=8500，代码池再 +1500=10000 | `85.0` |
| `capital.northbound` / `momentum.index_change_pct` | 结构性缺口 | `null`（reason 见 §9） |

`available=true`、`data_source_status="ok"`、`unavailable_dimensions=[]`、五维 `reason` 均为 `null`。

### I002 覆盖不全（缺一只票的一天）

`fund_flow` 去掉 `600002 @ 2026-01-07` ⇒ `expected_rows=6`、`rows_used=5`、`complete=false`：
`main_net_inflow_nd=null`（不拿 5 行冒充 6 行）、`main_net_inflow_nd_sampled=6000.0`；
集中度按绝对值算 `7000/8500 → 82.3529`（缺的那行计入分母缺口，`missing_flow_rows=1`）。
景气 / 估值 / 动量三份输入为空 ⇒ `data_source_status="partial"`、
`unavailable_dimensions=["prosperity","valuation","momentum"]`，但资金与拥挤度出数 ⇒
整体 `available=true`。

### I003 空缓存

六个数据集都没有记录 ⇒ 20 个承诺字段（默认窗口 `[5,20]` 铺出 `change_pct_5d/20d`）全部
`null`，`available=false`、`data_source_status="missing"`，`membership.reason` 点名
`sw_industry_map` 并给出同步命令，`capital.reason` 说明两路口径都无缓存。
**schema 一个键都不省**——这是「每个行业每个字段要么有值要么显式 null」那条验收的实现方式。

### I004 同名板块歧义

最新一日有三条板块记录、其中两条都叫「食品饮料」⇒ 命中 2 条 ≠ 1，板块三个字段 `null`，
`reason` 写明「命中 2 条（需要恰好 1 条）」，但 `caliber` 仍给 `board:eastmoney`、
`trade_date` 仍给 `2026-01-07`——**说清「我看了哪一批、为什么不给数」**，
比只留一个 `null` 更容易被误读成数据没同步。
本例只有 `sw_industry_map` 与 `industry_fund_flow` 两份输入有记录，
所以成分段的 reason 也照实说「本地无成分股资金流缓存」。

### I005 未绑定行业（综合）

`config/industry.yaml` 里 `综合` 的 `l1_code: null`（锚点实测归属不一致，不猜码，§8）⇒
成分集合为空（其余五个数据集都有记录，但没有一条属于本行业），
五维走 `_dimension_skeleton`：字段铺满、值全 `null`、
五个维度的 `caliber` 都是 `null`（没有口径可标），`membership.constituent_count=0`
而 `classified_codes=2`（缓存里有别的行业的归属记录，与「本行业没绑代码」是两件事）。

## 8. 探针记录（真实 peer 返回，2026-10-31）

形状与缺口只认实测。原始返回按 `tests/fixtures/peer_responses/README.md` 的裁剪规则
落为夹具，`tests/contract/test_tickhub_payload_mapping.py` 逐条锁形状。

| 工具 | 实调入参 | 实测形状 | 结论 |
|---|---|---|---|
| `sw_industry_history` | `code` 必填（空参被入参校验拒回 `code Field required`） | `data.{code, as_of: null, current, history[], total, first_seen, last_changed}`，行内日期是 `start_date`，`meta.confidence=C`（上游是申万分类表 xls，非日频） | 落库 `trade_date = start_date`，成分表按**懒缓存**逐代码同步（DEC-018），置信度 `low` |
| `eastmoney_finance_indicators` | `code` 必填（`periods` 可选） | `data.{code, indicators[]}`，行内 `report_date` 是 `"YYYY-MM-DD 00:00:00"` | `trade_date = report_date`，取前 10 位解析 |
| `baostock_valuation_history` | `code` 必填（`start`/`end`） | `data.{code, rows[]}`，行内 `date/close/pe_ttm/pb/ps_ttm/pcf_ttm/turnover_rate/tradestatus` | 估值 + 换手的唯一数据源；**北交所标的实测无源**，所以该段可能只有部分成分有数 |
| `board_fund_flow` | `board_type=industry`、`page`、`page_size`（**必须显式**，不靠默认值） | `data.{scope, total: null, page, page_size, items[]}`，行内 `code/name/main_net/main_net_pct/change_pct`，无日期字段 | 板块段字段直接来自 `items`；行内无日期 ⇒ 由落库分区日承担 `trade_date`。实测上游会在东财 BK 与新浪行业之间回落，`_source` 不同 ⇒ caliber 逐字段标注，两套榜不混算 |
| `sw_industry_index` | `l1_code` | 实测「东财未发布该代码的 K 线，且无零鉴权替代源」 | **指数口径涨跌幅接不上** ⇒ `momentum.index_change_pct` 永远 `null`（§9） |
| `northbound_flow` | 市场级 | 无行业拆分 | **行业北向净流入接不上** ⇒ `capital.northbound` 永远 `null`（§9） |
| `tencent_kline` | `code`、`period`、`adjust` | `amount` 实测可为空 | 成交额占比在部分缓存下是 `null` + `amount_reason`，不猜成交额 |

行业与代码的绑定方式（写进 `config/industry.yaml` 的 `provenance.bound_via`）：
上游只发布代码、不发布「代码 → 名称」快照，所以每个行业取 2 只无歧义龙头做**锚点股**，
2026-10-31 经 `sw_industry_history` 实测**两只回同一 `l1_code` 才写进清单**；
不一致的行业（实测只有「综合」：`600816 → 490000`、`600654 → 710000`）`l1_code` 留 `null`。

## 9. 结构性缺口（永远显式 `null`，等上游就绪）

| 字段 | 固定 reason | 为什么不做降级替身 |
|---|---|---|
| `capital.northbound` | 北向资金上游只有市场级（`northbound_flow` 无行业拆分），行业北向净流入接不上 | 市场级总量摊到行业是**编出来的分配**，不是缺失 |
| `momentum.index_change_pct` | 申万行业指数历史不可得（实测），指数口径涨跌幅不降级为成分股聚合 | 等权成分是**另一个字段**（§5 已单列），把聚合值标成指数值会让两个口径无法区分 |

这两条是 `ROADMAP.md` §4 判定「接不上」的结果，不是实现偷懒：
字段留在 schema 里、`provenance` 里 `sources: []` + `unverified: true`，
上游一旦就绪，只改 reason 与取值路径，schema 不动。

## 10. 溯源（R3 形状）

`payload.provenance` = `values` / `sources` / `unverified` 三键，形状同
`SPEC-RESEARCH-PROVENANCE.md` §1：

- `values`：**每一个**叶子路径（含 `industry.*` / `membership.*` / 五维全部字段）都有一条，
  值为 `{sources: [...], unverified: bool}`。有数且该路径的引用数据集本次真的读到 ⇒
  `sources: ["ds:<dataset>"]`；无数或本次没读到 ⇒ `sources: []` **且** `unverified: true`；
  「有值但无出处」这种状态被禁止存在（§9 的两个字段就是 `sources: []` + `unverified: true`）。
- `sources`：本次**真的用到**的证据项，`id` 是 `ds:<dataset>`（六个缓存数据集）或
  `cfg:industry_yaml`（清单）。每项带 `dataset` / `params` / `retrieved_at`：
  数据集的 `retrieved_at` 取缓存里的 `_ingested_at`（**这批数据什么时候入库**，
  不是本次运行时间——否则同一份夹具两次运行会写出两个时间，幂等验收直接挂）；
  配置的 `params` 带 `caliber` 与 `bound_via`。
- 路径 → 数据集的映射在 `_source_for()` 一处集中（景气 → `finance_indicators`、
  板块段 → `industry_fund_flow`、成分段 → `fund_flow`、估值与换手 → `valuation_history`、
  集中度 → `fund_flow`、动量 → `daily_k`、归属 → `sw_industry_map`）。

## 11. 输出契约

| 出口 | 载荷 | 退出码 / 状态 |
|---|---|---|
| `ldst industry list --output json` | 清单 + 每行业 `available` / `constituent_count` / 五维 `dimensions_available` / `data_source_status` / `reason` + `windows` / `formulas` / `datasets` | **恒 0**：清单是配置视图，配置读得到就出得来（指标全缺也逐行标 `missing`）；清单读不动 → 3 |
| `ldst industry show <name> --output json` | `LadderEnvelope`（`domain=sentiment`、`topic=industry_snapshot`），`payload` 是 §1–§10 的快照 | 五维任一字段算得出 → 0；全空 → **6**（DEC-012，stdout 仍是完整可解析信封）；行业不在清单 → 6 + `candidates`；配置错 → 3 |
| MCP `industry_snapshot` | 同一份快照放在 `payload.industry_metrics`，域 `sentiment` | 只读；`industry` 必填、**没有 `date`**（§1.2）；`available=false` → `source_status=partial` + `confidence=low` |

`source_status` / `data_source_status`：五维全出 → `ok`；部分维度出数 → `partial`；
一个字段都没有 → `missing`。`missing` 是载荷字段、`partial` 是信封字段，两者一起给，
下游按哪个都能判定。

**幂等**：同一份缓存 + 同一份配置，两次 `industry_snapshot()` 的输出逐字节相同
（`tests/unit/industry/test_industry_snapshot_golden.py::test_same_fixture_twice_is_byte_identical`）——
所有聚合输入先排序、`retrieved_at` 来自数据本身、公式里没有 `now()`。
命令面上 `_metric_envelope` 会盖一个 `fetched_at`（信封的取值时刻，契约字段），
所以 CLI 的幂等断言比较的是**去掉 `fetched_at` 之后**的完整信封。

## 12. 验收条款对照

| 任务验收项 | 落点 |
|---|---|
| 每个行业字段完整或显式 `null` | §1.3 + §7（I003/I005 铺满 schema）；`missing` / `metrics_present` 只数承诺字段 |
| 每个字段带溯源（R3） | §10；`tests/unit/industry/test_industry_snapshot_golden.py::test_provenance_points_at_the_datasets_that_contributed` |
| 不估算、不插值、不用模型补 | §1.3（经验分位不插值、空集不回 0、部分样本不冒充全样本）；§9（接不上的字段永远 `null`） |
| 公式单测 | `tests/unit/industry/test_industry_metrics.py`（每个期望值手算）+ §7 的五份夹具 |
| 同一夹具跑两次结果一致 | §11；模块面逐字节比对，命令面除 `fetched_at` |
| `--output json` 契约 + 文档 | `docs/CLI.md` §D‴、`docs/MCP_TOOLS.md`、`docs/SOURCES.md`、本文 |
| 申万一级 + 配置可裁剪子集 | `config/industry.yaml`（31 个行业，`enabled: false` 即裁掉）+ `tests/unit/industry/test_industry_catalog.py` |
