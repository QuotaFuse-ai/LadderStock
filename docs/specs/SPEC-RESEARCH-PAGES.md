# SPEC-RESEARCH-PAGES 个股深挖与六阶段研究报告（前端）

- 版本：v0.4（定稿：页面骨架 + 只读接口契约 + 五态矩阵；v0.2 补 `/research` 入口页与导航；
  v0.3 补 K 线图端点契约 §2.4 并标注 §2 接口实现状态；v0.4 补 §2.4 的 `series` /
  涨跌幅 / 均线 / 成交量单位与标签）
- 日期：2026-11-05
- 归属：前端架构
- 实现：`frontend/src/screens/research/`、`frontend/src/components/research/`、
  `frontend/src/api/research.ts`、`frontend/src/lib/citation.ts`
- 关联：`SPEC-RESEARCH-PROVENANCE`（R3 产物与角标口径）、后三段只消费上游、
  估值事实键、来源白名单、`UI-SPEC`（令牌与五态）

## 1. 路由与页面职责

| 路由 | 页面 | 职责 |
| --- | --- | --- |
| `/research` | 个股深挖入口 | 收 6 位代码的落地页：格式校验后跳 `/research/<code>`，侧栏「个股深挖」指向这里 |
| `/research/:code` | 个股深挖 | 公司画像、财务趋势、盈利预期、估值分位、风险清单；同时是六段的摘要入口 |
| `/research/:code/report` | 六阶段研究报告 | 左段导航 + 右 Markdown 正文 + 来源抽屉 + 失败段续跑 |

入口页只做格式校验、不取数：`demo` / `variant` / `date` / `state` 参数原样带到研究页，
所以从侧栏进来的演示态与深链一致。

`:code` 接受 6 位数字，可带 `.SH` / `.SZ` / `.BJ` 后缀；规范化口径与后端
`research/checkpoint.normalize_code` 一致（`normalizeResearchCode`）。非法代码直接进错误态，
给出路径示例，不发请求。

两页共用 `useResearchView()`（取数 / 五态 / 演示变体）与 `useResearchRun()`（续跑与轮询）。
页面不重算指标、不补齐缺失值、不重排来源，数字与出处原样透传后端产物。

## 2. 只读接口契约

浏览器读不到本机文件，页面只认四个只读接口；载荷与 MCP `research_status` 同形。
实现状态：§2.1–2.3 是前端提案、后端尚未接（缺口说明见 §2.3 末）；
§2.4 K 线图已随 QUOT-410 落地（DEC-055），可直接联调。

### 2.1 `GET /api/research/{code}?date=YYYY-MM-DD`

```json
{
  "code": "600000",
  "date": "2026-10-31",
  "available": true,
  "complete": false,
  "checkpoint": "data/state/research_checkpoint/600000/2026-10-31.json",
  "updated_at": "2026-10-31T08:20:00+08:00",
  "stages": [
    {
      "name": "financial",
      "title": "财务",
      "status": "ok",
      "degraded": false,
      "artifact": {
        "document": { "code": "...", "stage": "financial", "status": "ok",
                      "narrative": ["..."], "notes": [], "facts": {},
                      "provenance": { "values": {}, "sources": [], "unverified": false } },
        "markdown": "# 财务\n..."
      }
    }
  ],
  "pending": ["report"],
  "failed": ["valuation"],
  "source_status": "partial",
  "reason": "六段未完成，缺：valuation、report"
}
```

- `stages[]` 必须是六段（`company / financial / forecast / valuation / risk / report`）；
  缺失的段由前端按 `pending` 补位，但整份响应的 `stages` 键本身缺失视为解析失败。
- `artifact` 为空表示该段没有产物；`artifact.document` 与 `artifact.markdown` 至少一项可用。
- 解析器同时兼容 MCP 的 `{payload: {research: {...}}}` 外壳。
- `available=false` 或 HTTP 404 → 空态；`failed[]` / `pending[]` 非空 → 部分失败态。

### 2.2 `POST /api/research/{code}/run`

```json
{ "date": "2026-10-31", "only": "valuation" }
```

响应：`{ "job_id": "job-...", "status": "queued" }`。`only` 只能是六段名之一；
服务端等价于 `ldst research <code> --date <date> --only <stage>`，只重跑该段。

### 2.3 `GET /api/research/jobs/{job_id}`

