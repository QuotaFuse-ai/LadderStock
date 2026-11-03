# QUOT-306 S1 · 18 屏逐屏粗糙点清单

审计对象：`LadderStock` 前端 18 个屏幕（`frontend/src/screens/**`）。
基线：`origin/main @ 270caf8`（工作分支 `agent/ui-ux/8a4fb90e8173`，HEAD 与基线同一提交）。
方法：只读代码 + 只写本文档，未改任何屏幕代码、未新增依赖、未跑构建。
`docs/screenshots/` 仅有 `limit-up-empty.png` / `limit-up-normal.png` 两张（无 before 图），故一切以代码为准。

---

## 0. 怎么读这份清单

**每条格式**：`- [严重度] 文件:行号 — 问题 → 应该怎样（条款引用）`。
某维度干净时明确写「本屏无该问题」并给依据行号，不留下"没看"的歧义。

**条款引用图例**（QUOT-306 验收 2「每条带条款引用」的落地口径）：

| 写法 | 指向 |
|---|---|
| `UI-SPEC §n` | `docs/UI-SPEC.md` v1.0，标题即「四屏」，只覆盖 `/review` `/watch` `/validate` `/journal` |
| `C-xx` | 组件契约码，取各组件源码顶部 docstring 与 `UI-SPEC §6` 组件清单（C-09 = `components/ui/Table.tsx`，C-13 = `Badge`，C-17 = `EmptyState`，C-18 = `Skeleton`，C-19 = `ErrorBlock`/`Banner`） |
| `tokens` / `tw` | `frontend/tokens.json` v2.0.0 与 `frontend/tailwind.config.ts` 的 token→class 映射 |
| `验收 n` | QUOT-306 issue 的硬验收项 |
| **`规范缺口`** | 现状无条款可引，需要先定规则再改代码（属阶段三文档动作） |

> ⚠️ **前置事实**：全仓 40+ 处源码注释在引用 `DESIGN-SPEC §3.1 / §5 / §7.1 / §8 / §10` 与 C-01…C-20，但仓库和 git 历史里**都不存在 `DESIGN-SPEC.md`**。本清单一律改引 `UI-SPEC §n` + 组件契约码；`Layout.tsx`、`Feedback.tsx`、`Badge.tsx`、各屏文件里那些 `DESIGN-SPEC §x.y` 注释属于**待修的假引用**（聚合项 M-9）。

**严重度定义**（对齐 QUOT-306 验收，不是审美分级）：

- **P0**：信息错误或永久丢失、用 0 / 假定值冒充缺失、布局撑破、裸 `<table>` 绕开统一表体、用户把演示数据当真数据。必须改。
- **P1**：跨屏不同源（该用 C-09/C-13/C-17 却手写）、数值对齐基线破坏、过渡完全缺失、`truncate` 无 tooltip、原始英文枚举直显、断点错位。**「粗糙」的主体在这一档**。
- **P2**：刻度混用但可读、留白节奏不一、次要 A11y 标注、可选 token 化。排后面。

---

## 1. P0 汇总（15 条）

**A · 演示数据无水印（7 条，同一根因）** — `?demo=1` 时整屏渲染演示行，页面上没有任何「演示」标识。
条款：`demo.ts:1-9` 自我承诺「`?demo=1` 显式开启，页面带『演示态』水印」；根因 `components/PageShell.tsx:47-48` `DemoStateBadge` 只反映 `?state=`，`state===null` 直接 `return null`。

1. `frontend/src/screens/limitup/LimitUpScreen.tsx:128`
2. `frontend/src/screens/industry/IndustryMatrixScreen.tsx:287`（空态里 `:176` 就有「查看演示数据」按钮，一键进入无水印态）
3. `frontend/src/screens/industry/IndustryDetailScreen.tsx:439`
4. `frontend/src/screens/signals/SignalsScreen.tsx:238`（`:318` 同款入口链接）
5. `frontend/src/screens/radar/CalendarScreen.tsx:377`（`:486` 同款入口）
6. `frontend/src/screens/radar/PoolScreen.tsx:292`（`:382` 同款入口）
7. `frontend/src/screens/radar/PositionsScreen.tsx:639`（`:819` 同款入口）

> 全仓只有 3 处有演示标识：`radar/NewsScreen.tsx:369`「· 演示数据」、`research/ResearchDetailScreen.tsx:535`、`research/ResearchReportScreen.tsx:171`。**10 个能进演示态的屏里 7 个没有水印**，而 3 个有的恰好都不是同一套写法。

**B · 用 0 / 假定值冒充缺失（6 条）** — 条款：UI-SPEC §8「缺失一律 `—` + 原因，不得显示 0 或 0.00」、C-11 `MetricCard` 契约（`Card.tsx:114-125`）、`lib/format.ts:10` `MISSING`。

8. `frontend/src/screens/signals/SignalsScreen.tsx:237` — `payload?.counts.total ?? 0`：`header` 在 loading(:254)/offline(:264)/error(:281)/empty(:295) 四分支都渲染，取数前后都显示「命中 **0** 条」；同半句的 `数据时点 —` 用的是正确 `MISSING`，自相矛盾。
9. `frontend/src/screens/industry/IndustryMatrixScreen.tsx:286` — `rows.length`（`:206` `payload?.industries ?? []`）在 loading/error/offline 的 header 里显示「SW 一级 · **0 个行业**」，后半句 `数据时间 —` 又是对的。
10. `frontend/src/screens/radar/PositionsScreen.tsx:636` — `payload?.position_count ?? 0`：header 被 :738/:748/:766 三态复用，未知时显示「— · **0 只** · 价格来源 —」，与同屏 :755「不伪造任何持仓」的文案直接冲突。
11. `frontend/src/screens/radar/PoolScreen.tsx:156` — 抽屉权重列 `formatOneDecimal((payload.weights[part] ?? 0) * 100)` → 权重缺失时显示 **「0.0%」**；同根因 `:113` `weightSum` 累加同样 `?? 0`，合计权重随之虚低。
12. `frontend/src/screens/WeeklyScreen.tsx:543` — `value: number(theme.streak_days) ?? 0`：`HBarItem.value` 类型是 `number`，null 兜成 0 后 `HBarList` 仍画出 `max(2, …)`% 宽的**实体条**（`components/ui/MiniChart.tsx:30`），把缺失值可视化了。同根因 `:520` `number(band.count) ?? 0`。`tokens chart.empty`（`--ldst-chart-empty`）正是为「缺失槽位」存在的，此处应当用它。
   *（本条由子审计的 P1 上调：它不是"排序里的 0"，是画给人看的 0。）*
13. `frontend/src/screens/SettingsScreen.tsx:1346` — `board.data?.ports.board ?? 5950` / `?? 5951`：`state === 'offline'` 仍走这个 body 分支（`:1264`），把**假定默认端口当成真值**展示，既无 `—` 也无原因；`:541` `buildPortRows` 有同样的默认值回路。
   *（本条由子审计的 P1 上调：与 B 类同性质——用可信外观的假定值冒充未知，用户会照着它去改防火墙规则。）*

**C · 色序反转（1 条）** — 信息误导。

14. `frontend/src/screens/ReviewScreen.tsx:198` — `source_status === 'ok' ? 'ok' : 'partial' ? 'warn' : 'danger'`，即 **`partial`→黄、`degraded`→红**；UI-SPEC §5.2 明文（`docs/UI-SPEC.md:252-253`）`degraded`=warn(黄)、`partial`=danger(橙红)，且 §5.3（`:258`）提示条取最严重者 `partial > degraded`。用户会把「数据集缺项（最严重）」看成最轻的黄、把「降级完成」看成最重的红。

**D · 裸 `<table>`（1 条）** — 全仓屏目录里唯一的裸表。

15. `frontend/src/screens/radar/PoolScreen.tsx:125` — 抽屉内手写 `<table>`（125-183）绕开 C-09：4 列含 2 个数值列（权重 :156、得分 :158），行高 `px-3 py-2`(:149/:155) 而非 `h-row-compact`，无 sticky 表头、无行 hover/transition，与同屏主表体（`:425` 已是 DataTable）各写一套；`caption`(:126) 与 `scope`(:134) 反倒是给的。**同一份 `candidate.parts` 数据的两种渲染分叉**，是「不同源」最直接的证据（共享的只有单元格件 `PartCell`:48）。

---

## 2. 基线数据的校准（issue 给的「起点」逐条核）

issue 里那组数是起点不是结论，以下是在同一提交上的复测。**三处需要修正，否则会派错工**。

| issue 的说法 | 实测 | 影响 |
|---|---|---|
| 新 10 屏**零使用** DataTable，全部手写行列 | **不成立**。`IndustryMatrixScreen.tsx:401`、`IndustryDetailScreen.tsx:547`、`SignalsScreen.tsx:400` 与 `:469`、`PoolScreen.tsx:425`、`PositionsScreen.tsx:851` 都在真实使用 C-09，且 `caption`/`align`/`initialSort` 基本齐全 | 阶段二的重点**不是**「把新 10 屏换成 DataTable」。逐区块判定后，新 10 屏真正该换的只有 `PoolScreen.tsx:125`（P0）和 `ValidateScreen.tsx:587`（原四屏，缺 API 才没换）；其余手写行列是 `<dl>` / KPI 卡 / 日历矩阵 / 记录列表的**合法形态**（详见各屏维度 4） |
| 裸 `<table>` 全仓仅 1 处 `PoolScreen.tsx:125` | **成立**（`grep -rn "<table" frontend/src` 只命中 `components/ui/Table.tsx:160` 与该处） | 但 `ValidateScreen.tsx:587` 是「用 div 栅格造的表」，无表格语义、无 `caption`/`scope`/`role`，裸 `<table>` 计数器抓不到它 → 验收不能只 grep `<table` |
| `duration-*` 全 18 屏只出现 4 次 → 过渡基本没有 | **屏文件层是 7 处**：`HomeScreen:437`、`SettingsScreen:340/393/1221`、`CalendarScreen:153`、`NewsScreen:220`、`ResearchReportScreen:79`。UI-SPEC 四屏（review/watch/validate/journal）确实 0 处 | 准确表述应是「**屏文件层近乎为零、组件层由 C-01/C-02/C-07/C-09 自带过渡兜底**」。真实缺口只有三类，且都能一次修完：① 10 个「链接长得像按钮」的手写 class（M-2）② `Drawer`/`Toasts`/`ConfirmDialog` 三个基准件零动效（M-3）③ 展开/收起与状态色切换（`ValidateScreen:610/680`、`kit/AiErrorNotice.tsx:195`、`MarketScreen:711`） |
| 新 10 屏任意值 class 共 4 处（`h-[76px]`×2、`w-[208px]`、`w-[140px]`）；硬编码 hex 1 处（`IndustryDetailScreen.tsx`） | 任意值**屏文件共 21 处**（新 10 屏 6 处，其余 15 处在 `Validate:586/610/683/172`、`Weekly:381/506/554`、`Review:640/763`、`Journal:520`、`Market:529`、`Home:111`、`Watch:87`、`Settings` 无）。其中 **6 处是 `min-[…]:` 自定义断点**，性质和 `w-[…]` 完全不同，应分开派工。**hex 不成立**：`IndustryDetailScreen.tsx:410` 命中的是 `&#123;` HTML 实体，`grep -E "#[0-9a-fA-F]{3,8}"` 在 18 屏 + `industry/heat.ts` + 各 `demo.ts` 里**零真颜色**，颜色全走 `var(--ldst-*)` / `color-mix` / token 类 | ① 「无硬编码 hex」这条可以直接从验收待办里划掉，只保留 tokens.css / charts/cssVar.ts 兜底两个白名单说明。② 任意值要分三档定策略：装饰线宽（`w-[3px]`×3、`w-[2px]`）、容器宽（`max-w-[720px]`×2、`w-[208px]`、`max-w-[140px]`、`min-w-[960px]`、`min-w-[8.5rem]`）、断点（`min-[640px/1024px/1100px]:`×6）——第三档要先在 `tailwind.config.ts:40-44` 补档名才能消，属于配置动作不是逐行替换 |

---
## 3. UI-SPEC 覆盖的四屏（校准基准用）

### `/review` — `frontend/src/screens/ReviewScreen.tsx`

- **维度 1**
  - [P2] `ReviewScreen.tsx:765` — `mb-1.5`(6px) 不在 space 刻度（0/4/8/12/16/24/32/48）；同类 `:793`、`:820` 的 `gap-1.5`（tokens `space.*`）
  - [P2] `ReviewScreen.tsx:582` — `w-44`(176px) 属 Tailwind 默认档，不是任何宽度 token → **规范缺口**：日期输入应定控件宽档
- **维度 2**
  - [P2] `ReviewScreen.tsx:854` — 段字数 `{formatInt(…)} 字` 未上 `ldst-num`（UI-SPEC §7.2 计数必须 tabular；`MetricCard`/`Badge` 内部都有，唯此行裸文本）
  - [P2] `ReviewScreen.tsx:606` — 草稿字数用 `formatInt` 无千分位，UI-SPEC §4.1 明文「字数…千分位」
- **维度 3**
  - [P1] `ReviewScreen.tsx:782` — 段内失败面板「查看详情」展开/收起无 transition、无 `aria-controls`（实现在 `components/kit/AiErrorNotice.tsx:195-207`；修在组件，`:772` degraded 面板同病）
  - [P2] `ReviewScreen.tsx:730` — 段状态 pending→running→ok→degraded 切换时状态条与 Badge 换色无 `transition-colors duration-fast`（状态条本体 `components/ui/Card.tsx:100-103` 无过渡）
- **维度 4** — **本屏无该问题**。依据：`:666` 指标卡阵、`:709` AI 摘要 `ul`、`:719-861` 五段纵向卡流均符合 UI-SPEC §3.1 布局；本屏不存在多行同构记录集，「Review 不用 DataTable」不构成缺陷。
- **维度 5** — 本屏无图表。
- **维度 6**
  - [P0] `ReviewScreen.tsx:198` — **色序反转**，见 §1 表 C-14 条。
  - [P1] `ReviewScreen.tsx:206` — 徽标只有 `FRESHNESS_LABEL[freshness]` 文字，`source_status` 只体现在底色 → `degraded`/`partial` 对用户不可读，颜色成为唯一编码（UI-SPEC §5.2 三态均须标签 + §9 状态不只靠颜色；`:198` 的 tone 是纯颜色通道）
  - [P1] `ReviewScreen.tsx:196` — 第四份本地 `SourceBadge`（`Home:69` / `Market:105` / `Weekly:166` / 本屏），配套 `FRESHNESS_LABEL:183`、`EnvelopeMeta:190` 各自一套；UI-SPEC §6 组件清单第 286-287 行登记的 `kit/SourceBadges.tsx`（`FreshnessBadge`/`SourceStatusBadge`）**在仓库不存在**，全仓 `●/◐/○` 零命中（验收「freshness/source_status 均有文字+图形」）→ 聚合项 M-1
  - [P2] `ReviewScreen.tsx:186` — `archive` 文案写「归档」，UI-SPEC §5.1 规定「存档」（四屏实现一致地都错 → 定其一另改文档）
- **维度 7**
  - [P1] `ReviewScreen.tsx:767` — 模型名 `truncate` 无 `title`，长模型 id 永久丢失
  - [P2] `ReviewScreen.tsx:707` — 页头摘要区同一模型名既无 `truncate` 也无 `min-w-0`，超长时把同行 Badge 挤出容器（与 `:767` 是同一信息，一处丢字、一处破版）
- **维度 8** — 四态齐全且同源（loading `:501-511` C-18；error `:513-528` C-19+重试；empty `:628-663` C-17 带「复制命令/重新检查」两个出口；partial `:548-579`；offline `:534-547`）。无 0 冒充缺失：`:414-417` 用 `sentimentReady` 闸门整组置 `—`（注释明确空数组 `gap_levels` ≠ 0 档断层），`:671/:681/:691` 三值 null→`MISSING` 并给原因 `:674/:684/:695`。**本屏该维度无 P0/P1**。
  - [P2] `ReviewScreen.tsx:605` — 草稿恢复 Banner 缺 UI-SPEC §4.1 要求的 [查看改动]/[清空草稿] 动作与「关闭后本次会话不再出现」，当前是永不下掉的 info 条
- **额外必查**
  - [P1] `ReviewScreen.tsx:640` — 任意值 `max-w-[720px]`（空态动作列容器宽无 token；`JournalScreen.tsx:520` 同值各写各的）→ 聚合项 M-4
  - [P2] `ReviewScreen.tsx:763` — `desk:grid-cols-[2fr_3fr]`：2:3 比例符合 §3.1「AI 40%/判断 60%」，但应注册为布局 token 而非任意值
  - [P1] `ReviewScreen.tsx:666` — 指标卡 `grid-cols-1 desk:grid-cols-3`，1024–1279 档仍单列，违反 UI-SPEC §2「1024–1279 指标三列」（缺 `rail:grid-cols-3`）；[P2] `:763` 两栏挂 `desk:`(≥1280) 而 §3.1 规定 ≥1100 即两栏 → 聚合项 M-5
  - 裸 `<table>`：无。硬编码色值：无。`text-[9px]/[11px]`：无。`text-hero`：仅 `:669` 一处 ✓。`text-text-muted` 落 surface-3/soft：无（`:641` 代码块在 `bg-surface-3` 上用 secondary，合规）
  - [P2] `ReviewScreen.tsx:565` — 「定位首个失败段」只 `scrollIntoView`（`:455-463`）不转移焦点，键盘用户定位后焦点仍在按钮上（UI-SPEC §9 键盘可完成；reduced-motion 已处理 ✓）
  - 局部组件：`SourceBadge`(`:196`) → M-1

### `/watch` — `frontend/src/screens/WatchScreen.tsx`

- **维度 1** — **本屏无该问题**。依据：顶层 `:294/:303/:313` 统一 `gap-5`；状态条四格 `:337/:345/:349/:353` 统一 `p-4`；`:373` 双卡 `gap-4`、`:426` 阈值列 `gap-3` 属嵌套层级差，有理由。
- **维度 2** — **本屏无该问题**。依据：`:113/:124/:131` 触发值/阈值/连板数均 `align:'right'`，表头与数据同源于 `Column.align`（`Table.tsx:175`）。
- **维度 3**
  - [P2] `WatchScreen.tsx:115` — 本屏唯一新增交互是排序表头，`hover:text-text` 无过渡（根因在基准件 `Table.tsx:184`，修在 `components/ui`，非本屏独有）
  - 依据（其余不缺）：可交互元素全部已组件化——立即取数/清除快照 `:271/:279` → `Button.tsx:17` 含 fast；Switch `:457` → `Field.tsx:147`；Input `:433/:471` → `Field.tsx:13`；DataTable 行未挂 `onRowClick`（`:398` 未传），不存在「可点行缺过渡」
- **维度 4** — **本屏无该问题**。依据：信号流已是 C-09（`:398`）；`:336-360` 状态条是单行 4 格 `<dl>`，无多行同构记录，不构成替换对象。
- **维度 5** — 本屏无图表。
- **维度 6**
  - [P1] `WatchScreen.tsx:325` — 快照过期(cached)、源降级(degraded)、部分缺失(partial) 三种 UI-SPEC §3.2 状态矩阵要求「黄条 + 各自徽标」的情形，全部塌缩成一条通用「盘中快照不可用」Banner；`:209` 只读 `source_status`、**完全没读 `freshness`**，「缓存回放/已降级」不可辨（§3.2 状态矩阵、§1.4 降级可见）→ 聚合项 M-1
  - [P1] `WatchScreen.tsx:318` — 缓存回放徽标只有文字 + 颜色，无 §5.1 的 ●/◐/○（§11 验收清单「freshness 三态均有文字 + 图形」不满足）
  - [P2] `WatchScreen.tsx:340` — `StatusDot`(图形+文字) 用法合格；但四类 `needs_refetch_reason` 的按钮文案与 §4.2 表不齐：`clock-skew` 应为「重新校准后取数」实为统一「重新取数」(`:369`)，ttl/trade-date 两条缺 §4.2 要求的量化细节（TTL 时长、快照所属日期）
- **维度 7**
  - [P2] `WatchScreen.tsx:102` — 名称列 `truncate` 无 `title`；当前后端 `name` 恒 null（`api.ts:461`）实际显示「—」，属潜伏性丢失——后端一供名称即永久丢
  - [P2] `WatchScreen.tsx:356` — 剩余 TTL 缺失时渲染「— s」（缺失值 + 单位），§8 要求「—」并说明原因（`:231` `remainingSec` 可为 null）
- **维度 8**
  - 正面核实：`:185` `trustedRows = freshness.needsRefetch ? [] : storeRows` 确实生效（`:400` `rows={showRows ? trustedRows : []}`），过期时不渲染任何旧数值，`:363-371` 同时给「旧值已隐藏 + 重新取数」卡，符合 §3.2/§4.2。四态齐：`:296` C-18、`:305` C-19、`:316` `OfflineBanner`、`:384/:405` C-17（带「立即取数」出口）
  - [P2] `WatchScreen.tsx:115` — 排序把缺失值按 0 参与比较（`a.changePct ?? 0`），缺失与 0% 在排序结果里不可区分；展示不受影响但排序误导复核 → **规范缺口**：统一「缺失值沉底」；可推荐写法在同仓 `limitup/LimitUpScreen.tsx:39-41`（用 `NEGATIVE_INFINITY`）
  - [P2] `WatchScreen.tsx:296` — §3.2 加载态规定「信号行骨架 5 条 + 阈值区骨架」，实际 `ScreenSkeleton metrics={4} rows={8}` 整屏替换，阈值区无骨架
- **额外必查**
  - [P2] `WatchScreen.tsx:87` — 任意值 `w-[3px]`；但基准 `Card.tsx:102` 同款，根因是「3px 状态条宽」未进 tokens → 聚合项 M-4
  - [P2] `WatchScreen.tsx:238` — subtitle「剩余 Ns」未上 `ldst-num`（§7.2；`:347/:351/:355` 状态条数值已上 ✓）
  - [P2] `WatchScreen.tsx:512` — 清除快照不可逆，`:515` 写了「不可撤销」但无 §4.3 式强制勾选 → **规范缺口**：§4.3 目前只覆盖「重置分片」
  - 裸 `<table>`：无（仅 `DataTable :398`，`caption :402`）。硬编码色值：无。字号越界：无。`text-hero`：0 处 ✓。`text-text-muted` 均落 `bg-surface`(`:357/:489/:499`)，无 surface-3/soft 可疑组合。信号类型列 `:108` 显示后端 `KIND_LABELS` 中文（`server.py:734`），无英文直显
  - [P2] `WatchScreen.tsx:59` — `LEVEL_BAR` 与 `Card.tsx:67` `SECTION_BAR` 是同一 tone→bg 映射的第二次实现 → 应下沉 `components/ui`

### `/validate` — `frontend/src/screens/ValidateScreen.tsx`

- **维度 1**
  - [P2] `ValidateScreen.tsx:519` — 工具栏分隔条 `h-5 w-px`(20px/1px) 非 token 派生（`:525` 同）→ **规范缺口**：词汇表无 divider 档
- **维度 2**
  - [P2] `ValidateScreen.tsx:926` — 「下一交易日 {date}」裸文本未上 `ldst-num`，同句 `:924-925` 命中数有 → 同卡数字混用
  - [P2] `ValidateScreen.tsx:1010` — 对账计数句（checked/passed/failed/unknown 四个数）全无 `ldst-num`（§7.2）
  - 依据（手写表的对齐没破坏）：表头与数据共用同一 `ROW_GRID`（`:171-172`），操作列 header `text-right :596` 与数据 `justify-end :647` 一致；对账 DataTable 的 target/actual `align:'right'`+`ldst-num` `:987-1003` ✓
- **维度 3**
  - [P1] `ValidateScreen.tsx:680` — 行内编辑器展开/收起零过渡，内容瞬现瞬隐（结构变化应 `duration-base`，tokens `motion`）
  - [P1] `ValidateScreen.tsx:610` — 「标记确认」开关切换时左条 `bg-ok↔bg-warn↔bg-border-strong` 无 `transition-colors duration-fast`（tokens `motion.duration-fast` 的用途注释就是「开关切换」）；`:640-644` 待确认/已确认 Badge 换色同样突跳
- **维度 4**
  - [P1] `ValidateScreen.tsx:587` — **手写行列「其实是表格」**：表头行 `:587-597` + 数据行 `:599-727`，7 列，与同屏对账表 `:950`（已用 DataTable）并存 → 同一屏两种表格实现，是全仓「不同源」最典型的样本。挡住替换的不是能力而是 **C-09 缺「行内展开编辑」** → 正解：给 C-09 增 `expandable`（`renderExpanded` / `expandedContent`）后替换，而不是养第二套表体（验收 2/3）→ 聚合项 M-6
