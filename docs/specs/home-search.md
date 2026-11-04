> **变更记录**
>
> - **v1.0（2026-11-03，QUOT-348，UI/UX）** 首版。范围：首页右上角搜索/提问**入口**与**聚合搜索面板**的交互与视觉规格；含状态定义、键盘可达、空态/无结果/降级文案、跳转契约与实测对比度。**本文件不含实现代码，不改动任何 `frontend/src/` 文件。**
> - **v1.1（2026-11-03，QUOT-348 落盘轮）** 与已合入 main 的数据层 `frontend/src/lib/searchCore.ts`（QUOT-356，merge `8ecdd21`）对齐：组名统一为 `行业 / 题材`、类型标签收进 `SearchTag` 四值、组状态改按 `GroupStatus` 五值表达（新增 §8.8 四态矩阵与 §9.4 接口对应表）；§9.3 的后端提案标记为已被客户端聚合取代；视觉稿路径改为仓内真实路径（`docs/screenshots/` + `docs/specs/home-search-prototype.html`）。**仍未改动任何 `frontend/src/` 文件。**
> - **v1.2（2026-11-03，合入 main 轮）** 与 main 上的实装（`424af9f` 首页搜索入口 + 聚合面板，QUOT-357）对齐，**只改状态标记与文档编号，规格条文零改动**：① `UI-SPEC.md` 的小节号让位给先落地的 §3.19 AI复盘，本节改为 **§3.20**，变更记录行相应改为 v1.8；② C-21 `HomeSearchTrigger` / C-22 `SearchPanel` 由「待实装」更新为「已实装」并给出真实文件路径（§10.2、§13）；③ §13 的两条跨队协作项（`NewsScreen` 读 `?q=`、`Overlay.tsx` export `useModalChrome`）标记为已完成，并如实写明后端 `/api/news` 仍无 `q` 参数（资讯匹配走 §9.3 #3 的前端退化路径）。
> - **v1.3（2026-11-04，QUOT-398）** 面板外壳改为**居中模态**，只动 §5.1 示意图、§5.2 尺寸表、§5.3 与侧栏的关系、§5.5 结构里的结果区高度，以及 §6.2 行数核算的引用：① 定位从 `right-5 top-16`（右上角 640px 浮层）改为 `left-50% + translateX(-50%)`、顶边 `12vh`；② 宽度从 `w-drawer-wide`(640) / `w-drawer`(480) 改为 `min(86vw, var(--ldst-table-max, 1296px))`；③ 结果区从固定 `max-height: var(--ldst-table-h-lg)`(420px) 改为 **flex 吃掉面板剩余高度**（面板 `max-height: 73vh`），满命中不再被 420px 截断；④ 空态第一行改为「输入关键词开始搜索，或点下面最近搜索直接进入」；无结果态首句改为「没有命中，试试 6 位代码 / 行业名」。**交互契约（§7 键盘、§8 状态、§9 接口映射、§5.4 模态语义）零改动**，`useHomeSearch` / 路由映射 / 首页其它卡片均未动。
- 上游需求：QUOT-343（父：QUOT-343 描述里的第 2 条口径已过期，见本文 §3.1 的更正）。
> - 上游规范：`docs/UI-SPEC.md`（§5.1/§5.2/§5.3 溯源徽标、§7.3 层级与过渡、§8 文案、§9 可访问性）、`docs/tasks/TASK-UI-REDESIGN/DESIGN-SPEC.md`（§5 控件 C-xx、§8 五态——**该文件在 `docs/tasks/` 下，按 DEC-0026 被 `.gitignore` 排除，仓库内读不到**；本文所有 C-xx 编号在仓库内的可核对真源是 `docs/UI-SPEC.md §6.1`）、`frontend/tokens.json` + `frontend/src/styles/tokens.css`（令牌真源）。

## 0. 范围与非目标

**做什么**

1. 首页（`/`）右上角一个常驻搜索入口：输入即搜，点击/回车弹出聚合面板。
2. 面板内三组结果：**个股** / **行业 / 题材** / **资讯**，各自的排序规则、展示字段、来源标注。
3. 键盘与读屏契约、焦点管理、空态/无结果/加载/单组失败/整体失败的可见行为与文案。
4. 全部视觉取值来自既有令牌：**不新增颜色令牌、不引入新 UI 库**（§10 说明两处「结构尺寸」例外）。

**不做什么**（明确排除，避免实现期自行发挥）

| 不做 | 原因 |
|---|---|
| 自然语言「AI 提问」问答 | 前端与后端均无问答入口与作业通道（全仓 `提问/ask` 只出现在 `src/api/radar.ts`、`screens/radar/CalendarScreen.tsx` 的无关文案里）。入口文案因此不承诺「问」（§4.2） |
| 全市场 5000+ 标的的名称检索 | 现无 market-wide 代码↔名称索引端点（§9.2）。第一期检索域 = 今日数据并集，未命中≠不存在 |
| 拼音/首字母匹配、错别字纠正 | 排序规则先做确定性的前缀/包含匹配（§6.3），容错等检索域稳定后再谈 |
| 搜索历史上云、跨设备同步 | 只存本机（与 `localStorage['ldst.nav.expanded']` 同级的本机偏好，见 `Layout.tsx:173`） |
| 面板内直接改数据（加盯、写条件） | 面板只做「找到 → 跳过去」，写操作留在目标屏，避免一个组件跨两个责任目录 |
| 键盘快捷键设置界面 | `/` 一条固定快捷键（§7.2），不做可配置 |
| 演示水印 | QUOT-348 明确禁止。面板内**不得**出现 `DemoStateBadge` / 「演示态：…」角标；来源事实一律走 `SourceBadges`（§6.7） |

## 1. 用户、场景与判定标准

主用户：**收盘后用 15–30 分钟做判断的 A 股个人复盘者**（`docs/UI-SPEC.md §1` 同一口径）。搜索入口服务的是他手上的三个高频动作，而不是「探索全站」：

| 场景 | 输入长什么样 | 期望落地 | 频次判断 |
|---|---|---|---|
| 朋友提到一个代码，我要看它今天怎么样 | `600000` / `沪电股份` | `/research/600000` | 最高频 |
| 今天某题材在动，我要看它的成分与指标 | `机器人` / `白酒` | `/industry/机器人` | 高频 |
| 一条消息刷过，我要回到资讯流里找它 | `算力` / `发改委` | `/news?q=算力` | 中频 |

**这个入口做成了的判定标准**：用户在 3 秒内、不看提示文字，能用键盘从首页到达上述任一目的地。这条标准决定了 §4 的入口必须显示占位文案（而不是只有一个放大镜图标）、§7 的 ↑/↓ 必须能在结果间移动、§8 的每个失败态都必须给出下一步。

## 2. 术语

| 术语 | 指代 | 代码里建议的命名 |
|---|---|---|
| 入口 / 触发件（trigger） | 首页右上角那个看起来像输入框的按钮（§4.3 说明为什么不是 input） | `HomeSearchTrigger` |
| 面板（panel） | 弹出后的聚合结果容器 | `SearchPanel` |
| 组（group） | `stocks` / `themes` / `news` 之一，展示名取 `group.label`（个股 / 行业 / 题材 / 资讯） | `SearchGroup`（`searchCore.ts:85-94`） |
| 行（row / option） | 组内一条命中项 | `SearchOption` |
| 高亮（active） | ↑/↓ 选中的那一行，**不是 DOM 焦点** | `aria-activedescendant` |
| 命中域（universe） | 可被检索的当日对象集合（§9.2） | — |

## 3. 跳转契约（含对 QUOT-343 口径的更正）

### 3.1 更正：`/market/theme` 不是前端路由

QUOT-343 原文写「行业/题材 → 跳 `/industry` 或 `/market/theme`」。**`/market/theme` 是后端数据接口**（`GET /api/market/theme?date=`，`src/ladderstock/web/server.py:1450`），前端路由表里没有这一条。

前端真实路由只有 `frontend/src/routes.ts` 的 `ROUTE_PATHS` 常量（编译期 `Record<RoutePath, ReactElement>` 约束 `App.tsx`，加一条不写组件就编译不过）。与本任务相关的四条：

| 目标 | 路由 | 出处 |
|---|---|---|
| 个股深挖 | `/research/:code` | `frontend/src/routes.ts` |
| 行业详情 | `/industry/:name` | `frontend/src/routes.ts` |
| 行业列表 | `/industry` | `frontend/src/routes.ts` |
| 资讯雷达 | `/news` | `frontend/src/routes.ts`；题材榜今天住在 `/market?tab=theme`（`Layout.tsx:92`） |

### 3.2 四类输入的映射规则（实现照此分支，无歧义）

| 判定（按顺序命中即停） | 目标 | 构造方式 | 现网已有先例 |
|---|---|---|---|
| 6 位数字（`/^\d{6}$/`），或匹配到个股名称 | `/research/:code` | `navigate(\`/research/${encodeURIComponent(code)}\`)` | `screens/research/ResearchEntryScreen.tsx:21-41` 已是同一写法 |
| 命中行业字典名 | `/industry/:name` | 复用 `screens/industry/links.ts:20-28` 的 `industryHref(name)`（内部即 `encodeURIComponent` + `withParams`） | 行业页所有跳转都走它 |
| 命中题材名但**不在**行业字典 | `/market?tab=theme` | 带 `?tab=theme`，**不带** path 段 | `Layout.tsx:92` 的侧栏入口 |
| 都不命中，但用户显式提交（Enter）或选「在资讯雷达中搜索」 | `/news?q=<关键词>` | `new URLSearchParams({ q: kw }).toString()` | ⚠️ `?q=` **目前不存在**，见 §3.4 |

**编码示例**（`encodeURIComponent` / `URLSearchParams` 实测输出，实现直接对照）：

| 明文 | path 段编码后 | query 值编码后 |
|---|---|---|
| `机器人` | `/industry/%E6%9C%BA%E5%99%A8%E4%BA%BA` | `/news?q=%E6%9C%BA%E5%99%A8%E4%BA%BA` |
| `白酒` | `/industry/%E7%99%BD%E9%85%92` | `/news?q=%E7%99%BD%E9%85%92` |
| `半导体` | `/industry/%E5%8D%8A%E5%AF%BC%E4%BD%93` | `/news?q=%E5%8D%8A%E5%AF%BC%E4%BD%93` |
| `AI算力` | — | `/news?q=AI%E7%AE%97%E5%8A%9B` |
| `600000` | `/research/600000`（数字无需编码） | `/news?q=600000` |

> **必须统一走 `encodeURIComponent`（或 `URLSearchParams`），禁止手拼百分号**：`withParams`（`links.ts:10-18`）已经在用 `URLSearchParams`，双百分号编码会让 `/industry/%25E6%259C%25BA...` 这样的地址在行业页查不到名字，直接落进「该行业暂无数据」。

### 3.3 跳转时必须携带的两个参数

沿用 `industryHref` 的既有约定：跳转只带**演示开关**与**强制态**两个参数（`demo`、`state`），其余 URL 参数不带。
搜索面板**不带** `?demo=1` 之外的演示语义，也**不携带**任何水印信息：入口和面板永远按真实数据渲染（QUOT-348 硬约束）。