```json
{ "id": "job-...", "status": "running", "stage": "valuation",
  "progress": 0.45, "step": "估值：读取 FinanceCoffer 快照", "error": null }
```

`status ∈ queued|running|ok|degraded|failed`；`progress ∈ [0,1]`。
页面每 1s 轮询一次（与设置页任务轮询同一模式）；`ok/degraded` 后刷新整份状态回填产物，
`failed` 展示任务错误并提供「再试一次」。

**缺口说明**：以上接口当前后端未提供，请求会得到 HTTP 404。前端按
`isResearchEndpointMissing()` 区分「接口没接」与「产物不存在」，文案明确说明缺口，
不伪造数据；`?demo=1` 是显式演示开关，页面带水印。

### 2.4 `GET /api/research/{code}/kline`（已实现，QUOT-410 / DEC-055）

K 线主图数据源。三档周期 × 三档复权作为**运行时参数**透传给上游 `tencent_kline`，
聚合与复权全部由上游完成，前端不做二次计算、不补 0、不拼装。

请求：

```
GET /api/research/{code}/kline?period=daily|weekly|monthly&adjust=qfq|hfq|none&limit=N
```

| 参数 | 取值 | 缺省 | 说明 |
| --- | --- | --- | --- |
| `code` | 6 位 A 股代码，可带 `.SH`/`.SZ`/`.BJ` | 必填 | 与 §2.1 同一规范化口径；未知代码 404 |
| `period` | `daily` / `weekly` / `monthly` | `daily` | 非法值 422 |
| `adjust` | `qfq` / `hfq` / `none` | `qfq` | 非法值 422 |
| `limit` | 整数 1..500 | `120` | 非法或越界 422 |

响应（四段信封，与 §2.1 同族；HTTP 200 即使无数据）：

```json
{
  "payload": {
    "code": "600000",
    "period": "daily",
    "adjust": "qfq",
    "limit": 120,
    "market": "a_share",
    "market_label": "A 股",
    "period_label": "日线",
    "adjust_label": "前复权",
    "source": "TickHub-CN / tencent_kline",
    "fetched_at": "2026-11-05T15:36:00+08:00",
    "volume_unit": "手",
    "bars": [
      { "date": "2026-10-29", "open": 10.2, "high": 10.6, "low": 10.1,
        "close": 10.5, "volume": 725800, "amount": 76010.5 },
      { "date": "2026-10-30", "open": 10.5, "high": 10.9, "low": 10.4,
        "close": 10.8, "volume": 681000, "amount": null }
    ],
    "count": 2,
    "available": true,
    "reason": null,
    "source_detail": "tencent_kline@tencent",
    "data_source_status": "ok",
    "series": {
      "dates": ["2026-10-29", "2026-10-30"],
      "open": [10.2, 10.5], "high": [10.6, 10.9],
      "low": [10.1, 10.4], "close": [10.5, 10.8],
      "volume": [725800, 681000],
      "change": [null, 0.0286],
      "ma": { "5": [null, null], "10": [null, null], "20": [null, null], "60": [null, null] }
    },
    "notes": [
      "均线按本次返回的窗口计算，窗口不足的值为空",
      "volume 单位为手（1 手 = 100 股）：上游 tencent_kline 原样透传，本接口不换算"
    ]
  },
  "data_source_status": "ok",
  "data_as_of": "2026-10-30",
  "meta": { "topic": "research_kline", "source": "ladderstock",
            "freshness": "realtime", "fetched_at": "2026-11-05T10:00:00+08:00" }
}
```

- `bars[]` 按 `date` 升序（上游返回序），每根 `date / open / high / low / close /
  volume / amount`；`amount` 上游可能为 `null`，**原样透传，不要当 0 画**。
- `available=false` 时 `bars=[]`，`data_source_status="missing"`，`reason` 给出
  上游不可达 / 无数据的原因；**HTTP 仍是 200**，不伪造、不补零。
  peer 不可达、超时、空返回都归入这一态。空态下 `series` 的各列与 `ma` 各窗口
  都是空数组，**不出现伪造的 bar**。
- `series` 是参考库 `klineView.ts`（`KlineSeries`）可直接消费的列式形状，前端不重算：
  - `change` 是**小数**（`0.0123` = +1.23%）。优先上游 `change_pct`（百分数 ÷ 100）；
    缺失时用 `close[i] / close[i-1] - 1` 推导。首根、缺前收为 `null`，**不补 0**。
  - `ma` 的键是 `"5" / "10" / "20" / "60"`，简单移动平均，**只按本次返回的 bar 序列
    计算**；窗口不足或窗口内有缺值为 `null`（口径同时写在 `notes`）。
