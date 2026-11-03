## QUOT-306 一致性收口验收报告（QUOT-317）

- 验收人：测试工程师
- 日期：2026-11-02
- 基线 commit：`origin/main` = `801d0c8`（Merge PR #49 `fix/306-closeout`）
- 运行环境：Node v22.17.1 / npm 7.5.2 / win32 x64，工作区干净 checkout
- 原始门禁日志：`docs/tasks/TASK-QUOT-306/gate-logs/`（`build.log` / `test.log` / `check-ui.log` / `env.txt` / `commit.txt`）

结论先行：**有条件通过**。QUOT-306 的 7 条硬条件里 6 条通过，第 2 条（DataTable 收口）有一处**未收口**——`ValidateScreen.tsx:587` 的条件表体仍是手写 `ROW_GRID`，没换成 DataTable。另发现 1 个**测试不稳定（flaky）**缺陷。两条均已建修复子单挂到 QUOT-306 下。核心证据链（逐屏 after 截图 / 门禁原始日志 / 可复算口径脚本）齐全、真实、可复核，未出现「旧图充新」或「导出但没人用」。

---

## 一、7 条硬条件逐条结论

### 条件 1 — 证据：逐屏 after 截图 + before/after 对照 + 原始门禁日志
**通过。**
- 18 屏 × 暗/亮 = 36 张 `*-after-{dark,light}.png` 全部入库 `docs/screenshots/`，逐张读 PNG IHDR 确认为 **1440×900**（脚本见下「复算 2」）。36 张 `*-before-{dark,light}.png` 同批在库，before/after 对照成立。
- after 截图取自 commit `38b33f6`；逐屏比对「源文件最后提交时间 ≤ 对应 after 截图提交时间」，**18 屏全部无「图旧于码」**（复算 3）。截图由 `frontend/scripts/visual-shot.mjs` 从真跑的 dev server（5173/5199）经 headless CDP 抓取，非设计稿。
- `npm run build`（`tsc --noEmit` + `vite build`）exit 0；`npm test`（vitest run）单跑 332/332 通过。原始日志入 `gate-logs/`。
- 唯一保留：`npm test` 在整套并发下**偶发 1 条失败**（见复算 1 与条件外缺陷 F-2），非确定性。

### 条件 2 — DataTable 收口 API 缺口 + 改掉两处手写表体
**不通过（部分落地）。**
- ✅ `components/ui/Table.tsx` 补齐 `summaryRow`（`:82`，渲染于 `:161`）与 `expandable`（`:77`），closeout 在 MarketScreen reconcile 表实装 `summaryRow`（`git show 38b33f6` diff）。
- ✅ `radar/PoolScreen.tsx` 已换成 `DataTable`（`:20` 导入，`:196`/`:449` 使用），`:125` 手写表体不复存在。
- ✅ 全仓无裸 `<table>`（口径脚本 `bare-table` PASS，独立 grep 复核亦零命中，排除 Table.tsx）。
- ❌ **`ValidateScreen.tsx:587` 未收口**：条件校验项表体仍是手写 `ROW_GRID`（`:172` 定义、`:589` 表头、`:606` 行）；closeout 只把外层 `min-w-[960px]` 换成 token `min-w-table-min`，**没把这段换成 DataTable**。注：同屏的对账结果表（`:952`）已用 DataTable，`:587` 这块「可编辑条件表体」被漏掉。→ 修复子单 **QUOT-332**（F-1）。