### 3.4 `/news` 需要一个新参数：`?q=`

`NewsScreen` 现在从 URL 读 `date` / `until` / `source` / `code` / `industry`（`screens/radar/NewsScreen.tsx:404-411`），写回用 `setParam(..., { replace: true })`（`:417-422`）。**没有 `q`**。本规格对 `q` 的定义（前端架构师实现，UI/UX 不再追问即可落地）：

| 项 | 规定 |
|---|---|
| 语义 | 对 `NewsItem.title` 与 `NewsItem.summary` 做**不区分大小写的子串匹配**；两者都不含该词但 `related_codes` 含该词（6 位数字）时也算命中 |
| 与既有 `?code=` 的关系 | `q` 是**关键词**，`code` 是**代码精确过滤**，两者独立可叠加；`?q=600000` 不等于 `?code=600000`（前者命中标题里的「600000」字样） |
| 呈现 | 过滤条里出现一枚可关闭的关键词 chip：`关键词：机器人 ✕`，✕ 清除 `q` 并 `replace: true` |
| 无命中 | 复用 `NewsScreen` 既有空态，文案首句改为「窗口内没有含「{q}」的资讯」，出口保持既有「放宽日期」 |
| 分词 | 不引入分词/相关性打分；按 `published_at` 倒序（资讯流默认排序） |

## 4. 入口（首页右上角）

### 4.1 位置

挂在 `PageHeader` 的 **`actions` 槽位**，作为**第一个**子节点，位于「刷新全部」按钮左侧。

- 挂载点：`src/components/PageShell.tsx:26-40`，`actions` 渲染在 `<header className="flex min-h-topbar flex-wrap items-center justify-between gap-3">` 的右侧容器（`flex shrink-0 flex-wrap items-center gap-2`）。
- 调用点：`src/screens/HomeScreen.tsx:339-356` 现在只放了一枚 `Button variant="primary"`（刷新全部）。
- 标题栏高度 `min-h-topbar` = `--ldst-topbar-h` **56px**（`tokens.css:99`），入口高 40px 在其中垂直居中，上下各留 8px；**不需要**新建全局 topbar，也不改 `Layout.tsx`。

> 为什么放 `actions` 而不是新起一条 topbar：`Layout.tsx:395` 的 `<main id="main" className="px-5 py-5">` 里没有跨屏顶栏，只有侧栏自己有一条 56px 的头（`Layout.tsx:247`）。首页右上角在现有体系里就是 `actions` 槽位。加顶栏会同时动 `Layout.tsx`（架构师目录）与每一屏的页头，收益不足以付这个代价。

### 4.2 视觉与尺寸

| 属性 | 取值 | 令牌出处 |
|---|---|---|
| 高度 | **40px** | `--ldst-control-h-lg: 40px`，其 usage 注释原文即「主操作按钮 lg / **搜索框**」（`tokens.css:111`）——这一档本来就是给搜索框留的 |
| 宽度 | **256px**（`w-64`），`max-w-full`；rail（<1280）下 176px（`w-44`）。rail 下占位文案按 `truncate` 省略为「搜索个股 / 行业 …」（见附件 `09`，实测截断点在此），**不因此改文案**：放大镜 + `/` 键帽已足够表意，`aria-label` 不受截断影响 | Tailwind 内建宽度档，非任意值，不动令牌 |
| 内距 | `pl-7 pr-2`（左让出放大镜）/ `px-3`（无图标分支） | `Field.tsx:54-89` 的 `leadingIcon` 约定：图标绝对定位 `left-2`，输入 `pl-7` |
| 圆角 | `rounded`（8px，全站唯一档） | `--ldst-radius: 8px`（`tokens.css:95`） |
| 边框 | `border border-border-strong` | 输入控件边界承担 ≥3:1 识别职责（`tokens.css:19`，WCAG 1.4.11） |
| 底色 | `bg-sunken` | 「输入框 / 代码块」专用底（`tokens.css:18`） |
| 占位文案 | `搜索个股 / 行业 / 资讯`，14px `text-body`，`text-text-muted` | 全站最小字号 12px、输入框默认 14px（`tokens.css:67-70`） |
| 左侧图标 | `lucide-react` `Search`，`size-3.5`（14px），`text-text-muted`，`aria-hidden="true"` | 与 `NewsScreen.tsx:136` 代码框的 `leadingIcon` 完全一致 |
| 右侧快捷键提示 | 一枚 12px 的 `/` 键帽：`rounded-xs border border-border px-1 text-caption text-text-muted`，rail 下 `rail:hidden` | 12px 是全站最小字号；`--ldst-radius-xs: 4px` 用于 8px 容器内的嵌套元素 |

占位文案长度核算（避免实现时截断）：14px 中文字宽≈14px，`搜索个股 / 行业 / 资讯` 含 6 个汉字 + 3 个拉丁词 + 2 个 `/` ≈ **150px**；可用文字宽 = 256 − 28（左图标区） − 8（右内距） − 22（键帽） = **198px** → 单行不截断，余量 48px。

> **文案不写「提问」**：入口若写「问…」而面板只能跳转，用户会得到一个骗人的承诺；后端没有问答通道（§0）。改成名词短语「搜索个股 / 行业 / 资讯」，与三组结果**一一对应**——这是本规格唯一对需求原文文案做的收敛，理由如上，需产品确认（§14-A1）。

### 4.3 为什么入口是 `<button>` 而不是真的 `disabled` input

入口看起来是输入框，**行为必须是按钮**：

- 真 input 要在入口内直接输入并同步结果，等于把面板做两遍；`readonly` 的 input 会被读屏播报成「编辑框」但打字无声，属可访问性缺陷。
- `<button type="button">` 天然获得 Enter/Space 激活、焦点顺序与 `:focus-visible` 环（`styles/index.css:30-34`），零额外代码。
- ARIA：`aria-haspopup="dialog"`、`aria-expanded={open}`、`aria-controls={panelId}`。

### 4.4 四个状态的反馈

| 状态 | 变化 | 时长 |
|---|---|---|
| **默认** | 如上表 | — |
| **hover** | 边框 `border-border-strong` → `border-accent`；占位文字 `text-text-muted` → `text-text-secondary`；底色不变（`sunken` 没有配套的 hover 档，**不新造**，靠描边变化表达可点击） | `transition-colors duration-fast`（120ms，`tokens.css:156`：hover/focus/press 唯一档） |
| **focus-visible**（键盘到达但未打开） | 全局焦点环 `box-shadow: var(--ldst-focus-ring)`（`styles/index.css:30-34`）+ 边框 `border-accent`。同时用 `text-caption text-text-muted` 在键帽位置换成「按 Enter」字样提示可打开 | `duration-fast` |
| **打开（面板在场）** | 入口保持 `focus-visible` 观感不额外加重（焦点已移入面板，入口只作为 `aria-controls` 的锚点）；`aria-expanded="true"` | — |
| **按下** | `active:translate-y-px`（与 `Button.tsx:22` 全站按压反馈一致） | — |

对比度核对（WCAG，实测值见 §7.4 表）：`accent` 描边对 `sunken` 底 dark 5.9:1 / light 6.3:1，远超交互件 3:1 要求；hover 换色**不携带信息**，即使色盲或高对比模式下退化也仍然可用（描边始终存在，只是颜色变亮）。

### 4.5 打开的触发条件（对需求「点击或聚焦即弹出」的实现口径）

| 事件 | 是否打开 | 说明 |
|---|---|---|
| `pointerdown`（鼠标/触屏按下） | ✅ 打开 | 早于 `click`，感觉是「点上去就出来了」 |
| `keydown` Enter / Space | ✅ 打开 | 键盘用户主动按下 |
| 全局 `/`（非输入态） | ✅ 打开并把焦点交给面板输入框 | §7.2 |
| **`focus`（Tab 路过）** | ❌ **不打开** | 与需求原文的唯一分歧：Tab 经过即弹遮罩，会让键盘用户每按一次 Tab 就被迫 Esc 退出才能继续走位，也打断「先看清页面再决定搜什么」的习惯（尼尔森 #7 系统状态可见性 ≠ 抢占式弹窗）。改为焦点环 + 「按 Enter」提示承担同等可发现性 |

其余口径与需求一致：入口在视觉上就是一个输入框，弹出的是 overlay 面板。**这条分歧需要产品/老板点头**，已列入 §14-A2，并给出被否掉时的退路（`onFocus` 直接打开、Tab 时给 `?` 键提示）。

## 5. 面板外壳

### 5.1 挂载方式与层级

```
document.body
└─ createPortal
   └─ div  fixed inset-0 z-overlay          ← 容器（遮罩 + 面板）
      ├─ div  absolute inset-0 bg-scrim      ← 遮罩，点击关闭，aria-hidden
      └─ div  absolute left-50% top-[12vh]   ← 面板（水平居中，v1.3 / QUOT-398）
               translateX(-50%)
```

| 项 | 规定 | 依据 |
|---|---|---|
| 挂载 | `createPortal(..., document.body)` | `Overlay.tsx:112-133`（Drawer）与 `:196-215`（ConfirmDialog）都这么做，不发明第三种 |
| 层级 | `z-overlay` = **40**（`--ldst-z-overlay`，`tokens.css:122`） | 见下方「为什么不需要 z-dropdown」 |
| 遮罩色 | `bg-scrim`：dark `rgba(4,6,9,.68)` / light `rgba(16,24,40,.40)`（`tokens.css:47`） | 遮罩只压暗，**不用于**表达状态；透明度是既有的，不改 |
| 面板底 | `bg-surface` + `border border-border` + `rounded`（8px） + `shadow-dialog`（`0 24px 48px`，`tokens.css:62`） | 面板是「浮在页面上的卡片」，用它自己那一档阴影，不用 `shadow-card`（卡片唯一档，卡片之间不叠加） |
| 入场 | 遮罩淡入 + 面板 `opacity` 与 `scale-95→100`，`transition-all duration-fast ease-ldst`，起止两帧由 `useEnter(open)` 提供 | `Overlay.tsx:203-207` 是同一写法；`duration-fast` = 「展开/收起」唯一档（`tokens.css:156`）。**不用 `duration-base`**：`scripts/check-ui-consistency.mjs` 规则 6 只把「抽屉/Toast」的 180ms 列为登记例外，展开类必须 120ms |
| 减少动态 | `prefers-reduced-motion: reduce` 时令牌 `--ldst-duration-base/fast → 0ms` 已由 `tokens.css:125` 区块统一处理，规格不额外写 | `tokens.json` motion.reducedMotion |

**为什么不新增 `z-dropdown` 令牌**：现有档位 20(banner) / 40(overlay) / 50(dialog) / 60(toast) 里没有浮层档，看起来缺一个，但面板走的是「遮罩 + 弹层」模型（§5.4 的理由），`z-overlay` 已经正确压住侧栏与内容；再加一档会出现「40 和 45 谁在上面」的第二个问题。这是有意的例外，不是遗漏。

### 5.2 尺寸