- **维度 5** — 本屏无图表。
- **维度 6**
  - [P2] `ValidateScreen.tsx:933` — `status !== 'ok'` 一律压成「部分缺失」：后端若返回 `degraded` 或其它值，标签与 §5.2 语义不符且会漏出未映射值 → 应对照 `:80-85` `VERDICT` 做枚举全映射
  - 依据（合格）：判定四态、确认/待确认均为中文 Badge 文字+色双编码（`:80-85`/`:640-644`）；无原始英文枚举直显（`:300` 内部比较不渲染）
- **维度 7**
  - [P1] `ValidateScreen.tsx:615` — 代码列 `truncate` 无 `title`；同表 `:622` 触发、`:625` 备注、`:973` 对账条件列**都有** title → 同屏同表一半有一半没有
  - [P2] `ValidateScreen.tsx:591` — 表头写「代码 · 名称」，数据列 `:615-621` 只渲染 code，名称从未存在 → 表头虚报信息位
- **维度 8**
  - 依据（四态齐全）：条件视图 loading `:773` / error `:775-786` / offline `:788-803`（附「离线展示本机草稿（只读）」）/ empty `:805-851`（本地、后端两制空态均有「新增第一条」出口）/ partial `:855-860`；对账视图 loading `:869` / error `:871-881` / empty `:883-900`（出口「去写明日条件」）/ partial `:908-912`
  - 依据（无 0 冒充缺失）：仓位上限整列 `MISSING`+原因 title `:628-633` 并在表下 `:731-732` 再解释；target/actual 经 `:158-161` `formatMetricValue` null→`—`；`:902-903` `?? results.length` 是合法推导
  - [P1] `ValidateScreen.tsx:331` — §3.3「新增即插入空条件**并聚焦**代码/标的」只做了展开（`setExpanded`），无 focus 调用 → 每次新增用户还得自己点输入框
  - [P1] `ValidateScreen.tsx:640` — §3.3「确认后显示确认时间」未实现（数据层也没有 `confirmedAt`）→ **规范缺口**：先补 store 字段
- **额外必查**
  - [P1] `ValidateScreen.tsx:586` — 任意值 `min-w-[960px]`（表最小宽无 token）；[P1] `:171-172` `ROW_GRID` 整串任意值列宽 `grid-cols-[2.5rem_minmax(0,1fr)…5.5rem_6.5rem]`，列宽体系完全脱离 tokens；[P1] `:610` `w-[3px]`（M-4）
  - [P1] `ValidateScreen.tsx:610` — 这 3px 状态条是 C-10 `SectionCard` 状态条（`Card.tsx:100-103`，同为 `absolute inset-y-0 left-0 w-[3px]`+tone 映射）的**第二次重复实现**，还多出一档 `confirmed===null→bg-border-strong` → 应把「行级状态条」下沉进 C-10（`WatchScreen.tsx:87` 是第三处）→ 聚合项 M-4
  - [P1] `ValidateScreen.tsx:587` — **裸 `<table>` 计数器抓不到它**：手写 div 栅格完全无表格语义（无 `role="table"`/`columnheader`/`caption`/`scope`），读屏用户面对 7 列 div 一无所知（§9；对照同屏 `:950` 合规）
  - [P2] `ValidateScreen.tsx:683` — `min-[1100px]:grid-cols-[1fr_1fr_2fr]` 与 `desk:`/`rail:` 两套并存，1100 档与 §2 的 1024/1280 都对不上（M-5）
  - [P2] `ValidateScreen.tsx:1071` / `:1049` — Toast 与 ConfirmDialog 进出零过渡（本体 `Feedback.tsx:201-218`、`components/ui/Overlay.tsx` 只做了行为没做动效；tokens `motion.duration-base` 用途明文含「抽屉滑入、Toast 进出」）→ 聚合项 M-3
  - [P2] `ValidateScreen.tsx:937` — §3.3「对账按钮 15:00 前禁用并说明原因」：本屏根本没有对账触发按钮（对账由收盘任务写入），只有「人工复核」disabled + 15:00 文案 `:941` → **规范缺口**：登记「页面不发起对账」的 DEC
  - 硬编码色值：无（`:611` 全 token 类）。字号越界：无。`text-hero`：0 处 ✓。A11y 其余合格：`aria-expanded :650`、`aria-pressed :659`、`aria-describedby :606/:643`、`Field htmlFor :685-721`
  - 局部组件：`ROW_GRID`(`:171`) 本屏私有「表格行形状」→ 随 M-6 一并下沉

### `/journal` — `frontend/src/screens/JournalScreen.tsx`

- **维度 1**
  - [P2] `JournalScreen.tsx:197` — 列宽 `'144px'`/`:204` `'168px'` 是魔法数（`Column.width` 是组件合法通道，但数值无 token 来源）→ **规范缺口**：定列宽档
  - [P2] `JournalScreen.tsx:552` — `maxHeight="480px"` 视口高魔法数（tokens 无表体最大高）→ **规范缺口**：新增 table-max-height
- **维度 2** — **本屏无该问题**。依据：`:200-206` 数值列 `align:'right'`+`ldst-num`，表头与数据同源于 `Column.align`（`Table.tsx:171-175`）；指标值经 C-11 自带 `ldst-num`（`Card.tsx:146`）。
- **维度 3** — **本屏无该问题**。依据：可点元素 `:351/:526/:563` 均为 Button（过渡在 `Button.tsx:17`），表格行 hover 过渡由 `Table.tsx:135` 提供；屏文件 transition/duration 为 0 属「全走组件」的正常形态。
- **维度 4** — **本屏无该问题**（正面示范）：聚合表已用 C-09 DataTable（`:546-569`，`caption :550`、`density compact`、空态在表体内 `:553-568`）——这是基准四屏该有的样子。
- **维度 5** — 本屏无图表。
- **维度 6**
  - [P2] `JournalScreen.tsx:487` — 同为「口径」徽标，胜率 `:468` 带 title 悬浮解释、盈亏比 `:487` 与「聚合」`:453`、「本机」`:499` 不带 → 同一概念 tooltip 有无不一
  - 依据（其余无问题）：本屏数据面无 envelope，无需 freshness 徽标；`data_source_status` 未英文直显，均映射中文（`:289-297`）
- **维度 7** — **本屏无该问题**。依据：口径列允许换行不截断 `:208-211`；命令 code 块 `break-all :521`；卡内 truncate 都在组件内且是静态中文短词（`Card.tsx:140`）。
- **维度 8**
  - 依据（四态齐全）：loading `:370-377`、error `:379-386`（+重试取数）、empty `:503-534`（复制/刷新出口）、partial `:422-430`、offline `:410-421`
  - 依据（**「样本不足」已实现**，issue 点名的必查项核实通过）：`:390` `enoughSample`(<5 闭环) → 胜率 `:392-397`、盈亏比 `:398-403` 显示「样本不足」而非 0%，note 给口径 `:464/:483`，无样本时 `—` `:397/:403`，`:605-607` 重申「不拿 0 冒充」
  - [P2] `JournalScreen.tsx:323` — `viewStats?.total_trades ?? 0`（含 `:325` buy/sell）作用在可空对象上：现有推导链 `:253-274` 保证 normal 态非空，实际不可达 0、不会显示假 0，但按 §8 应改 `MISSING` 或收紧类型 → 列此备查，不构成 P0
- **额外必查**
  - [P1] `JournalScreen.tsx:370` — 隐私条**主路径存在**（`:432-435`「本机私有」Banner 无条件渲染，含「只写入本机 SQLite，不上传、不进 AI 上下文、不被 MCP 读取」，与 §3.4 硬约束一致；屏内无任何同步/分享/AI 入口 `:603`/`:611-613`），但 loading(`:370-377`) 与 error(`:379-386`) 两个提前 return 分支不含隐私条 → 「顶部常驻」在这两态被打断（§3.4 硬约束；主路径在，故不判 P0）
  - [P1] `JournalScreen.tsx:520` — 任意值 `max-w-[720px]`（与 `Review:640` 同值各写各的，M-4）
  - [P2] `JournalScreen.tsx:443` — `rail:grid-cols-2` 使 <1024 也两列，违反 §2「<1024 指标单列」；`rail` 是 ≤1279 语义，覆盖过宽 → **规范缺口**：`tw:40-44` 的 screens 表达不了 1024 这条线，需补 `mid:` 档（M-5）
  - [P1] `JournalScreen.tsx:216` — §3.4 L0 的「时间范围筛选」未实现，`api.journalStats({})` 固定请求全部时间，区间只在 `:121-126` 被动展示、无输入口；[P1] `:443` — §3.4 L1 的五项纪律统计（按计划执行率/追高/止损执行率/单日笔数/连亏后加仓）**无一实现**，四卡换成笔数/胜率/盈亏比/已实现盈亏，仅「偏离计划笔数」`:185` 沾边 → UI-SPEC §3.4 与 DEC-015 的冲突未回写规范（**规范缺口**：§3.4 应按 DEC-015 修订，否则基准屏的 L1 就是残缺的）；[P2] 全屏无 §3.4 L3「导出本机 CSV」「删除单条」入口（`:589` 引导去终端，可接受但未登记例外）
  - [P2] `JournalScreen.tsx:106` — 局部 `formatSignedMoney` 重新发明 `lib/format.ts:28-53` 的 tone/arrow/符号口径 → 应在 `lib/format` 扩展 money 版，否则第三处又会抄一遍（M-7）
  - 裸 `<table>`：无。硬编码色值：无。字号越界：无。`text-hero`：0 处 ✓。muted 于 surface-3/soft：无（`:521` 用 secondary ✓）。A11y：`caption :550`、禁用有可见原因 `:360-361`、notice `role="status" :438`

---
## 4. 另四张根屏（UI-SPEC 未覆盖，但被当作参照物）

### `/` — `frontend/src/screens/HomeScreen.tsx`

- **维度 1**
  - [P2] `HomeScreen.tsx:97` — `gap-1.5`(6px) 不在 space 刻度，与同屏 `:429/:449` 的 `gap-4`、`:339/:349/:358` 的 `gap-5` 混用（tokens `space.*` / `tw:80`）
  - [P2] `HomeScreen.tsx:502` — `mr-1.5`(6px) 同上；`WeeklyScreen.tsx:560` 同类「代码前缀」用 `mr-2` → 跨屏半步刻度不一致
  - [P1] `HomeScreen.tsx:458` — `bodyClassName="p-0"` 与 `:480/:490` 的 `p-4` 同层级混用；表体齐边是合理例外，但 `:480` 显式重复写 `p-4` 是 C-11 默认值（`Card.tsx:105`），属冗余噪音且**例外无注释登记**（UI-SPEC §12.3「偏差需说明理由，不静默例外」）
  - 依据：顶栏栅格 `:377`/`:449` 均 `gap-4`；页面级 `gap-5` 三处一致
- **维度 2**
  - [P2] `HomeScreen.tsx:147` — 「代码」列未声明 `align`（默认左）却上了 `ldst-num`，与 `:153/:161` 两个 `align:'right'` 数值列同表混排 → 代码列保持左对齐是对的，但需在 `Column` 注释登记，否则别的屏会照抄「数字列不写 align」（C-09 `Table.tsx:50` `alignClass`）
  - 依据（无破坏）：`142-165` 四列对齐全部由 DataTable 统给（`Table.tsx:170-177`），无「表头居中/数据右对齐」撕裂；`:500-506` 手写异动行数值侧用 `SignedNumber` + `justify-between`，右落位正确
- **维度 3** — **本屏无该问题**。依据：`:437` 待办行 `transition-colors duration-fast hover:bg-surface-3`（本仓可点行的基准写法）；`:326` 刷新走 C-01 Button；表体行过渡 `Table.tsx:135` 统给；`TemperatureBar 94-122` 非交互。
- **维度 4**
  - [P1] `HomeScreen.tsx:496` — 手写「最近异动」`<ul>`（`496-509`）：2 列、含可排序数值（`:246-253` 已按 `abs(change_pct)` 排序切片），完全可用 `DataTable` 的 `align:'right'`+`sort` 替换；现在既不能排序也没有 `caption`/`scope`（C-09；§9 表格须 caption+scope）
  - 依据（`:432-445` 待办列表不换是对的）：那是「整行链接 + StatusDot + 动作」，换成 DataTable 会丢 `<Link>` 语义；行高/内距 `min-h-control px-4 py-2`(`:437`) 与 CardHeader 对齐 ✓
- **维度 5**（本屏无 uplot；`TemperatureBar` 是 DOM 可视化）
  - [P2] `HomeScreen.tsx:105` — 五段区间带用内联 `style={{opacity: 0.6/0.75}}`（`:85-91` `BAND_STEPS`）在 `bg-chart-band` 单色上做透明度分层：透明度不是 token，深浅主题下同一 `chart.band`(rgba 0.12/0.10) 叠加不同 opacity 后对比不可控 → **规范缺口**：定「chart-band 分层」token 或禁止内联 opacity
  - [P1] `HomeScreen.tsx:111` — 任意值 `w-[2px]`（当前指针厚度未 token 化）→ **规范缺口**：补 `w-indicator`(2px)（M-4）
  - [P2] `HomeScreen.tsx:111` — 位置指针用 `bg-text`，而语义上的「参考线」已有 `--ldst-chart-threshold`（`MarketScreen.tsx:593` 的 50 中性线正是用它）→ 同一概念「指示线」两屏两个色源（§7.4）
  - 依据（无越界）：`:101` 轨道 `border-border bg-surface-2`、`:105` `bg-chart-band`，无颜色字面量、无 `bg-accent`/`text-up` 当序列色
- **维度 6**
  - [P1] `HomeScreen.tsx:69` — `SourceBadge` 是本屏本地实现（`Market:105`/`Weekly:166`/`Review:196` 各又写一份）；UI-SPEC §6 组件清单 `:286-287` 要求 `kit/SourceBadges.tsx` 的 `FreshnessBadge`+`SourceStatusBadge` 供四屏复用 → **跨屏不同源的共同根因，应最先下沉**（M-1）。本屏是其余屏照抄的对象，但参照物本身就是重复实现，**不能当结构基准**
  - [P1] `HomeScreen.tsx:79` — 把 `freshness` 与 `source_status` 合并成「实时·完整」一枚徽标，违反 §5.3「两枚并排，不合并成一个健康度」；且两态只有文字+颜色，没有 §5.1 的 ●/◐/○（§9）
  - [P1] `HomeScreen.tsx:78` — 溯源明细（来源/数据时间/取数）只挂在 `title` 属性：键盘与触屏用户永远拿不到（`Badge.tsx:31` 也只渲染 `title`，无 `aria-describedby`）→ **规范缺口**：§5.1「悬浮说明」需定义为可聚焦/可展开的等价通道（M-8）
  - [P1] `HomeScreen.tsx:390` — 「最高连板」卡的徽标取 `sentiment.data ?? ladder.data`：情绪源存在时，该连板指标的溯源永远显示**情绪接口**的 freshness/source_detail，hover 出来的来源与卡片内容不符（同屏 `:384/:394` 的 note 确实来自 `viewLadder`）→ 溯源信息错位（§5.2；验收「降级可见」）。`:380/:405/:417` 三张卡映射正确，只有这一处串了源
  - [P2] `HomeScreen.tsx:156` — 连板数列用 `text-up`（涨方向色）表达「板数高度」；§7.1 规定涨跌色只作用于数字/箭头且语义为方向，`MarketScreen.tsx:177` 同类「板数」列用 `text-text` → 同语义两屏两色，色语义漂移
  - 依据（尺寸基准可用）：徽标视觉重量（`Badge` h-5 / `text-caption`，`Badge.tsx:33`）与 Market/Weekly 一致
- **维度 7**
  - [P1] `HomeScreen.tsx:149` — 名称列 `truncate` 无 `title`；且该列未给 `width`（对比 `:147/:156/:163` 都给了 92/88/96px），长名称在 `w-full min-w-full` 表里会撑宽整张表并触发横向滚动（`Table.tsx:159-160`）→ 信息永久丢失 + 溢出风险（验收「表格长列要有 max-w」）
  - [P1] `HomeScreen.tsx:440` — 待办标题 `truncate` 无 `title`（`min-w-0 flex-1` 收缩正确，但被截掉的半句不可恢复）
  - [P1] `HomeScreen.tsx:501` — 异动行「代码+名称」整段 `truncate` 无 `title`；`:502` 的代码嵌在截断容器内，一旦截断连代码一起丢（对照正例 `MarketScreen.tsx:191` 的 `truncate`+`title={全量拼接}`）
  - [P1] `HomeScreen.tsx:382` — 五张 `MetricCard` 的 `note`（缺失原因，如 `:401`「本地无板块/题材数据（先运行 ldst sync）」）被组件内部 `truncate` 且不透传 `title`（`Card.tsx:155`）→ 「为什么是 —」这句最关键的说明会被无声截掉，属**基准组件缺陷**，本屏 4 处长 note 全受影响（§8）→ 聚合项 M-4
- **维度 8**
  - [P1] `HomeScreen.tsx:275` — 整屏空态（`state==='empty'`，`:201/:210` 判定）时摘要仍走 `数据完整 · 更新于 …`：四路请求都 ready 但无数据，标题告诉用户「数据完整」，画面全是 `—` → 状态文案与画面矛盾，用户无法区分「没数据」和「取到了但为空」（§8 先结论后原因；§1 原则 3）
  - [P1] `HomeScreen.tsx:467` — 连板空态 `EmptyState` 无 `action` 出口（只把 `ldst sync` 写进 description）；`:481`、`:494` 同样无出口。对照 `MarketScreen.tsx:148` `NotSynced`（结论→原因→命令 code 块→「重新检查」按钮）→ 同为「没数据」，两屏空态形态与出口重量不同源（C-17；`Feedback.tsx:67`「空态必须给出下一步动作」）
  - [P2] `HomeScreen.tsx:494` — 「最近异动」空态文案「去盯盘页取数」是口头指引，没有 `<Link>` 动作（同屏 `:432-445` 已有可点链接范式）
  - 依据（齐全）：`:337` loading→`ScreenSkeleton metrics={5}`（与 `:377` 实际 5 卡一致）、`:346` error→`ErrorBlock`+`describeApiError`、`:361` offline→`OfflineBanner`、`:363` partial→`ErrorBlock variant="banner"` 并列出 `failedLabels`(`:309-316`)；缺失值统一 `MISSING`（`:148/:156/:163/:400/:406/:418`），无 `.toFixed()` 直接作用于 null
  - 依据（`:250` 的 `?? 0` **不算违规**）：它作用在 `:249` `filter(r => r.change_pct !== null)` 之后的排序表达式，仅用于排序、不落显示 → 不是「用 0 冒充缺失」。这类「排序里的 0」与 §1 表 B 类「展示位的 0」必须区分，否则误报会淹没真报
- **额外必查**
  - 裸 `<table>`：无（表体全 DataTable，`460-476` 带 `caption :464`）。字号越界：无。`text-hero`：0 处 ✓。硬编码色值：无（仅 `:105` opacity、`:112` `left:${pct}%` 数据驱动定位）
  - 对比度：`:116/:511/:518` 的 `text-text-muted` 落在页面 `bg-bg`（`Layout.tsx:186`）上，`--ldst-bg` 比 `--ldst-surface` 更深，对比只升不降 → 本屏无该问题（§9 仅对 `--ldst-surface` 校验）
  - A11y：`:99-100` `TemperatureBar` 有 `role="img"` + 缺失态 label（正例）、`:103` 装饰层 `aria-hidden`、表头 `scope="col"`/`aria-sort` 由 `Table.tsx:168/171` 统给、`:437` 整行可点是 `<Link>` 键盘等价成立 → 无硬缺陷，仅缺「空态整屏出口」
  - 局部组件：`SourceBadge`(`:69`)、`TemperatureBar`(`:94`)、`BAND_STEPS`(`:85`)、`formatSeal`(`:126`)、`LADDER_COLUMNS`(`:142`)。其中 **`SourceBadge` 必须下沉**；`TemperatureBar` 建议连同 `FRESHNESS_LABEL/SourceLabel`(`:56/:62`) 一起进 `components/ui`——`FRESHNESS_LABEL` 与 `MarketScreen:93`、`WeeklyScreen:176` 是三份重复真源（M-1）

### `/market` — `frontend/src/screens/MarketScreen.tsx`

- **维度 1**
  - [P1] `MarketScreen.tsx:529` — 指标网格 `gap-3`(12px)，而 `HomeScreen:377`、`WeeklyScreen:381` 同为「顶部指标卡行」用 `gap-4`(16px) → 同层级区块跨屏两档不一致（§7.3；影响 3 屏视觉基线对齐，故 P1）
  - [P2] `MarketScreen.tsx:730` — 命令码块 `px-1.5 py-0.5`(6/2px)，同屏 `:159` 同一概念（`ldst sync --date …`）用 `px-2 py-1` → 同屏同类元素两套刻度，且都在 token 刻度之外（tokens `space` 无 0.5/1.5 档）
  - [P2] `MarketScreen.tsx:702` — 三段接入行 `px-4 py-2` 未给行高（对照 `HomeScreen:437` 同形态链接有 `min-h-control`）→ 单行/双行（`:718` 说明行存在时）行高在 40+ 与 60+ 间浮动
  - 依据：页面级 `gap-5`(`:476/:483`)、主副栏 `gap-4`(`:525/:526/:698`)、段内 `gap-3`(`:583/:636/:677`) 自身一致
- **维度 2**
  - [P1] `MarketScreen.tsx:177` — LADDER 表「板数」列有 `sort`、是数值，但**未声明 `align:'right'`**（`174-178`），同表 `:181`「家数」、`:198`「晋级率」都是右对齐 → 同一张表两个数值列一左一右，表头跟着 `alignClass` 左对齐（`Table.tsx:170-176`），排序按钮也在左（`Table.tsx:185` 的 `flex-row-reverse` 只在 right 时启用）→ 排序 affordance 在同表内两种位置（验收「数值列右对齐」）
  - 依据：`:210-228` 交易日/情绪温度、`:230-280` 题材 5 列均 `align:'right'`+`ldst-num`（`:223/:244/:255/:274`）✓
- **维度 3**
  - [P1] `MarketScreen.tsx:711` — 右栏「三段数据接入」切换按钮 `hover:text-text hover:underline` 完全没有 `transition-colors duration-fast`，下划线瞬时出现（对照 `Layout.tsx:163/249`、`Segmented.tsx:82`、`HomeScreen:437` 都写了）→ 同屏左侧 Segmented(`:515`) 有过渡、右侧同类切换无过渡，**同一事实（选分段）两种反馈节奏**
- **维度 4** — **本屏无该问题**（依据：三处区块已是 C-09 —— `:596` 历史序列、`:642` 梯队明细、`:682` 题材明细，均带 `caption` `:600/:646/:686`；`:700-723` 是带 `<button>` 的导航列表、`:726-744` 是口径说明块，换掉会丢可点语义与 `aria-current`）
  - [P2] `MarketScreen.tsx:171` — `LADDER_COLUMNS` 的「代表股」列（`:188-195`）把 3 只股票名拼进一个单元格：「梯队 → 个股」的下钻信息被压成一行文本，正解是展开行或抽屉（C-15）→ **规范缺口**：C-09 无 `expandable` 契约（与 M-6 同一个缺口）
- **维度 5（图表，本屏是全仓基准）** — **本屏无该问题**，且**可作为基准**：
  - 依据：`:589` `LineChart`、`:637` `BarChart` 只传数据与 `height/ariaLabel`，屏内不出现任何颜色字面量或把 `bg-accent`/`text-up` 当序列色；取色唯一通道 `Chart.tsx:52/160` `chartPalette()` → `charts/cssVar.ts:45-57` 读 `--ldst-chart-*`，`FALLBACK`(`cssVar.ts:10`) 只在 jsdom 生效；序列色 `palette.line`(`Chart.tsx:60`)、`palette.barUp`(`:192`)，阈值线 `palette.threshold`(`:82`)，区间带 `palette.band`(`:74`)
  - 网格线：`:99/:107` `grid:{stroke:palette.grid,width:1}` 用 `--ldst-chart-grid` 弱化 1px，未降级用 `border` 强度；柱图 x 轴 `grid:{show:false}`(`:176`) 正确（§6 CH-02）；刻度线 `ticks.stroke:palette.grid`(`:100/:177/:185`)
  - 轴文字：`:97/:104/:174/:181` `stroke: palette.axis`，字号由 `AXIS_FONT`(`:34`) 给（tokens `chart.axis` 注释即「坐标轴标签文字 12px」）
  - 「图必配表」执行最好的一屏：`:594/:640` 有含结论的 `ariaLabel`，`:596/:642/:682` 每个图后都配数据表（§6.4）
  - [P2] `MarketScreen.tsx:84` — `CHART_HEIGHT = 320` 以数字 prop 传入(`:591/:639`) 落为内联样式；tokens `layout` 无 `chartHeight` → **规范缺口**：§6.1 的「市场页折线 320」应进 token（建议 `h-chart-market`=320 / `h-chart-compact`=220），否则别的屏各写各的
  - [P2] `MarketScreen.tsx:678` — 同屏 DOM 横条（CH-03 `HBarList`）序列色是 `bg-accent`、轨道 `bg-surface-3`（`MiniChart.tsx:37-39`），**不走 `chartPalette()`** → 与折线 `chart-line` 构成两个真源 → **规范缺口**：`chartPalette()/cssVar()` 规则目前只覆盖 canvas，DOM 图的取色通道未定义（M-10）
  - [P2] `MarketScreen.tsx:566` — 副标题写「灰色区间带为 40–60」，实际是 `--ldst-chart-band`（暗色 `rgba(76,141,255,.12)`，低饱和蓝不是灰）→ 文案与视觉不符，二者必须动一个
