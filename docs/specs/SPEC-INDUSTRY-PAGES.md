# SPEC-INDUSTRY-PAGES 产业研究页与产业信号页（前端）

- 版本：v0.1（定稿：三页路由 + 三个只读接口契约 + 信号规则与证据链 + 五态矩阵）
- 日期：2026-10-31
- 归属：前端页面
- 实现：`frontend/src/screens/industry/`、`frontend/src/screens/signals/`、
  `frontend/src/components/industry/`、`frontend/src/api/industry.ts`
- 关联：`SPEC-INDUSTRY-METRICS`（R5 五维口径，**本文件不改口径**）、`UI-SPEC`（令牌与五态）

## 1. 路由与页面职责

| 路由 | 页面 | 职责 |
| --- | --- | --- |
| `/industry` | 行业矩阵 | 五维热力图（景气 / 资金 / 估值 / 动量 / 拥挤度 可切换）、排序、筛选、点击进行业 |
| `/industry/:name` | 单行业 | 五维仪表 + 历史分位 sparkline + 成分股表 + 相关研报/资讯 + 溯源 |
| `/signals` | 产业信号 | R5 字段派生的规则信号列表 + 证据链抽屉，可点回行业页 |

- `:name` 是申万一级行业名（中文，路由参数由 `react-router` 解出后需 `decodeURIComponent` 兜底）；
  空名或超长（> 40 字）直接进错误态，不发请求。
- 三页都是深链入口，**不占一级导航**（与 R8 研究页一致）；互链保留 `demo` 等演示参数。
- 页面只呈现后端给的值：不重算指标、不插值、不补 0、不把 `null` 当 0。
  矩阵的色阶是**展示层排序**，不改变任何数值。

## 2. 只读接口契约（前端提案，后端尚未实现）

三个接口都返回 `LadderEnvelope`（与 `/api/review` 同外壳，`domain="sentiment"`、
`topic="industry_snapshot"`），`payload` 见下。浏览器读不到本机缓存文件，
`signals` 的规则判定也放后端，保证「同一份缓存只有一个判定口径」。

### 2.1 `GET /api/industry`

载荷 = `ldst industry list --output json`（R5 §11）**加上每行 `metrics` / `as_of`**：

```json
{
  "domain": "sentiment",
  "topic": "industry_snapshot",
  "data_as_of": "2025-12-01",
  "payload": {
    "available": true,
    "caliber": "sw_l1",
    "windows": { "fund_days": 5, "valuation_lookback_days": 250,
                 "turnover_lookback_days": 60, "momentum_windows": [5, 20] },
    "count": 31,
    "as_of": {
      "prosperity": "2026-09-30",
      "capital": "2026-10-30",
      "valuation": "2026-10-30",
      "momentum": "2026-10-30",
      "crowding": "2026-10-30"
    },
    "industries": [
      {
        "name": "食品饮料",
        "l1_code": "340000",
        "bound": true,
        "enabled": true,
        "available": true,
        "constituent_count": 118,
        "data_source_status": "ok",
        "reason": null,
        "dimensions_available": {
          "prosperity": true, "capital": true, "valuation": true,
          "momentum": true, "crowding": true
        },
        "metrics": {
          "prosperity.revenue_yoy_median": 6.8,
          "prosperity.profit_yoy_median": 9.2,
          "prosperity.roe_median": 12.4,
          "capital.constituents.main_net_inflow_nd": 1830000000.0,
          "capital.constituents.main_net_inflow_nd_sampled": 1830000000.0,
          "capital.board.main_net_inflow": null,
          "capital.board.main_net_pct": null,
          "capital.board.change_pct": null,
          "valuation.pe_median": 22.1,
          "valuation.pe_percentile": 0.18,
          "valuation.pb_median": 3.2,
          "valuation.pb_percentile": 0.21,
          "momentum.change_pct_5d": 1.6,
          "momentum.change_pct_20d": 4.2,
          "momentum.amount_share_pct": 7.4,
          "momentum.index_change_pct": null,
          "crowding.turnover_median": 1.8,
          "crowding.turnover_percentile": 0.44,
          "crowding.fund_concentration_pct": 9.6
        },
        "as_of": { "prosperity": "2026-09-30", "capital": "2026-10-30",
                   "valuation": "2026-10-30", "momentum": "2026-10-30",
                   "crowding": "2026-10-30" }
      }
    ],
    "datasets": {},
    "source_detail": "本地缓存回放：…（零外呼）",
    "reason": null
  }
}
```

