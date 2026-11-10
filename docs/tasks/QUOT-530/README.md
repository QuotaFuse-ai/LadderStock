# QUOT-530 研究方向目录分页 + 只看可用：交付与验收取证

- 日期：2026-11-10
- 分支：`agent/agent/335291a61b69`
- 决策：`docs/decisions/DEC-071-skills-index-pagination.md`
- 规范：`docs/UI-SPEC.md` v1.21 §6.1 C-23、§9（状态不只靠颜色）

## 1. 交付内容

`/skills` 研究方向目录（`content/skills/catalog.json` 构建期内置 70 条）改为：

1. **每页 10 条**，默认第 1 页，共 7 页；分页条 `position: sticky; bottom: 0` 贴内容区下沿，
   滚动时保持可见。
2. **页码进 URL**（`?page=N`），可分享、可刷新；组内切换分组或切换「只看可用」都删除
   `page` 并回到第 1 页；越界页码（如 `?page=99`）按末页渲染并 `replace` 收敛地址栏。
3. **「只看可用」开关**（`?usable=1`）：只保留 `ready` / `partial` / `existing_page`
   三种状态（70 → 45 条），排除 `unconnected` / `planned` / `not_supported`；
   过滤后为空时给出「关闭『只看可用』」出口。
4. 新增通用组件 **C-23 `Pagination`**（上一页 / 页码 / 下一页 + 「第 X / Y 页 · 共 N 条 ·
   每页 M 条」摘要），从 `components/ui/index.ts` 导出；当前页同时用实心底色、`font-semibold`
   与 `aria-current="page"` 表达，不单靠颜色。

数据链路未变：仍只读 `lib/skills.ts` 的 `readCatalog()`（构建期 JSON），没有第二份清单、
演示数据或兜底假数据；六个状态徽标、分组 chips、`/skills/:id`、`/skills/pkg/:name` 与
「已在页面」跳 `/limit-up`、`/watch` 均原样保留。

### 1.1 改动文件

| 文件 | 内容 |
|---|---|
| `frontend/src/components/ui/Pagination.tsx` | 新增。`pageSlots()`（`:28`）≤7 页全量、更多页时首末页 + 当前页 ±2 + 两处 `…`；`Pagination`（`:65`）受控组件，只收 `page` / `pageCount` / `onPageChange` / `pageSize` / `total` / `label`，不发请求 |
| `frontend/src/components/ui/index.ts` | 导出 `Pagination` 与 `PaginationProps` |
| `frontend/src/lib/skills.ts` | 新增 `USABLE_CATALOG_STATUSES`（`:344`）与 `isUsableCatalogItem()`（`:350`），「可用」口径只在这里定义 |
| `frontend/src/screens/skills/SkillsIndexScreen.tsx` | `PAGE_SIZE = 10`（`:47`）、`pageFromParam()`（`:50`）、按 `group → usable → page` 派生（`:176–185`）、URL 收敛 effect、`toggleUsable`（`:213`）/`selectPage`（`:221`）、过滤行 + `Switch`（`:291–294`）、`role=list` 列表（`:312`）与 sticky 分页条（`:321–329`） |
| `frontend/tests/skills-pagination.test.tsx` | 新增 11 例：组件 7 页渲染 / 首末页禁用 / 20 页窗口两处省略 / 越界收敛；屏默认 10 条 7 页、下一页写 `?page=2`、深链 `?page=7`、`?page=99` 收敛、切组删 `page`、`?usable=1` 45 条 5 页且不含不可用徽标、过滤重置页码 |
| `docs/UI-SPEC.md` | v1.21：§6.1 登记 C-23 |

### 1.2 明确没做

- 没改 `content/skills/`、`lib/skills.ts` 的读取与语义（只加了「可用」判定辅助）。
- 没加后端接口或 `/api/skills` 请求（浏览器取证：`catalogCalls: []`）。
- 没改分组 chips 的集合与顺序，也没新增状态徽标。

## 2. 验收取证

### 2.1 自动化

| 项 | 命令 | 结果 |
|---|---|---|
| 类型检查 | `npm run typecheck` | 通过 |
| 单测 | `npm test` | 91 文件 / 908 例全通过（含新增 11 例） |
| 构建 | `npm run build` | 通过（仅既有 chunk 体积提示） |
| 一致性 | `npm run check:ui` | 3/6 通过；失败项全部落在既有文件（`ProxyPoolCard.tsx` / `PortfolioReplayTab.tsx` / `ResearchSymbolBar.tsx` / `AiDock.tsx` / `EventSection.tsx` / `StopListDialog.tsx`），本单新增 / 修改文件零告警 |

### 2.2 实机（Chrome CDP，1440×900，Vite dev 5174）

26/26 断言通过。关键条目：

| 断言 | 实测 |
|---|---|
| 默认 10 条/页，共 7 页 | 第 1 页 id 连续 1–10；摘要「第 1 / 7 页 · 共 70 条 · 每页 10 条」 |
| 页码按钮 1–7 全量 | 七个按钮；当前页 `aria-current="page"` 且 `font-semibold` |
| 首页「上一页」禁用、「下一页」可用 | 是 |
| 下一页写 URL | `/skills?page=2`，列表 11–20 |
| 深链 `?page=7` | 落在末页 61–70，「下一页」禁用 |
| 切分组 | `/skills?group=单票分析`（无 `page`），回到「第 1 / 2 页 · 共 12 条」 |
| 只看可用 | `/skills?usable=1`，45 条 5 页，列表无「未接通 / 待建 / 本阶段不做」 |
| 请求计数 | `catalogCalls: []`；仅外壳既有 `/api/control/commands` |
| sticky | 滚动 60px 前后分页条 `top 855 / bottom 900` 不变，首行 top 293 → 233；条底贴住滚动容器底 900 |
| 深色 | 深色主题生效、无横向溢出 |

截图（交付评论附件，本地副本在 `workdir/evidence/shots/`，按仓库 `.gitignore` 不入库）：

| 文件 | 内容 |
|---|---|
| `skills-p1-light.png` | 默认第 1 页（10 条 / 7 页） |
| `skills-p2-light.png` | `?page=2`，11–20 条 |
| `skills-p7-light.png` | `?page=7` 末页，下一页禁用 |
| `skills-group-light.png` | 切到「单票分析」，页码回到 1 |
| `skills-usable-light.png` | `?usable=1`，45 条 / 5 页 |
| `skills-sticky-light.png` | 滚动中分页条仍贴底 |
| `skills-p1-dark.png` | 深色主题第 1 页 |

### 2.3 已知边界

- sticky 分页条位于内容末尾之后的 sticky 块：内容可滚动时全程钉在内容区底沿；
  当剩余内容不足一屏（例如分类只有 12 条）时它随内容自然上移，但仍在视口内，
  不会遮挡条目。
- 分页与过滤均为纯前端状态，无写请求；刷新深链后状态由 URL 完整还原。