- **维度 6**
  - [P1] `MarketScreen.tsx:105` — 第三份本地 `SourceBadge`；`EnvelopeMeta` 在本屏(`:99-102`)与 `HomeScreen:54` 定义不同（Pick 字段数不同）→ 下沉前这三份类型无法互相替换（§6 清单；M-1）
  - [P1] `MarketScreen.tsx:115` — 徽标只显示 `FRESHNESS_LABEL[freshness]`，`source_status` 只体现在 `tone` 颜色(`:107`) → `degraded`/`partial` 无文字，违反 §5.2（三态均须标签）与 §9；同样缺 §5.1 图形。本屏比 Home 更弱 → **不能当溯源徽标基准**
  - [P1] `MarketScreen.tsx:570` — CH-01 卡头徽标 `history.data ?? sentiment.data`：历史序列缺失时徽标显示当日源的「实时/缓存」，而图上画的仍是历史点（`linePoints` 在 `:364-373` 分两种来源）→ 两事实被一枚徽标压成一个（§5.3）
  - [P2] `MarketScreen.tsx:121` — `SourceLine`（卡底「来源/数据时间/取数」整行）也是本屏本地实现，Home/Weekly 各无对应（Weekly 写进 `:603-608` 页脚，Home 只挂徽标 title）→ §5.1 L2 溯源三种落位；应连同 `NotSynced`(`:136`) 一起下沉 `SourceFooter`/`NotSyncedEmpty`
  - 依据（**降级可见的正例基准，建议全仓照抄**）：`:716` `Badge tone ok/warn` + 中文「已同步/数据未同步」文字双编码；`:571` 折线来源用独立 Badge 标「近 N 日/单点」；`:148` `NotSynced` 的「结论→原因→命令→重新检查」结构是全仓最好的空态出口
- **维度 7**
  - [P1] `MarketScreen.tsx:235` — 题材表「题材」列 `truncate` 无 `title`（对照同文件 `:191` 的正例）→ 长题材名永久丢失
  - [P1] `MarketScreen.tsx:264` — 「龙头」列 `width:'128px'` + `truncate` 无 `title`(`262-265`) → 龙头股票名丢失
  - [P2] `MarketScreen.tsx:235` — 「题材」列未设 `width`（其余四列 `:239/:251/:262/:271` 都设了）→ 长名挤掉「涨停家数/较前日」的视觉位置（`Table.tsx:170` 只对有 width 的列做样式）
  - 依据（正例）：`:191` `truncate`+`title` 全量拼接；`:203` `{MISSING}` + `title='后端未提供按板位的晋级率，不臆测'` —— 这是 §8「— + 原因」的最佳实现
- **维度 8**
  - [P1] `MarketScreen.tsx:607` — 历史表 `empty` 传裸文本 `<p>`（不是 C-17 `EmptyState`），且文案写「**故不出表**」，实际 DataTable 空态分支仍渲染完整表头 + `min-h-empty` 空容器（`Table.tsx:120-127/159-206`）→ 文案与画面矛盾；同屏 `:148` `NotSynced` 才是这里该有的写法
  - [P2] `MarketScreen.tsx:202` — 「晋级率」列恒为 `{MISSING}`：口径正确、有 tooltip，但整列永久空占 96px → **规范缺口**：C-09 无「列级禁用 + 说明」契约
  - 依据（**四态本屏最强，可当基准**）：`:473` loading→`ScreenSkeleton metrics={3} rows={6}`（与 `:529` 三卡一致）、`:486` offline、`:487-489` error→`ErrorBlock variant="banner"` 且强制态无 error 时用 `DEMO_ERROR`(`:87`) 而非 `undefined` 拼接、`:491` partial 列出失败源数量、`:500` empty→`Banner tone="info"` 并给 `ldst sync` 命令，三段各自 `NotSynced`(`:576/:629/:663`) 带出口按钮；`:352-353` 注释登记的「`?state=loading` 下刻意渲染空态」属登记过的例外（§12.3 ✓）
  - 依据（不用 0 冒充缺失）：`:534` `currentTemperature === null ? MISSING`、`:544` `formatRatePct(…).text`、`:554` `gapLevels === null ? MISSING`、`:224/:245/:256` `isFiniteNumber(…) ? … : MISSING`；屏内仅 `:344` `(ladderResult?.ladder.length ?? 0)` 用于「有没有数据」的布尔判定，不落显示 → 非违规
- **额外必查**
  - 裸 `<table>`：无。硬编码色值：无（屏内零 hex/rgb）。字号越界：无。`text-hero`：仅 `:532` 一处 ✓
  - [P1] `MarketScreen.tsx:529` — 自定义断点 `min-[640px]:grid-cols-3` 与 `:525` 的 `desk:grid-cols-12` 两套并存；§2 断点表规定 `<1024` 指标单列，640-1023 排 3 张 `h-metric`（每张约 190px）却放 36px 数值（`:533` `hero`）→ 全仓截断/挤压风险最高的一处（M-5）
  - [P2] `MarketScreen.tsx:203` — `text-text-muted` 落表体单元格，而 DataTable 行 hover 底色是 `bg-surface-3`（`Table.tsx:135`）；muted 只对 `--ldst-surface` 校验过（§9），surface-3 上未验证；同理 sticky 首列 `bg-surface`(`Table.tsx:147`) 与非 hover 行底色不同 → 建议基准侧把「弱化文本」统一升到 `text-text-secondary`（M-10）
  - [P2] `MarketScreen.tsx:515` — 分段用 C-07 `Segmented`（`radiogroup`+`radio`，方向键可切，`Segmented.tsx:37-55`）语义正确，但 `:525` 内容区没有 `role="tabpanel"`/`aria-labelledby` 关联；`:704-714` 右栏切换用 `aria-current="true"` 与 Segmented 的 `aria-checked` 是两套「当前段」表达 → 读屏对同一状态两种播报（**规范缺口**：需定 C-07 与面板关联条款）
  - 局部组件：`SourceBadge`(`:105`)、`SourceLine`(`:121`)、`NotSynced`(`:136`)、`FRESHNESS_LABEL`(`:93`)、`EnvelopeMeta`(`:99`)、`ThemeColumns()`(`:230` 工厂函数，Home/Weekly 用常量 → 同概念两种写法)。**`NotSynced` 最该被抬成全仓共享空态（它已经做对了），`SourceBadge`/`FRESHNESS_LABEL` 最该被取代。**

### `/weekly` — `frontend/src/screens/WeeklyScreen.tsx`

- **维度 1**
  - [P1] `WeeklyScreen.tsx:415` — 手写概览块 `rounded border border-border bg-surface-2 px-4 py-3`：与同屏 `:444` SectionCard（`bg-surface`+`shadow-card`+标题栏 `px-4 py-2`+体 `p-4`，`Card.tsx:20/38/105`）视觉重量不一致（少阴影、底色降一档、12/16px 混在一层）→ 同屏两套卡片刻度
  - [P2] `WeeklyScreen.tsx:556` — `gap-1.5`(6px) 与 `:461` `gap-2`、`:440/:506/:554` `gap-4` 并存；`HomeScreen:496` 同类「龙头/异动小列表」用 `gap-2` → 跨屏同层级列表间距 6 vs 8px（tokens `space` 无 1.5 档）
  - [P2] `WeeklyScreen.tsx:522` — `unit=" 只"`（`:546` 为 `" 天"`）带前导空格，叠加 `HBarList` 内部 `ml-0.5`(2px)（`MiniChart.tsx:45`）→ 数字与单位实际 10px；`MarketScreen:555` 同一 prop 用 `unit='档'` 不带空格 → 单位间距跨屏不一致（§8）
- **维度 2**
  - [P1] `WeeklyScreen.tsx:345` — 「每日序列」表 8 个数值列（`:340-357` 温度/晋级率/封板率/涨停/炸板/最高板/断层）渲染时**全部未上 `ldst-num`**（直接返回 `formatOneDecimal/formatRatePct/formatInt` 的字符串），DataTable 只给 `text-body text-text`(`Table.tsx:145`) → 数值列不等宽，与 `HomeScreen:156/163`、`MarketScreen:177/185/223` 同概念列两套写法，直接破坏 §7.2「所有价格、涨跌幅、计数、时间必须 `.ldst-num`」
  - [P2] `WeeklyScreen.tsx:362` — 「断层」列 `align:'right'` 但内容是 `1、3、5` 这类中文顿号拼接文本，右对齐的应是数值 → 建议拆成计数 + tooltip
  - [P2] `WeeklyScreen.tsx:346` — 排序哨兵 `(a.temperature ?? -1)` 把缺失当 -1 参与排序，会把「—」的行排到最前且不聚簇 → 应在 sort 里显式让 null 沉底/置顶并注释（§1 原则 3）
  - 依据：表头/数据对齐源仍由 `Column.align` 统给，本屏未手写表头
- **维度 3** — **本屏无该问题**（依据：`:263-297` 三个操作全走 C-01 Button；表体行过渡 `Table.tsx:135`；`:444-485` SectionCard 非交互；无手写可点行/可展开区）
  - [P2] `WeeklyScreen.tsx:415` — 该手写块若要变成可折叠（概览常需展开/收起）当前既无展开态也无可点元素；其 L2 内容(`:418-420` 模型名与生成时间)永远占位 → **规范缺口**：§3.1 只规定「段落状态」可折叠，未规定概览块
- **维度 4**
  - [P1] `WeeklyScreen.tsx:556` — 「本周龙头」手写 `<ul>`(`556-571`)：2 列（代码+名称 / 板数+日期）、数值右落位(`:563`)、可排序，而屏内 `:497` 已经在用 DataTable → 同一屏两种「多行多列」并存，换 DataTable 无障碍（`rowKey` 用 `${code}-${date}`，缺的 `caption` 可一并补）
  - [P1] `WeeklyScreen.tsx:586` — 「待验证项结算」手写 `<ul>`(`586-596`)：3 列（日期/条件/结算结果），`settled/passed` 是天然可排序列；`:587` `.slice(0,8)` 截断后既不提示「共 N 条只展示 8 条」也没入口，换 DataTable 可顺带解决（C-09 `maxHeight`）
  - [P2] `WeeklyScreen.tsx:517` — 涨跌分布 / `:540` 主线题材用 `HBarList`（CH-03 授权组件 `MiniChart.tsx:25`），不算手写表格，保持「图 + 表」配对即可（§6.4）
- **维度 5**（本屏用 Sparkline / HBarList，无 uplot）
  - [P2] `WeeklyScreen.tsx:390` — `Sparkline` 未传 `tone`，默认 `flat` → 折线序列色走 `text-text-secondary`(`MiniChart.tsx:83`)，而 CH-01 折线序列色是 `--ldst-chart-line`(`Chart.tsx:60`)。同一「情绪温度走势」两屏两色源 → **规范缺口**：MiniChart 应改用 `chart-line` 语义（M-10）
  - [P2] `WeeklyScreen.tsx:540` — `HBarList` 填充用 `bg-accent`(`MiniChart.tsx:39`)、轨道 `bg-surface-3`，未走 `chartPalette()`，而 tokens `chart` 组已有 `bar-up/series-2` 序列色 → **规范缺口**：DOM 图取色通道无约束（M-10）
  - 依据（无违规）：本屏无硬编码色值、无把 `text-up` 当序列色（涨跌方向色只出现在 `SignedNumber`/徽标语义里）
- **维度 6**
  - [P1] `WeeklyScreen.tsx:176` — **原始英文枚举直显**（已回读核实）：`envelope.freshness === 'cached' ? '缓存' : envelope.freshness`，`realtime`/`archive` 会把英文原文打到徽标上（`EnvelopeFreshness` 见 `api.ts:148`）。全仓没有 `FRESHNESS_LABEL` 共享真源，`HomeScreen:56`、`MarketScreen:93` 各有一份完整中文映射，只有本屏漏了 → §5.1 + 验收「枚举不得直显」（M-1）
  - [P1] `WeeklyScreen.tsx:168` — 只按 `source_status` 推导 `tone`（颜色），徽标文本里没有「完整/降级/部分」→ `degraded`/`partial` 对用户不可读，颜色成为唯一编码（§5.2 + §9）
  - [P1] `WeeklyScreen.tsx:166` — 又一份本地 `SourceBadge`（第四份），且签名字段类型手写而非复用 `LadderEnvelope`，与 `HomeScreen:54` 的 `EnvelopeMeta` 分叉 → 下沉时统一为两枚（§5.3、§6 清单）（M-1）
  - [P1] `WeeklyScreen.tsx:601` — 口径说明直接 `Object.entries(facts?.caliber)` 打印**后端英文键名**（如 `temperature_avg：62.8`）给用户，value 也未格式化 → 面向用户的字段名未经映射（§8；**规范缺口**：定「后端 key 不得直显，须有 label 映射」）（M-7）
  - [P2] `WeeklyScreen.tsx:385` — 四张 MetricCard(`:385/:396/:402/:408`) 挂同一个整页 `envelope`：温度、晋级率、封板率、连板高度实际来自不同数据集 → 溯源粒度不足（§5.2；比 `HomeScreen:390` 的错位普遍但危害小一档，周报本身是单端点产物）
  - [P2] `WeeklyScreen.tsx:453` — `MODE_LABEL[section.mode] ?? section.mode`：未登记的 mode 值英文直显（同屏 `:48-53` `SECTION_STATUS` 有 `?? pending` 兜底，`:56-60` `MODE_LABEL` 没有）→ 同一屏两套「未知枚举」处理策略
- **维度 7**
  - [P1] `WeeklyScreen.tsx:558` — `<li className="flex items-baseline justify-between gap-2">` 内的左 `<span>`（`:559`）**没有 `min-w-0`、没有 `truncate`** → 长股票名把右侧板数挤出卡片宽度（flex 子项默认 `min-width:auto` 不收缩）；正例见 `HomeScreen:501`（`min-w-0 truncate`）与 `MarketScreen:191`（`truncate`+`title`）
  - [P1] `WeeklyScreen.tsx:577` — 「待验证项结算」SectionCard 的 `subtitle` 是一条 100+ 字拼接串(`577-583`)，而 `CardHeader` 的 subtitle 是 `truncate` 且无 `title`（`Card.tsx:46`）→ 结算统计后半段永久丢失，这是本屏最关键的口径数字（M-4）
  - [P2] `WeeklyScreen.tsx:588` — 条件文本 `item.text` 长句无 `line-clamp`/`title`，与 `:590` 结果串挤在一行，且 `:587` `.slice(0,8)` 静默截断
  - [P2] `WeeklyScreen.tsx:433` — `missingDays` 拼成一条超长 Banner 正文(`434-436` `join('；')`)，5 天以上挤满一行；§2「同类提示条最多 2 条，其余收进『另有 N 条』」在此未实现（**规范缺口**：多元素缺失清单是否允许单条 Banner 平铺）
- **维度 8**
  - [P1] `WeeklyScreen.tsx:324` — 空态用 **C-19 `ErrorBlock variant="banner"`** 呈现「还没有可用周报」：组件语义是错误（`Feedback.tsx:131` 写死 `role="alert"`），读屏会把「本周未生成」播报成错误，且与 `MarketScreen:148` 的空态范式（`EmptyState`+同步命令+重新检查）不同源。文案本身合格（`:327-330` 给了 `ldst weekly --week` + 重试），只是选错组件（C-17 vs C-19；§5.4、§9 role 语义）
  - [P1] `WeeklyScreen.tsx:502` — 每日序列表体空态传的是裸文本 `<>本周没有可用单日指标。</>`，不是 C-17；`:569`、`:594` 两处 `<li>` 空态同样是裸文本 → 同一屏三种空态写法（`:324` ErrorBlock / `:502` 裸文本 / `:569` 裸文本）
  - [P0] `WeeklyScreen.tsx:543` — 见 §1 表 B（`?? 0` 画出实体条）
  - [P1] `WeeklyScreen.tsx:544` — `均涨幅 ${theme.avg_change_pct ?? MISSING}%`：缺失时渲染成 `—%`（缺失值 + 单位，§8 只允许「—」并说明原因）；存在时渲染后端原始浮点（`api.ts:316`）→ 未过 `formatOneDecimal`/`formatRatePct`，会出现 `3.456789%` 这类越界精度
  - [P2] `WeeklyScreen.tsx:521` — `hint` 里 `${band.share_pct}%` 同样未走格式化（`api.ts:306`），浮点原值直出
  - 依据（覆盖面是本屏唯一可参照项）：`:300` loading→`ScreenSkeleton metrics={4} rows={5}`（与 `:381` 实际 4 卡一致）、`:309` error、`:318/:322` offline、`:375` AI 降级→`Banner tone="warn"`、`:490` 失败段→`Banner tone="danger"`、`:432` 缺交易日→`Banner tone="info"`；缺失值统一 `MISSING`(`:340-367/:544/:561/:590/:601/:604`)
- **额外必查**
  - [P1] `WeeklyScreen.tsx:497` — 该 DataTable **未传 `caption`**（对比 `HomeScreen:464`、`MarketScreen:600/646/686` 全传了），§9「表格必须有 caption」直接不满足（验收项）
  - 裸 `<table>`：无。硬编码色值：无。字号越界：无。`text-hero`：`:385` 1 处 ✓。任意值 `xxx-[yyy]`：无
  - [P1] `WeeklyScreen.tsx:381` — 自定义断点 `min-[1024px]:grid-cols-4`：§2 断点表规定 1024–1279 指标**三列**，本屏排 4 列，且 4 张 `h-metric` 卡配 `text-page` 28px 数值 + 必截断的 label/note（同情形 `HomeScreen:377` 用 `rail:grid-cols-3` 是对的）；[P2] `:506`、`:554` 另有两处 `min-[1100px]:grid-cols-2`（M-5）
  - [P2] `WeeklyScreen.tsx:324` — 空态 `role="alert"`（同维度 8 那条）
  - 局部组件：`SourceBadge`(`:166`)、`SentimentDayRow` 本地行模型(`:181`)、`SECTION_STATUS`(`:48`)、`MODE_LABEL`(`:55`)、`number/text/asStringList` 取值三兄弟(`:104-115`，`MarketScreen` 用 `isFiniteNumber`) → **规范缺口**：「事实包字段安全取值」没有共享工具层，导致本屏 `?? 0` 与别屏 `?? MISSING` 分叉；建议下沉 `lib/safeValue.ts` 并强制 `formatOneDecimal/formatRatePct` 在 hint/value 出口（M-7）
  - **一句话**：`/weekly` 是基准 8 屏里离基准最远的一屏——数值列整体漏 `ldst-num`、两处 `?? 0` 把缺失画成实体条、枚举与后端键名直显、表缺 `caption`、三种空态写法、三处自定义断点挤爆指标卡，并且自己又造了第四份 `SourceBadge`。只有「四态与降级提示的覆盖面」这一项可参照。

### `/settings` — `frontend/src/screens/SettingsScreen.tsx`

- **维度 1**
  - [P2] `SettingsScreen.tsx:1246` — 控制面未启用分支纵列 `gap-3`，`:1263` 正常分支同角色同层级用 `gap-4` → 同层级两套刻度无理由（§7.3）
  - [P2] `SettingsScreen.tsx:325` — 任务进度行 `px-2 py-1.5`（6px）不在 space 刻度；同形态基准提示块是 `px-3 py-2`（`Feedback.tsx:43` Banner / `:210` Toast）
- **维度 2** — **本屏无该问题**。依据：`:482` 端口列 `align:'right'`；`:995` 与 `:1464` 两处 `dl` 的 `dd` 均 `text-right`，dt/dd 对齐同源。
- **维度 3**
  - 核实：全文件过渡 class 共 3 处——`duration-base` 1 处(`:340` 进度条 `transition-[width]`)、`duration-fast` 2 处(`:393` 「打开」外链 hover、`:1221` 分区菜单 hover)。
  - [P1] `SettingsScreen.tsx:570` — 抽屉开/关**完全没有**过渡（`LogsDrawer`→`Drawer`；根因 `components/ui/Overlay.tsx:111-141` 面板无 transition），而 tokens/§7.3 规定抽屉滑入 180ms(base)；`:660`、`:1443` 同病 → 修在 `Overlay.tsx`（C-15，M-3）。本屏是「屏层过渡最足」的屏，却恰恰在规范点名要用 base 的组件上为零
  - [P1] `SettingsScreen.tsx:1473` — Toast 进出无过渡（`Feedback.tsx:201-219` 无 transition），tokens `motion['duration-base'].usage` 明文含「Toast 进出」（C-14；本屏是最大使用者）（M-3）
  - [P2] `SettingsScreen.tsx:340` — `duration-base` 用在进度条宽度动画：tokens `motion['duration-base'].usage` 只写「抽屉滑入、Toast 进出」，hover/开关归 fast；进度条属状态驱动宽度变化，两套用途都不命中 → **规范缺口**：给进度条定档
- **维度 4**
  - [P2] `SettingsScreen.tsx:993` — 「label 左 / 值右 + `border-b` 行」手写两次（`:993` 关于区 dl、`:1462` 元数据抽屉 dl，className 结构逐字相同）；两列无排序，不是 DataTable 替换对象，但应下沉统一描述列表件 → **规范缺口**：C 系列无 `DescriptionList`；同屏 `PeerCard:245` 与 `StaticPeerCard:421` 卡头（`275-291` vs `425-433`）亦手写重复（M-2）
  - 依据（正面核实）：端口表已用 C-09(`:1356`，`caption :1360`、density 跟 prefs `:1361`) ✓；`:706/:729/:843/:852/:861` 全部复用 `components/ui/Field.tsx`（C-03/C-06），**无**自造 Row/Field/Chip 与之重复
- **维度 5** — 本屏无图表（`:589/:1426` 是日志/命令 `<pre>` 文本块，底色走 `bg-sunken` token ✓）。
- **维度 6**
  - [P2] `SettingsScreen.tsx:471` — 端口状态用 emoji ✅/⚠️/❌ 做图形编码（`:471/:472/:474`，`caption :1360` 还复述一遍）：确实「文字+图形、不靠色」，但图形词汇与全站 ●/◐/○ + `StatusDot`/`Badge` 体系不一致（§5.1）→ **规范缺口**：合法图形应为 token 图形集，禁 emoji
  - [P2] `SettingsScreen.tsx:327` — `ACTION_LABEL[job.action] ?? job.action`：后端 `action` 是非封闭 union（`api.ts:645` `… | string`），新动作名将以英文直显（§5.3 的兜底口子）
  - 依据（合格）：peer 状态 `StatusDot`+文字+脉冲(`:278`, C-12)、job 徽标 `Badge`(`:326`, C-13)、状态只取后端 `status_label`(`:268`，前端不推断) ✓
- **维度 7**
  - [P1] `SettingsScreen.tsx:329` — 任务步骤 `min-w-0 flex-1 truncate` 无 `title`：安装/启动失败时最关键的一行 `job.step` 超长即永久丢失（正例就在同文件 `:438` `truncate`+`title={peer.directory}`）
  - [P2] `SettingsScreen.tsx:281` — `PeerCard` 标题 h3 `truncate` 无 title(`:429` 同)；当前产品名短，风险潜伏
- **维度 8**
  - [P0] `SettingsScreen.tsx:1346` — 见 §1 表 B。
  - [P1] `SettingsScreen.tsx:577` — 抽屉加载态是裸文本「正在读取日志…」，该用 C-18 Skeleton；同病 `:683`（配置抽屉）、`:1387`（配置与数据目录「读取中…」）→ 状态存在但没走统一件，跨屏不同源
  - [P2] `SettingsScreen.tsx:582` — 日志空态 `EmptyState` 无 `action` 出口（§8、C-17 契约 `Feedback.tsx:67`）；可辩「关闭抽屉即出口」，但同屏 `:1288/:1301` 都给了 action，标准不一
  - [P2] `SettingsScreen.tsx:577` — 抽屉内加载/错误区无 `role="status"`/`aria-busy`，读屏对内容变化无感
  - 依据（合格）：主屏四态齐——`:1241` C-18、`:1243` C-19、`:1286/:1299` C-17、`:1264` `OfflineBanner`；`:968-978` 版本行 `MISSING`+原因写法标准；`:1299` 控制面不可用时空态明说「静态基线展示」不编造状态 ✓
