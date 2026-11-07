---
name: sector-rotation
label: 板块与指数
description: 用在看行业/概念板块的结构与轮动：板块全景、资金流排行、历史对比、板块内个股结构、指数与估值。要的是"钱在往哪儿挪、挪了多久"的带数据结论。只在需要真实板块数据时加载。
version: 1.0.0
status: ready
tools_allow:
  - industry_boards
  - concept_boards
  - board_members
  - board_history
  - board_fund_flow
  - fund_flow_ranking
  - sw_industry_index
  - sw_industry_history
  - index_quote
  - index_valuation
  - index_constituents
  - index_weights
  - index_kline
  - eastmoney_industry_reports
---

# sector-rotation（板块与指数）

五段：板块全景 → 资金流 → 历史对比 → 板块内结构 → 指数与估值。

## When to use

- "今天哪些板块资金在进"
- "这个板块是刚起来还是已经走了两个月"
- 产业研究页要写板块结论段
- 要判断某行业是普涨还是个别龙头拉指数

## When NOT to use

- 单只股票分析 → `a-share-dossier`
- 全天盘面情绪 → `a-share-tape`
- 产业链环节下钻、找供给卡口 → 这是产业研究 SOP，用 `industry-chain` 的方法，不是本 skill

## Prerequisites

1. TickHub-CN 在线（`th-cn doctor`）。
2. 板块名先落成 `industry_boards` / `concept_boards` 返回的**真实板块名**，禁止自造板块名或把题材词直接当板块名。
3. 指数代码先查 `index_list`（外呼时可不在本 skill 白名单，由调用方先给）。

## 核心原则

> "轮动"必须有时间维度：至少两个时点的资金流或涨跌对比，单日排行只能叫"今天资金流向"。

- 资金流数字只从 `board_fund_flow` / `fund_flow_ranking` 取，不引用研报里的估算。
- 不同板块口径（行业 vs 概念）分开写，不能混在一张排行里比较。
- 指数估值用 `index_valuation`；单只成分股的估值不出现在指数结论里。
- 派生的"连续 N 日净流入"必须由 `board_history` 的逐日值算，不能凭印象。

## 取数纪律

- 板块名匹配不上时，先列出候选板块名让用户选，不要模糊近似。
- `board_members` 只取前若干只并写明截断（避免把长尾当全部）。
- `sw_industry_index` 与 `industry_boards` 的名录不同源，混用时注明。
- 超时先 `_refresh=true`，再报缺，不切换成别的源硬凑。

## Fast path

```
th-cn doctor
th-cn tool call fund_flow_ranking --params '{"date":"2026-11-06","type":"industry"}'
th-cn tool call board_fund_flow    --params '{"board":"半导体"}'
th-cn tool call board_history      --params '{"board":"半导体","days":20}'
```

## Steps

见 `steps.json`：6 步，第 6 步只做结论，不调工具。
