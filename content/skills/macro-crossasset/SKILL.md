---
name: macro-crossasset
label: 宏观与跨资产
description: 用在看宏观数据与跨资产的联动：GDP/CPI/PMI/社融/LPR、利率走廊、国债收益率曲线、A50 期指、北向资金。A 股段数据齐全可直接跑；美债与全球政策利率段依赖 TickHub-US，当前未接通，缺哪段就写缺哪段。
version: 1.0.0
status: partial
blocked_by: "US 宏观段（美债收益率曲线、全球政策利率、COT）依赖 th_us_*，QUOT-471 未接通；A 股段可用"
tools_allow:
  - china_gdp
  - china_cpi
  - china_pmi
  - china_social_financing
  - china_lpr
  - china_money_supply
  - china_retail_sales
  - shibor_rates
  - repo_rate
  - bond_yield_curve
  - a50_futures
  - northbound_flow
  - macro_calendar
  - economy_snapshot
  - index_quote
  - th_us_get_treasury_yield_curve
  - th_us_get_policy_rates
---

# macro-crossasset（宏观与跨资产）

四段：增长与通胀 → 货币与利率 → 跨资产价格 → 传导与结论。

## When to use

- "最近宏观数据怎么样""社融和 CPI 指向什么"
- 利率走廊、国债收益率曲线的形态变化
- A50 期指与北向资金在同一天的背离
- 想把"宏观—资金—风格"串成一条可读的线

## When NOT to use

- 单只股票或板块 → 各走 dossier / sector-rotation
- 跨市场个股联动 → `cross-market`
- 只要一个数字（如"今天 LPR 多少"）→ 直接调 `china_lpr`

## Prerequisites

1. TickHub-CN 在线（`th-cn doctor`）。
2. 每个宏观指标都有**发布期**与**统计期**两个时间，必须都写；用统计期排序，不按发布日排序。
3. 美债段需要 `th_us_*`：未接通时本段输出「未接通」，其余段照常。

## 核心原则

> 宏观结论要写"同比/环比 + 统计期 + 是否修订"，缺一项就不是可核对的数字。

- 累计值 vs 当月值要分清（社融、GDP 多为累计，需换算）。
- 利率一律标期限（1Y/10Y/30Y，DR007、Shibor O/N 等），不带期限的利率没有意义。
- 北向资金写净买入还是净流入，按 `northbound_flow` 的口径原样标注。
- 跨资产只讲同区间同口径的相关，不做因果断言。

## 取数纪律

- 同一指标多源时以官方（统计局/央行）为 A 级，商业聚合为 B。
- 缺月份就写"该月未发布"，不用上月值顶替。
- 曲线形态（陡峭化/平坦化/倒挂）必须由两个具体期限的差值算出，写清差值。

## Fast path

```
th-cn doctor
th-cn tool call china_cpi        --params '{"period":"2026-10"}'
th-cn tool call bond_yield_curve --params '{}'
th-cn tool call northbound_flow  --params '{"date":"2026-11-06"}'
```

## Steps

见 `steps.json`：5 步。第 4 步（美债与全球政策利率）当前标「未接通」，其余步骤可跑。
