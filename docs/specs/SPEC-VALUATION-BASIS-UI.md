# SPEC-VALUATION-BASIS-UI 估值口径切换与展示

- 版本：v1.0
- 日期：2026-10-30
- 归属：前端架构
- 实现：`frontend/src/api/financecoffer.ts`（读侧客户端）、`frontend/src/components/valuation/ValuationBasisPanel.tsx`（展示面板）
- 测试：`frontend/tests/financecoffer-api.test.ts`、`frontend/tests/valuation-basis-panel.test.tsx`
- 关联：`docs/UI-SPEC.md`（界面规范）
- 口径真源：FinanceCoffer 读侧 `GET /v1/structured/{symbol}`；本文不复制任何 PE / PB / ROE 公式

## 0. 一句话

LadderStock 只负责「让用户选口径 + 如实展示服务端结果」：默认 `snapshot`，切换即带
`valuation_basis` 重新取数；界面显示响应回显的 `basis` / `default_basis` / `available_bases`；
缺失就是「暂无此数据」，未构建的口径单独说明，**任何情况下都不回落到另一口径的数字**。

## 1. 落点结论

### 1.1 R8 是否存在

**不存在。** 前端路由只有 `/` `/market` `/watch` `/review` `/journal` `/validate` `/settings`
（`frontend/src/App.tsx`），没有个股深挖页 / 研究报告页；R8 以 R1、R2、R3 交付为前提，当前未交付。

### 1.2 现有六屏有没有可挂的指标位

| 屏 | 为什么不是落点 |
| --- | --- |
| 首页 | 回答「今天值不值得动手」，指标是市场情绪 / 连板高度；没有单只标的上下文 |
| 市场 | 情绪、连板、题材三段都是市场级聚合，不是个股估值 |
| 盯盘 | 盘中信号表按标的逐行，但语义是「3 分钟阈值触发」；逐行拉 PE/PB/ROE 会把 5 秒级盯盘变成 N 次基本面请求，且与「当前是否触发」无关 |
| 复盘 | 市场级五段复盘，指标卡只有情绪温度 / 晋级率 / 断层 |
| 日志 | 本机交易记录与聚合统计，隐私边界独立，不接外部行情 |
| 验证 | 明日条件与收盘对账，是文本条件，不是指标卡片 |

结论：**六屏都没有 PE / PB / ROE 的展示位**。按「不造假页面」的边界，本版交付
「FC 只读客户端 + 面板组件 + 本文页面级规格」，**不新增路由、不挂假入口**。

### 1.3 第一版挂到哪里

挂载点是 **R8 个股深挖页的「估值」区块 / 六段研究报告的 valuation 段**（同一组件两处复用）：

- 个股深挖页：标的名与代码下方、走势图之后，独立「估值」卡；
- 研究报告页：`valuation` 段正文之后、来源列表之前，横向通栏。

R8 落地时只需：

```tsx
<ValuationBasisPanel symbol={code} />
```

组件不依赖页面上下文、不读全局 store；`symbol` 为空时显示空态、不发请求。

## 2. 读侧数据契约（FinanceCoffer）

### 2.1 请求

```http
GET /v1/structured/{symbol}?valuation_basis=snapshot|reference
```

- `valuation_basis` 是闭集，缺省时服务端按宽表 `default_basis` 返回；
- 本组件**总是显式带参数**：初始 `snapshot`，用户切换后带所选值，确保「点选值」与「请求值」可复核。
- 前端不直连 FC 数据库、不读宽表、不复制公式；FC 返回什么就展示什么。

### 2.2 响应回显

