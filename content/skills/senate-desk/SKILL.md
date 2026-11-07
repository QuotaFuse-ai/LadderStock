---
name: senate-desk
label: 多空辩论与回测
description: 用在 StockSenate 的深度投研：多空辩论要点、完整投研链路、策略回测推演。当前状态：未接通（8765 MCP 已跑但握手慢约 20s，LadderStock 未接客户端），只提供步骤。
version: 1.0.0
status: unconnected
blocked_by: "StockSenate MCP（127.0.0.1:8765）未接进 LadderStock；GET 超时须 POST 握手，约 20s"
tools_allow:
  - stock_senate_analysis
  - stock_senate_backtest
  - stock_senate_alert_query
  - stock_senate_report_query
  - stock_senate_config_query
---

# senate-desk（多空辩论与回测）

四步：多空要点 → 完整链路 → 回测推演 → 结论与分歧。

## When to use

- "帮我看看多空双方各怎么说"
- 要一份带风控环节的完整投研链
- 策略描述想跑一次历史回测

## When NOT to use

- 只要事实与数据 → 走 dossier / tape
- 要交易指令 → 不做

## Prerequisites

1. StockSenate MCP 在 `http://127.0.0.1:8765/mcp` 在线；必须 POST 握手，`GET` 会超时，单次约 20 秒，调用要留超时余量。
2. LadderStock 已接该客户端（当前未接）。

## 核心原则

> 辩论与回测的输出都是"观点 + 依据"，不是事实本身；必须标清哪部分是可核数据、哪部分是模型推论。

- 多空论点逐条标注依据来源（财务数据 / 事件 / 估值）。
- 回测必须写清区间、标的池、成本假设；缺一项结论不成立。
- 明确写"本段为模型推论，非事实"，与 dossier 的事实段分开。

## Steps

见 `steps.json`：4 步，`status: unconnected`。
