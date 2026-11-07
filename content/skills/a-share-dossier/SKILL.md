---
name: a-share-dossier
label: 个股全景
description: 用在一只 A 股要从"这是什么公司"走到"该不该继续看"的全景梳理：行情、财务、资金、事件、预期、估值六段，每段都有必须调用的取数工具。要的是一份带来源的结论，不是一段行情播报。只在需要真实数据时加载；只讲概念、讨论策略、不取数时不要加载。
version: 1.0.0
status: ready
tools_allow:
  - stock_profile
  - baostock_stock_basic
  - tencent_quote
  - eastmoney_financial_all
  - eastmoney_financial_report
  - eastmoney_finance_indicators
  - finance_crosscheck
  - cninfo_financial_notes
  - stock_fund_flow
  - dragon_tiger_seats
  - chip_distribution
  - top_shareholders
  - holder_number
  - cninfo_announcement_risk
  - cninfo_announcements
  - holder_trades
  - equity_pledge
  - lockup_expiry
  - ths_eps_forecast
  - eastmoney_reports
  - institution_survey
  - full_valuation
  - baostock_valuation_history
---

# a-share-dossier（A 股个股全景）

六段走完一只股票：定位 → 财务 → 勾稽 → 资金筹码 → 事件风险 → 预期估值 → 结论与反证。

## When to use

- "这家公司到底做什么、值不值得继续看"
- 要一份能拿去对账的个股研究，而不是一句涨跌幅
- 财报季前后要判断"业绩是加速还是减速"
- 用户已经在自选里放了这只票，想知道"我为什么拿着它"

## When NOT to use

- 只问一句现价 → 直接调 `tencent_quote`，不用本 skill
- 比较两只及以上股票 → 走并排对比，不走本合同比
- 问板块/指数 → 走 `sector-rotation`
- 不取数只讨论方法 → 不加载

## Prerequisites

1. TickHub-CN 在线（`http://127.0.0.1:8000/mcp` 或 stdio）。
2. 先跑一次 `th-cn doctor` 看各源健康；`QFSD-2001` 只是没 Redis，不是故障。
3. 代码统一 6 位（`600000` / `000001` / `300750`），港股 `hk00700` 走 US 侧，不在本 skill 内。

## 核心原则

> 每个数字必须来自工具结果。不推断、不插值、不复述记忆里的价格或比率。

- `data` 为空时写"该源无此记录"，并附 `meta.note`，不拿估算顶上。
- 派生指标（增速、分位、占比）必须写出两个工具来源 + 公式。
- 每个引用数字保留 `meta.source`；报告里带 `meta.trace_id`。
- `meta.confidence` A=交易所/官方、B=商业聚合、C=衍生/抓取；混用时标 C。
- 不做买卖建议，只给暴露度与事实。

## 取数纪律

- 历史问题先想 point-in-time：问"截至某日市场知道什么"就传 `data_as_of`。
- 批量不循环：多只票用 `tencent_quote_batch` / `stock_fund_flow_batch`。
- 宽响应只取需要的字段：`fields="code,price,volume"`。
- `QFSD-9001` 是参数错（坏代码/坏日期），改参数，别重试。
- 财务口径先 quarterize 再比：`eastmoney_financial_all` 返回多为累计数，单季要自己减。
- 扣非与归母分开看；勾稽用 `finance_crosscheck`，异常项要回原文（`cninfo_financial_notes`）。

## Fast path

```
th-cn doctor
th-cn tool call stock_profile   --params '{"code":"600000"}'
th-cn tool call tencent_quote   --params '{"codes":["600000"]}'
th-cn tool call eastmoney_financial_all --params '{"code":"600000"}'
```

## Steps

见同目录 `steps.json`：7 步，第 1 步定位与口径，第 7 步结论与反证。每步的 `tools` 是白名单，超出即失败。