- `metrics` 的键与 R5 `metric_paths()` 完全同名（点分路径），值就是快照里该路径的叶子；
  `null` 表示该字段无数据，**不得省略键**（省略会被前端当作解析失败）。
- 每行 `metrics` / `as_of` 是 R5 的**投影**：`build_snapshot()` 已经算出这些值，
  `industry list` 只是没有把它们放进行里；后端补投影即可，不需要新公式。
- 单个行业字段缺失（`null`）→ 单元格显示「无数据」+ title「无数据（未核实）：<维度 reason>」，
  不参与该列色阶；整行 `available=false` → 行尾「无数据（未核实）」徽标。

### 2.2 `GET /api/industry/{name}`

载荷 = `ldst industry show <name> --output json` 的 `payload`（R5 §11，五维 + `provenance` + `datasets`）
**加上三块页面数据**：

```json
{
  "payload": {
    "available": true,
    "industry": { "name": "食品饮料", "l1_code": "340000", "caliber": "sw_l1",
                  "in_catalog": true, "bound": true, "anchors": ["600519", "000858"],
                  "board_name": "食品饮料", "enabled": true, "note": null },
    "membership": { "constituent_count": 118, "classified_codes": 5132,
                    "as_of": "2025-12-01", "reason": null },
    "dimensions": {
      "prosperity": { "caliber": "sw_constituents", "revenue_yoy_median": 6.8,
                      "profit_yoy_median": 9.2, "roe_median": 12.4,
                      "stocks_with_report": 118, "sample_counts": {},
                      "available": true, "reason": null },
      "capital": { "caliber": {}, "constituents": { "main_net_inflow_nd": 1830000000.0,
                    "main_net_inflow_nd_sampled": 1830000000.0, "window_days": 5,
                    "coverage": { "expected_rows": 590, "rows_used": 590, "complete": true },
                    "reason": null },
                    "board": { "caliber": null, "trade_date": null, "main_net_inflow": null,
                               "main_net_pct": null, "change_pct": null, "reason": "…" },
                    "northbound": null, "northbound_reason": "…", "available": true, "reason": null },
      "valuation": { "pe_median": 22.1, "pe_percentile": 0.18, "pb_median": 3.2,
                     "pb_percentile": 0.21, "window_days": 250, "window_days_used": 249,
                     "available": true, "reason": null },
      "momentum": { "change_pct_5d": 1.6, "change_pct_20d": 4.2,
                    "index_change_pct": null, "amount_share_pct": 7.4,
                    "trade_date": "2026-10-30", "available": true, "reason": null },
      "crowding": { "turnover_median": 1.8, "turnover_percentile": 0.44,
                    "fund_concentration_pct": 9.6, "available": true, "reason": null }
    },
    "windows": {}, "formulas": {}, "datasets": {}, "provenance": {},
    "metrics_present": [], "missing": [], "unavailable_dimensions": [],
    "data_source_status": "ok", "reason": null, "source_detail": "…",
    "history": {
      "pe_percentile": [ { "date": "2026-10-28", "value": 0.19 },
                         { "date": "2026-10-29", "value": 0.18 },
                         { "date": "2026-10-30", "value": 0.18 } ],
      "pb_percentile": [ { "date": "2026-10-30", "value": 0.21 } ],
      "turnover_percentile": [ { "date": "2026-10-30", "value": 0.44 } ],
      "change_pct_20d": [ { "date": "2026-10-30", "value": 4.2 } ]
    },
    "constituents": [
      { "code": "600519", "name": "贵州茅台", "pe_ttm": 21.4, "pb": 7.8,
        "turnover_rate": 0.32, "revenue_yoy": 8.1, "profit_yoy": 9.6,
        "main_net_nd": 620000000.0, "as_of": "2026-10-30" }
    ],
    "related": [
      { "kind": "report", "title": "白酒行业 2026Q3 跟踪", "source": "东财研报",
        "as_of": "2026-10-29", "url": "https://…", "related_codes": ["600519"],
        "industries": ["食品饮料"] },
      { "kind": "news", "title": "…", "source": "财联社", "as_of": "2026-10-30",
        "url": null, "related_codes": [], "industries": ["食品饮料"] }
    ]
  }
}
```