| 字段 | 含义 | 前端处理 |
| --- | --- | --- |
| `basis` | 本次实际生效口径 | 作为「生效口径」展示，**不**用点选值替代 |
| `default_basis` | 构建期默认口径（服务端 `FC_METRICS_PROFILE`） | 作为「服务端默认」展示，只读 |
| `available_bases` | 该标的已构建的口径 | 展示「已构建」；不含所选口径 → 未构建态 |
| `pe` / `pb` / `roe` | 所选口径的数值；缺失为 `null` | 缺失显示「暂无此数据」，不补 0、不插值 |
| `pe_source` | PE 来源（`quote.pe_ttm` / `market_cap/net_profit` / `deducted_annualized`） | 有值时作为 PE 脚注 |
| `pe_reason` / `pb_reason` / `roe_reason` | 缺失原因码 | 映射为中文原因，未知码原样显示 |
| `pe_cross` | 另一口径的对照值 | **v1 不展示**，避免用户把对照值当成当前口径结果 |
| `price_date` / `price_close` | 取价日与收盘价 | 页脚展示 |
| `report_date` | 财务报告期 | 页脚展示 |
| `quarter_basis` | 年化方式（如 `single_quarter_deducted_x4`） | 页脚展示 |

单位：PE / PB 为倍数；**ROE 为小数**（`0.152` = 15.2%），界面统一换算为百分比。

### 2.3 缺失原因码

| 码 | 文案 |
| --- | --- |
| `basis_not_built` | 该口径未构建（独立警告条 + 三指标全缺失） |
| `no_price` | 无有效报价 |
| `no_earnings` | 无净利润数据 |
| `no_deducted_earnings` | 无单季扣非净利润 |
| `no_prior_cumulative` | 无上期累计数据 |
| `unsupported_report_period` | 报告期不支持 |
| `non_positive_earnings` | 净利润非正，不计算 |
| `no_market_cap` | 无总市值 |
| `no_equity` | 无净资产数据 |
| `no_vendor_pb` | 厂商未提供 PB |
| `equity_unavailable` | 无净资产数据 |
| `not_in_reference_basis` | 该口径不产出 ROE |

未知原因码显示「原因代码 xxx」，不猜测语义。

### 2.4 三条硬规则

1. **不回落**：请求口径未构建或缺失时，数值只能是 `null`；`pe_cross` 也不得顶替。
2. **不混口径**：一次响应里的 `basis` 与实际数值来自同一口径；界面不把两次响应的数字拼在一起。
3. **不缓存跨口径**：切换即重新请求；旧请求用 `AbortController` 取消，避免慢响应覆盖新口径。

## 3. 接入方式

```
浏览器 ──GET /fc-api/v1/structured/{symbol}?valuation_basis=…──▶ 本地壳 / 反代 ──▶ FinanceCoffer
```

- 默认基址 `/fc-api`（同源，避免浏览器 CORS 与端口漂移）；`VITE_FC_API_BASE` 可覆盖为绝对地址。
- **部署前置**：本地壳或反代必须把 `/fc-api/*` 指向 FC 读侧；若改为绝对地址直连，FC 必须放行 CORS。
  未满足时组件显示「FinanceCoffer 未接入」+ 重试，不显示任何占位数值。
- 超时 8s；未接入 / 超时 / HTTP / 解析错误分别给出标题、原因与重试。
- 本组件只读，不写 FC、不改 `default_basis`、不持久化用户选择（每次进入默认 `snapshot`）。

## 4. 组件契约

### 4.1 Props

| Prop | 类型 | 说明 |
| --- | --- | --- |
| `symbol` | `string \| null \| undefined` | 标的代码；为空显示空态、不发请求 |
| `initialBasis` | `'snapshot' \| 'reference'` | 默认 `snapshot` |
| `loader` | `(symbol, basis, signal) => Promise<StructuredMetrics>` | 可注入取数；缺省走 FC 只读 REST（测试 / 其他通道用） |
| `title` | `string` | 默认「估值指标」 |
| `className` | `string` | 外层卡样式 |

### 4.2 展示规则

- **头部**：标题 + 标的一行说明；右侧口径切换（`Segmented`，`radiogroup`，方向键可切换）。
- **状态行**：`生效口径：<basis>` + `服务端默认：<default_basis>` + `已构建：snapshot / reference`。
- **未构建**：警告条「所选口径未构建」，说明不回落到其他口径；三个指标全部显示缺失。
- **回显不一致**：请求口径与响应 `basis` 不同时，警告条写明服务端实际生效口径；数值按响应展示。
- **缺失**：指标值显示 `—`，脚注「暂无此数据 · 原因」；不显示 0、空串或另一口径数字。
- **页脚**：价格日、收盘、报告期、年化方式、来源「FinanceCoffer · 服务端计算」。

