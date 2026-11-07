---
name: review-journal
label: 复盘与日志
description: 用在把自己的交易记录和当日盘面拼在一起看：这笔交易发生在什么情绪里、胜率在哪些条件下更高、有没有违反自己定的纪律。要的是行为层面的洞察，不是收益播报。数据一半来自本机 LadderStock 的日志接口，一半来自 TickHub-CN 的盘面工具。
version: 1.0.0
status: ready
tools_allow:
  - "local:/api/positions"
  - "local:/api/journal"
  - "local:/api/realized"
  - market_sentiment
  - limit_up_pool
  - yesterday_limit_up_pool
  - stock_fund_flow
  - price_anomaly
  - tencent_quote_batch
---

# review-journal（复盘与行为诊断）

五步：读记录 → 对上当天盘面 → 分组统计 → 行为诊断 → 纪律检查。

## When to use

- "我这段时间的交易有什么规律"
- "为什么我总是在高点追"
- 复盘页 / 日志页要做月度或周度行为回顾
- 要把交易结果和当日情绪、连板高度对上

## When NOT to use

- 只是查某笔交易的成交明细 → 直接读日志接口
- 给买卖建议 → 本 skill 只给行为洞察与纪律违规记录，不给方向
- 用户没给时间区间、也没给账户记录 → 先问清区间

## Prerequisites

1. 本机 LadderStock 的日志接口在线（`/api/journal`、`/api/positions`、`/api/realized`），持仓与成交是本地隐私数据，不出本机。
2. TickHub-CN 在线，用于补当日盘面（`th-cn doctor`）。
3. 交易记录必须带**日期与代码**才能对上盘面；缺日期就停下来问，不要猜。

## 核心原则

> 行为结论必须可复算：说"冰点期胜率高"，就要给出冰点期的定义、样本数、胜率分子分母。

- 胜率 = 盈利笔数 / 总笔数，样本少于 5 笔只写"样本不足"，不下结论。
- 情绪标签用 `market_sentiment` 的实际分值，不用"感觉很差"。
- 纪律检查要用户先给出规则（单笔亏损上限、单日最大仓位等），没给就问，不替他定。
- 隐私：持仓数量、成本、盈亏只在本机呈现，不写进任何外发文件。

## 取数纪律

- 交易记录按 `date` 聚合后再对盘面，逐笔对会让样本过碎。
- 同日多笔合并为一笔持仓动作，并写明合并方式。
- 盘面数据按交易日的**收盘**口径取，不要用次日数据回填。
- 缺当日盘面数据的交易日，从统计里剔除并写明剔除了几天。

## Fast path

```
curl http://127.0.0.1:8770/api/journal?from=2026-10-01&to=2026-11-07
th-cn tool call market_sentiment --params '{"date":"2026-11-06"}'
```

## Steps

见 `steps.json`：5 步，前两步取数，后三步只做统计与判断。
