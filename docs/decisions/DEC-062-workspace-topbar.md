# DEC-062 工作区顶栏：面包屑 + 隐私模式 + 深浅色 + AI 入口，移动端抽屉

- 日期：2026-11-05
- 提出：项目领导人（QUOT-442，承接 QUOT-425 用户追加需求第 5 条与 11:38 更正）
- 状态：生效（代码已落 `frontend/`，验收口径见 QUOT-442）
- 编号：`DEC-062`。取值依据 QUOT-425 任务书指派；`docs/decisions/` 在 `origin/main` 上
  已合并的最大号是 `DEC-055`，`DEC-056`–`DEC-060` 属于 QUOT-425 内尚未合入的子任务，
  本单按任务书钉死为 062，避免与并行子任务抢号。
- 关联：DEC-020（持仓隐私边界）/ DEC-040（断点词汇）/ DEC-044（我的股票分组）/
  DEC-046（复盘三独立入口）/ `docs/UI-SPEC.md` §2 / `frontend/tests/topbar.test.tsx` /
  `frontend/tests/privacy.test.ts`
- 参考实现：`C:\Users\Administrator\Desktop\vibe-astock-main\desktop\src\verticals\finance\`
  （`components/layout/Layout.tsx:289-309`、`lib/nav.ts:95-107`、`hooks/usePrivacyMode.ts`、
  `lib/privacy.ts:1-11`、`components/ui/FinanceAiDock.tsx`）

## 1. 决定：顶栏是什么、放在哪

在侧栏右侧新增**纵向列**，自上而下两层：

```
<div class="flex">
  <aside>…侧栏…</aside>
  <div class="flex h-screen flex-1 flex-col overflow-hidden">
    <header class="workspace-topbar">…面包屑 + 工具区…</header>   ← 固定，不滚
    <div class="flex-1 overflow-y-auto">                          ← 唯一滚动容器
      <PersistenceBanner />
      <main id="main">…PageCommandBar + Outlet…</main>
    </div>
  </div>