| 项 | 取值 | 依据 |
|---|---|---|
| 水平定位 | **视口水平居中**：`left: 50%` + `transform: translateX(-50%)` | v1.3 / QUOT-398：老板看过参考实现（`vibe-astock` 首页「今天，想研究什么？」卡片）后否掉了右上角浮层。居中后与入口的横向位置无关，页头窄屏换行也不影响 |
| 宽度 | **`min(86vw, var(--ldst-table-max, 1296px))`**，两个断点同一口径 | 1440 视口下 = 1238px，左边界 101px：侧栏 232 + 两侧留白，压进内容区；1280 下 = 1100px、左边界 90px，仍 > 侧栏 232 − 不要求「躲开侧栏」，遮罩本来盖住它（§5.3）。**不新增令牌**：`--ldst-table-max` 是既有表格宽度上限，内联 `style` 写 CSS 变量不触发 `check:ui` 规则 3（禁 `w-[...]` 方括号任意值） |
| 顶边 | **`top: 12vh`** = 108px（900 高） | 视觉重心略高于正中；给下方留出结果区的余量。**不做 `translateY(-50%)` 的严格垂直居中**：结果区高度随命中数变化，严格居中会让面板「上下两头同时长」，输入框在使用中漂移 |
| 面板整体最大高 | **`max-height: 73vh`** ≈ 657px（900 高） | 与顶边 12vh 合计留出底部 15vh；窗口再矮时面板整体收缩、结果区跟着滚（结果区是 `flex-1 min-h-0`） |
| 结果滚动区 | **flex 吃掉剩余高度**：`min-h-0 flex-1 overflow-auto`，**不再设固定 `max-height`** | v1.3 起满命中（§6.2 ≈550px）在 900 高视口下不再必然滚动；窗口矮到装不下时才滚，§7.3 的「高亮行自动进视野」仍然必要 |

面板总高构成（实现按此核对，不要再自己算）：输入行 40 + 下内距 8 = 48；结果区 = 面板高 − 48 − 底部提示条 `min-h-control`(32) − 下内距 8；面板上下内距合计 16。900 高视口、空态下实测面板 301px（内容自然高度），满命中时到 73vh 封顶。

### 5.3 与侧栏 `Layout.tsx` 的关系

| 事实 | 结论 |
|---|---|
| 侧栏是 `sticky top-0 w-sidebar`（232px / rail 56px），**没有设置 z-index**（`Layout.tsx:246`） | 面板 portal 到 body 且在 `z-overlay`(40)，**必然盖住侧栏**，不需要给侧栏补 z-index，也不要为面板改 `Layout.tsx` |
| 面板水平居中、宽 `min(86vw, 1296px)` | desk 1280 时左边界 = (1280−1100)/2 = **90px**，与侧栏右边界 232px 有交集；rail 1024 时 = **72px**。**面板会压住侧栏的一部分**——这是居中模态的必然结果，不是缺陷：遮罩本来就盖住整屏、侧栏在面板打开时不可点（下一行），压住的是已压暗的不可交互区域。`LadderStock` 的侧栏没有 z-index（上一行），面板在 `z-overlay` 之上，视觉顺序正确 |
| 遮罩覆盖整屏（含侧栏） | 打开面板时侧栏不可点，Esc / 点遮罩即回。这是模态语义的代价，见 §5.4 |
| 面板**不跟随**入口位置 | 入口在 `actions` 槽，页头 `flex-wrap`：窄屏下入口可能换行到标题下方。面板固定在视口水平中央（`left-50% top-12vh`），不做「测入口矩形再定位」的逻辑——省一段 resize/scroll 测量代码；居中定位也因此与入口换行完全无关 |

### 5.4 模态还是非模态（规格做的取舍，需架构师知情）

**定为模态（modal dialog）**：`role="dialog"` + `aria-modal="true"` + 遮罩 + 滚动锁 + Esc 关闭 + 关闭后焦点回入口。

理由：QUOT-348 要求的两条焦点行为——「打开时焦点落到输入框」「关闭时焦点回到入口」——加 Esc 关闭、滚动锁、焦点陷阱，**正好是 `Overlay.tsx:32-77` 的 `useModalChrome` 已经在做的事**。复用它 = 零新增可访问性代码、行为与抽屉/确认框全站一致。做成非模态浮层反而要重写一套焦点与外点关闭逻辑，收益只有「遮罩下能看见侧栏可点」——而这不是搜索面板需要的。

⚠️ **交给架构师的一个动作**：`useModalChrome` 目前是 `Overlay.tsx` 的模块私有函数（未 export）。规格要求 **export 后复用**，不允许在面板里复制一份实现（同源原则，同 `SourceBadges.tsx` 文件头对「四份本地实现」的复盘）。

### 5.5 面板内结构

```
SearchPanel (dialog)
├─ 输入区  ── Input(C-03, leadingIcon=Search, h-control-lg) + 输入框内右端「清除 ✕」IconButton(仅非空时出现)
├─ 结果滚动区 (flex-1 min-h-0, overflow-auto, overscroll-contain；高度见 §5.2)
│   ├─ SearchGroup 个股        : 组头(sticky) + 行列表 + 组尾「查看全部」(可选)
│   ├─ SearchGroup 行业 / 题材 : 同上
│   └─ SearchGroup 资讯        : 同上
├─ 状态区（互斥占位：空态 / 无结果 / 整体失败）
└─ 底部提示条  ── ↑↓ 选择 · Enter 打开 · Esc 关闭   ← 左；右侧「快捷键 / 可搜什么」文字按钮
```

**清除 ✕ 的位置是有意定的，不是随手放的**：它挂在输入框**内**右端（`IconButton` 绝对定位在 `Input` 外层 `relative` 容器里，`right-1 top-1/2 -translate-y-1/2`，输入框 `pr-9` 让位），**不放在面板右上角**。面板右上角的 ✕ 在对话框惯例里是「关闭」（`Drawer` 的关闭件就在那个位置，`Overlay.tsx`），把「清除输入」放那儿会让鼠标用户按错、让键盘用户以为有一个不存在的关闭按钮。面板**没有独立的关闭按钮**，关闭路径是 Esc / 点遮罩 / 选中任一结果行（§7.1），鼠标用户点遮罩即可。DOM 顺序仍是 `input` 在前、`✕` 在后（§7.2 第 1 条：打开时焦点必须落输入框）。

组头 sticky：`sticky top-0 z-sticky bg-surface-2`（`--ldst-z-sticky: 10`，`bg-surface-2` 的 usage 正是「表头/次级容器」）——三组同屏滚动时，用户始终知道当前行属于哪一组，这是分组列表的基本可读性前提（尼尔森 #6 识别而非回忆）。

⚠️ 清除按钮只有一枚：`type="search"`（§7.2 第 2 条）在 Chromium / WebKit 下会自带原生 ✕，与自定义 `IconButton` 重复。面板输入框必须加 `appearance-none`（Tailwind 的 `appearance-none` 只重置表单控件外观，**不含** `::-webkit-search-cancel-button`），因此需要在 `Field.tsx` 的 `CONTROL` 或面板样式里显式写一条：`input[type="search"]::-webkit-search-cancel-button { -webkit-appearance: none; display: none }`。原型已按此实现，视觉稿里可见的 ✕ 只有自定义那一枚。

## 6. 结果分组

### 6.1 分组顺序与显隐

| 规则 | 内容 |
|---|---|
| 顺序**恒定** | 个股 → 行业 / 题材 → 资讯（= `SEARCH_GROUP_ORDER`，`searchCore.ts:37`），不随输入类型调换，不随得分调换 |
| 显隐 | **有命中才出组头**；命中 0 条的组整组不渲染（不出现「个股（0）」这种空组头）。三组全 0 → 走 §8.5 无结果态，面板不出空白块 |
| 不做意图分类 | 不按「看起来像代码就只查个股」裁掉请求：分类错了就是「搜不到」，而搜不到的代价远高于多两路请求。三组恒定请求（受 §8.3 最小输入长度门限约束），由「显隐」规则收拾噪音 |
| 计数 | 组头右侧 `ldst-num` 显示**该组本轮返回条数**（如 `个股 · 5`）。v1.1：`runSearch` 的 `hits` 已 `slice(0, hitLimit)` 且**不回传截断前总数**（§9.4），所以第一期不承诺「· 12」这种截断前计数；要做需要 §14-A5 那条小改 |

### 6.2 各组上限

| 组 | 面板内最多行 | 组尾出口 |
|---|---|---|
| 个股 | 5 | 命中 >5 时出「在结果中继续找：更多个股用资讯雷达按代码检索」——**第一期不做分页**，见 §14-B2 |
| 行业 / 题材 | 4（行业至多 3 + 题材至多 3，去重后 ≤4） | 「看全部行业 →」跳 `/industry` |
| 资讯 | 4 | 「查看全部 N 条 →」跳 `/news?q=<kw>` |

行数核算：5×38 + 3×32(组头) = 214；行业组 +148；资讯组 4×47 = 188。满命中总高 ≈ **550px**。v1.3 起结果区是 flex 剩余高度（900 高视口下 ≈ 560px），满命中基本一屏放下；窗口更矮时才会滚动，因此 §7.3 的「高亮行自动进视野」仍然不是可选项。

### 6.3 排序规则（可直接翻译成比较函数）

**归一化（三组共用，先归一再比）**：`trim` → 全角转半角 → 小写 → 去掉空格与 `-`。数字与中文不参与大小写折叠。

| 组 | 匹配与排序（前一条不满足才落到下一条） |
|---|---|
| 个股 | ① 代码全等 ② 代码前缀 ③ 名称全等 ④ 名称前缀 ⑤ 名称包含；同级内：**当日已在命中域内出现信号的（涨停池 / 盯盘 / 候选池）优先**；再按**代码升序**（稳定，永不随请求变化） |
| 行业 / 题材 | ① 名称全等 ② 名称前缀 ③ 名称包含；行业恒在题材前；行业同级按 `constituent_count` 降序（`null` 垫底，按 `industry.ts:88` 的口径 `null` 是「没有这个数」不是 0，**不得补 0 参与比较**）；题材同级按 `limit_up_count` 降序，`strength === null` 的排在后面（`DEC-031`：不补 0、不用板块涨跌幅代替） |
| 资讯 | ① 标题命中 ② 摘要命中 ③ `related_codes` 命中（仅当输入是 6 位数字）；同级按 `published_at` 倒序，`null` 垫底（`radar.ts:112` 该字段可空） |

**去重**：个股按 `code` 去重；行业与题材**同名只出一条**，类型标签取 `SearchTag` 四值之一，同名双源命中时记 `行业`（跳转本就是 `/industry/:name`，标签跟着落点走）——`SearchTag` 里没有「行业·题材」这一档，**不许自造第六个值**；资讯按 `content_hash` 去重（`radar.ts:115` 已有该字段）。

### 6.4 单条命中项展示哪些字段

