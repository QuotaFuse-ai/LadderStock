---
name: cross-market
label: 跨市场联动
description: 用在把 A 股与美股/港股放在一起看：A/H 溢价、中概股映射 A 股板块、隔夜美股对次日 A 股的方向映射、全球宏观到 A 股风格的传导链。当前状态：未接通（依赖 TickHub-US 适配器与港股报价源），只提供研究步骤。
version: 1.0.0
status: unconnected
blocked_by: "th_us_* 适配器未接（QUOT-471）；港股报价口径待核（tencent_quote 是否支持 hk 代码需实测）"
tools_allow:
  - tencent_quote
  - tencent_quote_batch
  - index_quote
  - northbound_flow
  - industry_boards
  - board_fund_flow
  - market_sentiment
  - th_us_get_quote
  - th_us_get_market_data
  - th_us_get_market_list
  - th_us_get_treasury_yield_curve
  - th_us_get_policy_rates
  - th_us_get_financial_statements
---

# cross-market（跨市场联动）

> **状态：未接通。** 需要两个前提：① `th_us_*` 适配器接进 LadderStock（QUOT-471）；
> ② 港股报价口径确认（A/H 溢价要用两地同一时点的价，`tencent_quote` 对 `hk00700` 是否可用需实测）。
> 本包先发骨架，执行时以「未接通」返回。

## When to use

- "A 股这只票对应的 H 股贵多少"
- "隔夜美股半导体涨了，今天 A 股半导体大概率怎样"
- "美债利率上行这段时间，北向资金和 A 股风格怎么变"

## When NOT to use

- 只分析 A 股 → `a-share-dossier`
- 只分析美股 → `us-dossier`
- 单纯宏观数据罗列 → `macro-crossasset`

## 核心原则

> 跨市场的结论必须写清"时点能不能对上"。两地收盘时间不同，任何溢价率都要写明 A 股用的是哪一刻、H 股用的是哪一刻。

- A/H 溢价率 = (A 价 ÷ H 价 × 汇率 − 1)，三个输入都要给来源与时点。
- "隔夜映射"只是相关性描述，不能写成因果；必须给出映射的逻辑环节（同产业链 / 同指数成分 / 同题材）。
- 北向资金用 `northbound_flow` 的实际口径（净买入 / 净流入），不混用。
- 美股数据一律标美元与美东时间。

## 取数纪律

- 两地价差超过 30 分钟不对齐时，标注"时点不对齐"，不给溢价结论。
- 中概股映射先列出候选板块名，由用户确认后再取数，不自己拍板映射关系。
- 汇率必须来自工具或用户给定；没来源就不算溢价率。

## Steps

见 `steps.json`：5 步。前 3 步依赖 US 侧（未接通），第 4 步只做结论。前端卡片显示「未接通」。
