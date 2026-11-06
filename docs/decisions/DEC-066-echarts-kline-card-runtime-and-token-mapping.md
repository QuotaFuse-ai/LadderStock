# DEC-066 K 线卡交付口径：ECharts 组合、缩放与缺失值映射

- 日期：2026-11-06
- 提出：产品经理（QUOT-440，父任务「按 vibe-astock 整体复刻我的股票三模块」）
- 状态：已采纳（对应实现 QUOT-429 / QUOT-449，代码已合入 `origin/main`）
- 编号：本决策任务书原定 `DEC-057`。开工扫描时该号已被
  `DEC-057-echarts-runtime-and-token-mapping.md`（QUOT-429，已批准）占用，
  按 DEC-045 §2 顺延为 `DEC-066`。
  **本决策不替代 DEC-057**：运行时、按需注册与令牌映射仍以 DEC-057 为准；
  本文只补齐「K 线卡最终长什么样、数据怎么映射」的交付口径。
- 关联：DEC-057（ECharts 运行时与令牌）、DEC-023（复权 / 聚合归上游）、
  DEC-055（`GET /api/research/{code}/kline`）、DEC-060（自选七列）、
  QUOT-428（载荷 `series`）、QUOT-429（KlineCard）、QUOT-449（自选行点击接线）
- 生效版本：`docs/UI-SPEC.md` v1.19、`docs/specs/SPEC-RESEARCH-PAGES.md` v0.5

## 1. 背景

DEC-057 已经定下「K 线改 ECharts、颜色只走令牌、前端不重算指标」，但那张卡在本仓的
最终形态还有几个必须钉死的交付细节：画几条线、成交量在哪、缩放怎么触发、多高、
空值在 ECharts 数据位里到底写什么。这些细节如果不写清楚，后续页面接线会各自发挥，
出现「同一只票两种画法」。

## 2. 决定

### 2.1 图形组合（与参考库 `klineView.ts` 对齐）

- **蜡烛**：ECharts `candlestick`，`barMaxWidth: 14`；
  涨为 `--ldst-up`、跌为 `--ldst-down`（A 股红涨绿跌），边框同色；
- **均线 4 条**：`MA5 / MA10 / MA20 / MA60`，来自后端 `series.ma` 的
  `"5" / "10" / "20" / "60"`，按数字升序画；颜色依次取
  `--ldst-chart-ma-1..4`；后端少给哪条就少画哪条，前端不补算；
- **成交量副图**：`bar`，`xAxisIndex: 1` / `yAxisIndex: 1`，`barMaxWidth: 14`，
  按当根涨跌取红 / 绿，`opacity: 0.7`；
  没有 `volume_unit` 或成交量全空时**不画副图**，只保留一个 grid；
- **双 dataZoom**：`inside` + `slider`；有成交量时两者都作用于 `[0, 1]`；
  `inside` 只允许 **Ctrl + 滚轮**缩放（`zoomOnMouseWheel: 'ctrl'`，
  `moveOnMouseWheel: false`）；`slider` 高 18、距底 8；
- **默认可见根数**：日线 120、周线 104、月线 120；
- **高度 420**（`KlineCard` 传入）；宽度跟随容器，`ResizeObserver` 自适应。

### 2.2 缺失值：数据位 `"-"`，文本 `—`

- ECharts 数据位：缺失写 `"-"`。ECharts 会把 `null` 当 0，画出贴底假柱，
  所以 OHLC / MA / 成交量都必须显式映射为 `"-"`；
- 卡内文本（报价条、提示框、脚注）：缺失写 `—`；
- 后端 `null` 原样进入映射层，前端不补 0、不插值、不回填前值。

### 2.3 取数与交互

- 数据只来自 `GET /api/research/{code}/kline`（DEC-055），
  `period=daily|weekly|monthly`、`adjust=qfq|hfq|none`、`limit`；
- 周期 / 复权切换用 `AbortController` 丢弃旧请求，结果连同请求键一起存，
  慢请求后到不覆盖新图；
- 复权选项只对 A 股 6 位代码显示；
- 脚注写「按住 Ctrl 滚轮（或双指捏合）缩放」、均线窗口口径与成交量单位；
- 自选页点行 → 表格下方渲染该票的 K 线卡，再点收起（DEC-060）。

### 2.4 明确不做

- 不恢复 / 不扩展 uPlot 自绘蜡烛作为交付标准（PR #94 的代码可保留，但不算验收）；
- 不在前端算复权、均线、涨跌幅；
- 不为 B 股 / 港股 / 美股做复权分支；
- 不在本版把 K 线卡塞进持仓页或其它屏（当前挂载点只有自选行点击）。

## 3. 理由

- **一套画法**：参考库已经验证「蜡烛 + 4 均线 + 成交量 + 双缩放」是可读的最小组合，
  自创布局会让用户在两处看到不同图形；
- **Ctrl 滚轮而不是直接滚轮**：页面本身要滚动，直接劫持滚轮会让用户卡在图表里；
- **空值必须显式映射**：这是 ECharts 的已知行为，写进决策避免每张图重新踩坑；
- **令牌而不是色板**：深浅主题切换必须只改一次 CSS 变量，DEC-057 已定，本文沿用。

## 4. 代价与风险

- **卡片信息密度高**：420 高度 + 双 grid 在小屏上会压缩成交量副图；
  当前按参考库固定值交付，响应式只做宽度，不做高度自适应。
- **MA 窗口依赖后端**：后端只按本次返回的窗口算均线，`limit` 太小会让长均线全空；
  这是契约行为（`notes` 已写明），页面不提示「请调大 limit」。
- **成交量单位是「手」**：上游原样透传，不换算成股；用户若按股理解会差 100 倍，
  已在脚注与 `notes` 写明。
- **只有一次挂载**：持仓页 / 研究页尚未复用该卡；后续复用需另立决策，避免隐式扩散。

## 5. 影响文件

- `frontend/src/charts/EChart.tsx`（按需注册，见 DEC-057）
- `frontend/src/charts/klineView.ts`（option、空值映射、dataZoom）
- `frontend/src/charts/cssVar.ts`（`--ldst-chart-*` 读取）
- `frontend/src/components/KlineCard.tsx`（高度 420、切参、脚注）
- `frontend/src/screens/my-stocks/WatchlistPanel.tsx`（行点击挂载）
- `frontend/src/styles/tokens.css` / `frontend/tokens.json`（6 个令牌）
- `frontend/tests/kline-view.test.ts`、`frontend/tests/kline-card.test.tsx`、
  `frontend/tests/my-stocks-screen.test.tsx`
- `docs/UI-SPEC.md` §3.11 / §7.1、`docs/specs/SPEC-RESEARCH-PAGES.md` §2.4 / §8

## 6. 生效版本与验收

- 生效：QUOT-429 合入起，QUOT-449 完成自选页挂载；文档随 `UI-SPEC` v1.19 登记。
- 验收：
  - 渲染 1 组蜡烛 + 4 条 MA + 成交量副图 + 双 dataZoom；
  - Ctrl + 滚轮缩放生效，普通滚轮仍滚动页面；
  - 卡高 420；
  - 空值在数据位为 `"-"`、文本为 `—`，无关 0 假柱；
  - 主题切换后颜色随令牌更新，业务代码无 hex / rgba 字面量；
  - 自选点行出现 K 线卡，再点收起。
