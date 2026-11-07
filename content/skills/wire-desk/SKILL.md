---
name: wire-desk
label: 产业链资讯
description: 用在读 QuotaFuse Wire 的分赛道资讯：每日产业要点、按关键词追一个事件的多源报道与时间线、某赛道的信源清单。当前状态：未接通（Wire MCP 已在本机跑，但 LadderStock 未接客户端），只提供步骤。
version: 1.0.0
status: unconnected
blocked_by: "Wire MCP（127.0.0.1:8791）未接进 LadderStock；4 个工具已实测可用"
tools_allow:
  - wire_get_digest
  - wire_search_events
  - wire_get_timeline
  - wire_list_sources
---

# wire-desk（产业链资讯）

三步取数 + 一步成稿：日摘要 → 事件追踪 → 时间线 → 交叉验证。

## When to use

- "今天的产业要闻有哪些""这条消息还有别家报道吗"
- 财报季前后要看某赛道的连续报道
- 要把一条事件排成时间线判断发酵程度

## When NOT to use

- 个股公告 → `cninfo_announcements` 走 dossier
- 行情与盘面 → `a-share-tape`

## Prerequisites

1. Wire MCP 在 `http://127.0.0.1:8791/mcp` 在线，4 个工具：`wire_get_digest`、`wire_search_events`、`wire_get_timeline`、`wire_list_sources`。
2. LadderStock 已接 Wire 客户端（当前未接）。

## 核心原则

> 资讯类内容必须保留「谁说的、什么时候说的、原文链接」，转述不等于事实。

- 每条要点带信源名与发布时间，缺就标缺。
- 同一事件多源报道时，先列各源标题与时间，再说差异，不合并成一个说法。
- 不做事实判断，只做"报道了什么"。

## Steps

见 `steps.json`：4 步，`status: unconnected`。