- `volume_unit` 如实等于 `"手"`：上游 `tencent_kline` 的 `volume` 按手返回
  （TickHub-CN `crawler/store.py` 明写「Tencent's K-line sends volume in 手 …
  the warehouse column is 股」，入库才 ×100），本端点现取现回、不替上游换算。
  1 手 = 100 股，换算口径写在 `notes`。
- `market` / `market_label` / `period_label` / `adjust_label` 由固定枚举映射得到
  （`a_share` / A 股、`daily|weekly|monthly` → 日线|周线|月线、`qfq|hfq|none` →
  前复权|后复权|不复权），不查库、不联网。
- `fetched_at` 与 `meta.fetched_at` 同值（本次现取的时刻）。
- `period` / `adjust` 仍回显请求参数（`daily|weekly|monthly`），**不是**参考库的
  `day|week|month`；展示用 `period_label`。
- `data_source_status` 三态：`ok`（有数且上游 `source_status=ok`）/
  `degraded`（有数但上游降级，如备源接力） / `missing`（无数）。
  `degraded` 时 `bars` 仍然可用，前端照常画图，仅提示数据源状态。
- 本端点**现取现回**（不经研究产物落库，60s 进程内 TTL 合并并发）；`freshness`
  见 `meta`。不落第二份存储，前端拿到只存 React 内存。
- 仅 GET；响应顶层不出现 `payload`/`domain`/`fetched_at` 以外的信封键，也不含
  隐私键。禁改 `code`/`period`/`adjust`/`limit` 回显值——它们是请求参数的回显，
  联调时用来核对透传是否生效。

### 2.5 `GET /api/research/{code}/earnings-brief`（已实现，QUOT-446）

财报前瞻：一致预期 + 最近 N 期财报明细。只读本机缓存
（`consensus_forecast` / `finance_indicators` / `earnings_preview`），不联网、
不补 0；统计量全部后端算，前端不做除法。

```
GET /api/research/{code}/earnings-brief?periods=6
```

| 参数 | 取值 | 缺省 | 说明 |
| --- | --- | --- | --- |
| `code` | 6 位 A 股代码 | 必填 | 未知代码 404 |
| `periods` | 整数 1..12 | `6` | 最近 N 个报告期，越界 422 |

`payload` 关键字段：

| 字段 | 说明 |
| --- | --- |
| `consensus` | 最新一致预期快照（`as_of` / `name` / `eps_current_year` / `eps_next_year` / `analyst_count` / `consensus[]` / `history[]`）；无缓存时 `null` + `consensus_reason` |
| `earnings[]` | 按 `report_date` 倒序；每项含 `actual`（EPS/营收/净利/ROE 等原值）与同报告期 `forecast`（`earnings_preview` 最新一条） |
| `earnings[].publish_date` / `reaction_date` / `reaction_change_pct` | **当前无源**：`finance_indicators` 只回报告期、没有公告日，一律 `null` + `*_reason`，不拿报告期末或抓取日冒充 |
| `stats` | `periods` / `with_forecast` / `with_publish_date` / `with_reaction` / `latest_report_*` / `consensus_*`；统计句所需数字都在这里 |
| `data_as_of` | `max(consensus.as_of, 最新报告期)`；全缺时 `null` |

`data_source_status`：有一致预期或有任一报告期 → `ok`；两者都没有 → `missing`。

### 2.6 `GET /api/research/{code}/event-study`（已实现，QUOT-446）

事件回看：六类事件的日期序列 + 每个事件对沪深300（`000300`）的窗口超额收益。
交易日历取本地 `index_daily`；个股收盘取 `daily_k`。每事件窗口固定为
`close[end] / close[start] - 1`，`excess_return = stock_return - benchmark_return`。

```
GET /api/research/{code}/event-study?sources=holder_trades,dragon_tiger&window_before=5&window_after=20
```

| 参数 | 取值 | 缺省 | 说明 |
| --- | --- | --- | --- |
| `code` | 6 位 A 股代码 | 必填 | 未知代码 404 |
| `sources` | 逗号分隔：`holder_trades` / `dragon_tiger` / `earnings_preview` / `institution_survey` / `share_buyback` / `lockup_calendar` | 全部六类 | 未知类型 422 |
| `window_before` / `window_after` | 整数 0..60 | `5` / `20` | 事件日前后的交易日数，越界 422 |