- **额外必查**
  - [P1] `SettingsScreen.tsx:1247` — 控制面未启用提示条手写 alert div（role/边框/底色/图标各写一遍），该复用 C-19 `Banner`（`Feedback.tsx:36-56` 已内置 tone→边框+图标+role）；`:1266` 异常模块黄条同病。且 `border-danger-soft`/`border-warn-soft` 是 14% 透明同色描边、几乎不可见，与基准 Banner 的实色 `border-danger`/`border-warn` 强度不一致（tokens `danger-soft.usage`=底、非边框）（M-1）
  - [P2] `SettingsScreen.tsx:1226` — 分区菜单激活项 `bg-surface-3`(`:1222`) 上叠 `text-text-muted` 说明字：muted 只对 `--ldst-surface` 校验过对比（§9）→ 可疑组合（M-10）
  - [P2] `SettingsScreen.tsx:1187` — subtitle「N 个模块 · 运行中 N」两处数值未上 `ldst-num`（§7.2）；全文件 `ldst-num` 实测 20 处，漏网点为 `:1187` 与 `:1346`
  - 裸 `<table>`：无。任意值 class：无（`:340` `transition-[width]` 属合法词汇表条目）。硬编码色值：无（`style` 只有 width 百分比，无颜色）。字号越界：无。`text-hero`：0 处 ✓。`w-menu`(`:1210`)/`w-drawer` 均 token ✓。禁用按钮均带 `disabledReason`/可见原因（`:443/:446/:1350/:353`，§8 ✓）
  - 局部组件（应下沉 `components/ui`）：`PeerCard`(`:245`) / `StaticPeerCard`(`:421`) 卡头重复、`dl` 行(`:993`/`:1462`) 重复、手写 alert 条(`:1247`/`:1266`) 重复 → M-2

---
## 5. 新 10 屏

### `/limit-up` — `frontend/src/screens/limitup/LimitUpScreen.tsx`（含 `demo.ts` / `useLimitUpView.ts`）

- [P0] `LimitUpScreen.tsx:128` — `?demo=1` 演示数据无水印（§1 表 A）。
- **维度 1**
  - [P2] `LimitUpScreen.tsx:235` — `maxHeight="calc(100vh - 320px)"`：320px 是魔数，不在任何间距/高度刻度；**规范缺口**：DataTable 滚动高度应定 token 档（`Table.tsx:35` 的 `maxHeight` 契约本身未约束取值）
  - 依据（一致）：顶层五处分支统一 `gap-5`(`:144/:153/:180/:194/:220`) → 其余无该问题
- **维度 2** — **本屏无该问题**。依据：`:72/:85/:96` 连板数/首次封板/末次封板均 `align:'right'`+`ldst-num`，表头同源于 `Table.tsx:175`。
- **维度 3** — **本屏无可交互元素缺过渡**。依据：全文件 duration-* 为 0，但无 `onRowClick` 可点行、无展开/收起；可交互仅刷新按钮(`:130`→`Button.tsx:17` 自带 fast) 与排序表头（根因 `Table.tsx:184`，与 `/watch` 同一组件缺陷）。
- **维度 4** — **本屏无该问题**，且**是全仓 DataTable 用法最好的示范**：唯一表状区块即 `:229`，`caption :233`、`initialSort :236`、`sortToggle :237`、表体内 `empty :238`。无手写 grid+map 表格、无裸 `<table>`。
- **维度 5** — 本屏无图表。
- **维度 6**
  - [P1] `LimitUpScreen.tsx:125` — **freshness 徽标整屏缺席**：`source_detail` 只作为 subtitle 纯文本，§5.1 的 `freshness`（实时/缓存/存档 + ●◐○）与 §5.3 的 `source_status` 双徽标并排**完全没有**；根因在解析层 `api/limitPool.ts:89-93` `payloadOf` 把 envelope 顶层字段全部丢弃，屏幕拿不到料（§1.4、§5.1、§5.3）→ M-1（本屏是「连徽标都没有」的极端情形）
  - 依据（降级方向合格）：partial→`Banner tone=warn`+原因(`:223-227`, C-19)；缺失值→`MissingCell`「—」+title+sr-only(`:29-36`)，**这是全仓最好的缺失值示范**
- **维度 7**
  - [P1] `LimitUpScreen.tsx:66` — 名称列 `truncate`（固定 112px 列宽 `:60`）无 `title`/tooltip → 长名称永久丢失；同文件 `MissingCell:31` 就给了 title，**同文件标准不一**
- **维度 8**
  - [P0] `LimitUpScreen.tsx:128` — （同上，演示水印）
  - 依据（其余齐全）：loading `:142-148` C-18、offline `:151-175`（`OfflineBanner` + 带出口 `EmptyState`）、error `:177-189`（`ErrorBlock`+重试）、empty `:191-217`（「刷新」出口，404→空态不谎报错误 `useLimitUpView.ts:63`）、partial `:223` C-19 Banner；**无 0 冒充缺失**：`demo.ts:63-71` 缺失给 null、屏幕渲染 `—`，`limitPool.ts:66-81` 不补 0
- **额外必查**
  - 无裸 `<table>`；无任意值 class；limitup 三文件（含 `demo.ts`/`useLimitUpView.ts`）**无任何硬编码色值/hex/rgb、无字号越界、无 `text-hero`** —— `demo.ts` 纯数据 + null，可当合规示范
  - [P2] `LimitUpScreen.tsx:125` — `${payload.count} 只` 数值未上 `ldst-num`（§7.2；同句 date 也未上）
  - [P2] `LimitUpScreen.tsx:29` — `MissingCell` 是本屏局部造的件但质量高，**应下沉 `components/ui` 供全仓复用**（与三屏各写 `SourceBadge` 互为反例）→ M-2
  - A11y：`caption :233` ✓、`MissingCell` sr-only 原因 `:33` ✓、图标 `aria-hidden` ✓、行不可点故无键盘等价问题 —— **本屏 A11y 是 18 屏里最好的**

### `/industry` — `frontend/src/screens/industry/IndustryMatrixScreen.tsx`

- [P0] `IndustryMatrixScreen.tsx:287` — `?demo=1` 演示源无水印；且 `:176` 空态里就有「查看演示数据」按钮，一键进入无标识态。
- **维度 1**
  - [P2] `IndustryMatrixScreen.tsx:421` — 同一 `CardBody`(`:420 gap-3`) 内两段图例，一段 `gap-x-6 gap-y-2`(32/8px)、另一段 `gap-x-4 gap-y-1`(16/4px)，同层级两套间距（§7.3）
  - [P2] `IndustryMatrixScreen.tsx:130` — 热力块自带 `px-2 py-1`，叠在 C-09 紧凑档 `cellPad=px-2`(`Table.tsx:90`) 上 → 单元格左右各 16px 内距、色块四周留 8px 缝，热力矩阵读起来是「离散胶囊」而不是连续色阶；应让色块填满 td（`className` 交 td 或 `column.className`）→ **规范缺口**：C-09 未定义「整格底色列」写法
  - [P2] `IndustryMatrixScreen.tsx:82` — `gap-0.5`(2px)、`:272` 同、`:426` `gap-1.5`(6px) 不在 tokens `space` 刻度（`space.1`=4px），来自 Tailwind 残留默认档；基准 kit 同病（`Badge.tsx:68`、`Card.tsx:138`）→ **规范缺口**：要么登记 2/6px 半步，要么全站统一 `gap-1`（M-4）
  - [P2] `IndustryMatrixScreen.tsx:406` — `density="compact"`(36px, tokens `layout.rowCompact`) 与本屏双行单元格(`:235-239`, 19.6+18≈37.6px) 冲突，行高实际被顶到 ~38px → C-09 的 36/38/40 三档失效
  - 依据：页面根 `gap-5`(`:303/:312/:329/:343/:356`) 与 `:418` Card 内距 `p-4`(`Card.tsx:54`) 一致
- **维度 2**
  - [P2] `IndustryMatrixScreen.tsx:250` — 表头副行「数据时点」是纯文本（`text-caption text-text-muted`），时间/数字未上 `ldst-num`，与 `:237` 主数据 `ldst-num` 不一源；同一张表里「时点」两种写法（§7.2）
  - [P2] `IndustryMatrixScreen.tsx:286` — PageHeader subtitle 里 `${rows.length} 个行业`、`数据时间 …` 未 `ldst-num`（`PageShell.tsx:30` subtitle 只接字符串，无 `meta` 之外的样式入口）→ **规范缺口**：PageHeader 需允许数字 span（与 §1 表 B 同一行，两个问题）
  - 依据：指标列 `align:'right'` 由 `:245` 统给，表头与数据同源（`Table.tsx:146/175`）；缺失单元格靠 `:130 justify-end` 保持右缘对齐 ✓
- **维度 3**
  - [P1] `IndustryMatrixScreen.tsx:177` — 手写「按钮样式链接」`hover:bg-surface-3` **无任何 `transition-* duration-fast`**：抄了 `Button.tsx:21` 的 `secondary` 外观但漏掉 `transition-colors duration-fast ease-ldst` 与 `font-medium`（同屏 `:289` 的真 Button 有）→ 刷新按钮与它手感/字重不一致（§7.3；验收 过渡一致性）→ M-2
  - [P1] `IndustryMatrixScreen.tsx:226` — 本表全部列可排序(`:233/:253`)，排序按钮的 `hover:text-text`(`Table.tsx:184`) 在基准件里就没有过渡，本屏全部表头继承该缺陷（基准件缺陷，同步登记）
- **维度 4**
  - [P1] `IndustryMatrixScreen.tsx:436` — `INDUSTRY_DIMENSIONS.map` 手写「维度名 + 时点」键值清单（flex-wrap span，5 项×2 字段，无对齐源），与 `IndustryDetailScreen.tsx:593`、`:612-615`、`:629` 是同一概念（键值清单）：本屏用 flex、Detail 用 `dl`，**四处四写法** → 应下沉 `KeyValueList`/`DefinitionList` 到 `components/ui`（验收 2/3）→ M-2
  - [P2] `IndustryMatrixScreen.tsx:401` — DataTable 的 `loading`/`error` props 未传(`401-416` 只传 `empty`)，四态被拆到屏级 `:305`/`:331`。当前可用（筛选是本地过滤 `:208-216`，不产生二次请求），但 C-09 契约是「loading/empty/error 都在表体内，避免整块区域消失」(`Table.tsx:11`) → 一旦筛选改服务端请求，表体不会有任何降级 → **规范缺口**：明确「屏级 vs 表体」归属
  - [P2] `IndustryMatrixScreen.tsx:407` — `maxHeight="calc(100vh - 280px)"` 的 280px 是魔法数（页头/工具栏/图例卡的实测高），窄视口下表可视区被压到 2-3 行 → **规范缺口**：token 化高度档或登记「矩阵表可视高 = 100vh − 固定 chrome」
  - 无裸 `<table>`/`<th>`/`<td>`（grep 0 命中）；`:366-399` 工具栏是控件行不是表格，无需替换
- **维度 5**（热力编码）
  - [P2] `IndustryMatrixScreen.tsx:41`（实现在 `industry/heat.ts:34`）— 顺序色阶用交互主色 `--ldst-accent` 混 `--ldst-surface`，未走 `chart-*` 令牌；tokens `chart` 组（grid/axis/band/line/bar-up/bar-down/series-2/threshold/empty）**没有「顺序色阶」令牌** → **规范缺口**：登记 `--ldst-chart-heat`（或明确允许 accent 派生），否则浅色主题/色盲模式下热力与「链接/焦点」同色相、语义撞车（M-10）
  - [P2] `industry/heat.ts:34` — 混色基准取 `--ldst-surface`，但 hover 行底色是 `bg-surface-3`(`Table.tsx:135`)，色块不跟随变暗 → 行高亮在矩阵里被色块切断（DEC-028「底色只表相对位置」）
  - 依据（合规）：无绕过变量的色值，`signedHeatStyle` 只用 `--ldst-up-soft/down-soft/flat-soft`(`heat.ts:42`)；本屏无 uplot
- **维度 6**
  - [P1] `IndustryMatrixScreen.tsx:60` — 同一枚举不同标签：`data_source_status` 的 `ok/partial/missing` 在本屏是「完整/部分/缺失」(`:60-64`)，`/industry/:name` 是「数据完整/部分缺失/无数据」(`IndustryDetailScreen.tsx:50-54`)，基准 `/` 屏是 `ok/degraded/partial`→「完整/降级/部分」(`HomeScreen.tsx:62-66`) → 同一枚徽标三套文案，跨屏不同源（§5.2；验收 徽标同源）→ M-7
  - [P1] `IndustryMatrixScreen.tsx:80` — 契约枚举 `ok|partial|missing`(`api/industry.ts:80`) 与 §5.2 规定的 `ok|degraded|partial` 不一致：没有「已降级」态、多出「缺失」态，且 `:273` 把 `missing` 映射到 danger（§5.2 里 danger 属 `partial`）→ **规范缺口**：把产业侧词表登记进 §5.2 或改回契约
  - [P1] `IndustryMatrixScreen.tsx:274` — `未绑定` 是裸文字（`text-caption text-text-muted`），同屏 `:273` 状态用 `Badge`、`/industry/:name` 同一事实用 `Badge tone="neutral"`(`IndustryDetailScreen.tsx:441`) → 同一状态三种表达（§1 原则 6）
  - [P2] `IndustryMatrixScreen.tsx:273` — `Badge` 只有文字 + 色调，无 §5.1/§5.2 的 ●/◐/○；基准屏同样只用文字徽标(`HomeScreen:78-81`)，所以**与基准重量一致**，但整套都没落 §9 图形半边（可用 `Badge.icon` 或 `StatusDot`，`Badge.tsx:23/56`）→ M-1
  - [P2] `IndustryMatrixScreen.tsx:114` — tooltip 用原生 `title`（与 `Badge.tsx:31` 同源 ✓），但原生 `title` 键盘/触屏不可达、不可样式化、延迟约 1s；全站无 Tooltip 件 → **规范缺口**：定 C-2x Tooltip 条款（M-8）
  - [P2] `IndustryMatrixScreen.tsx:250` — `'时点未知'` 与 `:238` `'成分未知'`、`:438` `MISSING` 是三种「缺失」话术（§8 缺失一律「—」+原因）
  - 依据（合格）：缺失单元格「文字 + 图形 + 原因」齐全(`:112-119`，`MISSING_CELL_TITLE` 来自 `heat.ts:46`)；无英文枚举直显
- **维度 7**
  - [P1] `IndustryMatrixScreen.tsx:236` — 行业名 `truncate` 无 `title` → 申万一级的长名（"食品饮料/医药生物"）在 176px(`:232`) 内被裁即**永久丢失**，且该列是本屏唯一识别符(`:404 rowKey`)（验收 truncate 须带 tooltip）
  - [P1] `IndustryMatrixScreen.tsx:237` — 第二行 `l1_code · 成分数` 同样 `truncate` 无 `title`（`min-w-0` 由 `:235` 提供，收缩正常，只是信息丢失）
  - [P2] `IndustryMatrixScreen.tsx:246` — 列宽 `110px` 非 4px 栅格倍数（同文件 `:232` 176、`:246` 另一支 136 都是 4 倍数）→ **规范缺口**：登记 `Column.width` 只允许 4 倍数（M-4）
  - [P2] `IndustryMatrixScreen.tsx:361` — Banner 正文一整段长句无断句层级，`partial` 提示在窄栏 3-4 行，与 §5.4「错误摘要最多 2 行 + 查看详情」口径不一
- **维度 8**
  - [P0] `IndustryMatrixScreen.tsx:286` — 见 §1 表 B。
  - [P1] `IndustryMatrixScreen.tsx:305` — `ScreenSkeleton metrics={5} rows={8}` 与真实结构**不同构**：真实首屏是「工具栏 + 矩阵表 + 图例卡」，没有 5 张指标卡；`PageShell.tsx:84` 把骨架卡网格写死 `grid-cols-2 gap-4 desk:grid-cols-5`，加载完跳成一张宽表 → 布局跳变、承诺错内容（C-18 契约「骨架与真实 DOM 同构」；`PageShell.tsx:79` 注释自陈此意图）→ M-9
  - [P1] `IndustryMatrixScreen.tsx:176` — `<a href="/industry?demo=1">` 硬写路径：切到演示源时丢掉当前 `?dim/?filter/?q/?state` 且整页刷新（同屏 `:408` 用 `industryHref`/`withParams` 保参，`links.ts:19`）→ §1 原则 2「状态优先：刷新后必须可恢复」→ M-2
  - [P1] `IndustryMatrixScreen.tsx:408` — 进入行业详情**只有行点击**：`onRowClick` 落在 `<tr>`(`Table.tsx:130-132` 无 `tabIndex`/`onKeyDown`/`role`)，键盘与读屏用户到不了 `/industry/:name`（§9 键盘可完成）→ M-6（C-09 需给可点行键盘等价）
  - [P1] `IndustryMatrixScreen.tsx:140` — `MatrixEmpty` 与 `IndustryDetailScreen.tsx:337 DetailEmpty`、`SignalsScreen.tsx:296-327` 是同一「接口未接入 + 不伪造数据 + 给出口」空态的**三份拷贝**，文案与按钮名各不相同（刷新/重试/刷新）→ 应下沉 `EndpointMissingEmpty`（M-2）
  - [P2] `IndustryMatrixScreen.tsx:281` — `partialRows` 只数「非 ok 的行数」(`:281`)，Banner(`:360-363`) 说「N 个行业的维度不完整」却没列是哪几个维度，与 `:41` 承诺的固定文案相比缺「必须同时列出缺失项」（§5.2 `partial` 行注）
  - 依据（四态齐全）：loading `:301-308` C-18、offline `:310-325`（C-19 `OfflineBanner` + C-17）、error `:327-339`、empty `:341-353`→`MatrixEmpty`(C-17, `:152-186`，**有出口**：刷新 `:171` + 演示入口 `:176`)、partial `:359-364`；表体空态 `:409-415` ✓
- **额外必查**
  - 裸 `<table>`：无；任意值 class：无；硬编码色值：无（`heat.ts` 全走 `var(--ldst-*)`+`color-mix`）；字号越界：无（最小 `text-caption` 12）；`text-hero`：0 处 ✓
  - [P1] `IndustryMatrixScreen.tsx:80` — 局部私有组件 4 个：`HeatSwatches`(`:80`)、`MetricCell`(`:94`)、`MatrixEmpty`(`:140`) 及 `:60/:66` 两张状态映射表；`MetricCell` 是「带热力的数值单元格」，属可复用件（Detail 的成分股表 `:245-307` 手写同形态但无热力）→ 应下沉，并让热力成为 `Column` 能力（M-2）
  - A11y：`caption`(`:405`) ✓、`scope="col"` ✓、`:132` 单元格 `aria-label` ✓、`:116` `—` 用 `aria-hidden` ✓、`:82` 图例色带 `aria-hidden` ✓；缺：可点 `<tr>` 键盘等价（见维度 8）、`:397`「点击行进入详情」仅鼠标语义无键盘提示
  - 对比度（按 §9 复算）：52% 档底色 ≈#325694 + `--ldst-text` #e8ecf2 = **5.8:1** ✓；muted 文字只在 hover 时落 `bg-surface-3` = **4.89:1** ✓ 勉强过。两个余量偏薄：`heat.ts:12` 的 52% 一旦上调、或有人把 muted 放进色块，立刻塌陷 → [P2] 预防性记录，本屏无对比度 P0/P1

### `/industry/:name` — `frontend/src/screens/industry/IndustryDetailScreen.tsx`

- [P0] `IndustryDetailScreen.tsx:439` — `?demo=1` 演示源无水印（`:372` 的候选跳转会把 `demo=1` 带进来）。
- **维度 1**
  - [P2] `IndustryDetailScreen.tsx:545` — 相邻三块同级卡内距不同源：成分股卡 `bodyClassName="p-0"`（表格贴边）、同侧相关资讯卡 `px-4 py-2`(`:574`)、「口径与成分」卡默认 `p-4`(`:592`, `Card.tsx:105`) → 三种内距（§7.3）
  - [P2] `IndustryDetailScreen.tsx:531` — 五维卡 `grid-cols-2 gap-3`(12px) 嵌在 `:530` 的 `gap-4`(16px) 列里，页面级 `:519` 又是 `gap-5`(24px)：24/16/12 递进合理，但 `:531` 的 12px 与 `:569` 侧栏卡的 16px 之间没有「卡内 vs 卡间」层级说明（刻度混用但可读）
  - [P2] `IndustryDetailScreen.tsx:143` — `gap-0.5`(2px)、`:593/:629` `gap-1.5`(6px)、`:642` `gap-1` 混用半步/整步（同 `/industry`）
  - [P2] `IndustryDetailScreen.tsx:176` — 卡内明细 `dl` 的 `border-t + pt-2 + gap-1` 与 `:618` 的 `mt-2 + pt-2`、`:642` 的 `mt-2 + pt-2 + gap-1` 三处手抄同一「分隔块」写法 → 应下沉为组件
  - 依据：DataTable 内距由 `Table.tsx:90` 统给，无数值列内距混用
- **维度 2**
  - [P2] `IndustryDetailScreen.tsx:596` — 「口径与成分」`dl` 右列对齐源不统一：`:610`、`:614` 给了 `text-right`，`:596`、`:600`、`:604` 没给（且 `:604` 无 `shrink-0`，「成分股 / 分类代码」在窄列会折行错位）→ 同一区块两种对齐
  - [P2] `IndustryDetailScreen.tsx:182` — DimensionCard 二级 `dl` 与 `:631` 溯源行内部一致 ✓，但 dt 字号 `text-caption` 而主数值 `:147` 用 `text-page`，卡内 20/28/12 三档并存、无 `text-section`(20) 过渡层，纵向基线略散（§7.2 字号阶梯）
  - [P2] `IndustryDetailScreen.tsx:434` — 头部 subtitle 把 `成分股 N 只`、`分类时点 …` 混进纯文本，无 `ldst-num`（`:542` 同；`:604` 却用了 `formatInt`+`ldst-num`）→ 同一屏数字两种排版（§7.2）
  - 依据（DataTable 部分无问题）：6 个数值列 `align:'right'` 全给(`:243/:255/:266/:278/:287/:295`)，单元格均 `ldst-num`(`:246/:258/:270/:304`)，缺失走 `MISSING`(`:247/:259/:271/:304`)
- **维度 3**
  - [P1] `IndustryDetailScreen.tsx:322` — 资讯标题链接 `hover:text-accent-hover hover:underline` 完全无过渡
  - [P1] `IndustryDetailScreen.tsx:355` — 空态出口链接 `hover:underline` 无过渡，且与同屏其它链接样式（`:373/:448` 按钮式）不一致
  - [P1] `IndustryDetailScreen.tsx:373` — 候选行业「按钮样式链接」：抄 `Button.tsx:21` `secondary` 外观但漏 `transition-colors duration-fast ease-ldst` + `font-medium`
  - [P1] `IndustryDetailScreen.tsx:448` — 返回「产业矩阵」链接与同排真 `Button`(`:453`) 并排，一个有过渡有 font-medium、一个都没有 → 同一操作区两种手感（§2 工具栏顺序；C-01）
  - 依据：本屏排序表头继承基准缺陷 `Table.tsx:184` 无过渡（影响 `:547` 表全部可排序列）
  - → 这 4 条全部由 **M-2（`Button as="a"`）** 一次消掉
- **维度 4**（本屏 4 处手写行列，逐块结论）
  - [P1] `IndustryDetailScreen.tsx:629` — 溯源清单：`ul` + 每行 `flex justify-between`，2 列×N 行（N=数据源数，demo 为 5），右列是 `retrieved_at` 时间（可排序）。该换 DataTable 的理由：逐条记录集、可排序、需要 `caption`；换不掉的唯一理由：侧栏仅 `desk:col-span-4`（≈340px 内宽），C-09 的 sticky 首列 + 32px 表头(`Table.tsx:92`) 收益低。**结论：优先下沉共享 `KeyValueList`；若产品要求「按取数时间排序审计」再换 DataTable**
  - [P1] `IndustryDetailScreen.tsx:176` — DimensionCard 二级指标 `dl`：2 列×最多 4 行，含可比较数值 → **不该换 DataTable**（键值对非记录集），但它是第 4 种手写写法，必须下沉一个组件（验收 2/3）
  - [P2] `IndustryDetailScreen.tsx:592` — 「口径与成分」`dl`：2 列×5 行，值为代码/日期/计数 → 同上不换，但对齐与 `ldst-num` 必须统一（见维度 2）
  - [P2] `IndustryDetailScreen.tsx:584` — 相关研报/资讯 `ul`(`:585`)：形态上是表（可按时间排序、可按类型筛选），换不掉的理由是标题需 2 行富文本 + 外链 + Badge，会被 `:552` 同款 36px 档挤掉 → 保留列表，但把 `RelatedRow`(`:310`) 下沉为 kit 的 `ListRow`（C-09/C-10 之外尚缺「记录列表」件，**规范缺口**）
  - [P2] `IndustryDetailScreen.tsx:547` — DataTable 用对了主体，但 `density="compact"`(`:552`) 与 `:233-236` 双行「标的」列冲突（36px 档失效）；`loading`/`error` props 未传（偏离 `Table.tsx:11` 契约）；`maxHeight="360px"`(`:553`) 魔法数
  - 无裸 `<table>`（grep 0 命中）