- `dimensions` / `provenance` / `datasets` / `formulas` 原样透传，页面不裁剪、不合并。
- `history.*` 是**当期分位所用的日频序列**（估值 250 日 / 换手 60 日 / 动量窗口）；
  缺该键 → sparkline 显示「历史分位序列未提供」，不画一条只有一点的线。
- `constituents` 是申万成分股逐股快照；缺失 → 表体空态「成分股明细未提供」。
- `related` 来自 R6 `news_stream`（`related_codes` / 行业标签关联）；缺失 → 空态
  「资讯接口未接入（R6 news_stream）」。`url` 只接受 `http(s)` 外链，其余不可点。
- 404 语义：行业不在清单时 R5 CLI 给 `available=false` + `candidates`，HTTP 仍应 **200**
  （载荷完整可渲染成空态）；只有路由本身没接才 404。前端把 404 显示为「接口未接入」。

### 2.3 `GET /api/signals?industry=<name>&rule=<id>`

```json
{
  "payload": {
    "available": true,
    "as_of": "2026-10-30",
    "rules": [
      { "id": "capital_valuation", "title": "资金流入 + 估值低位",
        "direction": "bullish",
        "expression": "capital.constituents.main_net_inflow_nd > 0 且 valuation.pe_percentile < 0.30" },
      { "id": "prosperity_valuation", "title": "景气向上 + 估值低位",
        "direction": "bullish",
        "expression": "prosperity.revenue_yoy_median > 0 且 prosperity.profit_yoy_median > 0 且 valuation.pe_percentile < 0.30" },
      { "id": "momentum_crowding", "title": "动量转强 + 拥挤度不高",
        "direction": "bullish",
        "expression": "momentum.change_pct_5d > 0 且 momentum.change_pct_20d > 0 且 crowding.turnover_percentile < 0.70" },
      { "id": "crowding_risk", "title": "拥挤度高位（风险）",
        "direction": "bearish",
        "expression": "crowding.turnover_percentile > 0.80 且 crowding.fund_concentration_pct > 25" }
    ],
    "signals": [
      {
        "id": "sig-capital_valuation-食品饮料",
        "rule_id": "capital_valuation",
        "rule_title": "资金流入 + 估值低位",
        "industry": "食品饮料",
        "direction": "bullish",
        "severity": "info",
        "as_of": "2026-10-30",
        "summary": "近 5 日主力净流入 18.30 亿，PE 分位 18%（< 30%）",
        "evidence": [
          { "path": "capital.constituents.main_net_inflow_nd", "label": "近 5 日主力净流入",
            "value": 1830000000.0, "unit": "元", "as_of": "2026-10-30",
            "dataset": "fund_flow", "comparator": ">", "threshold": 0, "passed": true },
          { "path": "valuation.pe_percentile", "label": "PE 近 250 日分位",
            "value": 0.18, "unit": "ratio", "as_of": "2026-10-30",
            "dataset": "valuation_history", "comparator": "<", "threshold": 0.30, "passed": true }
        ]
      }
    ],
    "counts": { "total": 1, "bullish": 1, "bearish": 0, "by_rule": { "capital_valuation": 1 } },
    "reason": null
  }
}
```

- **每条信号必须带完整 `evidence[]`**：字段路径、原值、比较符、阈值、该字段的数据时点 `as_of`、
  数据集名。缺任何一项的前端视为解析失败（不展示半条证据）。
- `as_of` 取该条信号用到字段时点的**最大值**；证据行各自保留自己的时点，不做统一覆盖。
- `null` 字段不参与规则判定（不把缺失当 0），该规则对行业不产生信号。
- 规则只用 R5 快照里已有的字段（见 §3），不引入新指标、不新增采集：
  - 例子里的「资金**连续 N 日**净流入」在 R5 里只有 N 日合计
    （`main_net_inflow_nd`），没有逐日序列；本期规则按「近 N 日合计为正」实现，
    证据文案写明「近 N 日合计」。真正的连续 N 日规则列为缺口（§7）。
- `industry` / `rule` 查询参数由后端过滤；前端也做一次本地过滤（不依赖后端顺序），
  但**不重算规则**。

## 3. 五维字段映射（矩阵与仪表）