| 组 | 左（等宽列） | 主文字 | 类型标签 | 右（辅助事实） | 数据字段 |
|---|---|---|---|---|---|
| 个股 | 代码 `ldst-num`（`w-16` 固定，与表格代码列同基线） | 名称 | `个股` | 涨跌幅 `SignedNumber`（红涨绿跌），**无数据时显 `—`** | 现有候选：`/api/ladder` 行含 `code`/`name`；`/api/watch/snapshot` 的 `name` **恒为 null**（`api.ts:459-466` 注释明确），所以名称来源必须走 §9.2 的索引，不能拿 watch 行当名称源 |
| 行业 | `—` | 行业名 | `行业` | `成分 N`（`constituent_count`）+ 涨跌幅若有 | `IndustrySummaryRow.name / constituent_count`（`api/industry.ts:83-97`） |
| 题材 | `—` | 题材名 | `题材` | `涨停 N`（`limit_up_count`）+ 龙头 `leader` | `MarketThemeRow.theme / limit_up_count / leader / strength`（`api.ts:554-562`） |
| 资讯 | `—` | 标题（单行 truncate） | 来源 `source` 文本（**不是徽标**） | 第二行元信息：`published_at`（`MM-DD HH:mm`）+ `related_codes.length` 若 >0 写「关联 2 只」 | `NewsItem.title / source / published_at / related_codes`（`api/radar.ts:107-119`） |

**字段可见性硬规则**（避免「字段缺失时留白」）：任何数值字段 `null` → 显示 `—` 并保留占位宽度，**不省略整列**、不写 0。这条来自 §6.3 的「不补 0」与 `UI-SPEC §8` 数字规范。

### 6.5 行的解剖与 class（实现可直接抄）

```
行（option）：flex items-center gap-2 rounded px-2 min-h-row cursor-pointer
  ├─ 选中条：border-l-2，高亮时 border-accent，否则 border-transparent（左侧 2px，不动行宽）
  ├─ 代码：span.ldst-num text-body text-text-secondary w-16 shrink-0
  ├─ 名称：span.text-body.text-text truncate            ← min-w-0 必须有，否则不截断
  ├─ 标签：Badge tone="flat"（h-5 max-w-full rounded-xs px-2 text-caption font-medium）
  ├─ 辅助：ml-auto shrink-0 .ldst-num text-caption text-text-secondary
  └─ 箭头：ChevronRight size-3.5 text-text-muted，仅 hover / 高亮时出现
```

| 状态 | 行变化 |
|---|---|
| 默认 | `bg-transparent`，名称 `text-text`、代码 `text-text-secondary` |
| hover | `bg-surface-3`（该令牌 usage 原文＝「悬停 / 选中行底色」，`tokens.css:19`） |
| 高亮（↑/↓） | `bg-surface-3` + 左侧 `border-accent` 条 + `aria-selected="true"`。与 hover 同底色，靠左侧条区分「键盘选中的是哪一条」 |
| 按下 | `active:translate-y-px` 不做（行是列表项不是按钮），改为底色 `bg-surface-3` 不变 + 立即跳转 |

**资讯行是两行结构**，高度不套 `h-row`：标题 14px×1.5 = 21 + 元信息 12px×1.5 = 18 + `py-1`×2 = 8 → **47px**，故用 `min-h-row py-1 flex-col items-start justify-center`（超出 38px 的部分由内容撑开，实测 47px）。这就是 §6.2 行数核算里资讯组按 47 计的原因。

### 6.6 高亮与 hover 同时存在时

以高亮为准（键盘意图 > 鼠标悬停意图）。鼠标移入某行时**同步移动高亮**（hover 即高亮），避免「高亮在第 1 行、鼠标在第 4 行、Enter 打开第 1 行」这种误解；移出结果区则高亮保留在最后一次的索引上（不清零）。

### 6.7 来源标注（唯一允许的溯源形态）

| 位置 | 内容 | 规则 |
|---|---|---|
| **每组组头右侧** | **一枚** `source_status` 徽标（数据完整 / 已降级 / 部分缺失 / 未取到，`UI-SPEC §5.2` 的 `SOURCE_STATUS_LABEL`），取值由 `GroupStatus` 映射（§8.8 表）。**v1.1 起不出 `freshness` 徽标**：面板一次打 6 个源（§9.4），没有「这一组的单一新鲜度」这种东西，新鲜度属于跳转后的目标屏 | **组级一枚，不给每行加徽标**。per-row 徽标只有行内数据时才成立；12 枚徽标会淹掉内容 |
| 输入区右端 | **不放**任何整局面板级「健康度」 | `UI-SPEC §5.3`：数据新 ≠ 数据全，是两条独立事实，**禁止合并成一枚总评级** |
| 行内 | 资讯行的 `source`（如「东方财富」）是**数据字段**，用 `text-caption text-text-muted` 文本呈现，不是 `Badge`，不是水印 | §5.1/§5.2 的徽标只表达新鲜度与完整性 |
| 面板任何位置 | ❌ **不得**出现 `DemoStateBadge`、「演示数据 · ?demo=1」、「演示态：…」 | QUOT-348 硬约束：视觉稿与实现都不要演示水印 |

实现要求：组级徽标用 `SourceBadges.tsx` 里**已导出的 `SourceStatusBadge`**（`:159`，入参 `status / detail / className`），**不要**用组合版 `SourceBadges`（`:192`）——后者在 `freshness === null` 时仍会渲染一枚「后端未提供 freshness」的占位徽标（`:138-140`），面板不需要它。**标签表、图形符号、严重度排序只允许存在于 `SourceBadges.tsx` 内**，面板不得自带第三份 `SOURCE_STATUS_LABEL`（该文件头部注释记录的正是四份本地实现导致的错映射事故）。目标屏（`/research`、`/industry` 等单源屏）继续用组合版两枚徽标，那里 freshness 有真值。

## 7. 键盘与可访问性

### 7.1 键位表

| 键 | 作用域 | 行为 | 备注 |
|---|---|---|---|
| `/` | 全局（未在输入态时） | 打开面板，焦点进输入框 | 焦点在 `input/textarea/select/[contenteditable]` 或面板已开时不劫持 |
| `Enter` / `Space` | 入口 | 打开面板 | `<button>` 原生 |
| `Esc` | 面板 | 关闭，焦点回入口 | `useModalChrome` 已实现（`Overlay.tsx:45-48`）。**输入法组合期间（`compositionstart` 未结束）Esc 不关闭面板**：中文输入法用 Esc 取消候选词，抢走它会让用户以为面板吞掉了按键 |
| `Enter` | 输入框（有命中） | 打开**当前高亮行**的目标；无高亮时按第一项 | 面板打开且已有结果时默认高亮第 1 行（§7.3） |
| `Enter` | 输入框（无命中） | 执行无结果态的出口：`/news?q=<kw>` | 出口可见（§8.5 底部提示行写明），不是隐藏动作 |
| `Enter` | 输入框（输入不足） | 不请求、不跳转，把「继续输入」提示 `role="status"` 播报出来 | 避免一个字符触发全表扫描后界面像卡住 |
| `↑` / `↓` | 输入框 | 在高亮序列中前后移动；**到头即停，不循环** | 不循环：循环后「第 1 行」的落点每次不同，比停住更难预期 |
| `←` / `→` / `Home` / `End` | 输入框 | **不劫持**，留给光标移动 | 与 `Tabs.tsx:34-37`、`Segmented.tsx:48-51`、`PageNav.tsx:83-86` 的 ←/→ + Home/End 惯例**有意不同**：那些组件里没有文本输入。在搜索框里劫持方向键会让用户无法编辑关键词，是比惯例不一致更严重的可用性缺陷 |
| `Tab` / `Shift+Tab` | 面板内任意可聚焦件 | **在面板内循环，不关闭**（`useModalChrome` 的 Tab 陷阱原样生效，不改） | 面板是 `aria-modal="true"` 对话框，Tab 跳出模态 = 焦点跑到遮罩后面的页面上，是 WCAG 2.4.3 的焦点丢失。可 Tab 件按 DOM 顺序固定为：输入框 → 清除 ✕（仅非空时存在）→ 各组「看全部行业 / 查看全部」出口链接 → 底部「可搜什么？」，**共 ≤5 个**；结果行 `role="option"` + `tabIndex={-1}` 不进序列，靠 ↑/↓ 走。所以 Tab 循环不等于键盘地狱；把模态去掉才是问题 |
| `Backspace`（输入框为空） | 输入框 | 清除上一条最近搜索；无记录时不关闭面板 | 与 macOS Spotlight 习惯一致；作为可选项，实现可先不做，见 §14-C1 |
| `⌘/Ctrl+K` | — | **不绑定** | 仓内无任何既有快捷键体系，两条（`/` 与 `Esc`）够本任务用，不做扩展 |

### 7.2 焦点管理清单（验收可直接逐条打勾）

1. 打开：焦点落输入框（`useModalChrome` 的 `setTimeout(…, 0)` 会把焦点给面板内**第一个可聚焦件**，因此**输入框必须是面板 DOM 顺序里的第一个可聚焦元素**——清除按钮 ✕ 写在输入框之后）。
2. 输入框需要 `type="search"`、`inputMode="search"`、`autoComplete="off"`、`spellCheck={false}`、`aria-label="搜索个股、行业题材或资讯"`（占位文案不是可编程标签）。
3. 面板：`role="dialog" aria-modal="true" aria-label="聚合搜索"`；输入框 `role="combobox" aria-expanded` + `aria-controls=<listbox id>`；结果区 `role="listbox" aria-label="搜索结果"`；行 `role="option" aria-selected` + `id=<稳定 id>`；输入框 `aria-activedescendant=<高亮行 id>`。
4. 结果变化（新一轮返回/输入变化）时高亮**重置到第一项**，但 `activeDescendant` 的 id 必须指向当前真实存在的行，防止读屏播报幽灵行。
5. 关闭（Esc / 点遮罩 / 选中任一结果行或组出口——**只有这三条**，没有关闭按钮）：焦点回入口——`useModalChrome` 的 `previous?.focus()`（`Overlay.tsx:36, 75`）天然满足；**不要用 `document.querySelector` 自己找回入口**。点清除 ✕ 不是关闭路径：它只把输入清空并回到 §8.2 空态，焦点必须仍在输入框。
6. 打开期间 `document.body.style.overflow = 'hidden'` 滚动锁，关闭时**恢复原值**（同一函数已做，`Overlay.tsx:37, 74`）。
7. 页面 `#main` 的「跳到主内容」直达链接（`Layout.tsx:240-243`）在面板打开时不可达——这是模态的固有取舍，因为 Tab 走位与 Esc 已足够；规格不额外加「跳过面板」焦点点。

### 7.3 高亮行自动进视野

`↑/↓` 移动后对高亮行执行 `scrollIntoView({ block: 'nearest' })`；`block: 'nearest'` 而非 `center`：`center` 会在每次按键时把整段结果推来推去，用户读不到稳定的上下文。sticky 组头会盖住顶部 32px，故滚动容器需 `scroll-padding-top`（结构实现细节，不影响视觉规格；不满足则高亮行会被组头压住一半）。

### 7.4 焦点环与对比度（实测）

焦点环真源：`--ldst-focus-ring: 0 0 0 2px var(--ldst-bg), 0 0 0 4px #6ba1ff`（dark）/ `… 2px var(--ldst-surface), 0 0 0 4px #1a5fd0`（light）（`tokens.css:63`、`tokens.json focus.ring`）。

按 `tokens.json meta.contrastPolicy`：正文（≥14px）与 12px 辅助文字对**实际底色** ≥4.5:1，交互件边框与图标 ≥3:1。本面板涉及的组合，**按 WCAG 相对亮度公式实测**（底色取面板实际用的 `surface` 与「悬停/选中行」的 `surface-3`；soft 类底色是先与其父底混合后的实际色值再算）：

