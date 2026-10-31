# SPEC-RADAR-PAGES 资讯雷达 / 事件日历 / 候选池 / 我的持仓（前端）

- 版本：v0.1（首版：四页路由 + 四个接口契约 + 导航挂载方案 + 缺口清单）
- 日期：2026-10-31
- 归属：前端架构（R10 / QUOT-148）
- 实现：`frontend/src/screens/radar/`、`frontend/src/api/radar.ts`
- 数据口径（**本文件不改口径**）：`SPEC-NEWS-RADAR`（R6）、`SPEC-CANDIDATE-POOL`（R7 候选池）、
  `SPEC-MY-POSITION`（R7 持仓）；视觉与五态：`docs/UI-SPEC.md`
- 关联决策：DEC-006（数据只落本机）、DEC-014（看板=控制面）、DEC-020（隐私库 v2 与持仓边界）、
  DEC-019（资讯来源与日历缺口）

## 1. 路由与导航挂载

| 路由 | 页面 | 所属一级导航 | 二级入口 |
| --- | --- | --- | --- |
| `/news` | 资讯雷达 | 市场 | 资讯雷达 |
| `/calendar` | 事件日历 | 市场 | 事件日历 |
| `/pool` | 候选池 | 复盘 | 候选池 |
| `/positions` | 我的持仓 | 日志 | 我的持仓 |

挂载理由（遵守「不新增顶级导航项」）：

- **市场**已经承载「今天发生了什么」（情绪温度 / 连板梯队 / 题材轮动），资讯与日历是同一类
  「读事实」的入口，放在市场下不再扩散；
- **候选池**是复盘动作的产出（明天看什么），跟「情绪复盘」同组；
- **我的持仓**与「交易日志」同属本机私有数据，放日志下，隐私口径一致。

分组后一级导航仍是 6 项 + 设置；`pages.length > 1` 的既有展开逻辑不改。

## 2. 共用约定

- 五态：`?state=loading|empty|error|partial|offline`（`lib/pageState.ts`），演示源 `?demo=1`。
  强制态优先于真实请求；演示数据带水印，**真实通道绝不回落到演示数据**。
- 接口未接入（HTTP 404 / 连接失败）视为「数据源暂缺」：空态「接口未接入」+ 重试，
  不是整屏报错，也不伪造行。
- 页面不重算任何指标：候选分项、盈亏、集中度、行业分布全部来自后端载荷。
  缺失值显示 `—` + 原因，**不补 0**。
- 深链保留查询串（`?date=` / `?month=` / `?code=` / `?demo=1`），刷新后可复现同一视图。

## 3. `GET /api/news` 资讯流

响应用 `LadderEnvelope` 外壳（`domain="news"`、`topic="news_stream"`、`data_as_of`=窗口右端），
`payload` = `ldst news <date> --output json` 的 `payload`（R6 `StreamResult.as_dict()`）
**加上两个页面字段**：

```jsonc
{
  "ok": true, "domain": "news", "topic": "news_stream", "data_as_of": "2026-10-30",
  "payload": {
    "window": { "start": "2026-10-30", "end": "2026-10-30" },
    "sources": ["announcement_cninfo", "flash_cls"],
    "codes": [],
    "summary": { "items": 12, "fetched": 15, "new_items": 12, "known_items": 0,
                 "merged_duplicates": 3, "dropped_undated": 0, "outside_window": 0,
                 "stored": 12, "events": 2, "persisted": true },
    "sources_status": [
      { "dataset": "announcement_cninfo", "tool": "…", "status": "ok", "items": 4,
        "detail": "", "source": "tickhub", "source_detail": "…",
        "freshness": "realtime", "confidence": "high", "degraded": false }
    ],
    "extraction": { "events": 2, "by_type": { "policy": 2 },
                    "skipped_no_url": 0, "skipped_excluded_source": 0,
                    "suppressed_by_negation": [] },
    "items": [
      { "id": "…", "source": "flash_cls", "title": "…", "url": "https://…",
        "published_at": "2026-10-30 09:31:00", "related_codes": ["600519"],
        "summary": "…", "content_hash": "…",
        "origins": [{ "dataset": "flash_wallstreetcn", "title": "…", "url": "https://…" }],
        "industries": ["食品饮料"] }
    ],
    "events": [
      { "id": "…", "type": "policy", "label": "政策发布", "event_date": "2026-10-30",
        "title": "…", "codes": ["600519"],
        "evidence": { "snippet": "…", "url": "https://…", "matched": ["央行"], "dataset": "flash_cls" },
        "item_id": "…", "date_basis": "published_at", "extractor": "rule:v1" }
    ],
    "data_source_status": "ok",
    "exit_code": 0
  }
}
```