### 条件 3 — 一致性可检查口径（脚本化，不靠眼睛）
**通过。** `frontend/scripts/check-ui-consistency.mjs` 纳入 `npm run check:ui`，六项全 PASS（复算 1 原始输出）：
| 口径 | 结果 | 复算证据 |
|---|---|---|
| bare-table 无裸 `<table>` | PASS | 排除 Table.tsx 后 grep 零命中 |
| hex 无硬编码颜色（tokens.css/cssVar.ts 除外） | PASS | 独立 grep 仅命中 `Button.tsx:19` 注释内值，被 `stripComments` 正确排除 |
| arbitrary 无任意值 class | PASS | 屏幕层 grep `(p\|gap\|w\|text…)-[…]` 零命中；结构尺寸改走 token（`min-w-table-min`=960px、`h-calendar-cell`=76px，`tailwind.config.ts`） |
| numeric 数值走 `.ldst-num` | PASS | 屏内无手写 `<td>`；`numeric:true` 仅在引用 DataTable 的文件 |
| datatable 真实引用屏数 ≥3 | PASS（15 屏） | 见复算 1 的 15 个引用清单，杜绝「导出但没人用」 |
| durations 过渡取 token 且在 24–160ms/登记例外 | PASS | `duration-fast`=120ms（区间内）、`duration-base`=180ms（抽屉/Toast，DEC-040 登记例外）；无裸 `duration-[数字]` |
- 四态（骨架/空/错误降级/正常）：由 `tests/quota-319b-screen-side.test.tsx`、`tests/radar-pages.test.tsx`「四页五态/六态」覆盖且样式同源，随整套通过。

### 条件 4 — 精致度 8 个抓手逐条结论
1. **数据密度与留白**：通过。行高/内边距走 token（`h-control`/`min-h-row`/栅格 `gap`），closeout 无新增任意值。
2. **表头与数字列对齐**：通过。DataTable 统一 `alignClass`（`Table.tsx:170-177`），数值列 `align:'right'`+`.ldst-num`；roughness-audit 记录的代码列左对齐为有意，已在 Column 注释登记。
3. **过渡（24–160ms，reduced-motion 可关）**：通过。`duration-fast`=120ms 为唯一交互档，`duration-base`=180ms 限抽屉/Toast（`ui-token-hygiene.test.ts:86` 强制）；`prefers-reduced-motion` 下两档同置 0ms（`tokens.json:527`）。
4. **图表取色与网格线**：通过。uPlot 取色只经 `charts/cssVar.ts` 解析 `--ldst-chart-*`，其十六进制为 jsdom 兜底值（口径脚本白名单），主题切换重读重绘；网格线 `--ldst-chart-grid` 弱化色同源。
5. **徽标 / 降级 / 新鲜度标识重量**：通过。`tests/quota-331-envelope.test.tsx`（12 项）验证 limit-up 徽标吃真实 freshness，非纯色通道。
6. **截断与溢出（省略号 + tooltip）**：通过。长代码/长标题 `truncate` 配 `title`/`cellTitle`；1280px 零横向溢出由 visual-shot `scrollWidth` 报告取证（脚本头注 + `-1024-` 截图组）。
7. **四态**：通过（同条件 3）。
8. **无数据不冒充**：通过。`tests/quota-319b-screen-side.test.tsx` 断言缺数写 `—`/「未取到」不写 0；B 族缺数不写 0 用例通过。

### 条件 5 — 边界：不改数据链路与接口契约、不改 NAV_SECTIONS 分组
**通过（就本视觉收口而言）。**
- closeout commit `38b33f6` 未触碰 `Layout.tsx`（`git show` 无该文件），未改数据链路/接口。
- 说明：main 上的 `e14471d`（「我的股票」导航聚合，DEC-044）确实动过 NAV_SECTIONS，但那是**独立的产品决策单**、非 QUOT-306 视觉收口范围，QUOT-306 的这条边界针对的是精修批次本身，成立。

### 条件 6 — 不新增依赖、不引 CDN/webfont
**通过。** `frontend/package.json` 本次仅 +1 行 `check:ui` 脚本，无 dependency 变化；`index.html` / `src/styles/*.css` grep 外链与 webfont 零命中（离线回环运行，DEC-006）。

### 条件 7 — disk-warden 冻结、LadderStock 侧自建最小巡检
**通过。** 巡检工具为 `frontend/scripts/visual-shot.mjs`（Node 内置 fetch/WebSocket + 本机 Chrome/Edge CDP，零第三方包），未引用 disk-warden uitour（QUOT-305 / DEC-039 冻结线未碰）。

