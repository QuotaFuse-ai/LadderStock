# DEC-063 单顶栏：页头条与命令条并入顶栏

- 日期：2026-11-05
- 提出：项目领导人（QUOT-457，承接用户「每个页面顶部都堆了三条横栏」的反馈）
- 状态：生效（代码已落 `frontend/`，验收口径见 QUOT-457）
- 编号：`DEC-063`。`docs/decisions/` 已合并的最大号为 DEC-062（工作区顶栏），顺延一位。
- 关联：DEC-062（顶栏结构 / 工具区顺序）/ DEC-061（命令条白名单）/
  `docs/UI-SPEC.md` §2 / `frontend/tests/topbar-single-bar.test.tsx` /
  `frontend/tests/page-command-bar.test.tsx`
- 参考实现：`C:\Users\Administrator\Desktop\vibe-astock-main\desktop\src\verticals\finance\`
  （只用作版面参照，只读，未改动）

## 1. 问题

DEC-062 之后外壳仍有三条横栏叠在内容顶部：

1. `header.workspace-topbar`：面包屑 + 工具区；
2. `PageCommandBar`：`拉取行情数据  [ldst sync]  [运行]`，独占一行；
3. 各屏 `PageHeader`：`市场  2026-11-05 · 情绪温度  [刷新]`，再跟一行胶囊页签。

真正承担「我在哪 / 我能操作什么」的一直是 ①。②③ 各占一行，滚动前先损失 100px 左右版面，
且同一类操作（刷新 / 运行命令）在三个位置分裂。目标是**全平台只留一条横栏**。

## 2. 决定

### 2.1 结构

```
<header class="workspace-topbar">                 ← 唯一横栏，固定不滚
  面包屑                                     [刷新][本页命令][工具区]
</header>
<div class="flex-1 overflow-y-auto">              ← 唯一滚动容器（QUOT-397 不变）
  <PersistenceBanner />                           ← 条件出现，不属于常驻横栏
  <main id="main">
    <div class="page-shell">
      <h1 class="sr-only">市场</h1> badge  subtitle  meta   [次级操作]
      <nav aria-label="页内导航">…胶囊页签…</nav>   ← 页签是第一行内容
      内容…
    </div>
  </main>
</div>
```

- **不再有页头条**：`PageHeader` 不渲染底色、分隔线、sticky、`min-h-topbar`。
- **不再有命令条**：`PageCommandBar` 从 `main` 移到顶栏，形状从「一行」改为「一颗图标按钮 + 浮层面板」。
- **主操作「刷新」进顶栏**：`PageHeader` 新增 `refresh` 属性，各屏只声明行为与状态。
- **页签进内容区**：`SectionNavPills` 仍由骨架件渲染在 `PageHeader` 之后，数据源仍是
  `NAV_SECTIONS`，不新增第二份页签表；位置从「页头条下方」变成「内容第一行」。

### 2.2 `PageHeader` 的新契约

```ts
interface PageHeaderProps {
  title: string                    // 渲染为 h1.sr-only，不占版面
  subtitle?: ReactNode             // 日期 / 摘要，12px 次级文本
  badge?: ReactNode                // 演示数据、来源状态
  meta?: ReactNode
  actions?: ReactNode              // 次级操作留在内容区（新增持仓、上周/下周、导出…）
  refresh?: { onClick; label?; loading?; disabled? }   // 主操作，送顶栏
}
```

- 标题为什么还留 `h1`：页面必须有一级标题给读屏与测试（`topbar.test.tsx` 断言顶栏无 heading）；
  顶栏面包屑的 `<strong>` 是导航位置，不是文档标题。`h1` 用 `sr-only`，视觉标题由面包屑承担。
- `h1` 与 subtitle 仍是同一父节点的兄弟：`quota-331-envelope.test.tsx` 的 `subtitleText()`
  依赖该结构，不改测试口径。
- `actions` 不搬家：这些是页面自己的操作（月份切换、导出、新增），不属于全局工具区。

### 2.3 刷新如何从顶栏触达

`lib/pageToolsSlot.tsx` 提供 `PageToolsSlotContext`（`HTMLElement | null`）。

- `Layout.tsx` 在顶栏工具区左侧留一个空 `div`，把节点通过 context 交给内容树；
- `PageHeader` 有 `refresh` 且拿到槽位时，用 `createPortal` 把同一颗按钮送进顶栏；
- **拿不到槽位时退回内容区就地渲染**（组件单测 / Storybook / 独立挂载），屏幕代码不区分环境；
- 因此全屏只有一颗「刷新」按钮，不会出现顶栏与内容区各一颗。

context 放在独立模块而不是 `PageShell.tsx`：`PageShell` 已经从 `Layout` 导入
`NAV_SECTIONS`，反向再导入会形成循环依赖。

### 2.4 本页命令的浮层

`PageCommandBar` 保留原有数据链路（`/api/control/commands`、`runCommand`、1.5s 轮询
`job`、409 由后端兜底），只换形状：

- 顶栏一颗 `SquareTerminal` 图标按钮；运行中换 `Loader2`，上次失败时图标转 `danger`；
- 点击展开 `role="dialog"` 面板：命令名 + `ldst <argv>` + 「运行」+ 状态徽标 + 错误 + 日志尾部；
- 点面板外部或按 `Escape` 收起；`aria-haspopup="dialog"` / `aria-expanded` / `aria-controls` 齐全；
- 演示态（`?demo=1`）或本页没有命令时**整颗不渲染**：按钮失效不能挡住页面数据（DEC-061 口径不变）。

### 2.5 顶栏工具区顺序

`[刷新][本页命令]  本地 A 股短线工作台  隐私  深浅色  AI`。

刷新与本页命令是**页面级**操作，排在全局工具之前；DEC-062 §6 约定的「状态灯后续插在工具区最左」
继续有效，插入点在刷新/命令之后、工作台小字之前。

## 3. 兼容性

- `<768px` 抽屉模式：顶栏右侧工具区在窄屏仍保留刷新与命令图标（`title` / `aria-label` 提供文案），
  抽屉与汉堡逻辑未改；`rail:`（1024–1279，56px 图标栏）未改。
- 页签条沿用 `NavPills` 的横向滚动 + `.ldst-no-scrollbar`，窄屏不折行、不溢出。
- 深浅色 / 隐私 / AI 三个入口的 DOM 与行为未改。
- 数据来源徽标、新鲜度、演示水印全部保留，位置从「页头右侧」变为「内容第一行」。

## 4. 未做（明确跳过）

- 不做路由级「顶栏标题可点击返回」：面包屑当前只做定位。
- 不把次级操作（上周/下周、新增持仓）收进顶栏：顶栏只放全局工具与本页主操作。
- 不改 `PageCommandBar` 的白名单与后端契约（DEC-061 保持）。

## 5. 证据

- 测试：`frontend/tests/topbar-single-bar.test.tsx`（4 例：唯一 header / 唯一刷新 /
  `h1.sr-only` / 次级操作留内容区）、`frontend/tests/page-command-bar.test.tsx`
  （4 例：渲染与展开 / 运行入队 / Esc 收起 / 无命令或演示态不渲染）。
- 回归：`npm run typecheck`、`npm test`（53 文件 515 例）、`npm run build` 全绿。
- 版面：`/market`、`/positions?demo=1` 的 1440×900 与 390×844 实机走查；命令面板以桩
  `/api/control/commands` 截图验证（命令名、argv、上次成功、日志、运行按钮齐备）。