- `items[]` 必须保持 R6 八键契约（`id/source/title/url/published_at/related_codes/summary/content_hash`），
  不得增删；`origins` / `industries` 是可选附加键（`industries` 见 §7 缺口 1）。
- `events[]` 是**新增的页面字段**（R6 CLI 只给 `extraction` 计数）：规则已抽出的事件带 `item_id`，
  页面据此在资讯流上标「已抽取事件」。事件对象仍是 R6 `NewsEvent.as_dict()` 的形状，一份口径。
- `sources_status[]` 里有 `status != "ok"` 或 `degraded=true` → 页面进 `partial`：列出失败栏目与原因，
  其余条目照常渲染（「某一路挂了」不等于整页没数据）。
- 查询参数：`date`（YYYY-MM-DD）、`until`（可选，≤31 天）、`source`（可重复 / 逗号分隔）、
  `code`（可重复 / 逗号分隔）、`limit`（默认 30）。
- 错误码：参数非法 `422`；本机库不可读 `500`；上游全挂但结构完整 → `200` + `data_source_status="missing"`。

## 4. `GET /api/events` 事件日历

`LadderEnvelope`（`domain="news"`、`topic="event_calendar"`、`data_as_of`=月份首日），
`payload` = `ldst events --month YYYY-MM --output json` 的 `payload` + `data_source_status`：

```jsonc
{
  "month": "2026-10", "window": { "start": "2026-10-01", "end": "2026-10-31" },
  "codes": ["600519"],
  "sections": [
    { "key": "earnings_disclosure", "label": "财报预约披露日", "status": "no_source",
      "entries": [], "note": "暂无数据源",
      "reason": "上游无「预约披露时间表」工具…", "ask": "上游需求：…" },
    { "key": "lockup_expiry", "label": "解禁日", "status": "sourced",
      "data_source": "lockup_calendar", "tool": "…", "degraded": false, "note": "",
      "entries": [
        { "id": "…", "date": "2026-10-20", "code": "600519", "section": "lockup_expiry",
          "label": "限售解禁：首发原股东限售股份", "detail": "可流通(万股) 120；占总股本 0.8",
          "source": "lockup_calendar", "provenance": { "dataset": "lockup_calendar", "…": "…" } }
      ] }
  ],
  "extracted_events": [
    { "id": "…", "type": "shareholder_meeting", "label": "股东大会", "date": "2026-10-28",
      "title": "…", "codes": ["600519"],
      "evidence": { "snippet": "…", "url": "https://…", "matched": ["股东大会"], "dataset": "announcement_cninfo" },
      "date_basis": "evidence_text", "extractor": "rule:v1", "basis": "rule_extracted", "note": "…" }
  ],
  "data_source_status": "partial"
}
```

- 小节顺序固定 `earnings_disclosure / lockup_expiry / shareholder_meeting / policy_window / earnings_preview`。
- `status="no_source"` 的小节**必须**带 `reason` + `ask`，`entries` 恒为 `[]`；
  页面画成「暂无数据源 + 怎么补」，**不是空白格**，也绝不估算日期。
- `extracted_events` 与五个小节是两件事：前者是规则从公告正文抽出、带证据的事件
  （`basis="rule_extracted"`），页面标注「规则抽取，非日历数据源」。
- 查询参数：`month`（YYYY-MM，必填）、`code`（可选，可重复）、`limit`。
- 错误码：`month` 缺失或非法 `422`；有源小节全挂 `200` + `data_source_status="missing"`。

## 5. `GET /api/pool` 候选池

