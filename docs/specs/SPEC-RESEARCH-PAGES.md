# SPEC-RESEARCH-PAGES 个股深挖与六阶段研究报告（前端）

- 版本：v0.1（QUOT-146 定稿：页面骨架 + 只读接口契约 + 五态矩阵）
- 日期：2026-10-31
- 归属：前端架构
- 实现：`frontend/src/screens/research/`、`frontend/src/components/research/`、
  `frontend/src/api/research.ts`、`frontend/src/lib/citation.ts`
- 关联：`SPEC-RESEARCH-PROVENANCE`（R3 产物与角标口径）、`DEC-024`（后三段只消费上游）、
  `DEC-025`（估值事实键）、`DEC-027`（来源白名单）、`UI-SPEC`（令牌与五态）

## 1. 路由与页面职责

| 路由 | 页面 | 职责 |
| --- | --- | --- |
| `/research/:code` | 个股深挖 | 公司画像、财务趋势、盈利预期、估值分位、风险清单；同时是六段的摘要入口 |
| `/research/:code/report` | 六阶段研究报告 | 左段导航 + 右 Markdown 正文 + 来源抽屉 + 失败段续跑 |

`:code` 接受 6 位数字，可带 `.SH` / `.SZ` / `.BJ` 后缀；规范化口径与后端
`research/checkpoint.normalize_code` 一致（`normalizeResearchCode`）。非法代码直接进错误态，
给出路径示例，不发请求。

两页共用 `useResearchView()`（取数 / 五态 / 演示变体）与 `useResearchRun()`（续跑与轮询）。
页面不重算指标、不补齐缺失值、不重排来源，数字与出处原样透传后端产物。

## 2. 只读接口契约（前端提案，后端尚未实现）

浏览器读不到本机文件，页面只认三个只读接口；载荷与 MCP `research_status` 同形。

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

1. **后端接口缺失**：`/api/research/*` 三个只读接口尚未合入；需后端按 §2 契约暴露，
   或先以 MCP `research_status` + 任务接口桥接。前端无需改字段即可切换。
2. **续跑接口错误语义**：`POST /run` 的 409（已有任务）/ 422（参数）分支待后端定义；
   前端目前统一走 `describeResearchError`，接口上线后可补专门文案。
3. **估值分位**：依赖 FinanceCoffer 快照提供 `pe_percentile_5y` / `pb_percentile_5y`
   （DEC-025）；缺失时页面明确留白。
4. **日志/导出**：报告导出 PDF、段级审计结果（`--check`）展示不在本页范围。

## 变更记录

- v0.1（2026-10-31，QUOT-146）：两页路由、六段导航、来源抽屉、失败段续跑、五态矩阵与
  只读接口契约定稿；记录后端接口缺口与演示参数。