---

## 二、追加必交项：流程 DEC 收口（2026-11-01 撞号事故）
**通过。** `docs/decisions/DEC-045-dec-numbering-and-spec-changelog.md` 已在 main，内容覆盖：开单时扫 `git ls-tree origin/main docs/decisions/` 取最大号 +1 并在单里落「已扫到的最大号」；未合并分支 DEC 不算占用、让号成本归零反向引用方；空号回填须写裁定来源（DEC-040 回填给 QUOT-308 已记）；`docs/UI-SPEC.md` 变更记录行串行合并成一条。
- **已扫到的最大号 = 045**（本单复算：`git ls-tree -r --name-only origin/main docs/decisions/` 末端为 DEC-045；本单未新开 DEC，直接引用 DEC-045，无需 +1）。

---

## 三、复算记录（可重跑）

**复算 1 — check:ui 六项原始输出**（`gate-logs/check-ui.log`，exit 0）：
```
PASS  bare-table   裸 <table> 仅允许出现在 components/ui/Table.tsx
PASS  hex          颜色只能来自 --ldst-* 变量
PASS  arbitrary    新刻度先进 tailwind.config.ts / token，不写 p-[13px]
PASS  numeric      DataTable 的 numeric 列在组件内统一套 .ldst-num
PASS  datatable    15 张屏引用
PASS  durations    hover/press/展开用 duration-fast；抽屉/Toast 的 duration-base 是登记例外
6/6 项通过
```
15 引用屏：HomeScreen, industry/IndustryDetailScreen, industry/IndustryMatrixScreen, intraday/IntradayScreen, JournalScreen, limitup/LimitUpScreen, MarketScreen, radar/PoolScreen, radar/PositionsScreen, research/ResearchDetailScreen, SettingsScreen, signals/SignalsScreen, ValidateScreen, WatchScreen, WeeklyScreen。

**复算 2 — 逐张 after 截图尺寸**：遍历 18 路由 × {dark,light} 读 PNG IHDR，全部 `1440x900`，`ALL 18 routes x dark/light present @1440x900`。

**复算 3 — 图不旧于码**：逐屏比较源文件最后提交 epoch 与 `*-after-dark.png` 最后提交 epoch，18/18 `src ≤ pic`。

**复算 4 — 门禁原始日志**：
- build（`gate-logs/build.log`）：`tsc --noEmit` 无错，`vite build` `✓ 1685 modules transformed` `✓ built in 3.68s`，exit 0（chunk >500kB 仅告警）。
- test（`gate-logs/test.log`）：见下 F-2，单跑 332 passed；整套并发 3 次得到 pass / fail(331+1) / pass。

---

## 四、不通过 / 缺陷清单（均已建修复子单）

**F-1（QUOT-332）｜条件 2 未收口** — `frontend/src/screens/ValidateScreen.tsx:587`（手写 `ROW_GRID` 条件表体未换成 DataTable，`:589`/`:606`）。
**F-2（QUOT-333）｜测试不稳定（flaky）** — `frontend/tests/intraday-screen.test.tsx:217` `findByText(/窗口外不补跑/)` 在 partial 态同时命中 `IntradayScreen.tsx:725`（Banner 文案）与 `:298`（说明条）两个节点，整套并发下偶发「Found multiple elements」；单跑/单测稳定通过。修法：收窄正则或改 `getAllByText` 后取首个，并去重屏内重复文案。

## 五、我给老板的表态
**这条单我收——但按「有条件通过」收**：核心质量证据（逐屏 after 截图 / 可复算口径脚本 / 门禁原始日志）齐全真实，条件 1、3、4、5、6、7 与流程 DEC 全过。**唯一实质缺口是条件 2 的 `ValidateScreen.tsx:587` 手写表体没换成 DataTable**，加上 1 条 flaky 测试，两条都已建子单挂 QUOT-306 下。建议这两条修复合入并复跑门禁后转 done；在此之前不宜宣布「18 屏一致性彻底收口」。