| 用途 | 前景 / 底色 | dark 实测 | light 实测 | 要求 | 判定 |
|---|---|---|---|---|---|
| 行主文字 | `text` / `surface` | 14.74 | 17.49 | 4.5 | ✅ |
| 行主文字（高亮行） | `text` / `surface-3` | 12.49 | 15.43 | 4.5 | ✅ |
| 组头、次级说明 | `text-secondary` / `surface` | 9.29 | 8.17 | 4.5 | ✅ |
| 占位符、时间戳（12px） | `text-muted` / `surface` | 5.82 | 5.43 | 4.5 | ✅ |
| 占位符、时间戳（高亮行上） | `text-muted` / `surface-3` | 4.93 | 4.79 | 4.5 | ✅ |
| 输入框占位符 | `text-muted` / `sunken` | 6.48 | 4.65 | 4.5 | ✅ |
| 焦点环外圈（键盘可见性） | `#6ba1ff` / `surface` | 6.80 | 5.85 | 3.0 | ✅ |
| 高亮行左侧选中条 | `accent` / `surface` | 5.46 | 5.85 | 3.0 | ✅ |
| 面板描边 / 输入框描边 | `border-strong` / `surface` | 3.27 | 3.19 | 3.0 | ✅（勉强，见下） |
| 涨/跌数字 | `up`,`down` / `surface` | 5.77 / 7.33 | 5.66 / 5.42 | 4.5 | ✅ |
| `Badge tone="flat"`（类型标签） | `text-secondary` / `surface` + 描边 | 9.29 | 8.17 | 4.5 | ✅ |
| `Badge tone="info"` soft 底 | `info` / 混合后 `#1b2637`(d) · `#e8effa`(l) | 6.14 | 4.51 | 4.5 | ✅（light 贴边） |
| `Badge tone="warn"` soft 底 | `warn` / 混合后 `#343022` · `#efe8db` | 5.32 | **4.28** | 4.5 | ❌ light |
| `Badge tone="danger"` soft 底 | `danger` / 混合后 `#362726` · `#f4e4de` | 5.74 | **4.22** | 4.5 | ❌ light |
| `Badge tone="ok"` soft 底 | `ok` / 混合后 `#1b3034` · `#ddeceb` | 5.57 | **4.29** | 4.5 | ❌ light |
| `Badge tone="neutral"` flat-soft 底 | `flat` / 混合后 `#252b32` · `#ecedef` | 5.76 | **4.45** | 4.5 | ❌ light（差 0.05） |

**两处由此得出的规格结论**（不是我加严，是数据在那儿）：

1. **类型标签一律用 `Badge tone="flat"`**（`border border-border text-text-secondary` 的描边版，`Badge.tsx` TONES 表里 `flat` 正是这一档），**不用 `tone="neutral"`**（`bg-flat-soft` 混合后 light 主题 4.45:1，12px 文字不达 4.5）。类型标签是识别信息不是状态信息，描边版语义上也更贴。
2. `SourceBadges` 的 `warn/danger/ok/info` soft 徽标在 **light 主题**下 12px 文字为 4.2–4.5:1，**是既有组件的历史偏差，本任务不修、也不许绕**（绕=自造第四份徽标，正是 `SourceBadges.tsx` 文件头明令禁止的事故）。已在 §14-D3 作为**独立遗留项**登记给架构师/设计复核：修法是 light 侧把四个 `*-soft` 混合底压深一档或文字提重一档，属设计系统级变更（我的权限里「设计系统调整」要上报）。面板内除组头徽标外不再新增 soft 底色，避免扩大这个问题的面积。

**暗色下焦点环是否「看得清」的专项回答**（需求点名要求）：dark 主题下焦点环**内圈**取 `var(--ldst-bg)`（#0e1116），而面板底是 `surface`（#151a21），内圈会在面板上读成一条更暗的 2px 缝——它仍然可见（作为分隔），但**不能把焦点环当成「哪一行被选中」的唯一线索**。因此规格规定：行的高亮状态由 **`bg-surface-3` 底色 + 左侧 2px `accent` 条 + `aria-selected`** 三重表达（色 / 形状 / 语义各占一路，符合 `UI-SPEC §9`「状态不只靠颜色」），焦点环只用于「键盘焦点在输入框」这一件事。

### 7.5 读屏播报契约

| 时机 | 播报 | 实现 |
|---|---|---|
| 面板打开 | 「聚合搜索，对话框」+ 输入框标签 | `role="dialog" aria-modal` + `aria-label` |
| 每次输入后的结果变化 | 不逐次播报整段结果（会打断打字）。改为**一次**状态播报：`aria-live="polite"` 区域写「找到 12 条：个股 5、行业 / 题材 3、资讯 4。第 1 条：600000 浦发银行，个股」 | `aria-live="polite"` + `aria-atomic="true"`，区域挂在输入框之后、结果区之前 |
| `↑/↓` | 「{代码} {名称}，{类型标签}，第 n / N 条」 | `aria-activedescendant` 由读屏自己念 option 内容，`aria-posinset` / `aria-setsize` 提供 n/N |
| 加载中 | 「正在加载搜索结果」 | `aria-busy="true"` 挂在 listbox + `.sr-only` 文案（同 `PageShell.tsx` 的 `ScreenSkeleton`：`role="status" aria-busy` + `<span class="sr-only">正在加载…</span>`） |
| 单组失败 | 「{组名}结果获取失败，其他结果仍可用」 | 该组状态行 `role="status"`（不是 `role="alert"`：三组里坏一组不是紧急中断） |
| 整体失败/离线 | 「本机服务未连接」+ 出口 | `role="alert"`，沿用 `PageShell.tsx OfflineBanner` 的既有文案口径（「先运行 `ldst run` 启动本机只读 API」） |
| 输入不足 | 「继续输入 {剩余} 个字符」 | `role="status"` |

## 8. 状态定义（本任务的核心交付）

### 8.1 状态机

```mermaid
title="搜索面板状态机（QUOT-348 §8）"
stateDir TB
[*] --> 入口默认
入口默认 --> 面板空态: 点击/Enter/斜杠
面板空态 --> 输入不足: 长度低于门限
输入不足 --> 请求中: 越过门限
面板空态 --> 请求中: 越过门限
请求中 --> 有结果: 至少一组非空
请求中 --> 无结果: 三组皆空且全部成功
请求中 --> 整体失败: 三路皆失败/离线
请求中 --> 部分失败: 一组失败另两组有值
有结果 --> 请求中: 输入变化(旧结果保留, 组头显旧徽标)
部分失败 --> 请求中: 输入变化或单组重试
无结果 --> 请求中: 输入变化
整体失败 --> 请求中: 重试全部
有结果 --> 面板空态: 清空输入
任意 --> 入口默认: Esc/遮罩/选中行跳转
```

**关键约束：任何状态都不许出现「只有转圈、没有文字」或空白区域。**骨架至少带 `.sr-only` 的「正在加载搜索结果」，空态/无结果态必带出口动作（`UI-SPEC §8` 空态要有出口）。

### 8.2 各态进入条件与可见内容

| 状态 | 进入条件 | 面板内容 | 可交互 | 退出 |
|---|---|---|---|--- |
| **空态（未输入）** | 面板打开且 `q === ''` | 两区：**最近搜索**（最多 8 条 chip，无记录时该区不出现）+ **可搜什么**（三行说明：6 位代码或名称 → 个股深挖；行业/题材名 → 行业详情；关键词 → 资讯雷达） | 行不可选（没有 option） | 输入越过门限 |
| **输入不足** | `0 < len < 门限` | 一行提示 `text-caption text-text-muted`：「继续输入 {N} 个字」；**不发请求** | ↑/↓ 无效 | 越过门限 / 清空 |
| **请求中** | 已发请求、任一组未回 | 未回的组用 `SkeletonRows`（`Feedback.tsx:183-195`，行高 `h-row` + 1px 分隔 + `ldst-skeleton` 扫光）；**已回的组立即渲染，不等齐** | ↑/↓ 在已回结果间可用 | 全部返回 / 失败 |
| **有结果** | 至少一组非空 | §6 分组 + §6.7 徽标 | 全部 | 输入变化 |
| **无结果** | 三组皆空**且都成功返回** | §8.5 文案 + 出口按钮 | Enter = 出口 | 输入变化 |
| **部分失败** | 一组失败、其余有值 | 失败组 → §8.6 降级行；其余组正常 | 单组重试 | 输入变化 |
| **整体失败** | 三路皆失败（含连不上） | §8.7 面板级提示（复用 `OfflineBanner` 文案口径） | 重试全部 | 重试 |
| **入口禁用** | 无 | **不存在这一态**：入口不依赖数据，服务没起来也要能点开面板，看 §8.7 的离线说明。禁用一个搜索入口是最坏的降级 | — | — |

### 8.3 请求门限与节流（写清数值，实现不再问）

| 项 | 规定 | 理由 |
|---|---|---|
| 门限 | 输入含**数字**：≥3 位才请求；纯**中文/字母**：≥2 字符才请求；混合串取「数字门限」 | 1 个汉字（如「机」）在生产数据上是全表包含扫描；6 位代码只要 3 位（`600`）就有意义 |
| 节流 | 停止输入 **250ms** 后发请求；新请求发出前 `AbortSignal` 取消上一轮 | 250ms 在中文输入法逐字确认节奏下不会连发 3 轮；`AbortSignal` 是既有 api 层的用法（`radar.ts:359` 的 `removePosition(code, signal?)`） |
| 结果竞态 | 只接受**最后一次**发出请求的响应（序号比较），过期响应丢弃但不清空已显示结果 | 避免「输入 机器人 → 打错删掉 → 慢响应回来覆盖」的结果闪烁 |
| 输入框本地过滤 | 已返回的组在输入变化时**不清空**（旧结果灰显 + 组头徽标不变），等新结果替换 | 清空→骨架→新结果的三段闪变在 250ms 内会让用户以为出错 |

### 8.4 加载中

- 组级：整组未回 → 该组出 3 行 `SkeletonRows`（`rows={3}`，`slots` 取 `['20%','34%','16%']` 现档）。
- **加载中的组头不出徽标**：信封还没回来就没有来源可标注，组头右侧留空（只有组名 + `搜索中…`）。不允许临时造「来源状态未知」这类第三种标签——§6.7 已禁止第三份 label map，`SourceBadges` 里没有「未知」这一档，硬加就是体系外颜色。
- 行级不逐个换骨架（一格里转圈 = 用户分不清哪个字段没到）。
- 骨架扫光是 `animation`（1.2s，`index.css:69-96`），**不计入 `check:ui` 规则 6 的过渡时长**（规则 6 只查 `transition` 与 `duration-*`）。
- 超 1.2s 未回：在面板底部提示条**左侧**出现一行 `text-caption text-text-muted`「本机接口响应较慢，Esc 可先关闭」——不是超时打断请求。规格**不**设自动超时中断：本机回环 API 的失败应由后端返回，不由前端猜（`UI-SPEC §4.2` 快照过期的同一口径）。

### 8.5 无结果态（文案已定稿，实现照抄）