- **维度 5**
  - [P1] `IndustryDetailScreen.tsx:90` — 手写分位条（`bg-surface-3` 轨道 + `bg-accent` 填充 + 2px 圆形指针）复制了 `HBarList` 的轨道/填充写法(`MiniChart.tsx:37-41`)却未复用 → 同一「水平条」两份实现，深浅主题/色盲模式改动要改两处（验收 组件同源；**规范缺口**：CH 组缺 `Meter`/`PercentileBar`）→ M-2
  - [P2] `IndustryDetailScreen.tsx:94` — `left: calc(${percent}% - 4px)` 的 4px 是硬编码指针半径半步（应由同一变量派生，tokens 无该刻度）
  - [P2] `IndustryDetailScreen.tsx:162` — `series.length >= 2` 才画 Sparkline，且 `HISTORY_KEY` 对景气/资金两维恒为 `null`(`:62-68`) → 这两张卡的「历史」区块**整块消失且无说明**（§1 原则 3「未接入必须显式可见」）；`:171` 末点日期是原始 `YYYY-MM-DD`，与 `:635` 用 `formatTime` 的写法不一（图表轴日期应走 `format.ts:119` `shortDate`）
  - [P2] `IndustryDetailScreen.tsx:164` — `Sparkline` 走 `currentColor` + `text-up/text-down` token(`MiniChart.tsx:83/93`) 合规 ✓；但它没有网格线/轴，故本屏无 `chart-grid`/`chart-axis` 的适用对象；本屏无 uplot
  - 依据：本屏完全不使用 `heat.ts` 的热力（仅 `:37` 引 `MISSING_CELL_TITLE`）→ 矩阵有色阶、详情没有，跨屏「同一数值的视觉编码」不一（**规范缺口**：详情页是否允许热力底色）
- **维度 6**
  - [P1] `IndustryDetailScreen.tsx:657` — `Object.entries(payload.formulas)` 的键**直接当可见文字渲染**（英文 `prosperity`/`valuation`…），而同屏 `:649` 同一维度用 `DIMENSION_TITLE` 中文 → 原始英文枚举直显 + 同屏两套标签（§8；验收 6）→ M-7
  - [P1] `IndustryDetailScreen.tsx:74` — 缺失文案 `无数据（未核实）` 只有文字 + `title`，**缺 `—` 图形半边**（`:154` 同），而 `/industry` 矩阵是 `—` + 文字(`IndustryMatrixScreen.tsx:116-117`) → 同一 `MISSING_CELL_TITLE`(`heat.ts:46`) 两种视觉重量（§9；验收 降级同源）
  - [P1] `IndustryDetailScreen.tsx:50` — 状态徽标词表与 `/industry` 不同源（`:50-54` vs `IndustryMatrixScreen.tsx:60-64`）→ M-7
  - [P2] `IndustryDetailScreen.tsx:633` — 溯源清单可见文字是原始数据集名（`daily_k`/`valuation_history`…，`demo.ts:455/447`），`title` 放的是 `source.id`(`:632`) → 与 `:657` 同类；若定位为「审计视图」需在 UI-SPEC 登记「允许原始英文数据集标识」，否则应加中文映射（**规范缺口**）
  - [P2] `IndustryDetailScreen.tsx:139` — 用 `ok/warn` 表达「有数据/缺数据」，与 `:56-60` 的 `danger`(missing) 并存 → 同一屏对「缺」用两种 tone 强度（§5.3 严重度取最者）；另 `:139` `Badge` 无 §5.1 图形（与基准一致，记 P2）
  - 依据（徽标本体合规）：全部走 `Badge`(`:139/:440/:441/:544/:573/:644`)，tone 取自 `Badge.tsx:9-18`，无自造胶囊；`:644` 维度缺口用 `Badge title={reason}` 与基准同源 ✓
- **维度 7**
  - [P1] `IndustryDetailScreen.tsx:234` — 标的名称 `truncate` 无 `title`(`:235` 同样) → 长名称永久丢失；对照 `:183`（dt truncate **带** `title={spec.path}`）与 `:632`（带 title），本屏两种写法并存
  - [P2] `IndustryDetailScreen.tsx:614` — 锚点 `anchors.join('、')` 无 `max-w`/`truncate`/`title`，多锚点时右列折到 3 行把行高顶开（`:610` 给了 `text-right` 但也无长度上限）→ **规范缺口**：dl 也应有长度约束
  - [P2] `IndustryDetailScreen.tsx:331` — `关联 ${codes.slice(0,4)}` 静默截断到 4 个且**不说明还有多少**（应「关联 N 个，另有 X」+ title）→ 信息永久丢失（§1 原则 3）
  - [P2] `IndustryDetailScreen.tsx:324` — 资讯长标题不截断（换行 2-3 行），与同屏卡片标题的 `truncate` 基线不一（`Card.tsx:43`）→ 侧栏列表行高参差
  - 依据：本屏 `min-w-0` 齐全（`:233`、`:569`、`:183`）✓
- **维度 8**
  - 依据（**四态齐全且无 0 冒充，本屏是三屏里唯一 subtitle 写法正确的**）：loading `:466-473`、offline `:475-490`、error `:492-504`、empty/partial `:506-513`→`DetailEmpty`（C-17 四分支：非法名 `:348` / 无此行业带候选出口 `:360` / 五维均无 `:384` / 接口未接入 `:403`）；正常态 Banner `:522-527`；`:432-436` 用三元式在 payload 为 null 时换成说明文案 —— **这条正确写法就是 M-9 的参照实现**；grep `?? 0`/`|| 0` 零命中，`:247/:259/:271` 三处 `.toFixed(2)` 都在显式 `!== null` 分支之后(`:246/:258/:270`)，属安全用法
  - [P1] `IndustryDetailScreen.tsx:470` — `ScreenSkeleton metrics={5} rows={6}` 与真实结构不同构：真实首屏是「2 列×5 张五维卡(`:531`) + 成分股表 + 4 张侧栏卡」，骨架是「1 行 5 张卡(`PageShell.tsx:84` 固定 `grid-cols-2 desk:grid-cols-5`) + 1 张表」→ 加载完成时卡数、列数、左右分栏全部跳变（M-9）
  - [P1] `IndustryDetailScreen.tsx:547` — 表体 `loading`/`error` props 未传：切行业名时 `:466` 整屏骨架顶替，用户丢滚动位置与上下文，而 C-09 的设计正是「状态在表体内、其他区块不动」(`Table.tsx:11`)
  - [P2] `IndustryDetailScreen.tsx:555` — 表体空态 `EmptyState compact` **无出口动作**(`:556-563` 只有说明文字) → 违反 §8/C-17（`Feedback.tsx:67` 明令），与同屏 `:398/:414` 空态（有按钮）不一致
  - [P2] `IndustryDetailScreen.tsx:577` — 相关资讯空态同样无出口
- **额外必查**
  - 裸 `<table>`：无；任意值 class：无；**硬编码色值：无**（issue 基线说的 1 处 hex 即本屏，实测 `:410` 是 `&#123;` HTML 实体误报；`:91/:93` 是 `bg-accent`/`border-accent` token 类；两处 `style` 是百分比宽度/位置 `:91/:94`，非颜色）；字号越界：无；`text-hero`：0 处 ✓（`:147` 是 `text-page` 28px）
  - [P1] `IndustryDetailScreen.tsx:70` — 局部私有组件 5 个：`PercentileGauge`(`:70`)、`DimensionCard`(`:105`)、`PctCell`(`:205`)、`RelatedRow`(`:310`)、`DetailEmpty`(`:337`) + `:50/:56` 两张映射表 → 与 `/industry` 的 `MetricCell`/`MatrixEmpty`、`/signals` 的 `RulesCard` 属同一批重复件，应整体下沉（M-2）
  - [P2] `IndustryDetailScreen.tsx:530` — 双栏 `desk:grid-cols-12` + `col-span-8/4`(`:529/:530/:569`) 与 `/signals` `:398/:399/:418` 完全同写法 ✓；但基准 `HomeScreen:449` 用 `grid gap-4 desk:grid-cols-12`（无 `grid-cols-1`）→ 三屏两套
  - A11y **做得好的部分**：`:83-89` 分位条用 `role="meter"`+`aria-valuemin/max/now/valuetext`（全站唯一）✓、`:167` Sparkline `role="img"`+`aria-label` ✓、`:551` `caption` ✓、`:318` 外链 `rel="noreferrer noopener"` ✓；缺：成分股表无 `onRowClick` 也无行内链接（`:547-565` 个股名不可点，属功能缺口非纯视觉）

### `/signals` — `frontend/src/screens/signals/SignalsScreen.tsx`

- [P0] `SignalsScreen.tsx:238` — `?demo=1` 演示源无水印；`:318` 空态里就有「查看演示数据」入口。
- **维度 1**
  - [P2] `SignalsScreen.tsx:343` — 指标卡行 `grid grid-cols-3 gap-3`(12px)，基准屏的指标卡行一律 `gap-4` 且带断点（`HomeScreen:377`、`JournalScreen:443`、`ReviewScreen:666`），`PageShell` 骨架也是 `gap-4`(`PageShell.tsx:84`) → 卡间距比基准差一档、且不随 1024–1279 收窄变化（§2；tokens `layout.breakpointMin`）
  - [P2] `SignalsScreen.tsx:343` — 同屏 `:343 gap-3` vs `:398 gap-4` vs `:446 gap-4`，「卡与卡之间」两个刻度
  - [P2] `SignalsScreen.tsx:458` — 抽屉里「规则表达式」块 `p-3`(12px)，同抽屉 `:469` 表体单元格 8px(`Table.tsx:90`)、`:464-467` 摘要块无内距 → 抽屉内三种内距（§7.3）
  - [P2] `SignalsScreen.tsx:508` — RulesCard 行 `px-4 py-2` 叠 `:500 bodyClassName="p-0"`，与 `:400` 表贴边一致 ✓，但 `:513` 行内 `gap-1` 与 `:447` `gap-2` 混用
- **维度 2** — 依据（数值对齐基线合规）：`证据`(命中数) `align:'right'`+`ldst-num`(`:194/:197`)、`时点` `align:'right'`+`ldst-num`(`:205/:209`)、证据表 `当前值/阈值` `align:'right'`+`ldst-num`(`:78/:81`)；`方向/级别` 居中 + Badge(`:177/:187`) ✓
  - [P2] `SignalsScreen.tsx:237` — 头部 `命中 N 条` 纯文本无 `ldst-num`（`:395` `N 条可见` 也无），而同屏卡内同类计数全带 `ldst-num`(`:197/:515`) → 同一屏「计数」两种排版（§7.2）
  - [P2] `SignalsScreen.tsx:83` — 证据表第二行 `比较符 阈值` 用 `text-caption text-text-muted` 但整列右缘靠 `:81 items-end`（不是 `align`），与 `:104` 同写法 ✓；问题是阈值数字与「当前值」字号不同（21 vs 18 视觉行），列内基线不齐但可读
- **维度 3**（本屏全部 hover 点零过渡，grep 确认 0 命中）
  - [P1] `SignalsScreen.tsx:161` — 行业链接 `hover:text-accent-hover hover:underline` 无过渡
  - [P1] `SignalsScreen.tsx:224` — 「查看证据」文本按钮 `hover:underline` 无过渡，与相邻真 Button(`:240` 带 transition) 手感不一
  - [P1] `SignalsScreen.tsx:319` — 空态「查看演示数据」手写按钮链接：抄 `Button.tsx:21` 外观、漏 transition + font-medium
  - [P1] `SignalsScreen.tsx:434` — 抽屉 footer「打开行业详情」同款手写按钮链接、同款无过渡，与同排 `Button`(`:438`) 字重/手感不一致
  - [P1] `SignalsScreen.tsx:423` — **抽屉打开/关闭完全无过渡**：`Drawer`(`Overlay.tsx:114-123`) 没有任何 `transition`/`animate`，而 tokens `motion.duration-base`=180ms 明确「用于抽屉滑入」（`tw:137` 已映射 `duration-base`）→ 证据链弹层瞬现瞬隐（§7.3；根因在 kit，本屏是唯一调用点）→ M-3
  - → 前 4 条由 **M-2** 一次消掉，第 5 条由 **M-3** 消掉
- **维度 4**
  - [P1] `SignalsScreen.tsx:362` — 工具栏「规则 / 行业」两个下拉用**手写 `<label>` + Select**(`:362-377`、`:378-393`)，而同产品的 `/industry` 同场景用 C-06 `Field`(`IndustryMatrixScreen.tsx:373/:386`，带 `htmlFor`/hint/error 槽) → 同概念两写法，未来加校验/提示只有一条路径能改（验收 组件同源；C-06）→ M-2
  - [P1] `SignalsScreen.tsx:504` — RulesCard 手写行列：`ul` + 每行 `flex justify-between`，3 列×N 行（标题/方向徽标/命中数）+ 第二行表达式，命中数是可排序数值列(`:506` 现算)。该换 DataTable 的理由：逐条记录集、需按命中数排序、需 `caption`；换不掉的理由：表达式(`:520`) 需整行 `break-all`、标题需 `title`，C-09 无「跨列副行」能力 → **规范缺口：C-09 需增 `subRender`/副行**，否则这类「记录 + 说明行」永远只能手写（M-6）
  - [P2] `SignalsScreen.tsx:64` — 两个 DataTable(`:400`/`:469`) 本体正确（caption ✓、align ✓、empty ✓、`initialSort ✓ :408`），但 `loading`/`error` props 未传；`density="compact"`(`:405/:474`) 与 `:69-73/:81-87/:104-108` 双行单元格冲突 → 36px 档失效
  - [P2] `SignalsScreen.tsx:90` — 证据表 4 列**无一提供 `sort`**(`:64-110`)，而当前值/阈值/时点都可排序（主表 `:156/:171/:179/:207` 给了 4 个）→ 同一屏两张表「排序能力」标准不一
  - 无裸 `<table>`（grep 0 命中）
- **维度 5** — 本屏无图表（`:6-:26` imports 无图表件；`:81-:110` 证据行全是文本 + Badge）。
- **维度 6**
  - [P1] `SignalsScreen.tsx:351` — `MetricCard` 的 note 直接写「规则方向 bullish」，`:352` 写「规则方向 bearish」→ **原始英文枚举直显给用户**，而同屏方向列渲染的是中文 `偏多/偏空`(`:56-58` `directionLabel`) → 同一事实两套标签（§8；验收 6）→ M-7
  - [P1] `SignalsScreen.tsx:44` — 级别词表自造：`info/warn/danger` →「提示/关注/风险」(`:44-48`)，而 §3.2 的信号级别词表是「提示/关注/强信号」→ 与盯盘屏两套级别话术；产业信号级别在 UI-SPEC **未登记** → **规范缺口**：§5 增「信号级别」徽标条款并统一三词（M-7）
  - [P2] `SignalsScreen.tsx:83` — 比较符直接渲染后端原始值 `>` / `<`(`:84`)，无「大于/小于」或 `≥` 归一（`demo.ts:832` 起是 `>`/`<`）→ 审计场景可接受，但需 §8 登记「比较符属技术符号，原样显示」
  - [P2] `SignalsScreen.tsx:106` — 证据行的 `dataset`（`daily_k`/`valuation_history`）原始英文作可见文字（同 `IndustryDetailScreen:633`）
  - [P2] `SignalsScreen.tsx:95` — `Badge` 体系一致(`:95/:181/:189/:448/:451/:514` 全走 Badge，tone 取自 `Badge.tsx:9-18`) ✓ 与基准重量一致，但同样**无 §5.1 图形半边**
  - 依据（本屏做得最好的一条）：本屏无 freshness 徽标可用（payload 无该字段，`api/industry.ts:336-351`），`:237` 用 `数据时点 …` 纯文本；`:477-479` 明确「时点来自各数据集自身 as_of，不冒充请求日期」—— 与 §5 溯源意图一致
- **维度 7**
  - [P1] `SignalsScreen.tsx:161` — 行业列 `line-clamp-1` **无 `title`**，行业名裁掉即永久丢失（列宽 96px `:155`）；而 `:172` 规则名 truncate **带** `title` ✓、`:510` 规则标题带 `title` ✓ → 同屏两种做法
  - [P2] `SignalsScreen.tsx:103` — 证据表 `时点/数据集` 列宽 `150px` 非 4px 倍数（本屏其余 96/132/56/72/152/40 都是 4 倍数 `:155/:170/:177/:196/:207/:217/:79/:93`）→ **规范缺口**（M-4）
  - [P2] `SignalsScreen.tsx:209` — 时点列 `whitespace-nowrap` + 列宽 92px，后端若给 `YYYY-MM-DD HH:mm:ss` 会溢出裁切（当前 date-only 安全）→ 建议统一走 `formatTime`(`format.ts:93`) 而非 nowrap 硬顶（§8 时间口径）
  - 依据（长代码名合规，正面示范）：`:71 path`、`:106 dataset`、`:460 expression`、`:520` 规则表达式**全部 `break-all` 完整换行、不截断** ✓；`:69/:104` 的 `min-w-0` 齐 ✓
- **维度 8**
  - [P0] `SignalsScreen.tsx:237` — 见 §1 表 B（`counts.total ?? 0`）。
  - [P1] `SignalsScreen.tsx:347` — `MetricCard value={payload?.counts.total ?? 0}` 与 `:351/:352 counts.bullish/bearish ?? 0`：直接违反 C-11 指标卡契约（`Card.tsx:114-125` 明确「缺失显示 `—` + 原因，不补 0」）。当前 normal/partial 分支下 payload 非空、total 恒有值（`api/industry.ts:882` 已兜 `?? signals.length`），所以 `?? 0` 是死分支，但一旦后端 `counts` 缺键就静默显示 0 → 判 P1（可随 `:237` 同源修掉）
  - [P1] `SignalsScreen.tsx:256` — `ScreenSkeleton metrics={3} rows={6}`：卡数 3 传对了，但 `PageShell.tsx:84` 把骨架网格写死 `grid-cols-2 gap-4 desk:grid-cols-5` → 3 张卡排在 5 列宽的格子里（右侧空 2 格），加载完跳成 `grid-cols-3`(`:343`)，且骨架行内距 `gap-4` 而真实是 `gap-3` → 四态「不同源」的直接证据（M-9）
  - [P2] `SignalsScreen.tsx:409` — 表体空态有 `title/description` **无出口动作**（缺「清空筛选」按钮，而同屏屏级空态 `:313-325` 有出口）→ 违反 C-17（`Feedback.tsx:67`；§8）
  - [P2] `SignalsScreen.tsx:400` — 表体 `loading`/`error` 未传；切 `direction` 是本地过滤(`:133-136`) 不产生请求 ✓，改服务端即缺降级（`Table.tsx:11`）
  - [P2] `SignalsScreen.tsx:328` — 空态下 RulesCard 移到卡下方(`:328`)、正常态移到右侧栏(`:418-420`) → 同一区块在两种状态下位置不同，读图记忆断裂（**规范缺口**：五态布局需登记「区块位置不随状态变」）
  - 依据（四态齐全）：loading `:252-259`、offline `:261-276`（`OfflineBanner`+C-17）、error `:278-290` C-19、empty `:292-331`（C-17，出口 ✓ 刷新 `:315` + 演示 `:317`；`:328` 空态仍保留 RulesCard，符合 §3.3「不要用空列表盖住历史结果」意图）、partial `:337-341`（Banner，条数取 `payload.dropped` `:339` 真值 ✓）
- **额外必查**
  - 裸 `<table>`：无；任意值 class：无；硬编码色值：无；字号越界：无；`text-hero`：**1 处**(`:346` MetricCard hero) ✓ 合规
  - [P1] `SignalsScreen.tsx:318` — `<a href="/signals?demo=1">` 丢当前 `direction/rule/industry` 筛选并整页刷新（同屏 `:433` 用 `withParams` 保参，`links.ts:19`）→ §1 原则 2（M-2）
  - [P1] `SignalsScreen.tsx:487` — 局部私有组件 `RulesCard`（+ `:64 EVIDENCE_COLUMNS`、`:44/:50` 两张映射表）→ 与 `IndustryDetailScreen.tsx:310 RelatedRow` 是同一「记录 + 副行」模式的两份拷贝，应一起下沉（M-2）
  - A11y：`caption`(`:404/:473`) ✓、`scope="col"` ✓、筛选下拉有 `aria-label`(`:365/:381`) ✓、`Segmented` 有 `ariaLabel`(`:357`) ✓、图标 `aria-hidden` ✓、`role="status/alert"` 由 Banner/ErrorBlock 提供(`Feedback.tsx:41/131`) ✓；缺：**「查看证据」按钮无 `aria-haspopup="dialog"`/`aria-expanded`**(`:218-227`，抽屉靠 `:424 open` 条件渲染，Overlay 未暴露状态)；行点击展开(`:407`) 落 `<tr>` 无键盘等价(`Table.tsx:130-132`)，所幸 `:218` 按钮提供替代路径 ✓（比 `IndustryMatrix:408` 好）；`Drawer` 焦点陷阱/Esc/焦点回归齐全 ✓(`Overlay.tsx:16-71`)

---
## 6. radar 4 屏 与 research 2 屏

### `/news` — `frontend/src/screens/radar/NewsScreen.tsx`（608 行）

- **维度 1**
  - [P2] `NewsScreen.tsx:287` — 详情卡同层级区块 gap 三种刻度：article `gap-3`(`:287`) / 事件 section `gap-2`(`:309`) / 合并来源 section `gap-1`(`:329`)，同层无差异由（tokens `space.*`；§7.3）
  - [P2] `NewsScreen.tsx:154` — `fieldset gap-1.5`(6px) 与 `:114 grid gap-3`(12px) 混用半步 off-token 间距（tokens `space` 无 1.5 档）
- **维度 2** — **本屏无该问题**。依据：`:167-175` 计数 `ldst-num`+`shrink-0` 右靠、`:225/:290` 时间 `ldst-num`；本屏无数值表列（见维度 4）。
- **维度 3**
  - [P2] `NewsScreen.tsx:521` — 手写 `<a>` 链接按钮 `hover:bg-surface-3` **无 `transition-colors duration-fast`**，且与 C-xx Button 焦点环/字号不一致 → M-2
  - [P2] `NewsScreen.tsx:359` — 原文链接实心按钮 `hover:bg-accent-solid-hover` 无 `transition-*`
- **维度 4**（逐块判定）— **本屏无 P0/P1 级 DataTable 可替换区块**
  - `NewsScreen.tsx:159-177` 来源账本：两列（栏目名+计数/降级），但每行是 `Checkbox` **交互控件**(`:161`)，DataTable 的 `render` 行不适合承载多选筛选器 → 换不掉，保留 ✓
  - `NewsScreen.tsx:209-262` 时间流：形似表（时间/来源/标题多列），但整行是单 `<button>` 选中驱动右栏(`:215`)，是「选择列表/feed」不是「可排序表体」，无列排序诉求 → 可保留，但应**显式登记为规范例外**（§1「例外须登记」）
- **维度 5** — 本屏无图表。
- **维度 6**
  - [P1] `NewsScreen.tsx:174` — 降级只渲染裸文本 `'降级'`，未复用 `Badge`/`StatusDot`，无 ●/◐/○ 图形编码；且 `row.freshness`（含超出 §5.1 三态的 `'daily'`，见 `radar/demo.ts:198/210`）在本屏**完全不呈现**，栏目账本只有 items 计数，用户无法区分实时/缓存/存档（C-12/C-13；§5.1/§5.3/§9；对照基准 `HomeScreen.tsx:69`）→ M-1
  - [P1] `NewsScreen.tsx:172` — `title` 只取 `row.detail||row.source_detail`，freshness/as_of 未进悬浮；`api/radar.ts:129 freshness: string` 为无约束类型，`'daily'` 无对应中文映射（`FRESHNESS_LABEL` 仅 3 键）→ 定 §5.1 第四态或收敛枚举（**规范缺口**，M-7）
  - 依据：本屏有全仓唯一的演示标识 `:369`「· 演示数据」✓（其余 7 屏没有）
- **维度 7**
  - [P1] `NewsScreen.tsx:244` — 资讯标题 `{item.title}` 位于 `flex flex-col` 行内，**既无 `truncate` 也无 `title`** → 长标题撑高行、无悬浮找回（与「truncate 无 title」相反的另一半缺陷；正解见 `PoolScreen.tsx:235-237`）
  - [P2] `NewsScreen.tsx:224` — `related_codes.join(' / ')` 与 meta 同处 `flex flex-wrap`(`:223`)，代码多时换行使行高不齐，无 `max-w` 约束（§11「1024/1440 无横向滚动」）
  - [P2] `NewsScreen.tsx:317` — 证据 snippet 全文展开无 `line-clamp`/`max-h`，长文使详情列纵向失控（**规范缺口**：定「证据摘要 ≤2 行 + 展开」，可借 §5.4 的 2 行规则）
