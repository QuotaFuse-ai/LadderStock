---
name: event-calendar
label: 事件日历
description: 用在把一只票或一个自选列表在未来 3-6 个月的关键事件排成时间轴：解禁批次、已公告增减持、业绩预告、机构调研、回购、质押、新股申购、宏观日程。要的是"哪天会发生什么、规模多大"。只在需要真实事件数据时加载。
version: 1.0.0
status: ready
tools_allow:
  - is_trading_day
  - market_calendar_summary
  - trading_calendar
  - macro_calendar
  - lockup_expiry
  - holder_trades
  - earnings_forecast
  - institution_survey
  - share_buyback
  - equity_pledge
  - ipo_calendar
  - dividend_history
  - cninfo_announcements
---

# event-calendar（事件日历）

先定时间基准，再把六类事件按时间排开，最后交一张"未来 3-6 个月"的时间轴。

## When to use

- "这只票未来几个月有什么大事"
- 自选列表要盯着解禁和减持
- 财报季前追踪业绩预告发布状态
- 事件日历页 / 我的股票页要填时间轴

## When NOT to use

- 已发生事件的深度解读 → 走公告解读，不走日历
- 单日盘面强弱 → `a-share-tape`
- 没有具体标的也没有自选列表的空泛提问 → 先问清对象再加载

## Prerequisites

1. TickHub-CN 在线（`th-cn doctor`）。
2. 先确认今天是交易日、最近交易日是哪天（`is_trading_day`、`market_calendar_summary`），后面所有"未来 N 个月"都从这个基准算。
3. 日期一律落成 `YYYY-MM-DD`，禁止用"下个月""月底"。

## 核心原则

> 日历的价值是"日期 + 规模 + 是否已公告"，三者缺一项这条就不算完整。

- 解禁必须写批次日期与解禁股数/占比，来自 `lockup_expiry`。
- 减持区分"已公告计划"与"已发生成交"，来自 `holder_trades` 的两类记录，不合并。
- 业绩预告写发布状态（未发/已发 + 预喜预忧区间），没发就写"未发布"，不推测。
- 回购写方案与实施进度两栏，只有方案没有进度要标出来。

## 取数纪律

- 同一天多个事件按类型分行，不合并成一句。
- `ipo_calendar` 与自选无关时不混进个股时间轴，单列一节。
- 空结果写"该源无记录"并给出查询区间。
- `_refresh=true` 只在源报超时时用一次。

## Fast path

```
th-cn doctor
th-cn tool call is_trading_day  --params '{}'
th-cn tool call lockup_expiry   --params '{"code":"600000","months":6}'
th-cn tool call holder_trades   --params '{"code":"600000"}'
```

## Steps

见 `steps.json`：5 步，第 5 步只做时间轴排版，不调工具。