`payload` 关键字段：

| 字段 | 说明 |
| --- | --- |
| `event_types[]` | 每类含 `dataset` / `label` / `count` / `dates`（去重升序）/ `events[]`（`date` + `detail` + `window`）/ `sample` |
| `window` | `start` / `end` / `stock_return` / `benchmark_return` / `excess_return` / `reason`；窗口不齐或缺价时逐项 `null` + `reason`，不估算 |
| `sample` | `required=8`、`window_available`、`status`、三个均值、`reason`；**可计算窗口 < 8 时 `status=insufficient_sample` 且三个均值恒为 `null`**，统计句由后端给数 |
| `counts` | 各数据集事件条数（同一公告日多行会各算一条，`dates` 去重） |
| `trading_days` / `benchmark_rows` | 本地 000300 交易日历长度与原始行数 |
| `data_source_status` | 无基准日历 → `missing`；有可计算窗口 → `ok`；有事件但窗口全不可算 → `partial` |

空事件类型的 `reason` 区分「未同步」与「上游真实为空」（读最近一次同步运行状态）。
`data_as_of` 为本地 000300 最后交易日。

## 3. 个股深挖页结构

| 区块 | 数据来源 | 降级表现 |
| --- | --- | --- |
| 公司画像 | `company.facts`：`industry` / `listed_board` / `main_business` / `controlling_shareholder` / `employees` + `narrative` | 缺失字段显示 `—`，不推断 |
| 财务趋势 | `financial.facts` 当期指标 + `*_series` 历史序列 | 无 `*_series` 时显示「没有历史序列」，不拼趋势线 |
| 盈利预期 | `forecast.facts`：`eps_*` / `net_profit_growth` / `institution_count` | 降级横幅；缺失项 `—` |
| 估值分位 | `valuation.facts` 中键名含 `percentile` / `分位` 的 0–100 值；复用 `ValuationBasisPanel` | 无分位时明确「当前快照未提供估值分位」 |
| 风险清单 | `risk.facts` 三个指标 + `risk.ai.risks`（字符串或 `{title|message|text}`） | 无结构化风险时给空态，不用叙述臆造 |

右侧同屏展示 `StageRail` 摘要，点击跳转到报告页对应段。

## 4. 六阶段报告页

- 左栏（仅 `desk` 起 208px 固定侧卡，窄屏折叠为顶部横排）：六段序号、标题、状态点与文字标签。
  状态：`ok` 完成 / `degraded` 降级 / `failed` 失败 / `pending` 待跑；运行中显示脉冲与进度条。
- 右栏：当前段 `<n>. <title>`、状态 Badge、`mode` / `model` 元信息、`来源（n）` 按钮与正文。
- 正文由 `MarkdownView` 渲染，只认后端会写的窄语法（标题 / 列表 / 引用 / 分隔线 / 段落）。
- 角标 `[n]`：n 为 `sources[]` 列表序号；只有 `verified=true` 的来源可点开抽屉。
  `verified=false` 不进入角标索引，正文对应位置由 `〔未核实〕` 兜底。
- 来源抽屉四件套：`endpoint` · `as_of` · 取数时刻 / 原始值 · `url`；缺失显示 `—`，
  `url` 仅接受 `http(s)` 外链。抽屉同时列出未核实来源并解释其为什么不能当依据。
- 失败段：`ErrorBlock`「本段生成失败」+ `failure.message/kind/action` + 「重试本段」+ 等价命令。
  页面不渲染失败段的假内容。
- 待跑段：空态 +「运行本段」，同样给出 `--only <stage>` 等价命令。

## 5. 五态与演示参数

五态与全站一致（`PageShell` / `resolveState` / `DEMO_STATE_LABEL`）：

| 状态 | 触发条件 | 页面表现 |
| --- | --- | --- |
| `normal` | 六段齐备 | 正常渲染，摘要「六段齐备 · 更新于 …」 |
| `partial` | `failed[]` 或 `pending[]` 非空 | 顶部横幅「N 段未完成」，可单段续跑 |
| `empty` | `available=false` 或 HTTP 404 | 空态 + 生成命令；404 时文案为「接口未接入」 |
| `loading` | 首次取数中 | 骨架屏（`ScreenSkeleton`） |
| `error` | 非法代码 / 超时 / 解析失败 / 5xx | `ErrorBlock`，附原因与错误码；可重试 |
| `offline` | `ApiError.kind === 'not-connected'` | 离线横幅 + 空态；不用缓存冒充新数据 |

