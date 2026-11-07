---
name: us-dossier
label: 美股港股全景
description: 用在美/港股个股：行情、财报三表、关键指标、内部人交易、13F 机构持仓、SEC 文件、期权异动、做空数据、分析师预期。当前状态：数据源未接通，本包只提供研究步骤与提示词，执行前会先报「未接通」。
version: 1.0.0
status: unconnected
blocked_by: "LadderStock provider_registry 尚无 th_us_* 适配器（QUOT-471 在补）"
tools_allow:
  - th_us_get_quote
  - th_us_get_market_data
  - th_us_get_fundamentals
  - th_us_get_financial_statements
  - th_us_get_key_indicators
  - th_us_get_analyst_estimates
  - th_us_get_insider_trades
  - th_us_get_13f_holdings
  - th_us_search_filings
  - th_us_get_filing_feed
  - th_us_get_options_flow
  - th_us_get_short_interest
  - th_us_get_short_volume
  - th_us_get_xbrl_facts
  - th_us_lookup_cik
---

# us-dossier（美股/港股全景）

> **状态：未接通。** TickHub-US 的 41 个工具在 `127.0.0.1:8002/mcp` 实测可握手、`tools/list` 返回 41 个，
> 但 LadderStock 后端 `provider_registry.py` 里 `th_us_*` 目前 0 命中，页面还取不到数。
> 本包先发骨架：步骤与提示词可用，执行时以「数据源未接通」返回，不伪造数字。

## When to use

- 美/港股个股的六段梳理（行情 → 财报 → 内部人 → 机构 → 期权/做空 → 预期）
- 想知道某只美股最近谁在买、谁在做空
- SEC 文件里的关键段落定位

## When NOT to use

- A 股 → `a-share-dossier`
- 跨市场联动 → `cross-market`
- 只问一句现价 → 连上后直接调 `th_us_get_quote`，不用本包

## Prerequisites

1. TickHub-US 在 `http://127.0.0.1:8002/mcp` 在线（streamable HTTP，必须 POST + `Accept: application/json, text/event-stream` 握手；`/health`、`/openapi.json`、`/api/mcp/tools` 全是 404，别按这些路径探活）。
2. LadderStock 已接 `th_us_*` 适配器（QUOT-471 交付后成立）。
3. 美股代码用 ticker（`AAPL`），港股用 `0700.HK`；CIK 用 `th_us_lookup_cik` 换。

## 核心原则

> 与 A 股同一口径：所有数字来自工具结果，缺数写缺数，不拿常识补。

- 美元/港元单位必须写在数字旁；市值、成交额都标币种。
- 财报口径：美股按财年（FY）与财季（FQ）写，不换算成自然季度而不说明。
- 13F 有 45 天滞后，必须写出报告期与申报日两个日期。
- 内部人交易区分 Form 4 的买入/卖出与期权行权，不合并计数。
- 做空数据区分 short interest（月度结算）与 short volume（日频），不混。

## 取数纪律

- 只调白名单里的 41 个 `th_us_*`，不调未登记的名字。
- `th_us_check_compliance` / `th_us_list_compliance_policies` 只用于查询规则，不做法律判断。
- 宽响应先用 `th_us_get_xbrl_facts` 定位科目，再取具体期间。

## Fast path（接通后可用）

```
python D:\vibe_astock_tmp\mcp_probe_any.py http://127.0.0.1:8002
# 握手成功后：tools/call -> th_us_get_quote {"symbol":"AAPL"}
```

## Steps

见 `steps.json`：6 步，`status` 同为 `unconnected`；前端卡片显示「未接通」，点开只给步骤与提示词。