| 维度 | 矩阵列（R5 路径） | 详情页仪表 |
| --- | --- | --- |
| 景气 | `prosperity.revenue_yoy_median` / `profit_yoy_median` / `roe_median` | 三个中位数 + 报表覆盖只数 |
| 资金 | `capital.constituents.main_net_inflow_nd` / `main_net_inflow_nd_sampled` / `capital.board.main_net_inflow` / `main_net_pct` / `change_pct` | 申万口径合计（元）+ 板块口径（独立标注 caliber） |
| 估值 | `valuation.pe_median` / `pe_percentile` / `pb_median` / `pb_percentile` | 两个中位数 + 两根分位仪表 |
| 动量 | `momentum.change_pct_5d` / `change_pct_20d` / `amount_share_pct` | 5/20 日等权涨跌幅 + 成交额占比（`cache_pool` 口径） |
| 拥挤度 | `crowding.turnover_median` / `turnover_percentile` / `fund_concentration_pct` | 换手分位仪表 + 集中度 |

- 分位值 `0–1`，展示为 `xx.x%`；`0` 是有效值，`null` 才是缺。
- 申万口径与板块口径**并列展示、不求和**；板块段必须带 `caliber` 文案。
- `momentum.index_change_pct` / `capital.northbound` 是结构性缺口，页面显示
  「无数据（未核实）」+ 固定 reason，不降级。

## 4. 页面结构

### 4.1 行业矩阵 `/industry`

| 区块 | 内容 | 缺数据表现 |
| --- | --- | --- |
| 顶部 | 标题、`可用行业数 / 总数`、`as_of`、维度切换 Segmented、刷新 | `available=false` → 顶部 partial 横幅 |
| 工具条 | 行业名搜索、筛选（全部 / 仅有数据 / 未绑定）、排序（表头点击） | 搜索无命中 → 表体空态 |
| 热力表 | 行=行业；列=当前维度的指标 + 成分股数 + 状态；单元格色阶=**同列相对位置** | 单元格「无数据」+ title「无数据（未核实）」；整行不可用 → 行尾徽标 |
| 脚注 | 色阶说明（相对位置，不代表好坏）、口径（申万一级）、数据时点 | — |

- 维度切换写 URL：`/industry?dim=valuation`，可深链、可截两种主题。
- 排序用 `DataTable` 的 `sort`（行业名、成分股数、每个数值列）；默认按「行业名」升序。
- 色阶只用令牌：`color-mix(in srgb, var(--ldst-accent) N%, var(--ldst-surface))`；
  带正负号的字段（同比、净流入、涨跌幅）用 `--ldst-up-soft` / `--ldst-down-soft` 底色 +
  `SignedNumber`（红涨绿跌），不参与 accent 色阶。**不引入图表库、不写字面量色值。**

### 4.2 单行业 `/industry/:name`

| 区块 | 数据 | 缺数据表现 |
| --- | --- | --- |
| 顶部 | 行业名、`l1_code`、`caliber`、`membership.as_of`、状态徽标 | 不在清单 → 空态 + 候选名称 |
| 五维仪表 | 5 张卡：主值 + 辅助值 + 分位仪表（有分位时）| 维度 `available=false` → 「无数据（未核实）」+ 维度 reason |
| 历史分位 | `history.pe_percentile` / `pb_percentile` / `turnover_percentile` / `change_pct_20d` 的 Sparkline | 只有 1 个点 → `—` + 「数据点不足」；无序列 → 空态说明 |
| 成分股 | `constituents[]` DataTable，可按 PE / 涨跌幅排序 | 空态「成分股明细未提供」 |
| 研报 / 资讯 | `related[]`，按 `kind` 分组，外链新窗口 | 空态「资讯接口未接入（R6 news_stream）」 |
| 溯源 | `provenance.sources[]`（数据集 / 参数 / 入库时间）+ `datasets` 状态 + `missing[]` | 空 `sources` → 「本次没有可引用的数据集」 |

### 4.3 产业信号 `/signals`

| 区块 | 内容 | 缺数据表现 |
| --- | --- | --- |
| 顶部 | 信号数、方向筛选（全部 / 看多 / 风险）、规则筛选、行业搜索、`as_of` | `available=false` → 空态 + 生成命令提示 |
| 规则说明 | 4 条规则的标题 + 表达式（可折叠） | — |
| 信号表 | 信号 / 行业（链接）/ 方向 / 证据数 / `as_of` / 「查看证据链」 | 无命中 → 空态「当前筛选下没有信号」 |
| 证据抽屉 | 规则表达式、行业、`as_of`、证据行（字段路径 / 值 / 比较符 + 阈值 / 数据集 / `as_of` / 通过）、去行业页链接 | 证据不完整 → 该条不展示（解析阶段丢弃并计数） |

