# DEC-057（已批准）K 线改 ECharts：按需注册、令牌色板与「前端不重算指标」

- 日期：2026-11-05（批准：2026-11-05）
- 提出：前端架构师（QUOT-429，父任务「按 vibe-astock 整体复刻我的股票三模块」）
- **状态：已批准（QUOT-429 任务书即批准口径：只允许 ECharts、颜色只来自
  `tokens.css` → `cssVar.ts`、均线与涨跌幅由后端算好）**
- 编号：`DEC-057`。按 DEC-045 §2 取值：`docs/decisions/` 已扫到的最大号为 055
  （DEC-055 两处同名并存，见 §4）；QUOT-426 已按任务书占用 056 / 058（账本 schema
  与收件箱表），本单取 057。
- 关联：DEC-055（`GET /api/research/{code}/kline` 端点与运行时透传）/
  DEC-023（复权与聚合口径归上游）/ QUOT-428（K 线载荷补齐 `series`）/
  `docs/UI-SPEC.md`（令牌与图表规范）

## 1. 背景

QUOT-411 用 uPlot 自绘蜡烛（PR #94）已交付，但用户明确不接受自绘方案，要求照参考库
`vibe-astock` 的 `lib/klineView.ts` 复刻：ECharts `candlestick` + 4 条 MA line +
独立成交量副图 + 双 dataZoom。参考库另有一套 `chartPalette.ts` 色板，本仓已有一套
`tokens.css` / `cssVar.ts` 令牌体系，直接搬参考色板会变成两套并行色板。

## 2. 决定

### 2.1 ECharts 固定 5.6.0，按需注册

- `frontend/package.json` 固定 `"echarts": "5.6.0"`（不带 `^`），lockfile 锁定；
- `charts/EChart.tsx` 只注册用到的图表与组件（Line / Bar / Heatmap / Candlestick +
  Grid / Tooltip / Legend / MarkLine / VisualMap / DataZoom + CanvasRenderer），
  禁止 `import * as echarts from "echarts"`；
- 容器用 `ResizeObserver` 跟随侧栏 / 窗口变化，卸载时 `disconnect + dispose`；
  `setOption(option, true)` 整份替换；初始化或渲染抛错时降级成一句
  「这张图没有画出来」，不把异常抛给 React 渲染树。

### 2.2 颜色只有一条链：`tokens.css` → `cssVar.ts`

新增令牌（深浅两套）：

| 令牌 | 用途 | 暗色 | 亮色 |
|---|---|---|---|
| `--ldst-chart-tooltip-bg` | 提示框底色 | `#1b2230` | `#ffffff` |
| `--ldst-chart-tooltip-text` | 提示框文字 | `#e8ecf2` | `#141a22` |
| `--ldst-chart-ma-1` | MA5 灰 | `#8b8f98` | `#6b7280` |
| `--ldst-chart-ma-2` | MA10 琥珀 | `#d4a017` | `#8a5a00` |
| `--ldst-chart-ma-3` | MA20 紫 | `#a855f7` | `#7c3aed` |
| `--ldst-chart-ma-4` | MA60 蓝 | `#3b82f6` | `#1d4ed8` |

- 涨跌直接复用 `--ldst-up` / `--ldst-down`（A 股红涨绿跌），不新增 `--chart-bar-*` 的
  平行语义；
- MA 色相沿用参考库 `MA_COLORS`（灰 / 琥珀 / 紫 / 蓝），但必须落成令牌：避开红绿，
  深浅底都按 ≥3:1 的图表元素对比度取值；
- `charts/cssVar.ts` 是唯一读取这些变量的模块，`ChartPalette` 新增
  `rise / fall / text / tooltipBg / tooltipText / ma[]`；新变量都在该文件写兜底值，
  测试环境（Node / jsdom 无样式表）与 `tokens.css` 暗色值一致；
- `useChartPalette()` 监听 `<html data-ldst-theme>`，主题切换后重算 option 重绘。

### 2.3 前端不重算指标

`charts/klineView.ts` 的 `parseKline` 只消费后端 `payload.series`：
`dates / open / high / low / close / volume / change / ma{5,10,20,60}`。
涨跌幅是小数（`0.0123 = +1.23%`），均线是后端按本次窗口算好的简单移动平均。
前端不做聚合、不推涨跌幅、不补均线——与 DEC-023「口径归上游」同一条原则。

### 2.4 缺失值的两种写法

- 文字（报价条、提示框）：`—`（`lib/format` 的 `MISSING`）；
- ECharts 数据位：`"-"`。ECharts 把 `null` 当 0 处理，会画出一根贴底的假柱，
  所以空值必须显式映射成 `"-"`。

### 2.5 卡片与取数

- `components/KlineCard.tsx`：日 / 周 / 月 + 前 / 后 / 不复权（复权仅 A 股 6 位代码
  显示），切参数 `AbortController` 丢弃旧请求，结果连同请求键一起存，
  慢请求后到不会覆盖新图；高度 420；报价条、`notes`、来源与取数时刻照参考库；
- 取数走 `api/research.ts` 的 `loadKline`（`GET /api/research/{code}/kline`），
  通过 `KlineLoader` 类型留出注入点，测试与组件自证页不联网；
- 组件自证页（`ui-kit.html`，不进生产包）用 fixture 演示数据证明蜡烛 / 4 MA /
  成交量 / 双 dataZoom 在深浅两套令牌下可渲染；fixture 明确标注非真实行情。

### 2.6 明确不做

- 不保留 / 不扩展 uPlot 的 K 线自绘实现（PR #94 的代码可留给别的图，但不是交付标准）；
- 不引全量 echarts、不做第二套图表色板、不在前端算复权；
- 不为 B 股 / 港股 / 美股做复权假分支（本期只覆盖 A 股红涨绿跌）；
- 本期不把卡片挂到具体屏幕：Stage 3 的自选页 / 持仓页任务负责接线。

## 3. 影响与风险

- 生产包本次增量 0：卡片尚未被 App 依赖图引用，Vite 会把 ECharts 摇掉；
  Stage 3 挂载后才会把按需注册的子集打进主包。按需注册是控制这块增量的前提；
- 新依赖 1 个（`echarts`），无网络 / 存储副作用；
- `frontend/tokens.json` 与 `src/styles/tokens.css` 需同时更新：令牌生成器的
  单一真源 `docs/tasks/TASK-UI-REDESIGN/tokens.json` 在本仓历史上已被移除，
  `gen-tokens.mjs --check` 因此无法运行（见 §4），本单按现有派生物手写同步；
- 后端 `series` 未合并（QUOT-428）时，`parseKline` 会明确报「载荷里没有 series」，
  不拿 `bars` 前端补算——联调时按 QUOT-428 契约对齐即可。

## 4. 已知遗留

- `docs/decisions/` 存在两个 DEC-055 文件（kline 端点 / 自选写面），
  按「只增不改」不回头改号；
- `docs/tasks/TASK-UI-REDESIGN/tokens.json` 已被历史清理删除，生成器 `--check`
  在当前仓会直接失败；修生成器链路是独立任务，不在本单范围。