演示参数（仅 `?demo=1` 生效；演示源绝不自动兜底）：

| 参数 | 值 | 作用 |
| --- | --- | --- |
| `demo` | `1` | 使用内存演示源，页面显示「演示数据」水印 |
| `state` | `normal` / `partial` / `empty` / `error` / `loading` / `offline` | 强制态；`partial` / `empty` 同时切换演示变体 |
| `variant` | `full` / `partial` / `empty` / `all-unverified` | 演示数据形态（优先级高于 `state` 映射） |
| `verify` | `none` | 等价于 `variant=all-unverified`（全段零来源，正文全部〔未核实〕） |
| `stage` | 六段名 | 报告页初始选中的段 |
| `date` | `YYYY-MM-DD` | 覆盖演示日期（默认 `2026-10-31`） |

真实模式下 `?state=` 也可用于演练（与全站一致），但页面会隐藏真实数据，避免强制态泄露。

## 6. 主题、响应式与无障碍

- 只用 `src/components/ui` 原子与 Tailwind 语义令牌；不使用 legacy class（`.card` / `.badge-*` 等）。
  `〔未核实〕` 使用既有 `bg-warn-soft text-warn`，不新增调色板。
- 深浅主题均由 CSS 变量驱动，页面未写死颜色。
- 1280 起报告页为「左段导航 + 右正文」；1024–1279 与移动端为单栏、无横向滚动；
  正文与来源地址 `break-words` / `break-all`。
- 状态不只靠颜色：每个状态点旁都有文字；进度条带 `role="progressbar"` 与 `aria-valuenow`；
  当前段 `aria-current="step"`；抽屉复用 `Drawer`（焦点陷阱、Esc 关闭、归还焦点）。
- 角标是 `<button>`，`aria-label` 形如「查看来源 1：financecoffer」；来源抽屉标题固定
  「来源 · <段名>」。

## 7. 已知缺口与下一步

1. **后端接口缺失**：`/api/research/{code}` / `/run` / `/jobs/{id}` 三个只读接口尚未合入；
   需后端按 §2 契约暴露，或先以 MCP `research_status` + 任务接口桥接。前端无需改字段即可切换。
   K 线主图不在此列——`/kline` 已随 QUOT-410 上线（§2.4），可直接联调。
2. **续跑接口错误语义**：`POST /run` 的 409（已有任务）/ 422（参数）分支待后端定义；
   前端目前统一走 `describeResearchError`，接口上线后可补专门文案。
3. **估值分位**：依赖 FinanceCoffer 快照提供 `pe_percentile_5y` / `pb_percentile_5y`
  缺失时页面明确留白。
4. **日志/导出**：报告导出 PDF、段级审计结果（`--check`）展示不在本页范围。

## 变更记录

- v0.4（2026-11-05，QUOT-428）：§2.4 载荷补齐 `series`（列式 OHLCV + `change` +
  `ma` 5/10/20/60）、`volume_unit`、市场/周期/复权标签与 `notes`；明确 `change` 为小数、
  首根不补 0，成交量单位如实为手（1 手 = 100 股，不换算）。
- v0.3（2026-11-05，QUOT-410 / DEC-055）：新增 §2.4 K 线图端点契约
  （`GET /api/research/{code}/kline`，三档周期 × 三档复权运行时透传给 `tencent_kline`）；
  §2 标注各接口实现状态（§2.4 已上线，§2.1–2.3 仍为前端提案），§7 缺口清单同步。
- v0.2（2026-11-01，QUOT-300）：新增 `/research` 入口页（`ResearchEntryScreen`）与侧栏「个股深挖」
  一级入口。此前两页只有路由、没有任何入链，用户必须手打带代码的 URL 才能进；
  导航与路由表的一致性由 `frontend/tests/nav-routes.test.tsx` 双向断言。
- v0.1（2026-10-31）：两页路由、六段导航、来源抽屉、失败段续跑、五态矩阵与
  只读接口契约定稿；记录后端接口缺口与演示参数。