</div>
```

- 顶栏高 `h-topbar`（`--ldst-topbar-h: 56px`），与侧栏品牌行、`PageHeader` 的 `min-h` 同值，
  三处一条水平线；底色 `bg-bg`、下边 1px `border-border`（对齐参考实现 `.workspace-topbar`）。
- **主内容区仍是唯一滚动容器**：窗口不滚，`PageHeader` 的 `sticky top-0` 才钉得住（QUOT-397 不变）。
- `PersistenceBanner` 保持在主内容列顶部、顶栏之下；`PageCommandBar` 位置不变。
- 顶栏是「当前在哪」的一层，页内 `h1` 是另一层；`PageShell` 的 title / subtitle 一律不动。
  参考库同样是「顶栏面包屑 + 页内大标题」两层。

## 2. 断点口径（本次唯一新增的断点词汇）

| 档位 | 值 | 用途 | 实现 |
|---|---|---|---|
| `sm` | `min-width: 640px` | `工作空间 /` 前缀在此及以上显示，以下只留栏目名 | `tailwind.config.ts` `extend.screens` |
| `lg` | `min-width: 1024px` | `本地 A 股短线工作台` 小字在此及以上显示 | 同上 |
| 抽屉 | `max-width: 767px` | `<768` 渲染汉堡 + 左侧抽屉；**≥768 不渲染汉堡** | `lib/mediaQuery.ts` `MOBILE_QUERY`（JS，非 CSS 隐藏） |
| `rail` | `max-width: 1279px` | 既有：1024–1279 收 56px 图标栏 | 不变 |
| `desk` | `min-width: 1280px` | 既有：完整侧栏 + 多栏 | 不变 |

说明：

1. 抽屉断点走 JS 而不是 `md:hidden`：要求是「≥768px 不渲染汉堡」，CSS 隐藏仍会留一个
   可聚焦的按钮在无障碍树里。`useMediaQuery` 在 jsdom / 无 `matchMedia` 环境按「不匹配」兜底，
   既有测试渲染 Layout 时不会多出汉堡。`<768` 时侧栏本身让位（`hidden`），否则 375px 屏上
   232px 侧栏与抽屉两套导航同屏；断点同样由 JS 判断，不新增 `md` 档。
   抽屉内任一链接点击即关闭（同路由点击不必等 `location.pathname` 变化）。
2. 只补 `sm` / `lg` 两档，**不引入 `md` 之外的其它命名断点**；`rail:` / `desk:` 保持原义，
   1024–1279 的 rail 行为零改动。`<768` 的抽屉是外壳层唯一例外，各屏自己不写窄屏布局。
3. Tailwind 在 `screens` 含对象（`rail` 是 `{max}`）时不支持 `min-*` / `max-*` 前缀变体，
   构建会 warn；这是既有配置的已知形态，`sm:` / `lg:` 标准变体不受影响。
4. UI-SPEC §0 的「视口 <1024 窄屏适配移出规范」仍然成立：本单只做**外壳**在手机宽度下的
   最小可用（抽屉 + 顶栏），不做逐屏窄屏重排。

## 3. 面包屑：`navTitleFor(pathname)`

- 纯函数放 `frontend/src/lib/nav.ts`，导航数据 `NAV_SECTIONS` / `PINNED_NAV` 一并搬到这里，
  `components/Layout.tsx` 原样再导出（导出名与顺序不变，`tests/nav-routes.test.tsx` 不受影响）。
- 返回**一级栏目名**（`section.label`），不是页面 label：
  `/positions` → 「我的股票」、`/watchlist` → 「我的股票」、`/market?tab=theme` → 「市场」、
  `/research/600519` → 「个股深挖」、`/` → 「首页」、匹配不到 → 「工作空间」。
- 只看 pathname（`?tab=` / `?date=` 不参与）；子路径按最长前缀归属，`/` 只认本身。
- 顶栏 DOM：`汉堡（仅 <768）→ 工作空间 /（sm+）→ <strong>栏目名</strong>`。

## 4. 隐私模式：「只遮屏幕显示」的边界

新增 `frontend/src/lib/privacy.ts`：

- `MASK = "••••"`；`hideIf(on, text)`：`on && text !== "—"` 时返回 MASK，**`—` 不遮**
  （遮了反而像有数）。
- `usePrivacyMode()`：`useSyncExternalStore` + 单键 `localStorage['ldst.privacy'] = on|off`；
  读取失败按关闭处理且不抛，写入失败只影响持久化、本次会话仍生效；返回 `{ on, toggle }`。
- 口径照参考 `lib/privacy.ts:1-11`：
  - **遮**：自己的数量、成本价、市值、浮动盈亏、成本提示；
  - **不遮**：百分比（盈亏率 / 集中度 / 覆盖率）、现价、PE / PB / 换手等公开行情；
  - 只遮屏幕显示，计算、存盘、发往 AI 的内容都用原值。
- 初版消费方：`screens/radar/PositionsScreen.tsx`——持仓明细表的成本价 / 数量 / 市值 / 浮动盈亏
  单元格，以及汇总卡（总成本 / 市值 / 浮动盈亏）与集中度、行业分布的「成本 …」提示。
  自选页当前没有成本列（代码 / 名称 / 现价 / 涨跌），无需接入。
- 隐私开关状态是**界面偏好**，不是持仓数据，不违反 DEC-020「持仓不落浏览器存储」。

## 5. 侧栏：只移除主题分段控件，其余不动

- 移除侧栏底部主题 `Segmented`（避免与顶栏重复入口）；「接入 AI」按钮与「设置」链接保留。
- **作废**上一条评论（2026-11-05 11:35）里按「侧栏」写的样式改造：分组三角、整栏可收、
  底部 pinned 接入 AI 的改版**均不在本单范围**。用户 11:38 更正截图红框圈的是顶栏
  （`workspace-topbar`），以更正为准。
- 侧栏现有胶囊样式、`ldst.nav.expanded` 展开状态、1024–1279 rail 行为全部保留。

## 6. 工具区组件化（给下一个任务留插槽）

`components/TopbarTools.tsx`：`本地 A 股短线工作台`（`lg`）→ 隐私开关 → 深浅色 → AI 入口。
下一个任务（顶栏服务健康灯 + 全局重启）在**最左侧**插入状态灯，只改这个组件，
不再动 `Layout.tsx` 的顶栏结构。容器保留参考实现的
`margin-right: var(--ai-trigger-space, 0px)` 预留位（默认 0，当前无浮动 Dock）。

- 深浅色：点击在 light / dark 间互切；当前 `system` 时按 `resolveTheme()` 的解析值切到反色；
  仍经 `setTheme()`，与设置页同源（`localStorage['ldst.theme']` + `<html data-ldst-theme>`）。
- AI 入口复用既有 `AiConnectDialog`（`?ai=1` 深链仍可用），**不新建 AI 运行时**。

## 7. 未做（明确跳过）

- 参考实现的 `UpdateBanner`：我方无此组件，跳过。
- 参考实现的浮动 `FinanceAiDock`：我方 AI 入口在工具区最右，不做浮动 Dock。
- 参考实现的侧栏收起 / 移动端 `inert` 全量口径：`inert` 未采用，抽屉用遮罩 + 滚动锁定。

## 8. 证据

- 测试：`frontend/tests/topbar.test.tsx`（12 例）、`frontend/tests/privacy.test.ts`（5 例）；
  `npm run typecheck` / `npm test`（51 文件 507 例，rebase 到含 QUOT-441 / QUOT-420 / QUOT-427
  的 main 后复跑）/ `npm run build` / `npm run check:ui`（6/6）全绿。
- 截图：`docs/screenshots/positions-442-{dark,light}.png`（顶栏双主题）、
  `positions-442-privacy-{dark,light}.png`（隐私开启态）、
  `positions-442-privacy-off-dark.png`（同屏对照，未遮）。