```
今日数据里没有匹配「{kw}」
换个写法试试：
· 个股用 6 位代码（如 002463），名称只匹配当日涨停池、盯盘与候选池里的标的
· 行业 / 题材用完整名称（如「机器人」而非「机器」）
· 资讯关键词可在雷达里按日期窗口再找
[ 在资讯雷达中搜索「{kw}」 ]   ← 主操作（Button variant="secondary"）
```

- 用 `EmptyState`（`Feedback.tsx:75-90`）的非 compact 形态：`min-h-empty`（200px）+ `text-subtitle` 标题 + `max-w-measure`（52ch）描述；**不加 `icon`**（`[&>svg]:size-12` 的 48px 大图标在 v1.3 的 1238px 面板里过重，且无信息增益）。
- **措辞刻意用「今日数据里没有匹配」而不是「没有 X 这只股票」**：命中域是当日并集（§9.2），说「没有」是在撒谎，用户会以为代码输错了。
- 出口按钮 `variant="secondary"`（不是 primary：它是兜底路径，不该比正常结果更抢眼），Enter 绑定到它。

### 8.6 单组失败（**不许整块面板报错**）

失败组保留组头（组名 + 「未取到」），内容换成一行降级条：

```
个股  ✕ 结果没取到        [重试]
```

- 结构：`flex min-h-control items-center justify-between gap-2 rounded-xs border border-danger bg-danger-soft px-2 text-caption text-danger`（`--ldst-danger`/`--ldst-danger-soft`，`tokens.css:43-44`）；「重试」是 `Button variant="ghost" size="md"` 内的文字按钮 `text-caption`。
- 该组的 `source_status` 若后端回了 `degraded`/`partial`/`missing`，仍由组头 `SourceStatusBadge` 表达（§6.7），**不在降级行里重复**一遍标签。
- 其余两组**不受影响**：继续可 ↑/↓、Enter、跳转。
- `role="status"` + `.sr-only`「个股结果获取失败，其他结果仍可用」；**不是** `role="alert"`（三坏一是降级，不该打断读屏）。
- 失败原因文案只在 `title` 悬浮里给（`source_detail` 原文，如「同 peer 备工具未返回」），面板不铺技术细节。
- 高亮序列跳过失败组的行（失败的组没有 option），↑/↓ 直接跨到下一组第一行。

### 8.7 整体失败 / 离线

```
本机服务未连接
搜索要读当日涨停池、行业字典与资讯流，都来自本机只读 API。
先运行 ldst run 启动服务（默认 127.0.0.1:5950），再重试。
[ 重试全部 ]  [ 打开设置页看数据源 ]
```

- 复用 `OfflineBanner`/`Banner tone="warn"` 的既有文案口径（`PageShell.tsx:96-115`），**不新写一套离线说法**——两处「服务没起来」的解释不一样，是用户最常见的困惑源。
- 「打开设置页」跳 `/settings`，与首页待办卡「数据降级 → 看数据源」同一条路径（`HomeScreen.tsx:322-326`）。
- 面板**保持打开**（不自动关闭）：关闭会让用户以为是自己按错了；保持打开并给出重试，才能一次点通。

### 8.8 四态矩阵（逐态文案 + 视觉口径，对齐 `GroupStatus`）

需求点名的四态，加上离线一共五态。`GroupStatus` 只有 `ok|empty|partial|error|skipped` 五个值（`searchCore.ts:71`），面板的可见态**全部**由它 + `intent` + 请求 in-flight 三个变量推出来，不留「UI 自己判断」的空隙。

| 态 | 进入条件（数据层字段） | 面板可见 | **定稿文案** | 视觉口径（令牌 / class） |
|---|---|---|---|---|
| **空输入** | `normalizeQuery(x).empty === true` → **不调 `search()`**（`runSearch` 也会返回三组 `skipped`，`searchCore.ts:418-429`） | 输入框 placeholder + 最近搜索 chips + 「可以搜这三类」三条引导 + 底部键位条；**无组头、无骨架、无徽标** | 占位「搜索个股 / 行业 / 资讯」；引导标题「可以搜这三类」；三条：「1 6 位代码或名称 → 个股深挖」「2 行业 / 题材全名 → 行业详情」「3 资讯关键词 → 资讯雷达带词」；尾注「搜索结果来自本机服务的当日数据：涨停池、连板天梯、盯盘、候选池、行业字典、题材榜与资讯流。」 | `EmptyState` 紧凑档 `py-4`；chips = `Badge tone="flat"` + `X` `IconButton`；焦点环 `--ldst-focus-ring`；正文行宽 `--ldst-measure: 52ch` |
| **加载中** | `intent !== 'empty'` 且长度门限已过、该组未回 | 已回的组照常渲染；未回的组 = 组名 + `搜索中…` + 3 行骨架；**组头不出徽标**（§8.4） | 组内「搜索中…」；底部 `aria-live` 「正在加载搜索结果」；超 1.2s 追加「本机接口响应较慢，Esc 可先关闭」 | `SkeletonRows rows={3} slots={['20%','34%','16%']}`（`Feedback.tsx:157-195`）；扫光 1.2s 是 `animation`，不计入 `check:ui` 规则 6；listbox `aria-busy="true"` |
| **无结果** | 三组 `status` 全为 `empty`（`skipped` 不算命中也不算失败） | `EmptyState`：标题 + 3 条下一步 + 一枚次级出口；**面板不关、输入不清** | 标题「今日数据里没有匹配「{kw}」」；三条：「个股用 6 位代码（如 002463），名称只匹配当日涨停池、盯盘与候选池里的标的」「行业 / 题材用完整名称（「机器人」而不是「机器」）」「资讯关键词可在雷达里按日期窗口再找」；出口按钮「在资讯雷达中搜索「{kw}」」；尾注「没匹配 ≠ 不存在：本机只索引当日数据。」 | `min-h-empty py-6`、`[&>svg]:size-12`；出口 `Button variant="secondary" size="md"`；标题 `text-subtitle`，说明 `text-body text-text-secondary` |
| **源失败降级（单组）** | 该组 `status === 'error'`（`sources` 全挂，文案取 `group.error.title/detail`）**或** `partial`（部分源挂，逐源原因在 `sources[].error`） | `error`：保留组头 + 一行降级条 + 重试；`partial`：命中行照常渲染 + 一行降级条；**其余两组不受影响，仍可 ↑/↓ 与 Enter** | `error`：「{组名}结果没取到，其他结果仍可用」+「重试」；`partial`：「{组名}：{K} 个源里 {M} 个没取到，结果可能不全」+「重试」；技术原因只在 `title` 悬浮里给 `error.detail` 原文 | `flex min-h-control items-center justify-between gap-2 rounded-xs border border-danger bg-danger-soft px-2 text-caption text-danger`；组头徽标 `error`→`未取到`、`partial`→`部分缺失`（danger 橙红，同 `SOURCE_STATUS_LABEL`）；`role="status"` **不是** `alert`；重试 = `Button variant="ghost" size="md"` |
| **整体失败 / 离线** | 三组全 `error` 且 `error.code` 指向连接层（本机 5950 未起） | 状态区一枚 warn `Banner` + 「重试全部」+「打开设置页看数据源 →」；面板保持打开 | 沿用 `OfflineBanner` 措辞：「本机服务未连接」+「先运行 `ldst run` 启动本机只读 API（默认 127.0.0.1:5950），再重试。」 | `Banner tone="warn"` 全宽（`Feedback.tsx:21-63`）；**入口不禁用**（§8.1 表第一行）；`--ldst-warn` / `--ldst-warn-soft` |

**`GroupStatus` → 组头渲染的完整映射**（`empty` / `skipped` 都不出组头，是同一件事的两种来路：前者打了源没命中，后者该意图下根本没有可打的源——例如代码意图不打 `themes`，见 `intents: ['name','keyword']`）：

| `status` | 出组头 | 组名右侧计数 | 状态徽标 | 组体 |
|---|---|---|---|---|
| `ok` | 是 | `· {hits.length}` | `数据完整`（ok 绿） | 命中行 |
| `partial` | 是 | `· {hits.length}` | `部分缺失`（danger 橙红） | 命中行 + 降级条 |
| `error` | 是 | 不显计数 | `未取到`（danger 橙红） | 仅降级条 |
| `empty` | **否** | — | — | 整组不渲染 |
| `skipped` | **否** | — | — | 整组不渲染 |

## 9. 数据契约（面板需要的东西，按现状盘点）

### 9.1 现有可用接口（本期能直接用的）

| 组 | 接口 | 前端封装 | 能给什么 |
|---|---|---|---|
| 行业 | `GET /api/industry` | `api/industry.ts:967` | `IndustrySummaryRow.name / l1_code / constituent_count / data_source_status / as_of`（`:83-97`）——**行业名索引，够用** |
| 题材 | `GET /api/market/theme?date=` | `api.ts:838` | `MarketThemeRow.theme / strength / limit_up_count / leader`（`:554-562`）——**题材名索引，够用但只有当日榜**（未上榜题材搜不到） |
| 资讯 | `GET /api/news?date=&until=&source=&code=&limit=` | `api/radar.ts:926-938` | `NewsItem.title / summary / source / published_at / related_codes / content_hash`（`:107-119`）；**没有关键词参数** → 见 §9.3 |
| 个股（部分） | `GET /api/ladder?date=` | `api.ts:822` | 行含 `code` + `name` → 当日涨停池的名称↔代码可用 |
| 个股（部分） | `GET /api/limit-pool?date=` | `api/limitPool.ts:198` | 同上，另一批 code/name |
| 个股（❌） | `GET /api/watch/snapshot` | `api.ts:826` | `name` **恒 null**（`api.ts:460-461` 注释：状态文件里没有名称来源）→ **不能当名称源** |

### 9.2 缺的那一块：个股代码↔名称索引

**结论：全仓没有任何按关键词检索标的的端点**（后端 `src/ladderstock/web/` 下 `server.py` / `radar.py` / `industry.py` / `research.py` 的路由清单里没有 search / query / lookup / suggest）。

第一期规格**不假装全市场可搜**，明确把命中域写成「当日并集」：

```
universe(交易日) = 涨停池 ∪ 连板天梯 ∪ 盯盘快照(补名称) ∪ 候选池 ∪ 资讯 related_codes
```

并把它同时反映在文案里（§8.5「名称只匹配当日涨停池、盯盘与候选池里的标的」）。**搜不到的标的是「今天没有信号的标的」，不是「不存在的标的」**——这条如果不在文案里交代，用户会怀疑代码打错了，是这个小功能最常见的差评来源。

### 9.3 需要后端新增的（最小面）—— **v1.1：本节已被 QUOT-356 的客户端聚合取代，保留作背景**

QUOT-356 的落法是「不新增后端端点，前端 `searchCore.runSearch` 并发打 6 个现有只读接口」（`searchCore.ts:8-10` 硬口径 #1），与本节提案相反。取舍合理：本期取数缺口（全市场名称检索）不是靠一个聚合端点能补上的，而客户端聚合当天就能跑通、单源失败隔离也已经在数据层做完。**下面这段留作后手**：若将来要做全市场检索或服务端排序，再按此形状加端点，届时 §9.4 的对应表整段作废、UI 只换 `SearchSource` 注入。