`LadderEnvelope`（`domain="sentiment"`、`topic="candidate_pool"`），`payload` = `ldst pool --output json` 的 `payload`
（`SPEC-CANDIDATE-POOL` §4，字段一个不改）：

```jsonc
{
  "date": "2026-10-30", "available": true, "reason": null,
  "source_detail": "limit_pool: archive（3 条）; concept_board: …; fund_flow: …",
  "height": 5, "candidate_count": 3,
  "candidates": [
    { "code": "600001", "name": "甲", "score": 91.0,
      "parts": { "strength": 83.3, "turnover": 60.0, "standing": 100.0, "capital": 100.0, "theme": 100.0 },
      "absent_parts": [],
      "raw": { "boards": 5, "turnover_rate": 12.0, "height": 5,
               "net_inflow": 30000000.0, "theme_tags": ["机器人"], "theme_board_change_pct": 8.0 } }
  ],
  "weights": { "strength": 0.3, "turnover": 0.1, "standing": 0.25, "capital": 0.2, "theme": 0.15 },
  "formula": "score = Σ(weight_i × part_i) / Σ(weight_i)…",
  "counts": { "limit_pool_rows": 3, "dropped_duplicates": 0, "unknown_code": 0,
              "theme_boards": 3, "fund_flow_rows": 3 },
  "missing": [], "data_source_status": "ok"
}
```

- `parts` 的五个键固定、不可增删；`null` 是「这个分项没数据」，`absent_parts` 点名。
  页面把 `null` 显示为「无数据（未核实）」，**不补 0、不重算总分**。
- `available=false` → 空态（原因取 `reason`，`missing` 点名缺的数据集）。
- 查询参数：`date`（缺省今天）、`limit`（默认 30）。
- 排序在页面侧只做**展示排序**（`score` / 分项），不改变接口顺序语义（接口已按 score 降序）。

## 6. `GET/POST/PATCH/DELETE /api/positions` 我的持仓

**平面 JSON（不是 `LadderEnvelope`）**：持仓是隐私数据，`SPEC-MY-POSITION` §6 明令不进信封、
不进 MCP、不进 AI 上下文。接口只在回环可读写的本机服务上存在（同 `/api/journal/stats` 的闸门）。

### 6.1 读：`GET /api/positions?date=YYYY-MM-DD`

载荷 = `ldst position list --output json` 的载荷（`journal/position.py::compute_metrics()`）：

```jsonc
{
  "local_only": true, "store": "local_sqlite",
  "data_source_status": "ok", "as_of": "2026-10-30", "price_source": "daily_k",
  "position_count": 3,
  "valuation": { "total_cost": 22000.0, "market_value": null, "priced_cost": 20000.0,
                 "unpriced_cost": 2000.0, "coverage_pct": 90.9,
                 "unrealized_pnl": 0.0, "unrealized_pnl_pct": 0.0 },
  "items": [
    { "code": "600000", "cost": 10.0, "qty": 1000.0, "entry_date": "2026-10-01",
      "industry": "银行", "note": "", "latest_price": 11.0,
      "market_value": 11000.0, "unrealized_pnl": 1000.0, "unrealized_pnl_pct": 10.0 }
  ],
  "concentration": { "available": true, "reason": null, "basis": "cost",
                     "top1_pct": 45.5, "top3_pct": 100.0, "hhi": 0.4215,
                     "holdings": [{ "code": "600000", "weight_pct": 45.5, "cost": 10000.0 }] },
  "sector_distribution": { "available": true, "reason": null, "basis": "cost",
                           "labeled_count": 2, "unlabeled_count": 1,
                           "industries": [{ "industry": "银行", "weight_pct": 54.5, "cost": 12000.0 }] }
}
```

- `market_value=null` 表示「有人没取到价 → 整体退回成本口径」，页面必须显示口径 `basis`
  与 `coverage_pct`，不得把 `null` 当 0；`latest_price=null` 的行显示 `—` + 原因。
- 空持仓：`data_source_status="missing"`、`position_count=0`，页面空态（不是错误）。

### 6.2 写：`POST /api/positions`、`PATCH /api/positions/{code}`、`DELETE /api/positions/{code}`

