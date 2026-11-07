---
name: a-share-tape
label: 盘面复盘
description: 用在收盘后或盘后复盘一次 A 股行情：情绪分与赚钱效应、连板梯队与断层、封板质量与炸板、亏钱效应、资金轮动，最后给出可次日验证的判断。要的是"今天盘面到底弱不弱"的带数据结论。只在需要真实盘面数据时加载。
version: 1.0.0
status: ready
tools_allow:
  - market_sentiment
  - market_overview
  - limit_up_pool
  - yesterday_limit_up_pool
  - ths_limit_up_pool
  - broken_board_pool
  - limit_down_pool
  - strong_stock_pool
  - price_anomaly
  - stock_changes
  - ths_hot_rank
  - eastmoney_hot_rank
  - board_fund_flow
  - fund_flow_ranking
  - sw_industry_index
  - margin_market
---

# a-share-tape（盘面复盘）

收盘后按五段读一遍：情绪 → 梯队 → 封板质量 → 亏钱效应 → 资金轮动，末尾落到明日验证点。

## When to use

- "今天盘面怎么样""情绪冰点还是高潮"
- 想知道连板梯队哪一层断了
- 要判断昨天涨停的那批今天强不强
- 复盘页 / 盯盘页要写当日盘面结论段

## When NOT to use

- 单一票的涨跌原因 → 走 `a-share-dossier`
- 板块结构分析 → 走 `sector-rotation`
- 非交易日：先说"非交易日，以下为最近交易日数据"，不要装作今天有行情

## Prerequisites

1. TickHub-CN 在线，先 `th-cn doctor`。
2. 交易日判断：`is_trading_day`（本 skill 白名单里没有则让调用方先给日期；不要凭模型记忆猜交易日）。
3. 所有"今天/昨日"必须落成具体日期再取数，禁止用相对词直接调工具。

## 核心原则

> 盘面结论必须能被数出来：涨跌家数、涨停数、炸板率、晋级率，每个都是工具返回值。

- 涨跌家数与成交额只从 `market_overview` 取，不引用新闻里的数字。
- 连板高度与晋级率从 `limit_up_pool` + `yesterday_limit_up_pool` 比对，不凭印象。
- 炸板率 = 炸板只数 / （涨停 + 炸板），两个输入都要给出来源。
- 不做次日涨跌预测，只给"可验证的判断"（例：若次日前排晋级率低于 X，则本轮结束）。

## 取数纪律

- `ths_limit_up_pool` 与 `limit_up_pool` 口径可能不同，混用时注明哪张表。
- 涨停原因归 `ths_limit_up_pool` 的归类字段，不自己编题材。
- `price_anomaly` 与 `stock_changes` 是两套异动口径，分别标注。
- 遇到 `QFSD-9003` / `QFSD-1001`（源错/超时）先 `_refresh=true` 重试一次，再报缺。

## Fast path

```
th-cn doctor
th-cn tool call market_sentiment --params '{"date":"2026-11-06"}'
th-cn tool call limit_up_pool    --params '{"date":"2026-11-06"}'
th-cn tool call broken_board_pool --params '{"date":"2026-11-06"}'
```

## Steps

见 `steps.json`：6 步，第 6 步不调工具，只做结论与次日验证点。