**一个只读聚合端点**（命名建议，最终以架构师定为准）：

```
GET /api/search?q=<kw>&limit=6
→ 200 {
  q: string,
  universe: { trade_date: string, covered: string[] },   // 供 §8.5 文案说明覆盖范围
  groups: {
    stocks:    Envelope<{ code, name, change_pct|null, source_hint }[]>,
    industries:Envelope<{ name, l1_code, constituent_count|null }[]>,
    themes:    Envelope<{ theme, limit_up_count|null, leader|null }[]>,
    news:      Envelope<NewsItem[]>
  }
}
```

硬性要求（不满足则 §6.7 / §8.6 的实现不了）：

1. **每组自带信封字段**：`freshness` / `source_status` / `source_detail` / `data_as_of`——面板的组头徽标直接吃 `SourceBadgeMeta`，不是整面板一个总状态。缺一组就显「未知」（QUOT-331）。
2. **单组失败不影响整体 200**：某路挂了返回该组 `source_status: "missing"` + `items: []`，**不 500**。§8.6 的降级显示依赖这条。
3. 资讯检索走**参数**（后端）而不是把当日全量拉到前端过滤：`GET /api/news` 现在只按 `date/until/source/code/limit` 过滤，请补 **`?q=`**（后端对 title/summary 做子串匹配），前端 §3.4 的 `/news?q=` 与 §9.1 的资讯组共用同一语义。参数缺失时前端**退化**为「资讯组用已加载的窗口内容做本地 `title/summary` 子串匹配」，并在组头徽标旁加一行 `text-caption text-text-muted`「按已加载窗口匹配」——即：功能可用但如实标注范围，不静默冒充服务端检索。
4. `limit` 由后端截断（个股 5 / 行业+题材 4 / 资讯 4），前端不再截一次；前端**仍**做排序（不依赖后端顺序，同 `api.ts:563` 注释的既有态度）。

### 9.4 与已落地的数据层（`frontend/src/lib/searchCore.ts`，QUOT-356）的对应表

**口径总则：命名与状态枚举以数据层为准，交互与文案以本规格为准。** 数据层已经把「谁是真源」这件事做成了类型（组顺序、组名、标签、状态、路由都在 `searchCore` / `searchRoutes` 里），UI 再硬编码一份就是第二个真源，正是 `SourceBadges.tsx` 文件头复盘的「四份本地实现」错误的重演。

| 规格里的口径 | 数据层导出 / 行为 | 以哪边为准 |
|---|---|---|
| 三组固定顺序 | `SEARCH_GROUP_ORDER = ['stocks','themes','news']`（`:37`），`runSearch` 按它 `map` 出 `groups`（`:471`） | **数据层**。UI 直接渲染 `result.groups` 的顺序，不自己排 |
| 组名展示文本 | `GROUP_LABELS`：`个股` / `行业 / 题材` / `资讯`（`:39-43`），随 `group.label` 下发 | **数据层**。UI 渲染 `group.label`，不写字面量；规格全文已改（v1.1） |
| 每条命中的类型标签 | `SearchTag = '个股'\|'行业'\|'题材'\|'资讯'`（`:34`） | **一致**。规格原稿的「行业·题材」标签是越界值，已改为同名双源记 `行业`（§6.6） |
| 组的可见状态 | `GroupStatus = ok\|empty\|partial\|error\|skipped`（`:71`）+ `groupStatus()`（`:349-357`） | **数据层**。§8.8 的映射表就是它 |
| 组头来源徽标 | 数据层**不给** `freshness` / `source_status`，给逐源 `SourceStat { id, label, outcome, hits, elapsedMs, error }`（`:76-83`） | **改规格**：`source_status` 由 `GroupStatus` 映射（§8.8 表），**面板组头不出 `freshness` 徽标**——面板一次跨 6 个源，没有单一新鲜度可标；新鲜度属于跳转后的目标屏。`SourceBadges` 在面板里只出现「状态」这一枚 |
| 每组上限与计数口径 | `hitLimit` 默认 8（`:413`），`hits` 已 `slice(0, limit)`（`:385`），**没有截断前总数** | **数据层 + 一条小改需求**。第一期 UI 传 `hitLimit: 5`，组头计数显示「本轮返回条数」；§6.1 原写的「截断前总数」在拿不到 `totalHits` 前**不承诺**（§14-A5）。行业 / 题材与资讯的 4 条上限先由 UI 截 |
| 资讯跳转参数 | `routeForHit`：代码意图 → `/news?code=`，其余 → `/news?q=<query.text>`（`:327-333`、`searchRoutes.ts:44-49`） | **数据层**。§3.2 表已补：代码意图走 `?code=`，它是 `NewsScreen` 今天唯一真能消费的参数 |
| 竞态：只接受最后一次响应 | `createSearchRunner` 自动 abort 上一轮，晚到的旧结果标 `superseded`（`:517-531`） | **数据层**。UI 只写 `if (result.superseded) return`，不再自己比序号；§8.3 的「序号比较」按此实现 |
| 关闭时取消在飞请求 | `SearchRunner.cancel()`（`:507`） | **新增进 §7.2**：三条关闭路径（Esc / 遮罩 / 选中行）都要先 `cancel()` |
| 最小输入门限（数字 ≥3、其余 ≥2 字）+ 250ms 防抖 | `runSearch` 只拦空串，无长度门限 | **规格**。门限属于 UI 侧的「不打无意义请求」，数据层有意不做（它还要服务 `/news` 等复用场景） |
| 「不许说标的不存在」 | `code_direct` 源：代码格式即出一跳，`verified:false` + `name:null`，注释要求 UI 标「未核实」（`:177-181`） | **数据层更强，规格补一行**（§6.5）：`verified === false` 的行名称位写 `未接入`（`text-text-muted`），副行写「代码格式，未经数据源核实」，仍可 Enter 跳 `/research/:code`；`name === null` 一律**不许拿代码顶替名称**（`:58` 注释） |
| 单源失败不整块报错 | 硬口径 #2 + 每组独立 `sources[].error`（`:11-12`） | **一致** |
| 空结果返回空结构不返回 null | 硬口径 #3（`:13-14`） | **一致**，§8.5 文案照旧 |
| 错误文案真源 | `toSearchError` 复用 `describeRadarError`（`:340-345`） | **数据层**。面板降级条直接显 `error.title` / `error.detail`，**不再自造**「{组名}结果没取到」——§8.8 的文案以 `error.title` 为准，规格文案作为 `error.title` 缺省时的兜底 |

**数据层实际注册的 6 个源**（`httpSearchSources`，`:171-322`；这就是 §9.1 盘点的落地版，也是「来源标注」的真值）：

| `group` | `id` | `label`（可直接用于降级条） | 打的意图 | 底层接口 |
|---|---|---|---|---|
| `stocks` | `code_direct` | 代码直达 | `code` | 无请求（纯格式推出，`verified:false`） |
| `stocks` | `limit_ladder` | 连板天梯 | 全部 | `api.ladder(date)` |
| `stocks` | `candidate_pool` | 候选池 | 全部 | 候选池只读接口 |
| `themes` | `industry_list` | 行业清单 | `name` / `keyword` | `httpIndustrySource` |
| `themes` | `market_theme` | 题材强度 | `name` / `keyword` | `api.marketTheme(date)` |
| `news` | `news_stream` | 资讯雷达 | 全部 | `httpRadarSource.news({date,limit,codes})` |

`partial` 的降级条因此可以说得很具体：「行业 / 题材：2 个源里 1 个没取到（题材强度）」——`sources[]` 里有 `label`，不需要新的文案机制。

## 10. 组件与令牌使用清单

### 10.1 复用（全部现成，无新 UI 库）

| 用途 | 复用对象 | 出处 |
|---|---|---|
| 面板输入框 | `Input`（`leadingIcon` 分支） | `ui/Field.tsx:54-89` |
| 焦点/遮罩/滚动锁/Esc/焦点回归 | `useModalChrome`（**需 export**，§5.4） | `ui/Overlay.tsx:32-77` |
| 起止两帧动画 | `useEnter` | `ui/useEnter.ts` |
| 类型标签 | `Badge tone="flat"` | `ui/Badge.tsx:9-17` |
| 涨跌幅数字 | `SignedNumber` | `ui/Badge.tsx:75+`（§7.1 颜色只作用数字与箭头） |
| 组头溯源 | `SourceStatusBadge`（单枚，不用组合版 `SourceBadges`） | `ui/SourceBadges.tsx:159` |
| 骨架行 | `SkeletonRows` / `Skeleton` | `ui/Feedback.tsx:157-195` |
| 无结果/空态 | `EmptyState` | `ui/Feedback.tsx:75-90` |
| 降级/离线提示 | `Banner`（`tone="warn"`） | `ui/Feedback.tsx:21-63` |
| 入口挂载 | `PageHeader actions` | `PageShell.tsx:26-40` |
| 清除按钮 | `IconButton` | `ui/Button.tsx:95+` |
| 图标 | `lucide-react`：`Search` `X` `CornerDownLeft` `ChevronRight` `RotateCcw` `WifiOff` | 仓内唯一图标库，不新增 |

### 10.2 新增（**只有呈现件，没有新令牌**）

| 新组件 | 建议组件编号 | 位置（**归前端架构师定**） | 说明 |
|---|---|---|---|
| `HomeSearchTrigger` | **C-21** | `components/search/` 或 `components/ui/SearchTrigger.tsx` | 纯展示 + 一个 `onOpen`；不请求 |
| `SearchPanel` | **C-22** | 同目录 | `role=dialog` + 输入 + 分组 + 状态区；只吃 props 与 `onSelect(href)` |
| `SearchGroup` / `SearchOption` | C-22 的内部件 | 同目录 | 不对外导出，避免变成第五份徽标 |

组件编号 C-21/C-22 是下一个空闲档（`UI-SPEC §6.1` 已用到 C-20），**本 PR 已把它们登记进 `docs/UI-SPEC.md §6.1` / §3.20**——这是文档动作，不是令牌动作。`DESIGN-SPEC §5` 在 gitignore 的 `docs/tasks/` 下，仓库内不可维护，不再作为登记目标。

**令牌结论：颜色 / 圆角 / 阴影 / 字号 / 间距 / 层级 / 时长全部零新增。** 出现的「新数值」只有两个宽度（256px / 176px），它们都落在 Tailwind **内建档位**（`w-64` / `w-44`）上（v1.3 起面板顶偏移与宽度改为内联 CSS 变量，见 §5.2），不是方括号任意值，因此不触发 `scripts/check-ui-consistency.mjs` 规则 3，也不需要进 `tailwind.config.ts`。若架构师希望把它们也登记成结构尺寸（同 `w-drawer: 480px` 的做法，`tailwind.config.ts:120-128` 有先例），建议 `width['search-entry'] = 256px`（面板宽度 v1.3 起走 `min(86vw, var(--ldst-table-max))`，不再需要结构宽度），并在注释里写明是**结构尺寸而非视觉令牌**——两种做法规格都接受，取其一即可（§14-B1）。

## 11. 视觉稿清单（仓库落盘路径）