| 方法 / 路径 | 请求体 | 成功响应 |
| --- | --- | --- |
| `POST /api/positions` | `{code, cost, qty, entry_date?, industry?, note?}` | `{ok:true, local_only:true, store:"local_sqlite", position:{…}}` |
| `PATCH /api/positions/{code}` | 至少一个 `{cost?, qty?, entry_date?, industry?, note?}` | 同上 |
| `DELETE /api/positions/{code}` | — | `{ok:true, local_only:true, store:"local_sqlite", removed:{code, cost, qty}}` |

语义沿用 CLI（`SPEC-MY-POSITION` §2）：`code` 唯一、重复 `add` 返回 `409`（提示改用 update）、
`PATCH`/`DELETE` 的 code 不在库里 `404`、请求体校验失败 `422`。

> **待决策（不在本文件单方面生效）**：UI-SPEC v1.0 §0 写「持仓不经 HTTP」，而页面要做增删改
> 就必然有一条本机写路径。DEC-015 / DEC-016 已有「回环独占 + 聚合只读 / 用户数据写」的例外先例。
> 本页实现按上面的契约走，但在决策落定前，**接口缺失时页面提供两条完全本机的兜底**：
> ①导入用户自己 `ldst position list --export` 出来的 JSON（FileReader，不上传、不落浏览器存储）；
> ②增删改对话框在提交失败时给出等价 CLI 命令并支持复制。是否开放写接口由安全工程师 / 人类拍板。

### 6.3 页面的隐私硬约束

- 持仓数据**只存在于 React 内存**：不写 `localStorage`、不写 IndexedDB、不进 URL、不进日志。
- 页面顶部常驻高对比说明：「数据只在本机，不上传、不进 AI」，并给出数据来源（本机 SQLite）与
  命令出处（`ldst position`）。
- 不做「分享 / 同步 / 导出到云」入口；导出只在用户显式点击时由 CLI 完成，页面不代劳。
- `items[].note` 等自由文本一律按纯文本渲染（不注入 HTML）。

## 7. 后端缺口清单（前端已按契约实现，接口上线即切真通道）

| # | 缺口 | 方法 / 路径 | 需要的字段 | 错误码 |
| --- | --- | --- | --- | --- |
| 1 | 资讯流读接口（R6 只交付 CLI / MCP） | `GET /api/news` | §3 全量，尤其 `items[].origins/industries`、`events[]` | 200 / 422 / 500 |
| 2 | 资讯条目的行业标签 | 同上 `items[].industries` | R6 目前不产出；页面先按「标题 / 摘要」关键词过滤，有 `industries` 时按标签过滤 | — |
| 3 | 事件日历读接口 | `GET /api/events` | §4 全量（含 `no_source` 的 `reason` / `ask`） | 200 / 422 / 500 |
| 4 | 候选池读接口（R7 只交付 CLI / MCP） | `GET /api/pool` | §5 全量 | 200 / 422 / 500 |
| 5 | 持仓读接口 | `GET /api/positions` | §6.1 全量 | 200 / 422 / 500 / 403（非回环） |
| 6 | 持仓写接口（**需隐私决策**） | `POST/PATCH/DELETE /api/positions` | §6.2 | 200 / 201 / 404 / 409 / 422 / 403 |

接口按本文实现后，前端不需要改字段即可切换真实通道（`api/radar.ts` 的解析已按本文写死）。

## 8. 验收项 → 用例

| 验收项 | 钉住它的用例 |
| --- | --- |
| 四页五态齐备 | `tests/radar-pages.test.tsx` 每页 `?state=` 各一条 |
| 资讯三栏 + 代码/行业过滤 + 已抽取事件标注 | `radar-pages.test.tsx`「资讯雷达」组 |
| 日历 `no_source` 显式标注、点日跳资讯 | `radar-pages.test.tsx`「事件日历」组 |
| 候选池可排序、分项可展开、显式导出 | `radar-pages.test.tsx`「候选池」组 |
| 持仓盈亏 / 集中度 / 行业分布 + 隐私提示 | `radar-pages.test.tsx`「我的持仓」组 |
| 持仓不落浏览器存储 | `tests/positions-privacy.test.tsx` |
| 载荷解析（含缺字段 / null 不补 0） | `tests/radar-api.test.ts` |
