# SPEC-RESEARCH-PAGES 个股深挖与六阶段研究报告（前端）

- 版本：v0.5（定稿：页面骨架 + 只读接口契约 + 五态矩阵；v0.2 补 `/research` 入口页与导航；
  v0.3 补 K 线图端点契约 §2.4 并标注 §2 接口实现状态；v0.4 补 §2.4 的 `series` /
  涨跌幅 / 均线 / 成交量单位与标签；v0.5 补 §8 相邻只读面契约摘要与 §8.2 未落地对照表）
- 日期：2026-11-06
- 归属：前端架构
- 实现：`frontend/src/screens/research/`、`frontend/src/components/research/`、
  `frontend/src/api/research.ts`、`frontend/src/lib/citation.ts`；
  §8 摘要对应的后端在 `src/ladderstock/web/radar.py`、`src/ladderstock/web/inbox.py`、
  `src/ladderstock/core/portfolio.py`、`src/ladderstock/core/portfolio_perf.py`
- 关联：`SPEC-RESEARCH-PROVENANCE`（R3 产物与角标口径）、后三段只消费上游、
  估值事实键、来源白名单、`UI-SPEC`（令牌与五态，v1.19 §3.11 / §10）、
  DEC-052 / DEC-055 / DEC-058 / DEC-059 / DEC-060 / DEC-064 / DEC-065

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

浏览器读不到本机文件，页面只认这几个只读接口；载荷与 MCP `research_status` 同形。
实现状态：`GET /api/research/{code}`、`GET /api/research/jobs/{job_id}`、
`POST /api/research/{code}/run` 已随 QUOT-400 上线（`web/research.py`），
§2.4 K 线图随 QUOT-410 落地（DEC-055），均**可直接联调**；
§2.1–2.3 的字段是当时的前端提案，落地的响应以 `web/research.py` 实际字段为准——
若与本节不一致，以代码为准（缺口与差异见 §7）。

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

**实现状态（QUOT-400 合入后）**：`GET /api/research/{code}`、`POST /api/research/{code}/run`、
`GET /api/research/jobs/{job_id}` 已上线，正常请求不再 404。字段若与本提案有出入，
以 `web/research.py` 为准。前端仍保留 `isResearchEndpointMissing()`，用于把「接口没接」
（历史 404）与「产物不存在」分开，不伪造数据；`?demo=1` 是显式演示开关，页面带水印。

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