- **维度 8** — **本屏无该问题**：loading `:453` C-18、offline `:462` `OfflineBanner`+C-17、error `:479` `ErrorBlock variant=banner`、empty `:493` C-17（带 `:517` 刷新 / `:520` 演示出口）、partial `:540` Banner —— 五态齐全且走 C-17/C-18/C-19 ✓；`?? 0` 仅作用于数组 `.length`(`:437/:438`)，`published_at` 缺失走 `formatTime`→`—`(`:225`)，`summary`/`evidence` 缺失走 `MISSING`(`:305/:317/:345`)，**无以 0 冒充缺失** ✓
- **额外必查**
  - [P2] `NewsScreen.tsx:555` — 任意值 `grid-cols-[184px_minmax(0,1fr)_340px]`（184/340 非任何 width token；中列已用 `minmax(0,1fr)` 防溢出）→ 侧列固定值应下沉为布局 token（M-4）
  - [P2] `NewsScreen.tsx:319` — `text-text-muted` 落在 `bg-surface-2`（section `:309`）上，token 仅对 `--ldst-surface` 校验过对比度（M-10）
  - 无硬编码色值/`rgb`/`hsl`/内联颜色（三屏 + `radar/demo.ts` + `radar/useRadarView.ts` 全无 hex）✓；无 `text-[9px]/[11px]` ✓；`text-hero` 0 处 ✓
  - 本屏 `truncate` 出现 0 次 —— 印证 issue 初判「这几屏 truncate 极少而资讯标题最长」

### `/calendar` — `frontend/src/screens/radar/CalendarScreen.tsx`（522 行）

- [P0] `CalendarScreen.tsx:377` — `?demo=1` 演示源无水印；`:486` 就有「查看演示数据」入口。
- **维度 1**
  - [P1] `CalendarScreen.tsx:135` 与 `:153` — 日格高度 `min-h-[76px]` 是任意值，**不在任何 token 刻度**（`h-row` 38 / `h-control` 32 / `h-metric` 96 / `min-h-empty` 200 均不含 76）；且**占位空格(`:135`) 与真实格(`:153`) 共用同一 `min-h`**，真实格有 chips 时 `flex-col` 会长高、占位格恒为 76 → 同周次行不齐、格高与 `h-row` 体系脱钩。建议定 `tokens layout.calendarCell = "76px"`（恰为 2×row38，可解释为「两行高」），暴露 `min-h-calendar-cell` 供两处同用；若需等高格则真实格改 `h-calendar-cell` + 内部 `overflow`。
    *（本条由子审计的 P0 下调为 P1：它是 token 脱钩与节奏不齐，不丢信息也不撑破容器，按本清单口径属 P1/验收「无任意值」档。）* → M-4
  - [P2] `CalendarScreen.tsx:167` — chips `gap-0.5`(2px)、`:153` `p-1.5`(6px) 均 off `space` 刻度半步值
- **维度 2**
  - [P2] `CalendarScreen.tsx:127` vs `:161` — 星期表头 `text-center`，而日号数字在 `flex items-start`(`:152`) 下**左对齐** → 表头与数据不同对齐源（§9 精神；日历非 DataTable，手写行必须自行统一）
- **维度 3**
  - [P2] `CalendarScreen.tsx:153` — 可点日格有 `transition-colors duration-fast` ✓，但同屏其余可点元素无过渡：`:220` 「当日资讯」Link `hover:bg-surface-3`、`:292` 底部「查看该日资讯」Link、`:484` 「查看演示数据」`<a>`（`IconButton` 上/下月与 `Button` 本月/刷新由 `Button.tsx:17/101` 自带 transition，无需重复）→ M-2
- **维度 4**（逐块判定）— **「手写栅格最多」但无 P0/P1 级必换 DataTable 区块**，判断成立：
  - `CalendarScreen.tsx:132-186` 月历格：**二维日期矩阵**（星期 × 周），非「多行同构记录」，DataTable 无法表达跨周对齐与占位格 → 换掉会破坏可读性，明确保留 ✓
  - `:243`/`:269` 当日条目/事件列表：每行字段随 `detail`/`evidence`/`note` 行数可变、无排序列，是「明细 feed」不是表 → 保留 ✓
  - `:308-341` SectionsCard：5 个固定小节、字段（label/status/条数/dataset/tool/note）实为表格形态，但仅 5 行、以 2 列卡片提升扫读性 → 建议**登记为例外**而非强行换（§1）
- **维度 5** — 本屏无图表。
- **维度 6**
  - [P1] `CalendarScreen.tsx:377`（连带）— 与 `/news` 同一缺口：本屏小节状态用 `Badge`(tone ok/warn/flat)(`:314/:318`) 文字+色双编码 ✓，但**无 freshness 三态徽标**（`data_as_of` 见 `radar/demo.ts:470`，未呈现）（§5.1）→ M-1
- **维度 7**
  - [P1] `CalendarScreen.tsx:248` / `:278` — 日历条目名 `entry.label` 与事件名 `event.title` **无 `truncate` 无 `title`**，且所在 `flex flex-wrap`(`:246`/`:271`) 不收缩；证据 snippet `:280` 全文无 `line-clamp` → 本屏 `truncate` 出现 0 次（对照 `PoolScreen.tsx:237`）
  - [P2] `CalendarScreen.tsx:508` — 任意值 `grid-cols-[minmax(0,1fr)_360px]`（360 非 width token；已用 `minmax(0,1fr)` 防溢出）→ M-4
  - [P2] `CalendarScreen.tsx:253` / `:281` — `text-text-muted` 落在 `bg-surface-2`(`:245`/`:270`) 与 SectionsCard 格 `bg-surface-2`(`:310`→`:337`) 上，对比度未在 raised 档校验（M-10）
- **维度 8**
  - [P2] `CalendarScreen.tsx:231` — 当日无事件 `EmptyState` **未传 `action`**：仅靠 header 的「当日资讯」链接(`:220`)间接给出口，空态本体缺显式动作（C-17；§8「空态要有出口」`Feedback.tsx:67`）
  - 依据（四态齐全）：loading `:416` C-18（`metrics=0` 合理）、offline `:425`、error `:442` C-19、empty `:456` C-17(`:479` 刷新 / `:483` 演示出口)、partial `:502` Banner ✓
  - 依据（**无 0 冒充缺失**）：`count`(`:138`)、`section.entries.length`(`:315`)、header 计数(`:374/:376`) 的 `?? 0`/`.length` 均为真实计数非缺失值；`entry.source||MISSING`(`:254`)、`data_source||MISSING`(`:324`) 缺失走 `—` ✓
  - 依据：`:135` 空格的 `border-transparent` **不是空态、也无「0 冒充缺失」**，仅是周首/周末的对齐填充，功能正确 —— 它的唯一问题就是上面那条任意值高度
- **其它**：无裸 `<table>`；无硬编码色值；无字号越界；`text-hero` 0 处 ✓

### `/pool` — `frontend/src/screens/radar/PoolScreen.tsx`（445 行）

- [P0] `PoolScreen.tsx:292` — `?demo=1` 演示源无水印；`:382` 就有「查看演示数据」入口。
- [P0] `PoolScreen.tsx:125` — **裸 `<table>`**，见 §1 表 D。
- [P0] `PoolScreen.tsx:156` — **权重 `?? 0` 冒充缺失（显示「0.0%」）**，见 §1 表 B；同根因 `:113` `weightSum`。
- **维度 1**
  - [P2] `PoolScreen.tsx:404` — 指标卡行 `gap-3`(12px) 与屏级 `gap-5`(`:394`) 及基准 `ScreenSkeleton` 指标行 `gap-4`(`PageShell.tsx:84`) 不一致（§7.3）
- **维度 2**
  - [P2] `PoolScreen.tsx:161` — 抽屉表「原始输入」列 `text-right`，内容 `rawFor`(`:162`) 含数字（换手率%、相对高度）却**未套 `ldst-num`**（权重列 `:155` 套了）→ 同表数值基线不一致（§7.2）
  - 依据（主表合规）：DataTable 数值列走 `Column.align`（`:224-270`），无撕裂
- **维度 3**
  - [P2] `PoolScreen.tsx:380` — 「查看演示数据」`<a>` `hover:bg-surface-3` **无 `transition-colors duration-fast`**，与 C-xx Button 不同源 → M-2
  - [P2] `PoolScreen.tsx:425`（继承）— 抽屉承载件 `BreakdownDrawer` 用 `if(!open) return null` 挂载/卸载，展开**无 `transition-transform duration-base`**（`Overlay.tsx:109/119`）→ 本屏无法自行补，属基准件缺口 → M-3
- **维度 4**
  - [P1] `PoolScreen.tsx:166-181` — 该手写表的存在，实为 DataTable **缺「summary/合计行」prop**（合计行在 `:166`）。应给 `DataTable` 加 `summaryRow?: ReactNode`（tfoot 语义），而非每屏自造 `<table>`；建议在 §6 补「表体唯一，含汇总也走 DataTable」条款（**规范缺口**）→ M-6
  - 依据：本屏**唯一手写表即 `:125`**；其余区块——指标卡(`:405-422` MetricCard)、主候选表(`:425` **已** DataTable)、缺失分项 Badge(`:263`)——均同源 ✓
- **维度 5** — 本屏无图表。
- **维度 6**
  - [P1] `PoolScreen.tsx:292`（连带）— 同 `/news`：无 freshness 三态徽标，`data_as_of`/`source_detail` 仅纯文本呈现(`:201/:290`)（§5.1）→ M-1
  - 依据（合格）：主表降级用 `Banner`(`:397`)、分项缺失用 `Badge tone=warn`(`:263`)，复用 C-12/C-13、文字+色双编码 ✓
- **维度 7**
  - [P1] `PoolScreen.tsx:224`/`:232`/`:246`/`:259`/`:270` — DataTable `Column.width` 全为任意 px（96/148/88/120/72），`:431` `maxHeight="calc(100vh - 360px)"` 任意内联（M-4）→ **规范缺口**：要么允许命名列宽、要么明确「表格列宽是内容尺寸非视觉 token」
  - 依据（正例）：`:237` 名称列 `truncate` + `title` 是全仓该写的样板（`/news`、`/calendar` 都缺）
- **维度 8**
  - [P2] `PoolScreen.tsx:289` — header `payload?.candidate_count ?? 0`（payload null 时显「0 只候选」）：本屏 loading/error 已早返回(`:309`/`:335`)，null 仅在强制态边角短暂可见，风险低 → **判 P2 而非 P0**（与 §1 表 B 那 6 条的区别是：那些在三态必渲染的 header 里，这条到不了正常路径）。`payload.missing.length`(`:419`)、`counts.*`(`:410`) 为真实计数非缺失
  - 依据（正确示范）：主表 `score===null?MISSING`(`:249/:173`)、`PartCell` null→`—`+sr-only 说明(`:50-57`) ✓
  - 依据（四态齐全）：loading `:309`、offline `:318`、error `:335`、empty `:349`(`:375` 刷新 / `:378` 演示出口)、partial `:397` 五态齐全且走 C-17/C-18/C-19 ✓；主表 empty 传入 DataTable 表体内(`:433`) 符合 C-09「四态在表体内」✓
- **其它**：无硬编码色值；无字号越界；`text-hero`：仅 `:407` MetricCard `hero` 一处 ✓

### `/positions` — `frontend/src/screens/radar/PositionsScreen.tsx`

- [P0] `PositionsScreen.tsx:639` — `?demo=1` 演示源无水印；`:819` 就有「查看演示数据」入口。
- [P0] `PositionsScreen.tsx:636` — `position_count ?? 0`，见 §1 表 B。
- **维度 1**
  - [P1] `PositionsScreen.tsx:109` — 指标行 `grid-cols-2 … desk:grid-cols-5`：1024–1279 档显示 2 列，违反 §2「1024–1279 指标三列」（tokens `layout.breakpointMin`=1024px；同域 `ResearchDetailScreen.tsx:115` 用 `rail:grid-cols-3` 实现了三列 → 同产品三端不一致）（M-5）
  - [P2] `PositionsScreen.tsx:109` — 指标行 `gap-3`，而同屏 loading 骨架 `PageShell.tsx:84` 用 `gap-4` → 加载→正常间距跳变（C-18；§7.3）
- **维度 2** — **本屏无该问题**。依据：数值列 `align:'right'` 齐全 `:266/:274/:282/:294/:307/:315/:328`，渲染均带 `ldst-num` `:269/:277/:288/:301/:320/:330`，表头与数据同源于 C-09(`Table.tsx:150/175`)。
- **维度 3**
  - [P2] `PositionsScreen.tsx:817` — 手写 `<a>`「查看演示数据」有 `hover:bg-surface-3` 但无 `transition-colors duration-fast ease-ldst`（基准 `Button.tsx:17`）→ M-2
  - [P2] `PositionsScreen.tsx:851` — DataTable 未传 `onRowClick`（行不可点），但 C-09 无条件给行加 `hover:bg-surface-3`(`Table.tsx:135`) → **暗示可点而不可点，affordance 误导**（**规范缺口**：应约定仅 `onRowClick` 存在时给 hover 底色）→ M-6
- **维度 4** — **本屏无该问题**：主表已用 C-09 DataTable(`:851`)，集中度/行业分布复用 C-20 `HBarList`(`:172/:198`)，估值行为 KPI 卡(`:109`)，无手写表格区块。
- **维度 5** — 本屏无 uplot；`HBarList` 色走 token 类 `bg-accent`(`MiniChart.tsx:39`)，未绕过 cssVar（依据 `:172/:198`）→ 同 M-10 的「DOM 图取色通道未定义」。
- **维度 6**
  - [P1] `PositionsScreen.tsx:639` — 载荷含 `data_source_status: 'ok'|'partial'|'missing'`(`api/radar.ts:281`)，但本屏 normal 态**从不渲染该状态徽标**（header badge 仅 `DemoStateBadge`），`missing` 时界面与 `ok` 完全相同；基准屏 Home/Market 均在右上给溯源徽标(`HomeScreen.tsx:79`、`MarketScreen.tsx:115`)（§5.2、§1.4 降级可见）→ M-1
  - 依据（**隐私条硬约束达成，非缺口**）：`PrivacyNotice`(`:83-100`) 进入 `notices`(`:686`)，在 loading `:739` / offline `:749` / error `:767` / empty `:783` / normal `:833` **五分支全部渲染**，且带 `role="note"` —— 这正是 `JournalScreen:370` 没做到的（§3.4）
- **维度 7**
  - [P1] `PositionsScreen.tsx:258` — 行业列 `truncate` **无 `title`**（对照 `:338` 备注列 truncate+title 的正确样板）；且 truncate 作用在 inline `<span>` 上**可能根本不生效**（inline 元素不产生块级裁切），长行业名撑列 + 信息丢失双重风险（验收 7；§9）
  - [P2] `PositionsScreen.tsx:127` — `MetricCard.note` 在组件内 `truncate` 无 `title`(`Card.tsx:155`)，本屏 note 文案长（「xxx 成本未取到价格，退回成本口径」）会被无声截断；属组件缺陷在本屏的后果，**修复应落 `Card.tsx`**（M-4）
  - [P2] `PositionsScreen.tsx:338` — `max-w-[140px]` 写法正确（`block`+`truncate`+`title` 三者齐备，`:338` 同行含 `title={row.note}`），但 140px 非刻度值：tokens 无列宽/最大宽刻度 → **规范缺口**：新增「表格单元格最大宽」档（如 128/160），同时与 `:335` `width:'140px'` 合并为一处真源（当前 max-w 与列宽重复声明）（M-4）
  - [P2] `PositionsScreen.tsx:248` — 全部列宽为任意 px（`:248/:256/:267/:275/:283/:295/:308/:316/:329/:335/:352`，88/96/80/104/112/140px）：C-09 `Column.width` API 本身无刻度约束 → **规范缺口**：定「列宽 4 倍数白名单」条款（M-4）
- **维度 8**
  - [P0] `PositionsScreen.tsx:636` — 见 §1 表 B。
  - [P1] `PositionsScreen.tsx:506` — 校验失败告警里的 CLI 兜底命令用 `Number(cost) || 0` / `Number(qty) || 0`(`:506-507`) 生成 `ldst position update --cost 0 --qty 0`：**输入非法时把非法值渲染成看似可执行的 0**，属呈现错误（§8；应显示 `<成本>` 占位，`:380` 已有 `'<code>'` 先例可对齐）
  - 依据（四态齐全且同源）：loading `:740` C-18、offline `:750-757`（`OfflineBanner`+C-17 带「重新检查」）、error `:768-773` C-19+onRetry、empty `:784-824`（三个出口动作）、表体空态在 C-09 内 `:861` C-17 ✓
- **其它**：裸 `<table>` 无（唯一表体经 DataTable `:851`，`caption :856` + scope）；无硬编码色值；无字号越界；`text-hero` 1 处 `:112` ✓；A11y：IconButton 均有 label `:356/:361/:692`、文件输入有 `aria-label :647`、告警 `role="alert" :498` ✓
  - [P2] `PositionsScreen.tsx:815` — 手写 `<a>` 复刻 Button secondary 样式（`components/research/bits.tsx:24` `LinkButton` 也是同款复刻）→「链接长得像按钮」两处各自实现（M-2）

### `/research/:code` — `frontend/src/screens/research/ResearchDetailScreen.tsx`

- **维度 1**
  - [P1] `ResearchDetailScreen.tsx:394` — 同一屏内 1024 档意图用两套断点表达：`:115` `rail:grid-cols-3`（≤1279，含 <1024 也 3 列）vs `:394` `min-[1024px]:grid-cols-3`；tokens `layout.breakpointMin`=1024px 已注册但 `tailwind.config.ts:40-44` `screens` 未映射该档名 → 自定义断点与 desk/rail 并存是三端不一致的直接来源；且 `:115` 的 `rail:` 使 <1024 仍 3 列，违反 §2「<1024 指标单列」（M-5）
  - [P2] `ResearchDetailScreen.tsx:94` — `gap-0.5`(2px) 不在间距刻度（0/4/8/12/16/24/32/48）；同类 `:412` `mt-0.5`
  - 依据（其余节奏一致）：区块纵向 `gap-5` `:648` 且五分支统一 `:564/:586/:598/:611`；双栏 `grid gap-4` 三处一致 `:658/:667/:676`；「分隔线+小节」`mt-4 border-t pt-3` 三处一致 `:144/:305/:407`
- **维度 2**
  - [P2] `ResearchDetailScreen.tsx:96` — `Fact` 的 `<dd>` 数值（如员工数 `:267-274`「12,345 人」）未加 `ldst-num`；`:209` 来源计数、`:541` meta 时间串同样未加（§7.2）
  - 澄清（针对 issue 的疑点「本屏 ldst-num 仅 3 处 vs Positions 15 处」）：**疑点不成立** —— 本屏数字主体走 `MetricCard`（值区自带 `ldst-num`，`Card.tsx:145`）、分位条(`bits.tsx:52`)、序列行(`:229`)，计数少是组件内聚而非漏网；真正漏网仅上述 `:96/:209/:541` 散文/内联场景，定 P2 而非 P1
- **维度 3**
  - [P2] `ResearchDetailScreen.tsx:214` — 「查看来源」裸 `<button>` 无任何 hover 态、无 transition（对照 `Button.tsx:17`）；`:589`「回到首页」链接同样无 hover 反馈
  - 依据（其余合规）：StageRail 项有 `transition-colors duration-fast`(`StageRail.tsx:59`，经 `:692` 消费)、`LinkButton` `:24`(`bits.tsx`)
- **维度 4**（**逐区块判定：本屏 DataTable 应换区块 = 0 个**——issue 说的「7 处手写栅格」多数是 dl/KPI 卡的合法形态）
  - `ResearchDetailScreen.tsx:262-275` 公司画像 facts 键值对（5 项双列）：**定义列表形态，保留 `<dl>`+SectionCard，不该换 DataTable** —— label/value 非对称两列、无可排序需求，换表体反而丢 dt/dd 语义（C-10/C-11；§9）
  - `:115-131` 财务当期快照 ×5、`:353-365` 盈利预期 ×4、`:394-405` 风险指标 ×3：**KPI 卡行，不该换**（`h-metric` 96px 与 hero 层级是卡的职责）
  - `:314-317` + `:223-238` 历史序列（唯一「重复行×固定列」区块）：3 列、行数 ≤4、无可排序数值需求 → **可换 C-09 但收益低，不建议派工**；真正该修的是行内对齐：[P2] `:227` `flex flex-wrap … justify-between` 各行列位随内容浮动、行间不对齐，且 `flex-wrap` 下长 label 会把数字/走势挤到第二行
  - `:407-418` 风险条目、`:462-464` 分位条：语句/组件列表，非表格，保留 ✓
  - [P2] `ResearchDetailScreen.tsx:280` — `-mx-4 -mb-4` 负边距把 CardFooter 拽到卡边的 hack **同屏重复 4 次**(`:280/:325/:370/:431`) → C-11 SectionCard 应新增 `footer` 插槽（**规范缺口**）→ M-2
- **维度 5** — 本屏无 uplot；Sparkline 用法合规(`:232`，色走 `text-*` token + currentColor，`MiniChart.tsx:83-93`)
  - [P2] `ResearchDetailScreen.tsx:232` — 未传 `tone`，所有序列（含明显上升/下降）统一灰 `text-text-secondary`(`MiniChart.tsx:64` 默认 flat) → **规范缺口**：定序列方向着色条款（M-10）
- **维度 6**
  - [P2] `ResearchDetailScreen.tsx:516` — `ResearchStatus.data_source_status`(`api/research.ts:122`) 未上屏（对照基准 `HomeScreen.tsx:79` 的溯源徽标重量）→ M-1
  - [P2] `ResearchDetailScreen.tsx:398` — 引用角标视觉重量不一致：正文角标 16px(`MarkdownView.tsx:75` `h-4`) vs 抽屉编号 chip 24px(`ProvenanceDrawer.tsx:57` `h-5`) vs `Badge` 24px(`Badge.tsx:33`) → 同一 `[n]` 概念三种大小（验收 徽标同源）
  - 依据（**降级呈现达标**）：段落状态 Badge 文字+色 `:256/:298/:342/:388/:448`、degraded Banner 文字说明 `:349`、StageRail `StatusDot` 图形+文字(`StageRail.tsx:76-82`)，无原始英文枚举直显（`STAGE_STATUS_LABEL` 全中文，`api/research.ts:43-48`）✓
- **维度 7** — 依据（**本屏无 truncate 丢信息**）：`:96` `break-words`+`min-w-0`、`:658-685` 区块均传 `className="min-w-0"`、正文 `[overflow-wrap:anywhere]`(`MarkdownView.tsx:130`)
  - [P2] `ResearchDetailScreen.tsx:659` / `:662` — grid 包装 div 自身无 `min-w-0`（仅卡内有），12 栅下长 token 有撑列风险
- **维度 8** — 四态齐全且同源：loading `:566`、error `:588-591`（C-19+重试+回首页出口）、offline `:600-604`、empty `:613-629`（C-17+刷新）；段级缺失用 `—`+「本段未提供」note，`present` 判定用 `!== null && !== undefined` 而非真值折叠(`:117/:355/:396`)，未取到价格/字段不补 0 ✓
  - [P2] `ResearchDetailScreen.tsx:689` — `status?.failed.length ?? 0 · status?.pending.length ?? 0`：演示强制态下 status 为 null 时副标题显示「失败 0 · 待跑 0」，而同屏 `:486-492` 兜底给 StageRail 渲染 6 个「待跑」，自相矛盾（正常态 status 非空，故 P2 非 P0）
- **其它**：裸 `<table>` 无；硬编码色值无；字号越界无；`text-hero` 0 处 ✓；本屏有演示标识 `:535` ✓（无需 M-11 的 P0 水印条目）

### `/research/:code/report` — `frontend/src/screens/research/ResearchReportScreen.tsx`

- **维度 1**
  - [P2] `ResearchReportScreen.tsx:205` — loading 用 `ScreenSkeleton metrics={6} rows={5}`（指标卡阵 + 表体），而本屏正常态是「左导航 + 右文档」双栏(`:297-443`)，无任何指标卡/表格 → **骨架结构与正文完全不同源**，加载→正常整屏跳版（M-9；`:566` 同问题见 Detail）
  - [P2] `ResearchReportScreen.tsx:298` — 左侧栏 `desk:w-[208px]` 任意值：tokens 无侧栏宽刻度（`w-drawer`480/`w-sidebar`232 均不适配）→ **规范缺口**：新增 `w-sidepanel`（建议对齐 232 或把 208 定入 tokens）（M-4）
  - 依据：纵向 `gap-5` 全分支统一 `:203/:225/:234/:247/:282`；双栏 `gap-4` `:297`；`CardBody gap-3` `:329`
