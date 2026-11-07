---
name: portfolio-check
label: 持仓体检
description: 用在一组持仓的组合体检：集中度、行业暴露、盈亏归因、资金面与风险提示。输入是持仓列表（代码+成本+数量），输出是暴露度诊断，不给买卖建议。数据全部来自 TickHub-CN 与本机持仓接口。
version: 1.0.0
status: ready
tools_allow:
  - "local:/api/positions"
  - "local:/api/realized"
  - tencent_quote_batch
  - stock_fund_flow_batch
  - stock_profile
  - baostock_stock_basic
  - price_anomaly
  - board_fund_flow
  - index_quote
---

# portfolio-check（持仓体检）

五步：读持仓 → 取现价算盈亏 → 集中度与行业暴露 → 资金面与异动 → 组合结论（只给暴露度）。

## When to use

- "我这个组合怎么样""是不是太集中了"
- 想知道自己行业暴露偏在哪
- 月度/季度组合复盘

## When NOT to use

- 单只票分析 → `a-share-dossier`
- 交易行为与纪律 → `review-journal`
- 要"该不该卖/买什么" → 不做，本包只给暴露度与集中度

## Prerequisites

1. 持仓列表：优先读本机 `/api/positions`；用户口头给清单时，必须齐「代码 + 成本 + 数量」，缺一项就问。
2. TickHub-CN 在线（`th-cn doctor`）。
3. 批量上限 50 只：超过就分批，并写明分批方式。

## 核心原则

> 体检报告里的每个比例都要能被复算：权重 = 该股市值 / 组合总市值，分子分母都写出来。

- 现价只来自 `tencent_quote_batch`，不用成本价代替市值。
- 行业归属用 `stock_profile` / `baostock_stock_basic` 的行业字段，不凭名称猜。
- 盈亏归因只算已实现 + 浮动两部分，写清各自口径。
- 结论只给"集中度偏高/行业暴露偏重/资金面背离"，不给买入卖出建议，不用"建议减仓"这类表述。

## 取数纪律

- 停牌或取不到价的标的单列，不计入权重分母，并写明剔除。
- 权重和暴露比例保留两位小数，四舍五入方式写一次。
- 现金/货基不参与行业暴露统计，单列。

## Steps

见 `steps.json`：5 步，第 5 步只做结论。