原型：`docs/specs/home-search-prototype.html`（内联 `tokens.css` 变量真值，**支持 dark/light 切换、可点可 Tab**，用 ↑/↓ 与 Enter 走一遍即可验收 §7）。截图由同一原型经 Chrome 无头渲染产出，尺寸 1440×900（与 `scripts/visual-shot.mjs:12-18` 的基准一致），落 `docs/screenshots/`，命名跟该目录 README 的 `<route>-<state>-<dark|light>.png` 口径（`<route>` = `home`）。**这些是规格图（设计稿渲染），不是实机截图**——面板尚未实装，`visual-shot.mjs` 拍不到它；实装后请按 §12 重拍 `home-search-*-after-*` 对比组。重生成命令见 `docs/screenshots/README.md` 的 QUOT-348 一节。

| 文件 | 覆盖需求里的哪个态 |
|---|---|
| `home-search-entry-states-dark.png` | 入口默认 / hover / focus（三种状态并排 + 2× 放大标注，含 §4.5 的「Tab 路过不弹」注记） |
| `home-search-panel-empty-dark.png` | 面板打开 = 空态（未输入）：焦点环在输入框、最近搜索 chips、「可以搜这三类」引导；清除 ✕ 此时**不出现** |
| `home-search-panel-results-dark.png` | 输入中（`电力`）：个股 5 行 + 行业 / 题材 3 行、高亮行（左侧 accent 条 + `bg-surface-3`）、组头徽标、组尾出口 |
| `home-search-panel-results-scrolled-dark.png` | 同一态滚动到底：sticky 组头仍钉在顶部、资讯组（双行 47px 行高）、「查看全部 12 条 →」（组头计数在第一期只显本轮返回条数，§6.1 v1.1）——证明 §6 的满命中总高（v1.3 结果区已改为 flex 剩余高度，窗口变矮时仍会滚）与 §7.3 的必要性 |
| `home-search-panel-loading-dark.png` | 加载中：个股一路已回，另两组 3×3 骨架 + 组头**无徽标**（§8.4），底部 `正在加载搜索结果` / 1.2s 慢响应提示 |
| `home-search-panel-no-result-dark.png` | 无结果态（§8.5 定稿文案 + 3 条下一步指引 + 兜底出口 + 「没匹配 ≠ 不存在」） |
| `home-search-panel-group-error-dark.png` | 单组失败（§8.6：组头保留 +「未取到」+ 降级条 + 重试，另两组仍可用） |
| `home-search-panel-offline-dark.png` | 整体失败 / 离线（§8.7：沿用 `OfflineBanner` 措辞，面板不关、入口不禁用） |
| `home-search-panel-results-light.png` | 浅色主题下的同一有结果态（需求点名要暗色，反过来浅色也要有一张才能核对 §7.4 表与浅色焦点环） |
| `home-search-panel-results-1024-dark.png` | rail 1024 断点（1024×900）：侧栏收成 56px 图标栏、入口 176px（占位文案截断）、面板 480px，左边缘 524px > 56px —— §5.3 的不相撞算式在真实像素上成立 |

原型顶部有一条状态切换条（12 个态 + 主题切换 + rail 断点切换；`?state=` / `?theme=light` / `?vw=rail` / `?chrome=0` / `?scroll=bottom` 只是给截图用的直达参数），验收时点它即可逐态核对。**切换条与页面底色不属于设计体系**，真应用里没有，截图用 `?chrome=0` 隐藏。

⚠️ 落盘位置不是随手选的：`docs/tasks/` 在 `.gitignore:44-51` 里按 DEC-0026 被排除（内部过程材料永不进版本管理），所以**规格与规格图必须落在 `docs/specs/` + `docs/screenshots/` 才进得了仓库**。

## 12. 验收清单（测试工程师 / `check:ui` 对照）

1. `cd frontend && npm run check:ui` 六项全 PASS（尤其规则 3 无 `[...]` 任意值、规则 6 无 `duration-*` 数字）。
2. 焦点链：Tab 到入口 → Enter 打开 → 焦点在输入框 → `↑/↓` 移动高亮且行滚入视野 → Enter 跳转 → 目标屏 URL 与 §3.2 表逐字一致（中文用 `%E6%9C%...` 全量编码断言，不要用手写百分号）。
3. Esc 关闭后 `document.activeElement === 入口`；关闭后面板 DOM 不存在（`if (!open) return null` 的既有即时挂载模式，`Overlay.tsx:118`）。
4. `Tab` 路过入口**不**打开面板（§4.5）。
5. 中文输入法候选期间按 Esc，面板**不**关闭；候选确认后**不**触发请求（composition 守卫）。
6. 实装后：无结果、单组失败、离线三态各补一张**实机** `home-search-<state>-after-<dark|light>.png`（`visual-shot.mjs` 的命名约定，落 `docs/screenshots/`），与本 PR 的 10 张规格图逐态对照。规格图是设计稿渲染，不能代替实机证据。
7. 面板内搜不到任何 `演示` / `demo` 字样水印；`DemoStateBadge` 不出现在面板子树里（QUOT-348 硬约束）。
8. 每组组头**只有一枚**状态徽标，文本与 `SourceBadges.tsx` 的 `SOURCE_STATUS_LABEL` 逐字一致（`数据完整 / 已降级 / 部分缺失`）+ 未取到档；面板子树内 `grep -c FreshnessBadge` 为 0，且**没有**第三份 label map（§6.7 v1.1）。
9. 浅色主题下类型标签实测对比度 ≥4.5（用 `Badge tone="flat"`；若有人改成 `tone="neutral"`，§7.4 表 4.45 会被打回）。
10. 断网/服务停时入口仍可点开并显示 §8.7，入口不禁用。
11. 面板内可 Tab 件按 §7.1 顺序且 **≤5 个**；Tab 循环不出模态；结果行 `tabIndex={-1}`。
12. 清除 ✕ 在**输入框内**右端、只在非空时出现、点击后焦点仍在输入框（不是关闭）；`type="search"` 的原生 ✕ 已按 §5.5 关闭，屏幕上任何时刻只有一枚 ✕。

## 13. 责任边界

| 目录/文件 | 归属 | 本规格 |
|---|---|---|
| `frontend/src/screens/HomeScreen.tsx` | 前端架构师 | **只给挂载点说明（§4.1）**，未改动，未要求改结构 |
| `frontend/src/components/Layout.tsx` | 前端架构师 | **不改**（§5.3 已论证无需给侧栏补 z-index 或新加 topbar） |
| `frontend/src/screens/radar/NewsScreen.tsx` | 前端架构师 | `?q=` 读取与关键词 chip **已在 main**（`NewsScreen.tsx:432`、`:610`）；后端 `/api/news` 仍无 `q` 参数，所以匹配发生在前端已加载窗口内（§9.3 #3 的退化路径，如实标注范围） |
| `frontend/src/components/ui/Overlay.tsx` | 前端架构师 | `useModalChrome` **已 export**（`Overlay.tsx:28`），面板按 §5.4 复用中 |
| `src/ladderstock/web/` | 后端服务小队 | 需要 `GET /api/search` 与 `/api/news?q=`（§9.3） |
| `frontend/tokens.json` / `tailwind.config.ts` | UI/UX + 架构师 | **零新增颜色令牌**（§10.2）；结构尺寸两档待架构师二选一 |
| `docs/specs/home-search.md`（本文件）+ `docs/specs/home-search-prototype.html` + `docs/screenshots/home-search-*.png` | UI/UX | 本任务的交付物，已随本 PR 落盘；`frontend/src/` 一律未触碰 |

## 14. 需要架构师/产品回来确认的点

按「不确认就无法开工」的程度排序。每条都给了推荐方案，**点头即可落地，不用再问**。

| ID | 事项 | 推荐 | 影响面 |
|---|---|---|---|
| **A1** | 入口文案去掉「提问」（§4.2）。后端无问答通道，「问」是骗人的承诺 | 采用「搜索个股 / 行业 / 资讯」；若产品坚持「提问」，需要先有 §0 里那条问答通道，另开需求 | 只影响一行 placeholder |
| **A2** | Tab 路过入口**不**自动弹面板（§4.5），与 QUOT-343「点击或聚焦即弹出」字面分歧 | 采用「pointerdown/Enter 打开、focus 不打开」；被否则改 `onFocus` 打开，需同时接受键盘用户每 Tab 一次被遮罩打断 | 一行事件绑定 |
| **A3** | `/api/search` 新端点（§9.3）是否本期做 | **已解决（v1.1）**：QUOT-356 走的是**客户端聚合**（`searchCore.ts` 的 6 个源拼 `/api/ladder` + 候选池 + `/api/industry` + `/api/market/theme` + 资讯流），§9.3 那个服务端聚合端点不再需要，已降级为背景材料。遗留的只有资讯关键词检索：`/api/news` 仍无 `?q=`，第一期由前端在已加载窗口内子串匹配（§9.3 #3 的退化路径），组头需如实标注匹配范围 | 一条后端小改 |
| **B1** | 入口结构宽度（256）是否登记进 `tailwind.config.ts` 的 `width`（§10.2）；面板宽度 v1.3 起已不适用 | 入口用 `w-64` 内建档，不登记 | 一行配置 |
| **B2** | 个股命中 >5 的出口（第一期是分页、是跳 `/market?tab=ladder`、还是只提示） | 只提示，不做分页（面板内分页 = 焦点位置与滚动位置两套新状态，收益不抵复杂度） | 交互复杂度 |
| **C1** | 「空输入 Backspace 清除上一条最近搜索」是否做 | 可不做（§7.1 已标为可选） | 键盘行为 |
| **D2** | 题材名不在行业字典时跳 `/market?tab=theme` 而不是 `/industry/:name`（§3.2 第 3 行）——`/industry/:name` 对未登记题材会显「该行业暂无数据」 | 采用 §3.2 表；若架构师希望题材统一落 `/industry/:name`，需要行业页先支持题材名解析 | 路由语义 |
| **D3** | **既有偏差**（非本任务引入）：`Badge` 的 `*-soft` 底色在**浅色主题**下 12px 文字 4.22–4.45:1，低于 `tokens.json meta.contrastPolicy` 的 4.5:1（§7.4 表） | 面板内规避（类型标签用 `tone="flat"`）；根治需调 light 侧 `warn/danger/ok/flat-soft` 四个混合底或对应文字色，属**设计系统级变更，要上报**，不在本任务内动 | 设计系统 |
| **A5** | 组头「截断前总数」拿不到：`runSearch` 只回 `slice(0, hitLimit)` 之后的 `hits`，没有截断前计数（§9.4） | 第一期组头显**本轮返回条数**（§6.1 v1.1），不做「还有 N 条未显示」的提示。若产品坚持要，需要数据层在 `SearchGroup` 上加 `totalHits`（`toHits` 里 `slice` 前记一次长度，一行） | 数据层字段 |
| **D4** | 搜索历史存哪：`localStorage` 键（同 `ldst.nav.expanded`，`Layout.tsx:173`）还是 IndexedDB 分片（`frontend/stores/` + `manifest.json` 有 schema 迁移机制） | `localStorage`，键 `ldst.home-search.recent`，最多 8 条。搜索历史不是业务草稿，不该进迁移链与 `schema_version` | 持久化一致性 |