- `bars[]` 保持上游 `records[]` 的返回序（本层**不排序、不反转**；`tencent_kline`
  按时间升序回，所以实测是升序，但契约上这是「原样透传」而不是「本层排过序」。
  前端按 `series.dates` 原样画图）；每根 `date / open / high / low / close /
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

前端消费（`EarningsSection`，QUOT-450）：

- 一致预期取 `consensus.consensus[]` 的 `current_year` 与下一财年；区间、机构数照抄，
  `最高 ÷ 最低` 只做一次显示换算（接口未返回 dispersion）。
- 「实际」列取 `earnings[].actual`；「预期」列只认同一报告期 `forecast`，缺失写 `—`，
  **不拿当期一致预期倒填历史**。
- 发布日 / 反应日 / 当天涨跌当前无源，逐期 `—`，原因汇总进「没取到的」；
  `stats.with_reaction` 是统计句的唯一依据。
- 契约目前**没有**「此前涨跌绝对值平均 / 最近一次 ÷ 平均」两个数，所以 `with_reaction ≥ 4`
  时卡片也只写「接口未返回…本卡不在这里做除法」。这两个数需由后端补齐，前端不自行计算。
- 下一期预约披露日上游没有工具，卡片不提供该数字。
- 演示模式（`?demo=1` 或设置页开关）不读真实接口，卡片显示「演示模式不读取真实财报明细」。

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
| `window.stock_start_close` / `stock_end_close` / `benchmark_start_close` / `benchmark_end_close` | 起止交易日的两只序列收盘**原值**（QUOT-451）；只用于页面复算 `close[end] / close[start] - 1`，不参与任何均值，窗口未对齐时为 `null` |
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

1. **后端接口已合入**：`GET /api/research/{code}`、`POST /api/research/{code}/run`、
   `GET /api/research/jobs/{job_id}` 三条已随 QUOT-400 / QUOT-446 上线
   （`web/research.py` 的 `build_router()`，注册于 `web/server.py:1539`，前缀 `RESEARCH_PREFIX`），
   前端无需改字段即可联调；同族只读面另有 `/{code}/overview`、`/{code}/kline`（§2.4）、
   `/{code}/earnings-brief`、`/{code}/event-study`。
2. **续跑接口错误语义**：`POST /run` 的 409（已有任务）已定义并抛 `HTTPException`，
   触发时前端统一走 `describeResearchError` 提示；422（参数）分支仍缺专门文案。
3. **估值分位**：依赖 FinanceCoffer 快照提供 `pe_percentile_5y` / `pb_percentile_5y`
  缺失时页面明确留白。
4. **日志/导出**：报告导出 PDF、段级审计结果（`--check`）展示不在本页范围。
5. **相邻面的缺口单列**：`/api/events/timeline`、`/api/portfolio/xray`、
   `/api/portfolio/replay` 都不在当前代码里，见 §8.2 的对照表——
   它们不是「接口没接」，而是**名字本身不存在**，按 §8.1 的实际路由联调。
   （`/inbox` 页面已从该清单移除：QUOT-435 已交付，见 §8.2 末「前端消费方」。）

## 8. 相邻只读面契约摘要（「我的股票」三模块）

本节是**摘要 + 指路**，不重复完整口径：字段与算法以代码为准
（`src/ladderstock/web/radar.py`、`src/ladderstock/web/inbox.py`），决策依据在
`docs/decisions/`。写在这里的原因是个股深挖与「我的股票」共用同一条 K 线接口
（§2.4）与同一套「缺数据 = `null` + 原因」的纪律，文档必须能对上号。

### 8.1 已落地

| 接口 | 参数 | 载荷要点 | 决策 |
| --- | --- | --- | --- |
| `GET /api/portfolio` | `method=average\|fifo`（缺省 `average`） | **平面 JSON、`no-store`、回环独占**，顶层刻意避开 `holdings`/`positions`/`trades`（`PRIVATE_KEYS`）。键：`local_only` / `store` / `method` / `method_label` / `methods[]` / `today` / `price_lookup` / `daily_lookup` / `items[]` / `closed[]` / `realized[]` / `dividends[]` / `issues[]` / `ledger[]` / `legacy[]` / `migration_pending` / `migration` / `totals_cny`。`items[]` 每行 `symbol/name/shares/unit_cost/cost_total/opened_at/lots[]/realized_pnl/dividends/fees/trade_count/latest_price/change/change_pct/market_value/pnl/pnl_pct/price_reason`；4 位小数；取不到现价是 `null` + `price_reason`，不拿成本价顶替 | DEC-065 |
| `GET /api/portfolio/perf` | `days`（缺省 365，`0` = 全历史，上限 3650） | 平面 JSON、`no-store`、与 `/api/portfolio` 同一条闸门。**只读本机 `daily_k` 缓存**，不联网、不落盘。键：`as_of` / `groups[]` / `skipped[]` / `gaps[]` / `sources[]` / `notes[]`；`gaps[]` 逐条 `{symbol, reason}`；`days` 只裁走势序列，收益指标始终按全部流水。流水为空 → `groups=[]` + `notes`，不是 500 | DEC-064 / DEC-065 |
| `POST /api/portfolio/ledger` | body 只认 10 个字段：`symbol/name/type/trade_date/shares/price/fee/amount/ratio/note`，多一个 400 | `{ok, local_only, store, trade}`；`trade` 投影用 `date`（不是 `trade_date`），含 `id/migrated_from/undated/created_at` | DEC-065 |
| `POST /api/portfolio/ledger/{trade_id}/delete` | 路径参数 = 流水 id | 硬删一笔，**写前先备份**；id 不存在 404 | DEC-065 |
| `POST /api/portfolio/legacy/{position_id}/adopt` `/dismiss` | 路径参数 = 旧 `positions` 行 id | `adopt` 转成期初流水（已转过的原地替换，不留第二笔）；`dismiss` 只写 `portfolio_legacy_marks`，**不删 `positions` 行** | DEC-065 |
| `GET /api/watchlist` | 无 | 平面 JSON、`no-store`、回环独占、只 GET。顶层 `items` + `watch_count`（`watchlist` 是 `PRIVATE_KEYS`，不能出现在信封出口）；每条 `code/added_at` + 报价七列 `name/latest_price/change_pct/pe_ttm/pb/turnover_pct` 与逐条 `metrics_reason`；空态是 200 + `available=false` + `reason` | DEC-052 / DEC-055 / DEC-060 |
| `POST /api/watchlist`、`POST /api/watchlist/{code}/delete` | body 只认 `code` | 需同源来源证据（`Origin` 或 `X-LDST-Client: 1`）；add 幂等不刷新 `created_at`；delete 不在清单里 404，不静默成功 | DEC-055 |
| `GET /api/inbox` | `limit` 1..200（缺省 50） | 平面 JSON、`no-store`、只 GET。顶层**恰好** `local_only / store / items / unread / jobs` 五个键；`jobs[]` 为 `{id, label, trigger, enabled}`（读调度表，读不到是 `[]`），**没有 `last_status`**。渠道只有 `console` / `desktop`：不接 webhook、不接第三方推送、不发网络请求、没有免打扰设置 | DEC-058 |
| `POST /api/inbox/read-all`、`POST /api/inbox/{item_id}/read` | 路径参数 = 条目 id | 写面需同源证据；`read-all` 返回本次实际改写条数与剩余 `unread`（幂等）；单条不存在 404 | DEC-058 |
| `GET /api/events` | `month=YYYY-MM`（必填）、`code`（可重复）、`limit` | 这是**事件日历**，走 `LadderEnvelope`（与上面几条平面 JSON 不同）。`payload`：`month` / `window{start,end}` / `codes[]` / `summary{sections,entries,extracted_events,no_source[]}` / `sections[]` / `extracted_events[]` / `notice`。每节 `key/label/status/entries[]/note`，`status` 非「有源」时是 `reason` + `ask`；节内条目 `id/date/code/section/label/detail/source/provenance`。`no_source` 小节就是「暂无数据源」，不估算、不编日期 | DEC-018 R6 / DEC-059 |
| `GET /api/positions` | `date=YYYY-MM-DD`（缺省取缓存最后一天） | 旧的「导入视图」，与 `/api/portfolio` 的**账本视图**分开：前者读 `positions` 行，后者重放流水。`positions` 行不会被账本改写或删除。**当前前端消费方只剩首页 D 块**（`screens/home/useHomeSource.ts:52`）——`/my-stocks?tab=hold` 自 QUOT-434 起改读 `/api/portfolio`，`radar/PositionsScreen.tsx` 不再挂在任何路由上（`/positions` 只是重定向） | DEC-033 / DEC-052 读 / DEC-055 写 / DEC-065 |

**币种与市场边界（三个面同一条）**：只支持 A 股、只用人民币。
`/api/portfolio` 与 `/api/portfolio/perf` 里没有币种列、没有汇率换算，
`market="A股"` / `currency="CNY"` / `label="人民币 · A 股"` 是固定值；
B 股（`^(?:900|200)\d{3}$`）在账本写入即拒。已知缺口：`POST /api/positions`
与自选写入**没有**做 6 位代码 / B 股校验（DEC-065「已知缺口」条）。

**缺数据的统一表现**：值是 `null` 并同处给 `reason`（`price_reason` /
`metrics_reason` / `gaps[]` / 节内 `reason`），页面显示 `—`。任何面都不补 0、
不插值、不倒填、不自采。

### 8.2 未落地（当前代码里不存在，不要按这些名字联调）

| 名称 | 现状 | 实际可用的是什么 |
| --- | --- | --- |
| `GET /api/events/timeline` | **没有这个路由**。radar 路由表里只有 `/events`（`radar.py:1546`），没有 `timeline`，也就没有任务书里设想的 `overdue` 字段 | `GET /api/events?month=&code=&limit=`（§8.1）；「逾期 / overdue」这个概念在代码里不存在 |
| `GET /api/portfolio/xray` | **不存在**。radar 里的 portfolio 家族只有根路径 `/api/portfolio`、`/api/portfolio/perf`、`POST /api/portfolio/ledger`、`POST /api/portfolio/ledger/{id}/delete`、`POST /api/portfolio/legacy/{id}/adopt`、`POST /api/portfolio/legacy/{id}/dismiss` 六条 | 持仓「集中度 / 行业穿透」类需求由 QUOT-437 承接，落地前不要写进页面 |
| `GET /api/portfolio/replay` | **不存在**。重放是库内函数 `replay_portfolio(trades, method)`，其结果已经并进 `GET /api/portfolio` 的 `items[] / closed[] / realized[] / dividends[] / issues[]`，没有独立 HTTP 路由 | `GET /api/portfolio`（§8.1）；逐日回放子页由 QUOT-438 承接 |

**前端消费方（QUOT-431 / 434 / 435 合入后，`origin/main` @ `6db2cd7`）**：§8.1 的账本、自选、收件箱三条面**都有真实消费方**，本仓不存在「后端已上线、前端没有屏」的悬空接口——

- `/api/portfolio` → 持仓标签 `screens/my-stocks/PortfolioPanel.tsx:95`（`api/radar.ts:1796-1801`）；另有一条轻用量：自选标签的「持有」徽标只取 `items[].symbol`（`api/radar.ts:1343-1352`，失败只丢徽标，不拖累自选表格）。
- `/api/portfolio/perf` → 收益子页 `screens/my-stocks/PortfolioPerfTab.tsx:44`（`api/radar.ts:1806-1807`）。
- 四条写面 → 记一笔 `PortfolioEntry.tsx:46`、删除 / 迁移确认 `PortfolioPanel.tsx:129-135`。
- `/api/watchlist` 读与两条写 → `screens/my-stocks/WatchlistPanel.tsx:89`，另有个股深挖的加自选按钮 `components/research/ResearchSymbolBar.tsx:71`。
- `/api/inbox` 读与两条标已读 → `screens/my-stocks/InboxScreen.tsx:142`，取数层是单独一份 `frontend/src/api/inbox.ts`（三条路由、字段解析、错误口径都在那里，不并进 `radar.ts`）。
- 版面与验收见 `docs/UI-SPEC.md` §3.11 / §3.21 / §11.11 / §11.21。

## 变更记录

- v0.5（2026-11-06，QUOT-440）：文档收口，**只改文档、不改代码**。新增 §8 相邻只读面
  契约摘要：§8.1 逐条列出已落地的 `GET /api/portfolio`、`GET /api/portfolio/perf`、
  `POST /api/portfolio/ledger|ledger/{id}/delete|legacy/{id}/adopt|legacy/{id}/dismiss`、
  `GET /api/watchlist` 及其写面、`GET /api/inbox` 及其写面、`GET /api/events`、
  `GET /api/positions` 的参数与载荷要点，并指向 DEC-052 / DEC-055 / DEC-058 / DEC-060 /
  DEC-064 / DEC-065；明写 A 股 + 人民币唯一边界（`B_SHARE` 写入即拒、无币种与汇率列）
  与「缺数据 = `null` + 同处 `reason`，页面显示 `—`」的统一表现。§8.2 是未落地对照表：
  `GET /api/events/timeline`（含任务书设想的 `overdue` 字段）、`GET /api/portfolio/xray`、
  `GET /api/portfolio/replay` 在当前代码中**路由不存在**，按这些名字联调必然 404。
  **本节写作期间 QUOT-434 / QUOT-435 合入 `origin/main`，§8.1 / §8.2 与 §7 按合入后的代码
  改写过一次**：原本登记的「`/inbox` 前端没有屏」「`/api/portfolio/perf` 没有任何前端消费方」
  两条已失效并删除，改为 §8.2 末的**前端消费方清单**（`PortfolioPanel` / `PortfolioPerfTab` /
  `WatchlistPanel` / `InboxScreen` + `api/inbox.ts`）；`GET /api/positions` 行补注「消费方只剩首页
  D 块，持仓标签已改读 `/api/portfolio`」；§7 第 5 条同步摘掉 `/inbox`。xray / replay 仍缺，
  页内子标签固定两项的理由写在 §8.2。
  §2.4 更正一处与代码不符的表述：`bars[]` 是**保持上游返回序、本层不排序不反转**，
  不是「本层按 `date` 排升序」（`src/ladderstock/web/research.py:1128`）。§7 加第 5 条
  指向 §8.2。版本号说明：§2.5 / §2.6 由 QUOT-446 / QUOT-450 / QUOT-451 合入时未追加
  变更行、顶部版本行仍停在 v0.4（历史漂移），本版把它补齐为 v0.5，旧内容原文未改。
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