- **维度 2**
  - [P2] `ResearchReportScreen.tsx:431` — 页脚「来源 N 条 · 已核实 N 条」计数未加 `ldst-num`（`:69` 进度百分比已加 ✓）；本屏无表列，无对齐破坏
- **维度 3**
  - [P2] `ResearchReportScreen.tsx:79` — `transition-[width] duration-base` **缺 `ease-ldst` 缓动**（词汇表要求 transition+duration+ease 三件套；屏内唯一带 easing 的基准写法是 `Button.tsx:17`）；width 用 `duration-base`(180ms) 本身正确
  - [P2] `ResearchReportScreen.tsx:413` — 正文角标按钮 hover 仅 `hover:underline` 无 transition(`MarkdownView.tsx:75`，经 `:413/:149` 消费)
  - 依据：StageRail 切换项过渡达标(`StageRail.tsx:59`，经 `:301` 消费)；「来源(n)」「再试一次」走 C 系 Button ✓
- **维度 4** — **本屏无该问题**：无手写行/列数据栅格 —— StageRail 是导航按钮组非表格（依据 `:301-307`、`StageRail.tsx:45-53`），其余全部为文档流/CardFooter ✓
- **维度 5** — 本屏无图表（进度条 `:77-79` 用 `bg-accent` token 类，非数据序列色）。
- **维度 6**
  - [P1] `ResearchReportScreen.tsx:275` — `mode=${document.mode}` **原始英文枚举直显**（取值 'structured'/'parsed'/'text'，见 `api/research.ts:84`、`demo.ts:301/:339/:382`）；同型 [P1] `:436` — 页脚 `失败：${document.failure.kind ?? 'unknown'}` 直出 kind 枚举（'format'/'network' 等，`demo.ts:465`），且 `:349` 详情串再接 `（${code}）` 也是原始码直显 → §5.4 已给五类错误的中文标题表，应映射后显示（§5.3/§1.4；验收 6）→ M-7
  - 依据（降级/新鲜度标识本身达标）：degraded Banner+文字 `:292/:409`、状态 Badge `:317`、`更新于 formatTime` `:437`、StageRail `StatusDot`+「降级」文字(`StageRail.tsx:79-81`) —— 文字与图形同时存在（§5.1/§9）✓
- **维度 7** — **本屏无该问题**。依据：`:311` `min-w-0 flex-1`、`:298` `min-w-0`、段名截断在 `StageRail.tsx:69` `truncate` 且 title 语义由文字全称兜底；无 truncate-无-title 点。
- **维度 8** — **本屏四态是三屏中最全的**：整屏级 loading `:201` / error+onRetry `:227` / offline+重试 `:236` / empty+刷新出口 `:249-265`；段级 failed→`ErrorBlock`+重试本段+等价 CLI `:357-374`、pending→`EmptyState`+运行本段 `:383-397`、完成无产物→说明 `:420-424`，**均有出口动作**；重跑进度 `role="status"`+`progressbar`+`aria-valuenow` `:66-77` ✓。**维度 8 本屏无该问题**：`:161` `failed.length ?? 0` 仅 normal/partial 路径可达（status 非空），不构成 0 冒充。
- **额外必查**
  - [P2] `ResearchReportScreen.tsx:301` — StageRail 激活项 `bg-surface-3`(`StageRail.tsx:60`) 上叠 `text-text-muted` 序号(`StageRail.tsx:64`)：对比度仅对 `--ldst-surface` 校验过（§9），surface-3 上未验证（M-10）
  - [P2] `components/research/bits.tsx:7` — `LinkButton` 整份复刻 Button 样式(`bits.tsx:24-28` vs `Button.tsx:17`)，与 `PositionsScreen.tsx:815` 的手写 `<a>` 是同一需求第三套写法 → 应下沉 `components/ui` 提供 `Button as="a"` 变体（M-2）。**此为该组件真实缺口**；`bits.tsx` 内**未**再造 Badge/Chip/StatRow（徽标全部复用 C-13），此点无重复实现
  - [P2] `components/research/ProvenanceDrawer.tsx:69` — `grid-cols-[64px_minmax(0,1fr)]` 任意值（键列宽 64px 无刻度）；[P2] `components/research/StageRail.tsx:59` — `min-w-[8.5rem]` 任意值（经 Report `:301` / Detail `:692` 消费）→ M-4
  - [P2] `components/research/facts.ts:71` — `trimNumber` 去尾零致同组百分号小数位不一（`12.30%→"12.3"`、`12.00%→"12"`），同排 MetricCard 数字位数参差（影响 Detail `:304/:354/:395` 三组）→ §8 各场景位数已有定值，应**按键定 digits 而非一律 trim**（M-7）
  - 裸 `<table>` 无；硬编码色值无（`:79` `style` 仅 width 百分比）；字号越界无；`text-hero` 0 处 ✓；本屏有演示标识 `:171` ✓

---
## 7. 按整改动作聚合（供阶段二派工）

逐屏共登记 **315 条**（P0 17 行，去重后 §1 的 **15 条**；P1 121；P2 177）。下面收敛为 **13 个整改动作**：M-1…M-11 与前文交叉引用同号，另补 M-12 / M-13 两类（前文以 §1-B、各屏维度 7 出现，此处归口）。排序按「一次消掉条数 × 是否含 P0」。

> 计数口径：一条粗糙点可能同属两个动作（如 `PoolScreen.tsx:125` 既是「裸表」也是「C-09 缺 `summaryRow`」）。下表按**主责动作**归一，交叉处在括号内注明，避免阶段二重复派工。

---

### M-11 · 演示态水印补一处（根因）→ 一次消掉 **7 条 P0 + 3 条 P2 = 10 条**

**改一个文件**：`frontend/src/components/PageShell.tsx:47-48` — `DemoStateBadge` 现在 `if (!state || state === 'normal') return null`，`?demo=1` 且无 `?state=` 时整屏无标识。改为 `demo === true` 也渲染（与 `?state=` 合成一枚「演示数据 / 强制态：X」徽标，走 C-13 `Badge`）。

- 直接消掉 §1-A 的 7 条 P0：`limitup/LimitUpScreen.tsx:128`、`industry/IndustryMatrixScreen.tsx:287`、`industry/IndustryDetailScreen.tsx:439`、`signals/SignalsScreen.tsx:238`、`radar/CalendarScreen.tsx:377`、`radar/PoolScreen.tsx:292`、`radar/PositionsScreen.tsx:639`
- 顺带统一已有的三份各写各的标识：`radar/NewsScreen.tsx:369`「· 演示数据」纯文本、`research/ResearchDetailScreen.tsx:535`、`research/ResearchReportScreen.tsx:171`
- 依据已存在：`demo.ts:1-9` 自我承诺「`?demo=1` 显式开启，页面带『演示态』水印」+ `PageShell.tsx:46` 注释契约。建议阶段三在 UI-SPEC §5 补一句「演示态一律同一枚水印徽标」。
- **10 个能进演示态的屏里 7 个没有水印，3 个有的恰好也不是同一套写法。**

### M-12 · 缺失值不得用 0 / 假定值冒充 → 一次消掉 **6 条 P0 + 3 条 P1/P2 = 9 条**

**改哪里**：`frontend/src/lib/format.ts` 增 `countOrMissing(v: number | null | undefined): string`（null/undefined → `MISSING`）；页头副标题的拼接收敛为 C-04 `PageHeader` 的结构化 `meta`，不再由各屏手写模板字符串。

- §1-B 的 6 条 P0：`SignalsScreen.tsx:237`、`IndustryMatrixScreen.tsx:286`、`radar/PositionsScreen.tsx:636`、`radar/PoolScreen.tsx:156`（同根因 `:113`）、`WeeklyScreen.tsx:543`（同根因 `:520`）、`SettingsScreen.tsx:1346`（同根因 `:541`）
- 关联：`radar/PositionsScreen.tsx:506-507`（校验告警里的 CLI 兜底命令用 `Number(cost) || 0` 渲染成 `--cost 0`，应显示 `<成本>` 占位，同屏 `:380` 已有 `'<code>'` 先例）、`WatchScreen.tsx:115` / `HomeScreen.tsx:250` / `MarketScreen.tsx:344` 的「排序把缺失当 0」——**后三处不是 P0**（不参与展示），但需要同一条「缺失值沉底」规则收口；`limitup/LimitUpScreen.tsx:39-41` 的 `NEGATIVE_INFINITY` 才是可推荐写法。
- 前置条款：UI-SPEC §8 已有时口径，**缺「`DataTable` 排序缺失沉底」与「`HBarList` 缺失槽位用 `--ldst-chart-empty`」两句**（阶段三）。`WeeklyScreen.tsx:543` 正是把缺失画成了实体条（`MiniChart.tsx:30` 的 `max(2, …)`% 下限）。

### M-1 · 溯源 / 降级徽标下沉（含色序反转修复）→ 一次消掉 **1 条 P0 + 12 条 P1 + 3 条 P2 = 16 条**

**改哪里**：新建 `frontend/src/components/ui/SourceBadges.tsx` —— UI-SPEC §6 组件清单 `docs/UI-SPEC.md:286-287` 早已登记 `kit/SourceBadges.tsx`（`FreshnessBadge` + `SourceStatusBadge`），**该文件不存在**。导出两枚徽标 + 唯一一份 `FRESHNESS_LABEL` / `SOURCE_STATUS_LABEL` / 严重度排序；tone 严格按 §5.2（`degraded`=warn 黄、`partial`=danger 橙红）与 §5.3（`partial > degraded > cached > realtime`）；图形半边用 §5.1 的 ●/◐/○（`Badge.icon` 或 `StatusDot`）。

- 唯一 P0：`ReviewScreen.tsx:198` 色序反转（`partial→warn`、`degraded→danger`，与 §5.2 相反；用户把「数据集缺项（最严重）」看成最轻的黄）。
- 四份本地实现（P1）：`HomeScreen.tsx:69`、`MarketScreen.tsx:105`、`WeeklyScreen.tsx:166`、`ReviewScreen.tsx:196`，配套 `EnvelopeMeta` 类型三份分叉（`HomeScreen.tsx:54` / `MarketScreen.tsx:99-102` / `WeeklyScreen.tsx:166`）→ 下沉前互不可替换。
- 整屏缺徽标（P1）：`WatchScreen.tsx:325`（§3.2 三种状态塌成一条通用 Banner，`:209` 根本没读 `freshness`）、`LimitUpScreen.tsx:125`（根因在解析层 `api/limitPool.ts:89-93` `payloadOf` 丢弃 envelope 顶层字段）、`radar/NewsScreen.tsx:174`、`radar/CalendarScreen.tsx:377`、`radar/PoolScreen.tsx:292`、`radar/PositionsScreen.tsx:639`（`data_source_status: ok|partial|missing`(`api/radar.ts:281`) 完全不上屏，`missing` 时界面与 `ok` 相同）、`research/ResearchDetailScreen.tsx:516`。
- 图形半边缺失（P1/P2）：`WatchScreen.tsx:318`、`ReviewScreen.tsx:206`、`IndustryMatrixScreen.tsx:273`（与基准同重量但整套都没落 §9 图形半边）。
- 其余：`WeeklyScreen.tsx:176`（枚举兜底直显，亦属 M-7）、`ReviewScreen.tsx:186`（「归档」vs §5.1「存档」）、`radar/PositionsScreen.tsx:338` 区分的引用角标三种大小（`MarkdownView.tsx:75` h-4 / `ProvenanceDrawer.tsx:57` h-5 / `Badge.tsx:33` h-6 —— 同一 `[n]` 概念三种视觉重量）。

### M-2 · `Button as="a"` + 局部组件下沉 → 一次消掉 **4 条 P1、共 31 条**

**改哪里**：`components/ui/Button.tsx` 增 `as={'a' | Link}` 变体（把 `Button.tsx:17` 的 `transition-colors duration-fast ease-ldst` + `font-medium` + 焦点环原样带过去）；再把各屏自造件迁入 `components/ui`。

- 手写「链接长得像按钮」13 处：`IndustryMatrixScreen.tsx:177`、`IndustryDetailScreen.tsx:322`/`:355`/`:373`/`:448`、`radar/NewsScreen.tsx:521`、`radar/CalendarScreen.tsx:220`/`:292`/`:484`、`radar/PoolScreen.tsx:380`、`radar/PositionsScreen.tsx:817`/`:815`、`components/research/bits.tsx:7`（`LinkButton` 整份复刻 `Button.tsx:17`，与 `Positions:815` 是同一需求第三套写法）。
- 重复实现的局部件（应下沉）：`EndpointMissingEmpty`（`IndustryMatrix:140` + `IndustryDetail:337` + `Signals:296-327` 三份拷贝，按钮名各叫刷新/重试/刷新）、`DescriptionList`/`KeyValueList`（`IndustryMatrix:436` flex、`IndustryDetail:176`/`:592`/`:629` dl、`SettingsScreen:993`/`:1462` dl 行逐字相同 —— **同一概念四处四写法**）、`MetricCell`(`IndustryMatrix:94`)、`MissingCell`(`LimitUpScreen:29`，质量高可直接升为公共件)、`RelatedRow`(`IndustryDetail:310`)/`RulesCard`(`Signals:487`) 同一「记录 + 副行」两份拷贝、`PercentileGauge`(`IndustryDetail:90`，复制 `MiniChart.tsx:37-41` 的水平条)、`SourceLabel`(`HomeScreen:458`/`MarketScreen:121`)、`LEVEL_BAR`(`WatchScreen:59`) 与 `SECTION_BAR`(`Card.tsx:67`) 重复的 tone→bg 映射、3px 行状态条（`ValidateScreen:610` 是 `Card.tsx:100-103` 的第二次实现，`WatchScreen:87` 第三次）、`formatSignedMoney`(`JournalScreen:106`) 重新发明 `lib/format.ts:28-53`、`SignalsScreen:362`/`:378` 手写 `<label>`+Select 而非 C-06 `Field`（同产品 `IndustryMatrix:373`/`:386` 用了 `Field`）、`SettingsScreen:1247`/`:1266` 手写 alert div 而非 C-19 `Banner`（且 `border-*-soft` 14% 透明描边几乎不可见，与基准 Banner 的实色 `border-danger` 强度不一）、`IndustryDetailScreen:280`/`:325`/`:370`/`:431` 的 `-mx-4 -mb-4` footer hack ×4。
- 前置条款：**规范缺口**两件 —— C-11 `SectionCard` 需 `footer` 插槽；C 系列无 `DescriptionList`（阶段三补 §6 清单）。
- 顺带修：`IndustryMatrixScreen.tsx:176`、`SignalsScreen.tsx:318` 的 `<a href="…?demo=1">` 丢 `?dim/?filter/?q/?state` 且整页刷新（同屏 `:408`/`:433` 已用 `links.ts:19` 的 `withParams`）→ 换成 `Button as={Link} to={withParams(...)}` 即同时满足 §1 原则 2「刷新后必须可恢复」。

### M-13 · 截断必须成对（`truncate` + 找回通道）→ 一次消掉 **20 条**（14 P1 / 6 P2）

**改哪里**：`components/ui/Card.tsx:46`（`CardHeader` subtitle）与 `:155`（`MetricCard.note`）—— 两处组件内部 `truncate` 却不透传 `title`，是「缺失原因/口径说明被无声截掉」的根因；屏侧再逐条补 `title`。

- 覆盖：`ReviewScreen:767`/`:707`、`WatchScreen:102`、`ValidateScreen:615`、`HomeScreen:149`/`:440`/`:501`/`:382`(note)、`MarketScreen:235`/`:264`、`WeeklyScreen:558`/`:577`、`SettingsScreen:329`/`:281`、`LimitUpScreen:66`、`IndustryMatrixScreen:236`/`:237`、`IndustryDetailScreen:234`、`radar/NewsScreen:244`、`radar/CalendarScreen:248`/`:278`、`radar/PositionsScreen:258`/`:127`。
- 正解样板（同仓已有，照抄即可）：`radar/PoolScreen.tsx:237`、`radar/PositionsScreen.tsx:338`（`block`+`truncate`+`title` 三者齐备）、`LimitUpScreen.tsx:29-36` `MissingCell`。
- 与 M-8 关系：本动作先用原生 `title` 止血（零依赖、即刻可上线），`Tooltip` 作为后续升级 —— **但原生 `title` 键盘/触屏不可达**，所以这 20 条的最终收口仍在 M-8。
- 同类但不同根：`IndustryDetailScreen.tsx:331`「关联 N 个」静默切到 4 个且不说明还剩多少、`IndustryMatrixScreen.tsx:130` 热力块内距把色块切成胶囊、`radar/NewsScreen.tsx:317` 证据 snippet 全文展开无 `line-clamp`（**规范缺口**：定「证据摘要 ≤2 行 + 展开」）。

### M-3 · 基准件动效补齐 → 一次消掉 **13 条**（6 P1 / 7 P2）

**改哪里**（全在 `components/ui`，屏文件一行不动就连带生效）：`Overlay.tsx:109-141`（`Drawer` 用 `if(!open) return null` 挂载即卸载 → 零过渡；应 `transition-transform duration-base ease-ldst`）、`Feedback.tsx:201-219`（`Toasts` 进出零过渡）、`Overlay.tsx` `ConfirmDialog`（弹出零过渡）、`Table.tsx:184`（排序表头 `hover:text-text` 无过渡，影响全仓每一张可排序表）、`Card.tsx:100-103`（状态条 tone 换色无 `transition-colors`）、`components/kit/AiErrorNotice.tsx:195-207`（详情展开/收起无过渡、无 `aria-controls`）。

- 覆盖：`SettingsScreen:570`/`:660`/`:1443`（抽屉）、`SettingsScreen:1473`（Toast）、`SignalsScreen:423`（证据链抽屉瞬现瞬隐）、`radar/PoolScreen:425`（继承 `BreakdownDrawer`）、`ValidateScreen:1071`/`:1049`、`ReviewScreen:782`/`:772`、`ReviewScreen:730`、`WatchScreen:115`、`LimitUpScreen:74`。
- 屏侧仅剩：`ValidateScreen:680`（行内编辑器展开，应 `duration-base`）、`ValidateScreen:610`（确认状态色切换）、`MarketScreen:711`、`ResearchReportScreen:79`（`transition-[width]` 缺 `ease-ldst`）、`IndustryMatrixScreen:177` 等手写链接（属 M-2）。
- 依据：`tokens.json motion['duration-base'].usage` 明文写「抽屉滑入、Toast 进出」，`['duration-fast'].usage` 写「hover、开关切换」—— **规范早就定了，是基准件没落**。这也印证 §2 的校准结论：屏文件层 transition 近零而体感不至于全裸，真实缺口集中在浮层与状态色切换。
- 唯一需要定档的：`SettingsScreen:340` 进度条 `transition-[width] duration-base` —— 两套 usage 都不命中进度条，建议把「状态驱动的宽度/进度」明确写进 `duration-base` 的 usage（**规范缺口**）。

### M-5 · 断点归一（先改配置，再改屏）→ 一次消掉 **13 条**（6 P1 / 7 P2）

**改哪里**：`frontend/tailwind.config.ts:40-44` 的 `screens` 只有 `rail:{max:1279}` 与 `desk:1280`，而 `tokens.json layout.breakpointMin`=1024px 已注册却没有档名 → UI-SPEC §2 的三档（<1024 单列 / 1024–1279 三列 / ≥1280 五列）**在现有词汇里根本表达不出来**。补 `mid:{min:'1024px'}`（或 `bpMin`）一档，再替换 6 处 `min-[…]:`。

- 自定义断点与 `rail:`/`desk:` 并存（P1）：`MarketScreen:529`（`min-[640px]:grid-cols-3` —— 640–1023 排 3 张 `h-metric` 卡塞 36px `hero` 数值，全仓挤压风险最高）、`WeeklyScreen:381`（`min-[1024px]:grid-cols-4`，§2 该档应三列）、`research/ResearchDetailScreen:394` vs `:115`（同一屏两套 1024 表达）、`radar/PositionsScreen:109`（1024–1279 只有 2 列）、`ReviewScreen:666`（该档仍 1 列）、`ValidateScreen:683`（`min-[1100px]`，与 1024/1280 都对不上）。
- `rail:` 语义覆盖过宽（P2）：`JournalScreen:443`、`research/ResearchDetailScreen:115`（<1024 也三列，违反「<1024 单列」）。
- 附带：`ReviewScreen:763`（两栏挂 `desk:`，§3.1 规定 ≥1100 即两栏）、`IndustryDetailScreen:530`、`SignalsScreen:343`、`IndustryMatrixScreen:305`/`IndustryDetailScreen:470` 的骨架列数（与 M-9 同源，另计）。
- **派工注意**：这条必须**先改 `tailwind.config.ts` 才能改屏**，否则阶段二只会把 `min-[1024px]:` 换成另一种同样对不上条款的写法。它也是 M-4「任意值·断点档」那一支的前提。

### M-4 · 任意值 token 化（必须分三档）→ 一次消掉 **21 条**（6 P1 / 15 P2）

屏文件任意值 class 实测 **21 处**（issue 给的 4 处不准，见 §2）。**分三档处理，混成一档会派错工**：

1. **装饰线宽（4 条，最易）**：`w-[3px]`×3（`WatchScreen:87`、`ValidateScreen:610`，本体 `Card.tsx:102`）、`w-[2px]`（`HomeScreen:111`）→ tokens 补 `--ldst-status-bar:3px` / `--ldst-indicator:2px`。
2. **容器 / 侧栏宽（7 条）**：`max-w-[720px]`×2（`ReviewScreen:640`、`JournalScreen:520` —— 同值各写各的）、`w-44`（`ReviewScreen:582` 日期输入，属 Tailwind 默认档而非任何 token）、`desk:w-[208px]`（`ResearchReportScreen:298`，`w-drawer`480/`w-sidebar`232 均不适配）、`min-w-[8.5rem]`（`StageRail.tsx:59`）、`grid-cols-[184px_…_340px]`（`NewsScreen:555`）、`grid-cols-[minmax(0,1fr)_360px]`（`CalendarScreen:508`）→ tokens `layout` 补 `maxWidth-actioncol` / `width-sidepanel` / `min-w-stage`。
3. **表格列宽 / 表体高度（10 条，需先定条款）**：`ValidateScreen:586` `min-w-[960px]` + `:171-172` `ROW_GRID` 整串、`radar/PoolScreen:224/232/246/259/270` + `:431` `maxHeight="calc(100vh - 360px)"`、`radar/PositionsScreen:248…:352` 全部列宽（88/96/80/104/112/140px）+ `:338` `max-w-[140px]`（写法本身正确，值无刻度）、`JournalScreen:197/204` `144px`/`168px` + `:552` `maxHeight="480px"`、`IndustryMatrixScreen:246` `110px`（非 4 倍数，同文件 `:232` 176 与 `:246` 另一支 136 都是 4 倍数）+ `:407` `calc(100vh - 280px)`（窄屏下表体只剩 2–3 行）、`IndustryDetailScreen:553` `360px`、`LimitUpScreen:235` `calc(100vh - 320px)`、`ProvenanceDrawer.tsx:69` `64px`。
   - **C-09 的 `Column.width` / `maxHeight` API 本身没有刻度约束** → 阶段三先定「列宽只允许 4px 倍数」+「表体高度定档（table-min / table-max-h）」两条，否则这一档做不完；`radar/PositionsScreen:335` 的 `width:'140px'` 与 `:338` 的 `max-w-[140px]` 目前还是同一数字重复声明，token 化时应合并为一处真源。
   - `radar/CalendarScreen:135`/`:153` `min-h-[76px]` 建议单独定 `layout.calendarCell = 76px`（恰 = 2×`h-row` 38，语义可解释）：占位格与真实格共用同一档；若要等高，真实格改 `h-calendar-cell` + 内部 `overflow` 而不是 `min-h`（现写法占位格恒 76、真实格带 chips 会长高，同周次行不齐）。
- 半步间距是第四个小档，别混进上面三档：`gap-0.5`/`gap-1.5`/`py-1.5`（`ReviewScreen:765`/`:793`/`:820`、`WatchScreen`、`IndustryMatrix:82`/`:272`/`:426`、`IndustryDetail:143`/`:593`/`:629`/`:642`、`radar/NewsScreen:154`、`radar/CalendarScreen:167`、`SettingsScreen:325`）—— **基准 kit 自己也在用**（`Badge.tsx:68`、`Card.tsx:138`）→ 规范缺口：要么在 tokens `space` 登记 2/6px 半步，要么全站统一 `gap-1`，二者必须选一个，不能由各屏各写。

### M-7 · 枚举与后端 key 的中文映射出口 → 一次消掉 **14 条**（11 P1 / 3 P2）

**改哪里**：新建 `frontend/src/lib/labels.ts` 作唯一真源（`FRESHNESS_LABEL` / `SOURCE_STATUS_LABEL` / `MODE_LABEL` / `KIND_LABEL` / `LEVEL_LABEL` / `DATASET_LABEL` / `FAILURE_KIND_LABEL`），并定「后端 key 不得直显」的取值约束 —— `WeeklyScreen.tsx:104-115` 的 `number/text/asStringList` 与 `MarketScreen` 的 `isFiniteNumber` 是三份各写的取值层，一并收口（**规范缺口**）。