### 4.3 五态

| 态 | 表现 |
| --- | --- |
| 未选标的 | 空态「未选择标的」，说明数值来自 FC，不发请求 |
| 取数中 | 三张指标骨架 + `aria-busy`，不显示上一口径旧值 |
| 就绪 | 状态行 + 指标卡 + 页脚 |
| 未构建 / 缺失 | 警告条 / `—` + 原因；仍保留状态行与页脚 |
| 取数失败 | `ErrorBlock`：标题 + 原因 + 代码 + 「重新取数」 |

## 5. R8 页面级规格

### 5.1 位置与布局

- **个股深挖页**：页头（标的名 / 代码 / 最新价）之后、走势图之后；通栏，宽度跟随主内容列。
  `≥1024px` 三列指标卡；`<1024px` 单列。切换控件在卡片右上，不与页面级操作混排。
- **研究报告页**：`valuation` 段正文之后、`## 来源` 之前；同一组件、同一参数，不另写一套。
- 页面级不得再出现第二组 PE / PB / ROE 数字；跨口径对照留给后续「对比视图」，v1 不做。
- 页面滚动定位：口径切换不改变滚动位置；切换后的加载骨架高度与指标卡一致，避免跳动。

### 5.2 文案

| 场景 | 文案 |
| --- | --- |
| 标题 | 估值指标 |
| 副标题 | `{symbol} · PE / PB / ROE 由 FinanceCoffer 服务端按所选口径计算` |
| 切换项 | `快照 snapshot` / `对标 reference` |
| 生效口径 | `生效口径：快照 snapshot` |
| 默认口径 | `服务端默认：快照 snapshot`（tooltip：构建期默认口径，页面切换不改变它） |
| 未构建 | `所选口径未构建` / `服务端尚未构建 <口径>，本次不展示任何数值，也不会回落到其他口径。` |
| 回显不一致 | `服务端生效口径为 <口径>` / `你选择的是 <口径>；数值按响应回显的实际口径展示，避免「看着是点选值、算的是另一套」。` |
| 缺失 | `暂无此数据 · <原因>` |
| 空态 | `未选择标的` / `选择一只标的后展示 PE / PB / ROE；数值由 FinanceCoffer 服务端计算，界面只做展示。` |
| 失败 | `FinanceCoffer 未接入` / `估值取数超时` / `估值响应解析失败` / `估值取数失败` |

### 5.3 无障碍与响应式

- 切换控件是 `radiogroup` + `radio`，选中项 `tabIndex=0`，方向键 / Home / End 可切换。
- 状态行 `role="status" aria-live="polite"`，切换后播报生效口径；警告/错误用 `role="alert"`。
- 状态不靠颜色区分：未构建、缺失、失败都有文字。
- 1440 / 1280 无横向滚动；1024 以下指标卡单列，切换控件允许换行到副标题下一行。

## 6. 验收

自动化（前端现有门禁）：

- `frontend/tests/financecoffer-api.test.ts`：URL 带 `valuation_basis`、基址覆盖、严格解析、
  `basis_not_built` 保留、HTTP / 网络错误分类。
- `frontend/tests/valuation-basis-panel.test.tsx`：默认 `snapshot`；切换到 `reference` 重新取数并回显
  `basis` / `default_basis`；缺失显示「暂无此数据」；未构建三指标全缺失且不显示 `pe_cross`；
  回显不一致提示；失败重试；空标的；ROE 小数换算。
- `npm run typecheck` / `npm test` / `npm run build` 全绿。

人工（R8 落地时）：真实标的切换两口径，核对界面数字与 `fcoffer query structured` 原始输出一致；
未构建口径不出现另一口径数字。

## 7. 非目标

- 不做两口径并排对比、不做 `pe_cross` 可视化（后续可做「对比视图」，需单独定口径）；
- 不修改服务端 `default_basis`，不把用户选择持久化为默认；
- 不做离线缓存、不做本地计算、不做插值；
- 不新增导航入口或占位页面；R8 未落地前不挂载到六屏。

## 8. 变更记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v1.0 | 2026-10-30 | 首版：落点结论、FC 读侧契约、组件契约、R8 页面级规格、缺失与未构建规则 |