## 5. 五态与演示参数

五态与全站一致（`PageShell` / `resolveState` / `DEMO_STATE_LABEL`）：

| 状态 | 触发条件 | 页面表现 |
| --- | --- | --- |
| `normal` | 数据可用且无缺口 | 正常渲染，摘要带 `as_of` |
| `partial` | 部分维度 / 部分行业不可用，或信号中有字段缺失 | 顶部横幅「N 项无数据」，其余照常 |
| `empty` | `available=false` 或 HTTP 404 | 空态；404 文案「接口未接入」 |
| `loading` | 首次取数中 | `ScreenSkeleton` |
| `error` | 非法行业名 / 超时 / 解析失败 / 5xx | `ErrorBlock` + 错误码 + 重试 |
| `offline` | `ApiError.kind === 'not-connected'` | 离线横幅 + 空态 |

演示参数（仅 `?demo=1` 生效；演示源绝不自动兜底）：

| 参数 | 值 | 作用 |
| --- | --- | --- |
| `demo` | `1` | 内存演示源，页面显示「演示数据」水印 |
| `state` | `normal/partial/empty/error/loading/offline` | 强制态；`partial`/`empty` 同时切换演示变体 |
| `variant` | `full` / `partial` / `empty` / `all-null` | 演示数据形态 |
| `dim` | 五维名 | 矩阵初始维度 |
| `rule` | 规则 id | 信号页初始规则筛选 |
| `industry` | 行业名 | 信号页初始行业筛选 |

## 6. 主题、响应式与无障碍

- 只用 `src/components/ui` 原子与 Tailwind 语义令牌；无 legacy class（`.card` / `.badge-*`）。
- 1280 / 1440 两档无横向滚动：矩阵表格列宽固定、允许表体纵向滚动；1024–1279 单栏。
- 状态不只靠颜色：单元格缺数据有文字，信号方向有徽标文字，分位仪表带数值文本。
- 仪表：`role="meter"` + `aria-valuemin/max/now` + 文字说明；色阶单元格带 `aria-label`。
- 证据抽屉复用 `Drawer`（焦点陷阱、Esc 关闭、归还焦点）；表格行可键盘进入（`onRowClick` + 行内链接）。
- 外链 `rel="noreferrer noopener"`，非 `http(s)` 一律不渲染成链接。

## 7. 已知缺口（后端清单）

| # | 缺口 | 方法 / 路径 | 需要的字段 | 错误码 |
| --- | --- | --- | --- | --- |
| 1 | 行业列表 HTTP 接口不存在；`ldst industry list` 也没有逐行业指标投影 | `GET /api/industry` | `industries[].metrics`（R5 `metric_paths()` 同名键）+ `industries[].as_of` + 顶层 `as_of` | 200；清单读不动 500；配置错 503 |
| 2 | 单行业 HTTP 接口不存在 | `GET /api/industry/{name}` | R5 `show` 载荷原样 + `history` / `constituents` / `related` | 200；名称不在清单也 200（`available=false` + `candidates`）；路由未接 404 |
| 3 | 历史分位序列未出载荷（R5 只在内部算分位） | 同上 `history` | `pe_percentile` / `pb_percentile` / `turnover_percentile` / `change_pct_20d` 的 `{date,value}[]` | — |
| 4 | 成分股明细未出载荷（R5 只有 `constituent_count`） | 同上 `constituents` | `code/name/pe_ttm/pb/turnover_rate/revenue_yoy/profit_yoy/main_net_nd/as_of` | — |
| 5 | 资讯流未交付（R6 `news_stream`） | 同上 `related`，或 `GET /api/news/stream?industry=` | `kind/title/source/as_of/url/related_codes/industries` | 404 → 前端空态「接口未接入」 |
| 6 | 信号接口不存在 | `GET /api/signals?industry=&rule=` | `rules[]` + `signals[].evidence[]`（§2.3） | 200；参数非法 422；内部 500 |
| 7 | 「资金连续 N 日净流入」缺逐日序列 | `GET /api/signals` | 逐日 `main_net` 序列（R5 只有 N 日合计） | — |

以上接口按本文件字段实现后，前端无需改字段即可切换真实通道（与 R8 同一策略）。

## 变更记录

- v0.1（2026-10-31）：三页路由、三个只读接口契约、4 条信号规则与证据链、
  五态矩阵、演示参数、后端缺口清单。
