# QUOT-553：连板梯队 CH-02 柱图 x 轴重复类目修复（uPlot splits 强制数据索引）

状态：待验收（PR + CI 证据见 PR 评论）
范围：呈现层 only——只改 `frontend/src/charts/Chart.tsx`；不动 `home/`、`settings/`，
不改后端聚合、不重算指标、不改 `LadderLevel` 数据形状。

## 根因（与 issue 只读定位一致，实机复现）

- x 轴是类目索引（0..N-1），uPlot 按画布宽度自动撒**小数**刻度；
  `values` 用 `Math.round` 取标签，多个小数刻度折叠到同一索引 → 标签重复
  （实机 2026-11-10：1板×2 / 2板×4 / 3板×4 / 7板×3），多余刻度位没有柱。
- 柱 `align: 1`（左缘对齐刻度）+ x 量程贴边 [0, N-1]：首柱被 y 轴裁掉一半，
  **末柱（7板=1）整体落在画布外不可见**。
- 小值柱在大量程下只有 ~4px（7板=1 / 量程 74），几乎不可见。

## 修复（`frontend/src/charts/Chart.tsx`）

| 点 | 改法 |
|---|---|
| BarChart x 刻度 | `splits: () => xs`——刻度数=类目数，每板级一个标签一根柱，不抽稀 |
| BarChart x 量程 | `range: [min-0.5, max+0.5]`——左右各留半个类目位，首尾柱与标签不被裁 |
| BarChart 柱身 | `align: 0`——柱身居中于类目刻度，柱与标签一一对应 |
| 小值柱 | `hooks.draw`：柱高 < 6 CSS px 时柱顶如实标家数（`palette.text`，canvas 像素按 `uPlot.pxRatio` 换算）；不改数据、不抬高柱身 |
| LineChart x 刻度 | 同根因收敛：`indexSplits` 钉在整数索引，按 `u.bbox.width / uPlot.pxRatio` 步长抽稀（每刻度 ≥48px），标签唯一不重叠 |
| 标签取值 | 两图 `values` 改为整数索引直取 `points[value]`，不再 round |

## 验证

- 实机（5950 后端 + 5199 取证代理，`visual-shot.mjs`）：
  - before：`/market?tab=ladder&date=2026-11-10` 标签重复、末柱不可见（截图随评论附件）。
  - after：1板 / 2板 / 3板 / 7板 各一次，4 根柱居中对齐，7板=1 柱可见且柱顶标「1」；
    暗/亮双主题、1280px 视口零横向溢出；demo 态 5 板级（1板..5板）同样唯一对齐。
  - CH-01 折线 after：刻度为整数索引抽稀（10-30 / 11-03 / 11-05 / 11-09），无重复。
- `frontend/tests/quot-553-chart-splits.test.tsx` 8 例：刻度=类目索引、标签唯一、
  量程半格留白、align 0、小柱标注（含 2x 高分屏 canvas 像素）、单板级不退化、
  折线宽/窄画布刻度行为。
- `npx tsc --noEmit` 0 错；`npx vitest run` 102 files / 1007 passed；`npx vite build` ok。

## 规范同步

- `docs/UI-SPEC.md` v1.22：§3.6 图表口径补「x 轴刻度钉在数据索引」条 + 变更记录。

## 红线核对

- 只改 `frontend/src/charts/Chart.tsx`（产品代码）；新增测试与文档按仓库惯例。
- 未删断言 / 未 sleep / 未提 timeout / 未 skip；未改 `frontend/src` 迁就测试。
- 图表单一实现，未写第二份 Chart；密钥 / 连接串零入库。