- 原始英文直显（P1）：`ResearchReportScreen:275`（`mode=structured|parsed|text`，见 `api/research.ts:84`、`demo.ts:301/:339/:382`）、`:436`（`failure.kind`，`demo.ts:465`）+ `:349`（错误码 `（${code}）`，§5.4 已有五类错误的中文标题表却未用）、`IndustryDetailScreen:657`（`Object.entries(payload.formulas)` 键名当可见文字，同屏 `:649` 却用 `DIMENSION_TITLE` 中文）、`IndustryDetailScreen:633`（数据集名 `daily_k`/`valuation_history`…，`title` 放 `source.id`；若定位是审计视图需在 §8 登记「允许原始英文数据集标识」，否则加映射）、`WeeklyScreen:176`、`WeeklyScreen:601`（`caliber` 英文键 + 值未格式化）、`SignalsScreen:351`/`:352`（「规则方向 bullish/bearish」而 `:56-58` 同屏已有 `偏多/偏空`）、`SettingsScreen:327`（`ACTION_LABEL[job.action] ?? job.action`，`api.ts:645` 的 action 是非封闭 union —— 兜底口子就是漏出口子）。
- 同枚举多套标签（P1）：`IndustryMatrixScreen:60-64`「完整/部分/缺失」vs `IndustryDetailScreen:50-54`「数据完整/部分缺失/无数据」vs 基准 `HomeScreen:62-66`「完整/降级/部分」；`IndustryMatrixScreen:80` 契约枚举 `ok|partial|missing`（`api/industry.ts:80`）与 §5.2 的 `ok|degraded|partial` 不同轨，且 `:273` 把 `missing` 映射 danger（§5.2 的 danger 属 `partial`）；`IndustryMatrixScreen:274`「未绑定」裸文字 vs `IndustryDetailScreen:441` 同一事实用 `Badge tone=neutral`；`SignalsScreen:44-48` 自造「提示/关注/风险」vs §3.2「提示/关注/强信号」；`IndustryDetailScreen:74`/`:154` 用 `MISSING_CELL_TITLE` 却缺 `—` 图形半边（`/industry:112-119` 有）。
- P2：`ValidateScreen:933`（`status !== 'ok'` 一律压成「部分缺失」，应对照 `:80-85` VERDICT 做全枚举映射）、`components/research/facts.ts:71`（`trimNumber` 去尾零 → `12.30%→"12.3"`、`12.00%→"12"`，同排 `MetricCard` 位数参差，应按键定 digits 而非一律 trim，影响 `ResearchDetailScreen:304/:354/:395`）、`IndustryMatrixScreen:250`/`:238`/`:438` 三种「缺失」话术（时点未知 / 成分未知 / `MISSING`）。

### M-6 · C-09 `DataTable` 能力扩展 → 一次消掉 **9 条**（6 P1 / 3 P2）

**改哪里**：`components/ui/Table.tsx` 补四个 prop，再替换两处手写表。**这条不是「把新 10 屏换成 DataTable」**（那是 issue 的错误前提，见 §2）——逐区块判定后全仓真该换的只有 2 块。

1. `expandable`（`renderExpanded` / `expandedContent`）→ 解锁 `ValidateScreen:587` 的 7 列手写表（表头 `:587-597` + 数据 `:599-727`，与同屏 `:950` 已用 DataTable 并存，是全仓「不同源」最典型样本；**挡住替换的是 API 不是能力**；`ROW_GRID:171` 一并下沉）、`MarketScreen:171`/`:188-195`（「梯队 → 3 只个股」压成一行文本，正解是展开行或抽屉）。
2. `summaryRow?: ReactNode`（tfoot 语义）→ 解锁 `radar/PoolScreen:125` 的 **P0 裸 `<table>`**（合计行在 `:166-181`，正因缺这个 prop 才自造表体；`caption`:126 / `scope`:134 反倒是给的，两处唯一共享的是单元格件 `PartCell`:48）。
3. 可点行键盘等价（`tabIndex` + `onKeyDown` + `role`）→ 修 `Table.tsx:130-132`，消 `IndustryMatrixScreen:408`（键盘与读屏到不了 `/industry/:name`）、`:397`「点击行进入详情」的纯鼠标语义；`SignalsScreen:504` `RulesCard` 的替换依赖 1+3。
4. 行 hover 底色条件化（仅 `onRowClick` 存在时给 `hover:bg-surface-3`）→ 修 `Table.tsx:135`，消 `radar/PositionsScreen:851`「暗示可点而不可点」的 affordance 误导。
5. 双行单元格与 `density="compact"`(36px) 冲突：`IndustryMatrixScreen:406`（`:235-239` 实测约 37.6px）、`IndustryDetailScreen:552`（`:233-236`）→ 需要 `subRender` 能力，或明确 compact 档不容双行；否则 C-09 的 36/38/40 三档形同失效。
6. `loading`/`error` 归属：`IndustryMatrix:401`、`IndustryDetail:547`、`radar/Positions:851`、`Journal:546` 都只传 `empty`，四态被拆到屏级，偏离 `Table.tsx:11`「loading/empty/error 都在表体内」的契约 → **需阶段三先定「屏级 vs 表体」归属条款**再统一，不要盲目改（当前筛选多为本地过滤，改了反而引入新问题）。
- 验收口径提醒：`ValidateScreen:587` 是用 div 栅格造的表，无 `role="table"`/`columnheader`/`caption`/`scope`，裸 `<table>` 计数器抓不到 → **「grep `<table` 只 1 处」不能作为「表体同源」的验收**。

### M-9 · 骨架与正文同构 + 清理 `DESIGN-SPEC` 假引用 → 一次消掉 **9 条**（7 P1 / 2 P2）

**改哪里**：`components/PageShell.tsx:84` 把骨架卡网格写死 `grid-cols-2 gap-4 desk:grid-cols-5`，而 `ScreenSkeleton` 只有 `metrics`/`rows` 两个数字参数，无法表达「左导航 + 右文档」「工具栏 + 宽表 + 图例卡」这类结构 → C-18 的「骨架与真实 DOM 同构」契约（`PageShell.tsx:79` 注释自陈）目前**不可能被满足**。给 `ScreenSkeleton` 增 `layout?: ReactNode`（各屏传自己的区块骨架）。

- 覆盖：`IndustryMatrixScreen:305`（`metrics=5 rows=8` vs 真实「工具栏+矩阵表+图例」）、`IndustryDetailScreen:470`（`metrics=5 rows=6` vs 真实「2 列×5 卡+表+4 侧栏卡」，加载完成时卡数/列数/分栏全跳）、`SignalsScreen:256`（`metrics=3` 却排进 5 列格、右侧空 2 格，加载完跳 `grid-cols-3`:343）、`WatchScreen:296`（§3.2 规定「信号行 5 条 + 阈值区骨架」，实际整屏替换、阈值区无骨架）、`research/ResearchReportScreen:205`（骨架是指标阵+表，正文是双栏文档，完全不同源）、`research/ResearchDetailScreen:566`、`radar/PositionsScreen:84`（骨架指标行 `gap-4` vs 正文 `gap-3` 的间距跳变）、`HomeScreen`/`MarketScreen` 同类（P2）。
- 同一条里的文档动作：全仓 40+ 处源码注释引用 `DESIGN-SPEC §3.1/§5/§7.1/§8/§10` 与 C-01…C-20，**但仓库与 git 历史都不存在 `DESIGN-SPEC.md`**（`Layout.tsx`、`Feedback.tsx`、`Badge.tsx`、各屏皆有）。本清单已统一改引 `UI-SPEC §n` + 组件契约码；这些假引用属 `//` 注释不影响运行，但阶段二若有人照 §x 去查规范会查不到 → 改注释或补档，二选一，别放着。

### M-10 · DOM 图取色通道 + 对比校验位 → 一次消掉 **14 条**（1 P1 / 13 P2）

**改哪里**：`components/ui/MiniChart.tsx`（`HBarList` 填充 `bg-accent`:39、轨道 `bg-surface-3`:37、`Sparkline` 默认 `tone=flat` → `text-text-secondary`:83）与 `industry/heat.ts:34`（顺序色阶用交互主色 `--ldst-accent` 混 `--ldst-surface`）。tokens `chart` 组只有 grid/axis/band/line/bar-up/bar-down/series-2/threshold/empty —— **既没有「顺序色阶」令牌，也没有约束 DOM(svg/flex) 图的取色**：canvas 走 `chartPalette()`/`cssVar()`，DOM 图各走各的，同一个「走势」在不同屏有两个色源。

- 取色（9 条）：`MarketScreen:678`、`WeeklyScreen:390`（`Sparkline` 未传 tone，与 CH-01 折线 `--ldst-chart-line`(`Chart.tsx:60`) 两屏两色源，同一「情绪温度走势」不同色）、`WeeklyScreen:540`、`IndustryMatrixScreen:41`（实现 `heat.ts:34`，缺 `--ldst-chart-heat`；色盲/浅色主题下热力与「链接/焦点」同色相、语义撞车）、`heat.ts:34` 混色基准取 `--ldst-surface` 而行 hover 是 `bg-surface-3`（`Table.tsx:135`）→ 行高亮在矩阵里被色块切断（DEC-028 底色只表相对位置）、`research/ResearchDetailScreen:232`（所有序列统一灰，明显涨跌也不着色）、`radar/PositionsScreen:172`/`:198`、`IndustryDetailScreen:90`（分位条，同时属 M-2）、`IndustryDetailScreen:162`（景气/资金两维 `HISTORY_KEY` 恒 `null` → 历史区块整块消失且无说明，违反 §1 原则 3「缺失必须显式可见」；`:171` 末点日期用原始 `YYYY-MM-DD` 而 `:635` 用 `formatTime`）。
- 对比可疑（5 条）：`text-text-muted` 落在只对该 token 校验过 `--ldst-surface` 之外的底色 —— `MarketScreen:203`（表体 + hover `bg-surface-3`）、`SettingsScreen:1226`、`radar/NewsScreen:319`（`bg-surface-2`）、`radar/CalendarScreen:253`/`:281`、`ResearchReportScreen:301`（StageRail 激活项 `bg-surface-3` 上叠 muted 序号）。
- 预防性：`IndustryMatrixScreen:122-123` 的 52% 档底色 ≈#325694 vs `--ldst-text` #e8ecf2 = **5.8:1**、muted on `surface-3` = **4.89:1** —— 两个余量都不厚，`heat.ts:12` 一旦上调或有人把 muted 放进色块就立刻塌陷。这里也是全站唯一「色阶由 token 混色派生」的地方，值得单独写进 §9 做对比校验位。

### M-8 · Tooltip 条款 → 消掉 **2 条**，但解锁 M-1 / M-13 的 20 条真正达标

全站无 Tooltip 组件（`components/ui` 没有 C 编号的 tooltip），所有「悬浮找回」都压在原生 `title` 上（`Badge.tsx:31`、`IndustryMatrixScreen:114`/`:133`、`heat.ts:46`、`radar/NewsScreen:172`）→ 键盘与触屏不可达、不可样式化、延迟约 1s。UI-SPEC §5.1 的「悬浮说明」需要定义为**可聚焦/可展开的等价通道**，否则 M-1 的溯源明细（`HomeScreen:78` 只挂 `title`）和 M-13 的 20 条截断在 A11y 上永远是半达标（§9「键盘可完成」）。

---

### 阶段二派工建议

**第一批 5 条（按 ROI）**

| 序 | 动作 | 一次消掉 | 含 P0 | 改哪里 | 是否需先补条款 |
|---|---|---|---|---|---|
| 1 | M-11 演示水印 | 10 | 7 | `PageShell.tsx:47-48` 一处 | 否 |
| 2 | M-12 缺失值不用 0 | 9 | 6 | `lib/format.ts` + 6 处 header/抽屉/图 | 部分（缺失沉底、chart.empty） |
| 3 | M-1 溯源徽标下沉 | 16 | 1 | 新建 `components/ui/SourceBadges.tsx` + 10 屏调用 | 否（§5.1/§5.2 已写明） |
| 4 | M-2 `Button as="a"` | 31 | 0 | `ui/Button.tsx` + 13 处链接 + 12 组重复件 | 是（`DescriptionList`、`SectionCard footer`） |
| 5 | M-13 truncate+title | 20 | 0 | `Card.tsx:46/:155` + 18 处屏侧 | 否（Tooltip 后置） |

第一批覆盖 **86 条 / 315（27%）**，含全部「信息错误或永久丢失」类 P0。

**第二批**：M-3 基准件动效（改 `components/ui` 即全仓受益，性价比最高的一批）、M-5 断点（必须先改 `tailwind.config.ts`）、M-9 骨架同构、M-4 任意值的档 1 + 档 2。
**第三批**：M-6 C-09 能力扩展（4 个 prop，是 `Validate:587` 与 `Pool:125` 两处手写表的唯一正解）、M-7 枚举映射、M-10 取色与对比、M-8 Tooltip、M-4 档 3。
**第四批（文档，且必须在第二/三批之前先出条款）**：M-4 的「列宽 4px 倍数 + 表体高度档」、M-6 的「表体唯一，含汇总也走 C-09」「四态归属屏级 vs 表体」、M-9 的「骨架须复刻正文布局」+ 假引用清理、M-10 的「DOM 图取色通道 + 顺序色阶令牌」、M-1 的「freshness 是否有第四态」（`api/radar.ts:129 freshness: string` 无约束、`radar/demo.ts:198/210` 出现 `'daily'` 而 `FRESHNESS_LABEL` 仅 3 键）、M-7 的「后端 key 不得直显」「信号级别三词表」。
除以上点名者，各屏正文还留了 **56 处**`规范缺口`标记（即「无条款可引、改了也没验收标准」的条目），全部属于这一批：阶段三应把它们逐条转成 UI-SPEC 条款，并为新 10 屏补 §3.5–§3.12 —— **UI-SPEC v1.0 只覆盖 `/review`§3.1、`/watch`§3.2、`/validate`§3.3、`/journal`§3.4，新 10 屏目前没有任何条款可依**，本清单对它们只能引 tokens + 四屏基准类比（见 §0 引用图例）。

> ⚠️ 务必写进阶段二工单描述、与 issue 前提不同的一点：**「把新 10 屏的手写行列换成 DataTable」不是本阶段的主要工作量**。逐区块判定后新 10 屏真正该换的只有 `radar/PoolScreen.tsx:125` 一处（还被 C-09 缺 `summaryRow` 挡住），原四屏也只有 `ValidateScreen.tsx:587` 一处（被缺 `expandable` 挡住）。`/research/:code` 的「7 处手写栅格」经逐区块复核是 `<dl>` 键值对与 KPI 卡阵的合法形态，换成表体反而更差（依据见各屏维度 4）；`radar/CalendarScreen.tsx:132-186` 的月历是二维日期矩阵，DataTable 表达不了跨周对齐与占位格。若照原前提派工，会得到一批「把可读的键值对改成难读的表格」的返工。

---

## 8. 附录

### 8.1 逐屏条目分布

| 屏 | 文件 | P0 | P1 | P2 | 合计 | 结构最接近基准之处 | 离基准最远之处 |
|---|---|---|---|---|---|---|---|
| `/review` | `ReviewScreen.tsx` | 1 | 6 | 10 | 17 | 四态 + `sentimentReady` 整组置 `—`（`:414-417`） | 溯源徽标本地四写其一 + 色序反转（`:196/:198`） |
| `/watch` | `WatchScreen.tsx` | 0 | 2 | 10 | 12 | `trustedRows` 过期即隐藏旧值（`:185`）完全符合 §3.2 | freshness/source_status 塌成一条 Banner（`:325`） |
| `/validate` | `ValidateScreen.tsx` | 0 | 9 | 8 | 17 | 双视图四态齐全、仓位上限整列 `MISSING`+原因（`:628-633`） | 手写假表格与同屏 DataTable 并存（`:587` vs `:950`） |
| `/journal` | `JournalScreen.tsx` | 0 | 3 | 6 | 9 | 「样本不足」实现到位（`:390-403`） | §3.4 L0 时间筛选与 L1 纪律统计未落地（`:216/:443`） |
| `/` | `HomeScreen.tsx` | 0 | 13 | 7 | 20 | KPI 卡阵与 `SourceBadge` 是别人的参照物本身 | `SourceBadge` 留在屏内、缺失原因交给会截断的 `note`（`:69/:382`） |
| `/market` | `MarketScreen.tsx` | 0 | 10 | 11 | 21 | 表体密度与 `h-row` 体系最完整 | `min-[640px]:` 挤压 `hero` 卡（`:529`，全仓最高风险） |
| `/weekly` | `WeeklyScreen.tsx` | 1 | 15 | 14 | 30 | 五段结构与段级状态映射 | 缺失被画成实体条（`:543`）、英文键直显（`:601`） |
| `/settings` | `SettingsScreen.tsx` | 1 | 5 | 11 | 17 | 复用 C-03/C-06 表单件最彻底，无自造 Field | 离线仍展示假定端口（`:1346`）、抽屉零动效（`:570`） |
| `/limit-up` | `limitup/LimitUpScreen.tsx` | 2 | 2 | 3 | 7 | **DataTable / 缺失值 / 五态是本仓示范** | demo 无水印（`:128`）、envelope 字段被解析层丢弃（`api/limitPool.ts:89-93`） |
| `/industry` | `industry/IndustryMatrixScreen.tsx` | 2 | 13 | 17 | 32 | 缺失单元格「—+文字+title」可作样板（`:112-119`） | 头部 `0 个行业`（`:286`）、状态词表三套（`:60-64`） |
| `/industry/:name` | `industry/IndustryDetailScreen.tsx` | 1 | 14 | 21 | 36 | 四态分支最细（`DetailEmpty` 四分支 `:337-435`） | 私有件与手写 `dl` 写法最密集，是 M-2 主战场 |
| `/signals` | `signals/SignalsScreen.tsx` | 2 | 14 | 16 | 32 | 证据链 `break-all` 不截断长代码名（`:71/:106`） | 「命中 0 条」（`:237`）、`bullish` 直显（`:351`） |
| `/news` | `radar/NewsScreen.tsx` | 0 | 3 | 8 | 11 | 五态齐全 + 缺失全走 `MISSING` | `freshness` 整屏不呈现、标题无截断找回（`:174/:244`） |
| `/calendar` | `radar/CalendarScreen.tsx` | 1 | 3 | 6 | 10 | `Badge` tone 双编码优于 `/news` | `min-h-[76px]` 与 `h-row` 脱钩 + 占位/真实格不齐（`:135/:153`） |
| `/pool` | `radar/PoolScreen.tsx` | 3 | 3 | 5 | 11 | 主表体完全同源、`PartCell` 缺失 `—`+sr-only | 抽屉第二套 `<table>` + 权重 `0.0%` 冒充缺失（`:125/:156`） |
| `/positions` | `radar/PositionsScreen.tsx` | 3 | 4 | 7 | 14 | DataTable + 五态 + 隐私条常驻全齐（`:83-100` 五分支都渲染） | 「0 只」（`:636`）、`data_source_status` 不上屏（`:639`） |
| `/research/:code` | `research/ResearchDetailScreen.tsx` | 0 | 1 | 10 | 11 | `present` 判定用 `!==null` 而非真值折叠（`:117/:355/:396`） | 断点双轨（`:115` vs `:394`）、footer 负边距 hack ×4 |
| `/research/:code/report` | `research/ResearchReportScreen.tsx` | 0 | 1 | 9 | 10 | 段级 failed/pending/无产物都有出口动作，三屏最好（`:357-424`） | `mode=` / `failure.kind` 原始枚举直显（`:275/:436`） |
| **合计** | | **17 行 / 去重 15 条** | **121** | **177** | **315** | | |

*（P0 列按屏内出现行计；`radar/PoolScreen` 与 `radar/PositionsScreen` 的 demo 水印与「裸表 / 假 0」各自在 §1 已并入同一根因，故 §1 权威总数为 15。）*

### 8.2 逐屏一句话结论

- `/review`：四态与缺失值是基准水准，差在溯源徽标（本地四写其一、缺图形双编码、色序反转成 P0）与 rail 档指标断点。
- `/watch`：还原度较高的屏，`trustedRows` 是规范的正例；差距集中在降级视觉 —— freshness 根本没读，三类状态塌成一条。
- `/validate`：四态与缺失纪律合格，但它是「不同源」最重的一屏：手写假表格与同屏 DataTable 并存，列宽全任意值、truncate 一半有一半没有。
- `/journal`：最干净的屏；两处要补 —— loading/error 态隐私条断供，§3.4 的 L0 时间筛选与 L1 纪律统计未落地（规范与 DEC-015 冲突需回写）。
- `/`：它是别人的参照物，自身却把最关键的 `SourceBadge` 留在屏内，并把「为什么是 —」交给一个会截断的 `note`。
- `/market`：表体最完整，风险最集中在 `min-[640px]:grid-cols-3` —— 640–1023 排三张 `h-metric` 卡塞 36px 数值，撑破概率全仓第一。
- `/weekly`：条目数第二高、问题种类最杂，也是唯二把「缺失」画成实体图形（`?? 0` → HBarList 实体条）并把英文键名直显给用户的屏。
- `/settings`：表单件复用最好，却恰恰在 §7.3 点名要 `duration-base` 的抽屉上过渡为零，且离线时把假定端口当真值展示。
- `/limit-up`：DataTable 用法、缺失值、五态分支就是本仓基准示范；差两口气 —— envelope freshness 被解析层丢掉、demo 数据无水印。
- `/industry`：四态与缺失语义已站住，差距在三处 —— 手写按钮链接丢动效与字重、状态词表三屏三套、加载态用 0 冒充未取到的行业数。
- `/industry/:name`：四态分支做得最细，但它是私有组件与手写 `dl` 写法最密集的一屏，是 M-2 的主战场。
- `/signals`：证据链不截断长代码名是对的；两条 P0（「命中 0 条」、demo 水印），P1 集中在徽标词表与抽屉动效。
- `/news`：结构、四态、缺失值都贴基准；主要差距是新鲜度/降级视觉重量缺失与标题无截断找回。
- `/calendar`：Badge 复用优于 `/news`；最突出的是任意值日格高度破坏与 `h-row` 体系同源、长文本无找回。
- `/pool`：主表体已完全同源、四态与 MISSING 最规范；差距全部在抽屉里的第二套表（P0 不同源 + P0 权重 0 冒充 + 数值列漏 `ldst-num`）。
- `/positions`：radar 四屏里结构最接近基准（DataTable/四态/隐私条全齐），差距集中在「0 冒充未知」与 `data_source_status` 完全不上屏两处可信度呈现。
- `/research/:code`：信息架构与四态/降级接近基准，离基准最远的是断点双轨与 footer 负边距 hack；它是「不需要换 DataTable」的屏。
- `/research/:code/report`：段级降级路径是三张 research 屏里做得最好的，差距在两处原始枚举直显，以及与 Detail 共享的徽标重量/断点/LinkButton 同源问题。

### 8.3 本清单没做的事（边界与已知未覆盖）

1. **没有跑构建、没有起 dev server、没有截图比对**。issue 允许「before 图尚未入库就以代码为准」；`docs/screenshots/` 现仅 `limit-up-empty.png` / `limit-up-normal.png` 两张，无 before 基线，故全部结论来自代码静态阅读。1024 / 1440 两档「无横向滚动」（§11 验收）**未实测**，只做了 `truncate` / `min-w-0` / `minmax(0,1fr)` 的存在性判定 —— 阶段二若要验收撑破，需要真机或 Playwright 截图。
2. **`ldst-num` 漏网 16 条（1 P1 / 15 P2）没有单独成动作**：它们逐条散布在各屏维度 2，修复动作是同一种「把裸数字包进 `<span className="ldst-num">` 或在 `lib/format` 出口统一加类」，建议在 M-12 落地时一并做（`WeeklyScreen:345` 的 8 个数值列是唯一 P1）。
3. **未审 `components/ui` 之外的重复实现**：`components/kit/`、`charts/` 只读了 `Chart.tsx`/`cssVar.ts`/`MiniChart.tsx`/`AiErrorNotice.tsx` 的相关段落，未做逐行清单（它们不是「屏」，但 M-2 / M-3 / M-10 的修复落点大量在那里）。
4. **未做深浅两主题的对比度实测**：M-10 的对比条目只登记了「tokens 只对 `--ldst-surface` 校验过」这一事实与两处已手算的最坏档余量（5.8:1 / 4.89:1），其余按可疑登记，未逐个换算。
5. **未审 <640px**：UI-SPEC §2 只定到 1024/1280 三档，`rail` 以下无条款，各屏窄屏表现本清单不作判定（属 M-9 文档批次的缺口）。
6. **未提任何改动**：全程只读代码 + 只写本文档，未新增依赖，未改任何屏幕代码，未碰 `disk-warden`。
