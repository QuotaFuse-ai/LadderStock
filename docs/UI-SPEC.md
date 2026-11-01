# 看板信息架构与视觉规范（19 屏）

- 版本：v1.3
- 日期：2026-11-02
- 归属：UI/UX
- 状态：生效（v1.0 为「待评审」；v1.1 只扩覆盖与校正事实，`done` 仍由人类确认）
- 适用：本地看板 `frontend/`（React + Vite + Tailwind，端口 5950，仅回环）
- 代码事实基准：`origin/main` @ `270caf8`。本文所有 `文件:行号`（省略 `frontend/src/` 前缀）都在该 revision 上核对过。
- 唯一真源：`frontend/tokens.json`（v2.0.0）→ 由 `frontend/scripts/gen-tokens.mjs` 生成 `styles/tokens.css` 与 `tailwind.config.ts` 的 `theme.extend`。本文与生成物冲突时**以生成物为准并回改本文**（v1.1 就是这样做的，理由见 §12.4）。

## 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-10-28 | 首版：四屏 IA、状态恢复、降级与溯源、AI 四类错误、视觉 token、组件清单 |
| v1.1 | 2026-11-01 | 覆盖 4 屏 → **18 屏**（QUOT-308，决策 DEC-040）。§0 逐屏清单 + §2 外壳/断点/标题层级按 `components/Layout.tsx:64-119`、`tailwind.config.ts:40-44` 校正（删除从未实现的「<1024 顶部横向导航」「<720 表格转卡片」，最低支持视口定为 1024）；§3.5–§3.18 补 14 屏信息架构；§5 增加溯源徽标唯一实现、演示态水印、`freshness` 缺省态；§6 组件清单按实际文件重写（v1.0 登记的 `kit/Badge.tsx` 等 11 个文件不存在）；§7 token 表按 `tokens.json` v2 全量校正（圆角收敛 8/4/999，字号 12/14/16/20/28/36，补 accent-solid / chart / skeleton / scrim 族）；§7.5 按 DEC-040 裁定 120ms / 180ms 分工；§8 补缺失值、时间格式、计数与后端 key 直显规则；§10 扩到 18 屏；§11 由「四屏清单」改为「逐屏清单」；§12.4 规范变更与理由，§12.5 整改清单 R-01…R-26。 ；同版补「盯盘·竞价与盘中」（`/intraday`，QUOT-301 / QUOT-302 / SPEC-INTRADAY-READ）：§0 与 §10 记为第 19 屏。 |
| v1.2 | 2026-11-02 | 导航信息架构更新（QUOT-304，决策 DEC-044）：新增一级组「我的股票」（我的持仓 `/positions` + 事件日历 `/calendar`），从「日志」「市场」移出，顺序为 首页 → 市场 → 盯盘 → 复盘 → 日志 → 我的股票 → 验证 → 产业研究 → 个股深挖；自选、提醒无已交付页面，登记为范围外，不建占位入口。§2「导航顺序」与导航可达性段落同步改写；路由零变更。 |
| v1.3 | 2026-11-02 | QUOT-306 / QUOT-317 收口：一致性口径脚本化（`frontend/scripts/check-ui-consistency.mjs` + `npm run check:ui`，六项：裸表 / hex / 任意值 / 数值基线 / DataTable 引用 / 过渡时长）；`DataTable` 补整行 `summaryRow`（列级 `summary` 保留，二者互斥）；四处任意值收口为 `min-h-calendar-cell` / `min-w-table-min` 与 `truncate` 列；§0 表格「事件日历 / 我的持仓」的侧栏分组同步为「我的股票」（v1.2 遗漏）；编号与变更记录合并规则见 DEC-045。 |
| v1.3.1 | 2026-11-02 | 外壳层 §2 逐条走查落地（QUOT-316，只动 `components/Layout.tsx` / `PageShell.tsx` / `PersistenceBanner.tsx`，`NAV_SECTIONS` 结构零变更）：① §2 提示条「同类最多 2 条 + 另有 N 条提示」此前无实现，`PersistenceBanner` 现按 tone 各截 2 条、余量并成一行且逐个列出分片名（折叠不丢信息），顶内距 `pt-3`→`pt-5` 与 `main` 的 `py-5` 对齐；② §11.0「数字必 `.ldst-num`」在页头不可达——`PageHeader` 的 subtitle / meta 由外壳统一挂 `.ldst-num`（只加可继承的 `font-variant-numeric`，字号与颜色仍归各屏），19 屏一次生效；页头换行纵向间距 12px→4px；③ §2 侧栏二级缩进 8px→16px（导引线落在一级图标正下方），`rail:` 档展平后加组间 1px 分隔线，否则 19 行同形图标无分组可读性；④ §2 阅读型内容度量：外壳自有的 `OfflineBanner` 说明段挂 `max-w-measure`（`--ldst-measure: 52ch`）；⑤ `Layout.tsx` 头注释的 `DESIGN-SPEC §3.1/§4.1` 假引用（M-9）改引 `UI-SPEC §2`；⑥ R-21 实测更正为「非缺陷」，见 §12.5。规范条文本身未放宽：以上都是代码向 §2 / §11.0 收敛。 |

## 0. 范围与非目标

**覆盖（19 屏）**——路由以 `App.tsx:54-76` 为准（v1.1 的 18 屏 + QUOT-302 新增的 `/intraday`），侧栏入口以 `components/Layout.tsx:64-119` 为准。

| # | 屏 | 路由 | 侧栏入口（分组 · 标签） | 契约码 | v1.0 |
|---|---|---|---|---|---|
| 1 | 复盘 | `/review` | 复盘 · 情绪复盘 | P-04 | 已覆盖 |
| 2 | 盯盘 | `/watch` | 盯盘 · 盘中信号 | P-03 | 已覆盖 |
| 3 | 验证 | `/validate` | 验证 · 验证 | P-06 | 已覆盖 |
| 4 | 日志 | `/journal` | 日志 · 交易日志 | P-05 | 已覆盖 |
| 5 | 首页 | `/`（index，`App.tsx:54`） | 首页 · 首页（单页分组） | P-01 | 新增 |
| 6 | 市场 | `/market?tab=sentiment\|ladder\|theme` | 市场 · 情绪温度 / 连板梯队 / 题材轮动（3 个入口同屏） | P-02 | 新增 |
| 7 | 周度复盘 | `/weekly` | 复盘 · 周度复盘 | — | 新增 |
| 8 | 资讯雷达 | `/news`（**不是** `/radar`） | 市场 · 资讯雷达 | — | 新增 |
| 9 | 事件日历 | `/calendar` | 我的股票 · 事件日历 | — | 新增 |
| 10 | 候选池 | `/pool` | 复盘 · 候选池 | — | 新增 |
| 11 | 我的持仓 | `/positions` | 我的股票 · 我的持仓 | — | 新增 |
| 12 | 产业矩阵 | `/industry` | 无侧栏入口（深链） | — | 新增 |
| 13 | 行业详情 | `/industry/:name` | 无侧栏入口（矩阵下钻） | — | 新增 |
| 14 | 产业信号 | `/signals` | 无侧栏入口（深链） | — | 新增 |
| 15 | 涨停样本 | `/limit-up` | 验证 · 涨停样本 | — | 新增 |
| 16 | 个股深挖 | `/research/:code` | 无侧栏入口（深链） | — | 新增 |
| 17 | 研究报告 | `/research/:code/report` | 无侧栏入口（深挖页跳转） | — | 新增 |
| 18 | 设置 | `/settings` | 侧栏底部固定项 | P-07 | 新增 |
| 19 | 盯盘·竞价与盘中 | `/intraday`（QUOT-302 新增） | 盯盘 · 竞价与盘中 | — | 新增 |

> 任务书里的 `/radar`、`/radar/pool` 等路由在代码里不存在：雷达家族的实际路由是 `/news` `/calendar` `/pool` `/positions`（`App.tsx:65-68`）。本规范一律按代码写。

**不覆盖（明确不做）**：

- 任何行情采集、数据清洗、指标计算逻辑（数字只由程序算，AI 只写叙述）。
- 交易日志 / 持仓 / 自选的网络读写（隐私数据只进本机 SQLite，`journal*` 不经 HTTP）。
- 买卖建议、目标价、收益预测（产品边界：复盘辅助，不输出交易信号）。
- 新增数据源或"某个 peer 挂了就换一家"的降级（只允许同 peer 备工具 → 缓存回放）。
- **视口 < 1024px**：v1.0 §2 写的「<1024 侧栏折叠为顶部横向导航」「<720 表格转卡片列表」在 `270caf8` 上没有任何实现（`Layout.tsx:194` 只有 `w-sidebar` / `rail:w-sidebar-rail` 两档，`rail` = `max-width:1279px`）。v1.1 把最低支持视口正式定为 `--ldst-bp-min: 1024px`，窄屏适配移出规范（DEC-040 §3，理由见 §12.4-B）。
- `NAV_SECTIONS` 的分组结构本身（QUOT-306 边界：QUOT-300 / QUOT-304 正在改同一文件）。§2 只登记**现状**并约束视觉层，不改导航信息架构。

## 1. 设计原则

1. **程序算数，AI 解读**：指标卡里的数值永远来自 `ldst sentiment show / ladder show` 及其只读端点；AI 文本在独立分区，视觉上不与数字混排，AI 不可覆盖指标（`core/boundary.py:AI_WRITABLE`）。页面侧同样不得二次计算、补数、排序（`docs/specs/SPEC-RESEARCH-PAGES.md`）。
2. **状态优先**：任何需要用户保持上下文的地方（复盘正文、未提交验证条件），刷新后必须可恢复或明确说"需重取"。不可恢复时**说清原因**，不给空页面。
3. **不编造数据**：未接入、过期、降级、演示四种状态都必须显式可见；宁可显示「—」与原因，也不显示占位数值（`lib/format.ts:10 MISSING = '—'`）。
4. **降级可见**：`freshness` / `source_status` / AI 降级 / 导出降级 / **演示数据**，全部有文字徽标，颜色只是辅助。
5. **隐私不出本机**：日志与持仓常驻隐私说明（`radar/PositionsScreen.tsx:84-99`）；界面不出现"同步到云端""分享"类入口。
6. **一致优于新奇**：全 18 屏共用一套外壳（`components/Layout.tsx` + `PageShell`）、一套徽标、一套空态、一套表体；例外必须在本文件登记。
7. **一份实现，不许各写一套**：同一语义（溯源徽标、"接口未接入"空态、指标卡阵、状态条）在 18 屏里只允许有一个实现；需要差异时用 props，不复制粘贴（v1.1 实测有 4 份 `SourceBadge`、3 份"接口未接入"文案，全部列入 §12.5）。

## 2. 全局外壳

以 `270caf8` 的真实 DOM 为准（v1.0 的示意图描述的是一个更高的抽象条 + 四屏导航，与代码不符）。

```
┌──────────────┬──────────────────────────────────────────────┐
│ 品牌 h-topbar│ PersistenceBanner（迁移/恢复失败，条件出现） │
│ ───────────  ├──────────────────────────────────────────────┤
│ 首页         │ main#main：max-w-table · px-5 py-5           │
│ 市场  ▾      │  ┌────────────────────────────────────────┐  │
│  情绪温度    │  │ PageHeader：h1 + badge + subtitle      │  │
│  资讯雷达    │  │              + meta + actions（右）    │  │
│ 盯盘         │  ├────────────────────────────────────────┤  │
│ 复盘  ▾      │  │ 提示条区（Banner，最多同类 2 条）      │  │
│  周度复盘    │  ├────────────────────────────────────────┤  │
│ 日志  ▾      │  │ 主内容（卡 / 表 / 列表 / 图）          │  │
│ 验证  ▾      │  └────────────────────────────────────────┘  │
│ ───────────  │                                              │
│ 主题 Seg.    │                                              │
│ 设置         │                                              │
│ 本机说明     │                                              │
└──────────────┴──────────────────────────────────────────────┘
```

| 项 | 规范 | 代码依据 |
|---|---|---|
| 侧栏宽 | 完整侧栏 `--ldst-sidebar-w: 232px`；`≤1279px` 收成 56px 图标栏（`rail:w-sidebar-rail`） | `Layout.tsx:194`、`tailwind.config.ts` `width.sidebar-rail` |
| 侧栏定位 | `sticky top-0 h-screen`，不随内容滚动 | `Layout.tsx:194` |
| 内容容器 | `main#main` = `mx-auto w-full max-w-table px-5 py-5`（上限 `--ldst-table-max: 1296px`） | `Layout.tsx:328` |
| 阅读型内容 | 段落 / 叙述块再收一层 `--ldst-content-max: 1080px`，中文正文度量 `45–75ch` | §8、R-24 |
| 断点词汇 | 只有两档命名变体：`rail:` = `max-width 1279px`、`desk:` = `min-width 1280px`（`--ldst-bp-base`） | `tailwind.config.ts:40-44` |
| 断点用法 | `desk:` 用于 ≥1280 的多栏与指标列；`rail:` 用于图标栏下的隐藏/降级；**禁止** `min-[1024px]:` / `min-[1100px]:` / `min-[640px]:` 这类临时断点（10 处，R-04） | 现状：`WeeklyScreen.tsx:381,506,554`、`MarketScreen.tsx:529`、`research/ResearchDetailScreen.tsx:94,262,394` |
| 标题层级 | 页面 `h1` = `text-page`（28px/600，每屏唯一）；页内分区 `h2` = `text-section`（20px/600）；卡片标题 = `text-subtitle`（16px/600）；正文/单元格/按钮 = `text-body`（14px）；表头/时间戳/徽标/说明 = `text-caption`（12px）；主指标数值 = `text-hero`（36px，每屏 ≤1 处） | `styles/tokens.css:67-78`、`PageShell.tsx:28`、`ui/Card.tsx` |
| 工具栏 | 统一 `PageHeader`：左 `h1 + badge + subtitle + meta`，右 `actions`（`gap-2`，主操作实心在最右） | `PageShell.tsx:24-36` |
| 顶栏高度 | 侧栏品牌行与 `PageHeader` 的 `min-h` 都取 `--ldst-topbar-h: 56px` | `tokens.css:98`、`PageShell.tsx:26` |
| 键盘 | 首元素「跳到主内容」（`Layout.tsx:191`）；Tab 顺序与视觉顺序一致；`main` 可聚焦（`tabIndex=-1`） | `Layout.tsx:191,328` |
| 焦点可见 | `:where(a,button,input,select,textarea,summary,[tabindex]):focus-visible` 走 `--ldst-focus-ring` | `styles/index.css:30` |
| 提示条 | `PersistenceBanner` 在 `main` 之上（`Layout.tsx:327`），页面级 `Banner` 在 `PageHeader` 之下；同类最多 2 条，其余收进「另有 N 条提示」 | `PersistenceBanner.tsx:22` |
| 导航顺序 | 首页 → 市场（3 段 + 资讯雷达）→ 盯盘（盘中信号 / 竞价与盘中）→ 复盘（情绪 / 周度 / 候选池）→ 日志（交易日志）→ **我的股票**（我的持仓 / 事件日历）→ 验证（验证 / 涨停样本）→ 产业研究（产业研究 / 产业信号）→ 个股深挖；主题分段控件与设置固定在侧栏底部（QUOT-304 / DEC-044） | `Layout.tsx` `NAV_SECTIONS` |
| 侧栏本机说明 | 「仅监听本机回环 · 草稿只存本机 IndexedDB」常驻 | `Layout.tsx:320-322` |

**19 屏导航可达性**：QUOT-300 补上 `/industry` `/signals` `/research` 入口、QUOT-304 补齐「我的股票」组后，**全部路由都有侧栏入口或由有入口的页面链入**（`/industry/:name`、`/research/:code`、`/research/:code/report` 由落地页链入）；`frontend/tests/nav-routes.test.tsx` 双向断言守住这一点。§3 的「入口」列必须写明用户实际怎么走到该屏，写不出路径的按 R-05 处理。

## 3. 逐屏信息架构

信息层级用语：

- **L0** 当前在哪、看哪天、数据是否可信（用户 1 秒内要确认的事）
- **L1** 主任务的操作与结果
- **L2** 溯源、参数、辅助信息
- **L3** 低频操作（导出、重置、调试）

### 3.1 复盘（`/review`）— 主任务：收盘后 15–30 分钟内完成五段判断

| 层级 | 内容 |
|---|---|
| L0 | 标题「收盘复盘」、交易日选择、草稿保存时间、数据可信徽标 |
| L1 | 指标卡 ×3：情绪温度 / 晋级率 / 梯队断层（程序直出，不可编辑） |
| L1 | 五段结果：情绪 → 资金 → 题材 → 龙虎榜 → 龙头（顺序固定） |
| L1 | 每段内「AI 叙述」（只读）与「我的判断」（可编辑，自动存本机） |
| L2 | 每段的模型名、降级标记、段落数据时间；指标卡的数据时间与来源 |
| L3 | 重新生成、重试失败段、导出（PDF→MD 降级）、清空草稿 |

**布局**

- 指标卡：三列等宽（`grid-cols-1 desk:grid-cols-3`，`ReviewScreen.tsx:666`），数值用等宽数字，右上角放溯源徽标。
- 五段区：纵向卡片；`≥1280`（`desk:`）时每段内部左右两栏（`grid gap-4 desk:grid-cols-[2fr_3fr]` = AI 叙述 40% / 我的判断 60%，`ReviewScreen.tsx:763`），`rail:` 改为上下堆叠且 AI 叙述在上。**v1.0 写的 `≥1100px` 阈值不存在**（该档断点在系统里没有），v1.1 起以 `desk:` 为准。
- 段落状态：`待生成`（灰）/ `生成中`（骨架 + 扫光）/ `已完成`（正常）/ `降级`（黄边 + 「降级完成」徽标）/ `失败`（红边 + 段内错误面板，成功段保留）。

**状态矩阵**

| 状态 | 表现 | 文案要点 |
|---|---|---|
| 空态（该日无复盘） | 指标卡显示「—」+「未生成」；五段只显示输入框与引导 | 「先同步数据再生成复盘；也可以先写判断，正文会存本机」 |
| 加载态 | 指标卡骨架 3 个 + 五段骨架；工具栏按钮禁用并显示「生成中…」 | 顶部状态行「程序指标计算中，AI 叙述随后到达」 |
| 部分失败 | 顶部黄条 + 失败段内错误面板；成功段内容保留 | 「5 段中 2 段失败，已保留 3 段，可单独重试」 |
| 全失败 | 顶部红条 + 五段错误面板 + 「导出 Markdown」可用 | 「AI 全部失败，指标与正文仍可查看/导出」 |
| 未接入 | 指标区显示未接入说明，不显示数值 | 「本地接口未接入：先运行 `ldst run`」 |
| 断网 | 顶部红条 + 「重试」 | 不丢草稿；输入框保持可写 |

**禁止**：AI 文本里出现精确指标数字（"今日情绪 62"）时，视觉上不得渲染为指标样式；指标卡不允许直接编辑。

### 3.2 盯盘（`/watch`）— 主任务：盘中识别阈值信号并决定是否行动

| 层级 | 内容 |
|---|---|
| L0 | 快照可信状态（有效期 / 取数时间 / 剩余 TTL）、当前交易日 |
| L1 | 信号流：时间 + 代码 + 名称 + 信号类型 + 触发值 + 阈值 + 级别 |
| L1 | 阈值设置：涨幅阈值（默认 3.0%，1.0–10.0，步进 0.5）、窗口（3 分钟，固定）、封板/开板检测开关、同一标的冷却时间 |
| L2 | 快照来源（peer / 工具 / 主备）、扫描间隔、最近一次扫描时间 |
| L3 | 清除本机快照、通知链路自检（对应 `ldst notify test`） |

**信号流规则**

- 倒序排列，最新在上；同标的同类型 60 秒内合并为一条并显示「×N」。
- 级别：`提示`（灰）/ `关注`（黄）/ `强信号`（红）。级别只由规则决定，不由颜色意象决定。
- 每条信号必须显示实际值与阈值（如「3 分钟 +4.21% ≥ 3.00%」），否则用户无法复核。
- 空流不等于空态：显示「最近一次扫描 10:32:15，暂无信号」——证明盯盘在跑。

**状态矩阵**

| 状态 | 表现 | 文案要点 |
|---|---|---|
| 非交易时段 | 信号流替换为说明卡；阈值仍可看 | 「当前非交易时段，盯盘未运行」 |
| 空态（无信号） | 保留扫描时间与空列表说明 | 「暂无信号」 |
| 加载态 | 信号行骨架 5 条 + 阈值区骨架 | 「正在取盘中快照…」 |
| 快照过期 | 隐藏所有旧数值，显示重取卡 | 「快照已超过 TTL（8 分 12 秒），旧值已隐藏」 |
| 缓存回放 | 黄条 + `freshness=cached` 徽标 + 顶部时间 | 「当前为缓存回放，非实时；数据时间 10:24:03」 |
| 源降级 | 黄条 + `source_status=degraded` | 「主工具不可用，已切同 peer 备工具」 |
| 未接入 | 空态 + 重取按钮 | 「未接入行情：先运行 `ldst run`」 |
| 阈值非法 | 输入框红边 + 行内提示，不保存（分片保留上一个合法值） | 「阈值需在 1.0–10.0 之间，步进 0.5」 |

**阈值持久化**：阈值属于用户偏好，已由 DEC-037 落地 `watch_settings` 分片（`config/state.yaml` + `stores/manifest.json` + 契约测试三处同步）；卡片文案为「改动即时保存到本机 · 刷新 / 重启 / 多窗口共用」，不再出现「本次会话有效」。

**v1.1 补充**：本页的可信状态来自本机 `persist/freshness`（`WatchScreen.tsx:180-185`，`needsRefetch` → 行清空），**不是**信封 `freshness`。它必须与 §5 的徽标同源显示（同一标签表、同一 ●/◐/○ 图形），当前用 `StatusDot` 自写文案（`WatchScreen.tsx:340-341`）→ R-26。

### 3.3 验证（`/validate`）— 主任务：收盘后写明日条件，次日收盘后对账

| 层级 | 内容 |
|---|---|
| L0 | 交易日 / 模式切换（「明日条件」草稿 ↔「收盘对账」结果） |
| L1 | 条件列表：代码 / 标的、触发条件、失效条件、仓位上限、是否已确认 |
| L1 | 对账结果：逐条判定（命中 / 未命中 / 未触发 / 数据缺失）+ 命中率 |
| L2 | 判定依据（当日实际值、数据时间、来源徽标）；人工复核入口 |
| L3 | 删除条件、清空草稿、导出对账结果 |

**交互**

- 新增即插入空条件并聚焦「代码 / 标的」；自动存本机（`validation_draft`）。
- 「标记确认」是一次轻量承诺，确认后仍可修改，但显示确认时间；未确认条件在列表中用左侧黄色竖条标记。
- 对账按钮在 15:00 前禁用并说明原因；对账结果只读，允许「人工复核」覆盖结论并记录原因（写入本机）。

**状态矩阵**

| 状态 | 表现 | 文案要点 |
|---|---|---|
| 空态 | 引导卡 + 新增按钮 | 「还没有验证条件：写下明天要盯的代码与触发条件」 |
| 加载态 | 条件骨架 3 条 | 「正在读取本机草稿…」 |
| 未到对账时间 | 对账区灰态 + 禁用按钮 | 「15:00 后可对账；现在可以先写明日条件」 |
| 部分数据缺失（退出码 8） | 黄条 + 缺失数据集清单 | 「部分数据缺失（涨停池），结论仅供参考」 |
| 全失败 | 红条 + 重试 | 「对账未完成：数据源不可用，未生成任何结论」 |
| 无草稿但已有对账 | 显示对账结果 + 「基于已提交条件」 | 不要用空列表盖住历史结果 |

**v1.1 补充**：条件表是**手写网格**（`ValidateScreen.tsx:585-595`，`overflow-x-auto` + `min-w-[960px]` + `grid h-control`），不是唯一表体 C-09。v1.1 的口径：允许它在 §6.2 登记为已知例外，但必须补齐表体四项契约（sticky 表头 / hover / `duration-fast` / `--ldst-row-h*`），整改为 C-09 需要先补 `expandable` API → R-03。

### 3.4 日志（`/journal`）— 主任务：记录每笔交易与理由，定期看纪律

| 层级 | 内容 |
|---|---|
| L0 | 「本机私有」常驻说明；时间范围筛选 |
| L1 | 交易记录：时间、代码 / 名称、方向、价格、数量、理由标签、是否按计划 |
| L1 | 纪律统计：按计划执行率、追高次数、止损执行率、单日笔数、连亏后加仓次数 |
| L2 | 统计口径说明（点击展开公式与时间范围） |
| L3 | 导出本机 CSV（用户显式触发）、删除单条 |

**隐私规范（硬约束）**

- 顶部常驻一条中性信息条：「交易日志、持仓、自选只写入本机 SQLite，不上传、不进 AI、不被 MCP 读取」。
- 不出现任何"同步""分享""云端备份"入口；导出按钮必须二次确认并说明文件去向。
- 不在本屏展示任何 AI 生成内容（避免隐私进入 AI 上下文）。

**状态矩阵**

| 状态 | 表现 | 文案要点 |
|---|---|---|
| 空态 | 引导卡 | 「从今天的一笔开始：记录代码、价格、理由，30 秒即可」 |
| 加载态 | 列表骨架 5 条 + 统计骨架 | — |
| 本地库不可读 | 红条 + 修复指引 | 「本地日志库无法读取：运行 `ldst doctor` 检查数据目录权限」 |
| 统计样本不足 | 统计卡显示「样本不足」而非 0% | 「少于 5 笔，暂不计算执行率」 |

**禁止**：盈亏排行榜、收益预测、"AI 点评你的交易"。

**v1.1 补充**：「我的持仓」（§3.11）与本页同为 `本机私有`，隐私信息条规格同源；两屏必须使用同一个 `PrivacyNotice` 实现，当前只有持仓有 → R-25。

### 3.5 首页（`/`，P-01）— 主任务：开盘前 5 分钟判断"今天有没有要做的事"

| 项 | 规范 | 代码依据 |
|---|---|---|
| 入口 | 侧栏首项「首页」；`/index.html` 与旧根路径重定向到此 | `App.tsx:54,78`、`Layout.tsx:66-70` |
| 工具栏 | `PageHeader` 标题「首页」；subtitle = 一句话可信摘要（`数据完整 · 更新于…` / `部分数据降级 · …` / `本地服务未连接` / `取数失败`）；badge = `DemoStateBadge`；右侧唯一主操作「刷新全部」 | `HomeScreen.tsx:270-275,318-335` |
| L0 | 摘要 + `TemperatureBar`（情绪温度条）+ 5 张指标卡（`grid-cols-2 gap-4 rail:grid-cols-3 desk:grid-cols-5`） | `HomeScreen.tsx:372-427` |
| L1 | 「待办」卡（手写 `ul`/`Link` 行，`min-h-control px-4 py-2`）→ 跳转 `/validate` `/review` `/settings` | `HomeScreen.tsx:429-447` |
| L1 | `desk:grid-cols-12`：左 8 列「今日连板梯队 Top 10」（C-09 `DataTable maxHeight=420px`）、右 4 列「题材强度 Top 5」+「最近异动」 | `HomeScreen.tsx:450-514` |
| 六态 | loading / error / offline / partial 齐全；**缺整屏 empty 分支**（`derived==='empty'` 时仍渲染正常 DOM，`usable` 只含 normal\|partial） | `HomeScreen.tsx:210,215,337-370` |
| 溯源 | 屏内自写 `SourceBadge`（`实时·完整` / `缓存·降级`，`tone` 由 `source_status` 决定，hover 出 `source_detail`+`data_as_of`+`fetched_at`）；「主线题材」卡**没有**徽标 | `HomeScreen.tsx:56-82,380-417` |

**禁止**：首页不得成为第二套数据链路——它只读 `/api/sentiment` `/api/ladder` `/api/validation` `/api/watch/snapshot`（`HomeScreen.tsx` 内调用），不得自算指标；重复展示的区块必须与来源屏**同组件同 token**（重复清单见 R-13）。

### 3.6 市场（`/market?tab=`，P-02）— 主任务：把当日市场拆成三段事实看清

| 项 | 规范 | 代码依据 |
|---|---|---|
| 入口 | 侧栏「市场」下 3 个入口指向同屏不同 tab；高亮跟随 `?tab=`（默认 `sentiment`） | `Layout.tsx:72-78,153-159` |
| tab 同步 | 状态只存 URL：`params.get('tab') ?? 'sentiment'`；切 tab 走 `push`，改日期走 `replace` | `MarketScreen.tsx:288-302` |
| 工具栏 | `PageHeader` 标题「市场」+ subtitle 摘要 + `DemoStateBadge`；右侧唯一「刷新」 | `MarketScreen.tsx:446-471` |
| 选择器 | 一行 `flex flex-wrap items-end gap-3`：日期 `Field`（`w-44`）+ `Segmented`（**不是** C-08 Tabs）+ 说明「三段共用同一个交易日」 | `MarketScreen.tsx:506-522` |
| 主区 | `grid gap-4 desk:grid-cols-12`：左 8 列（当前段），右 `aside` 4 列（三段数据接入 + 同步与口径） | `MarketScreen.tsx:525-526,698` |
| sentiment | 3 指标卡（`grid-cols-1 gap-3 min-[640px]:grid-cols-3` ← R-04）+ CH-01 折线（`height=320`，band 40–60，阈值 50）+ `DataTable maxHeight=240px` | `MarketScreen.tsx:529,589-608` |
| ladder | CH-02 柱（`height=320`）+ `DataTable` | `MarketScreen.tsx:637-649` |
| theme | `HBarList` Top 8 + `DataTable maxHeight=320px` | `MarketScreen.tsx:678-690` |
| 六态 | 整屏 loading/offline/error/partial 齐全；空态在 theme 段被抑制（R-14）；每段有 `NotSynced` 空态（含 `ldst sync --date` 与「重新检查」）；**缺段级 error**：404 被吞成 null，只靠全局 partial 条 | `MarketScreen.tsx:473-504,136-169,307-322` |
| 溯源 | 屏内第三份 `SourceBadge`（实时/缓存/归档）+ `SourceLine` 页脚「来源/数据时间/取数」 | `MarketScreen.tsx:93-130,609,650,691` |

**图表口径**：网格 `width:1`、轴标签 `12px`、轴线尺寸 44/36、容器 padding `[8,8,0,0]`、隐藏 legend、`minWidth 240` + ResizeObserver。**折线线宽必须是 1.5px**（token 注释口径），当前实现为 2 → R-23。

### 3.7 周度复盘（`/weekly`）— 主任务：一周一次，把五段结论回看成趋势

| 项 | 规范 | 代码依据 |
|---|---|---|
| 入口 | 侧栏「复盘」分组第二项 | `Layout.tsx:91-100` |
| 工具栏 | `PageHeader` 标题「周度复盘」；subtitle `${week} · start ~ end · N 个交易日有数据`；右侧 `上周 / 下周 / 刷新` 三键（次操作在左，主操作「刷新」在最右） | `WeeklyScreen.tsx:259-298` |
| 主区 | 4 指标卡（首卡 `hero` + `Sparkline`）→ 程序直出概览卡（手写 `section` + `Badge`）→ 缺日 `Banner info` → 五段 AI `SectionCard`（编号标题 + 状态徽标 + `降级` + `AiErrorNotice`）→ 失败汇总 `Banner danger` → 每日序列 `DataTable`（compact）→ 两列 `min-[1100px]:grid-cols-2`（涨跌分布 / 主线题材 `HBarList`）→ 本周龙头 / 待验证结算 | `WeeklyScreen.tsx:371-610` |
| 六态 | loading/error/offline 齐全；**empty 只出头部 + 一条横幅**，不保留指标卡阵（与其它屏不对称，R-15）；每日序列表的空态是**裸文本**不是 C-17（`WeeklyScreen.tsx:502`，R-16） | `WeeklyScreen.tsx:300-337,502` |
| 溯源 | 屏内第四份 `SourceBadge`，直接把信封原值渲染：`freshness==='cached'?'缓存':envelope.freshness` → `realtime` / `archive` **上屏英文 key**（R-06）；AI 降级走 `Banner warn` + `Badge 降级` + `AiErrorNotice kind="degraded"` | `WeeklyScreen.tsx:166-179,376,457-468` |

**断点用法**：本页全部用 `min-[1024px]` / `min-[1100px]`，绕过 `rail:`/`desk:` → R-04 的主要整改对象。本页**没有任何 `transition-*`**（交互过渡只能来自 kit 继承）→ 记入 §11.7 检查项。

### 3.8 资讯雷达（`/news`）— 主任务：开盘前后 10 分钟扫完当日增量资讯

| 项 | 规范 | 代码依据 |
|---|---|---|
| 入口 | 侧栏「市场」分组「资讯雷达」 | `Layout.tsx:79` |
| 工具栏 | 标题「资讯雷达」；subtitle `窗口 …→… · 共 N 条 · 已抽取事件 N`；右侧「刷新」 | `NewsScreen.tsx:433-451` |
| 主区 | `grid-cols-[184px_minmax(0,1fr)_340px] gap-4 desk:grid-cols-[200px_minmax(0,1fr)_400px]` 三列：来源列 / 时间线列（手写 `ul`/`li`/`button`）/ 详情列（`article`） | `NewsScreen.tsx:555,101-360` |
| 六态 | 整屏 5 态齐全（loading `ScreenSkeleton metrics=3 rows=7` / offline / error / empty 含"接口未接入"分支 / partial）；列级只有空态，**无列级 loading/error** | `NewsScreen.tsx:457-547,206,285` |
| 溯源 | `Badge tone="info"` 「已抽取事件」+ tooltip；来源行状态用文字（计数 / 「降级」）；页脚口径说明 | `NewsScreen.tsx:228-231,167-175,600-605` |

**整改指向**：三列宽度是任意值（R-02）；「原文链接」手写链接式按钮（R-09）；标题不截断（R-10）；header 计数 `?? 0`（R-07）。

### 3.9 事件日历（`/calendar`）— 主任务：月视图定位到某一天，读当日事件

| 项 | 规范 | 代码依据 |
|---|---|---|
| 入口 | 侧栏「市场」分组「事件日历」 | `Layout.tsx:80` |
| 工具栏 | 标题「事件日历」；subtitle `${month} · N/N 小节有源 · 规则抽取 N 条`；右侧 `‹ 本月 › 刷新` | `CalendarScreen.tsx:371-414` |
| 主区 | `grid-cols-[minmax(0,1fr)_360px] desk:grid-cols-[…_400px]`：月格（`grid-cols-7 gap-1`，格 `min-h-[76px] p-1.5`）+ 当日面板；下方「小节状态」卡（`gap-3 p-4 desk:grid-cols-2`） | `CalendarScreen.tsx:508,125-178,308` |
| 六态 | 整屏 5 态齐全；**月格无自身 loading/empty/error**，空白天只渲染空白块 | `CalendarScreen.tsx:420-506,135` |
| 溯源 | 小节状态徽标 `有源 · N 条` / `有数据源（降级）` / `暂无数据源`（`ok/warn/flat`）+ `非日历数据源` 徽标 + `data_source`/`tool`/`note` 文本 | `CalendarScreen.tsx:314-337,266` |

**口径**：事件由后端规则抽取，页面不二次判定（`NewsScreen.tsx:324` 已声明，本页同样适用）。

### 3.10 候选池（`/pool`）— 主任务：把复盘产出的候选按分项拆开核对

| 项 | 规范 | 代码依据 |
|---|---|---|
| 入口 | 侧栏「复盘」分组「候选池」 | `Layout.tsx:99` |
| 工具栏 | 标题「候选池」；subtitle `${date} · 高度 … · N 只候选 · 来源 …`；右侧条件性「导出」+「刷新」 | `PoolScreen.tsx:286-307` |
| 主区 | 3 指标卡（`grid-cols-3 gap-3`）→ `DataTable density=compact maxHeight=calc(100vh - 360px) onRowClick` → 页脚口径 → 行详情 `Drawer width="lg"` | `PoolScreen.tsx:404-442` |
| 表体 | **抽屉内的分项表必须是第二份手写 `table`**——这是全仓唯一真违规裸表，根因是 C-09 缺 `summaryRow` | `PoolScreen.tsx:125-183`、`ui/Table.tsx` |
| 六态 | 整屏 5 态齐全 + 表内空态（C-09 `empty`）；无表级 loading/error | `PoolScreen.tsx:313-401,433` |
| 溯源 | 缺失项 `Badge tone="warn"`；抽屉内 `Banner warn` + `source_detail`/`data_as_of`/`formula`；导出按钮标注"只写本机" | `PoolScreen.tsx:263,187,196-201,98-100` |

**数字**：`PartCell` 缺失走 `—`+「无数据（未核实）」（`PoolScreen.tsx:53-59`，合格）；抽屉权重缺失渲染 `0.0%`（R-07）；「原始输入」单元格数字未套 `.ldst-num`（R-08）。

### 3.11 我的持仓（`/positions`）— 主任务：核对本机持仓的成本、市值与集中度

| 项 | 规范 | 代码依据 |
|---|---|---|
| 入口 | 侧栏「日志」分组「我的持仓」（与交易日志同组，同为 `本机私有`） | `Layout.tsx:109` |
| 工具栏 | 标题「我的持仓」；subtitle `${as_of} · N 只 · 价格来源 …`；右侧 `导入本地 JSON / 刷新 / 新增持仓`（主操作最右） | `PositionsScreen.tsx:633-682` |
| 主区顺序 | 隐私条与消息区 → 5 估值卡（`grid-cols-2 gap-3 desk:grid-cols-5`）→ 明细 `Card`+`CardBody p-0`+`DataTable` → 集中度 2 张 `SectionCard`（`HBarList`）→ 页脚 → `Drawer`/`ConfirmDialog` | `PositionsScreen.tsx:684-871,109-224` |
| 六态 | 整屏 5 态齐全 + 表内空态 + 集中度/行业空态；**估值卡无卡级 empty/loading**；导入视图直接跳过估值卡与集中度 | `PositionsScreen.tsx:740-841,109-157,843` |
| 溯源 | `PrivacyNotice`（`role="note"`，accent 左边条）；估值卡 note 带 `as_of`/`price_source`/覆盖率；HHI 口径注；导入结果 `Banner warn` | `PositionsScreen.tsx:84-99,115-128,185-187,713-731` |

**硬约束**：隐私条常驻且不与消息条争位；导入/新增/删除一律不出本机（`DEC-033`）；删除走 C-16 `ConfirmDialog`，不得用 `window.confirm`。

### 3.12 产业矩阵（`/industry`）— 主任务：一屏看完 SW 一级行业的热力与缺口

| 项 | 规范 | 代码依据 |
|---|---|---|
| 入口 | **无侧栏入口**，深链进入；需从首页/市场补跨链（R-05） | `App.tsx:70`、`Layout.tsx:64-119` |
| 工具栏 | 标题「产业研究」；subtitle `SW 一级 · N 个行业 · 数据时间 …`；右侧「刷新」 | `IndustryMatrixScreen.tsx:286-300` |
| 主区 | partial 条 → 筛选行（`flex-wrap gap-3`：`Segmented` + `Select w-32` + `Input w-56` + 计数）→ `DataTable density=compact maxHeight=calc(100vh - 280px)` → 图例卡「读图与口径」 | `IndustryMatrixScreen.tsx:360-451` |
| 六态 | 5 态齐全（含 `MatrixEmpty`）+ 表内 filtered-empty | `IndustryMatrixScreen.tsx:302-354,410` |
| 溯源 | 状态列 `Badge`（`ok/warn/danger`）+ 热力图例；`source_detail` 只在卡片底部 12px 小字（R-11） | `IndustryMatrixScreen.tsx:60-92,274,446` |
| 热力 | 颜色一律 `color-mix(in srgb, var(--ldst-accent) P%, var(--ldst-surface))`，P = 4%→52% 按同列经验分位映射；缺失格**不上色**、title 固定「无数据（未核实）」 | `industry/heat.ts:11-46` |

**口径**：分位是「同列经验分位，并列取中值，全等取 0.5」，页面不得改算（`heat.ts:15-26`）。

### 3.13 行业详情（`/industry/:name`）— 主任务：读单行业五维事实与成分股

| 项 | 规范 | 代码依据 |
|---|---|---|
| 入口 | 矩阵行点击下钻；`/industry/:name` 也可深链 | `App.tsx:71` |
| 工具栏 | 标题 = 行业名（回退「行业详情」）；subtitle = 代码 · 成分数 · 状态；badge 组 = `DemoStateBadge` + 状态 + `未绑定`；右侧「产业矩阵」返回 + 「刷新」 | `IndustryDetailScreen.tsx:430-462` |
| 主区 | `grid-cols-1 gap-4 desk:grid-cols-12`：左 8 列 = 五维卡阵（`grid-cols-2 gap-3`）+ 成分股 `DataTable maxHeight=360px`；右 4 列 = 相关研报/资讯、口径与成分、溯源与缺口 | `IndustryDetailScreen.tsx:529-665` |
| 六态 | 整屏 5 态齐全；`DetailEmpty` 细分四种空因（非法名 / 无匹配候选 / 五维全 null / 接口未接入）；成分与相关有子空态；**溯源卡无条件常渲**（无自身空态） | `IndustryDetailScreen.tsx:466-513,337-418,555,576,624` |
| 溯源 | `PercentileGauge`（`role="meter"` + aria）；维度卡 `有数据/缺数据` 徽标；`SectionCard tone` 随状态；来源徽标带 `retrieved_at`；缺口逐项徽标 | `IndustryDetailScreen.tsx:81-102,139,627-651` |

**数字**：缺失一律 `—` + muted + title（`IndustryDetailScreen.tsx:207-213,247-304`，合格）；成分数为 null 时写「成分未知」而非 `0 只`。

### 3.14 产业信号（`/signals`）— 主任务：按规则命中清单下钻到证据

| 项 | 规范 | 代码依据 |
|---|---|---|
| 入口 | **无侧栏入口**，深链 / 从行业页跳转（R-05） | `App.tsx:72` |
| 工具栏 | 标题「产业信号」；subtitle `规则判定基于 R5 指标 · 数据时点 … · 命中 N 条`；右侧「刷新」 | `SignalsScreen.tsx:235-247` |
| 主区 | 3 指标卡（`grid-cols-3 gap-3`）→ 筛选行（`Segmented` + `Select w-56` + `Select w-40` + 计数）→ `desk:grid-cols-12`：左 8 列 `DataTable maxHeight=calc(100vh - 380px)`、右 4 列规则卡 → 证据 `Drawer width="lg"` | `SignalsScreen.tsx:343-423` |
| 六态 | 5 态齐全；空态区分「接口未接入」与「没有命中」；表内 filtered-empty；规则卡有空态 | `SignalsScreen.tsx:252-331,299-311,409,501` |
| 溯源 | 方向 / 强度 / 证据成立与否 `Badge`；抽屉里证据行 + `code break-all` 规则式 | `SignalsScreen.tsx:181-189,448-452,95,520` |

**必须整改**：`payload?.counts.* ?? 0` 与 subtitle「命中 0 条」在 loading/offline/error 路径把缺失渲染成 `0`（R-07 的头号样本，同半句的「数据时点 —」写法正确，自相矛盾）。

### 3.15 涨停样本（`/limit-up`）— 主任务：核对当日涨停池与连板序列

| 项 | 规范 | 代码依据 |
|---|---|---|
| 入口 | 侧栏「验证」分组「涨停样本」；支持 `?date=` 深链 | `Layout.tsx:113-119`、`App.tsx:63` |
| 工具栏 | 标题「涨停样本」；subtitle `date · count 只 · 来源 source_detail`；右侧「刷新」 | `LimitUpScreen.tsx:122-137` |
| 主区 | partial 条 → 单张 `DataTable density=compact maxHeight=calc(100vh - 320px) initialSort=streak desc` → 页脚口径；无卡片 | `LimitUpScreen.tsx:223-250` |
| 六态 | 5 态齐全；「当日涨停池未同步」空态与表内空态分别存在 | `LimitUpScreen.tsx:142-217,238` |
| 溯源 | 只有 `DemoStateBadge` + subtitle 里的 `source_detail`；**信封的 freshness/source_status 未上屏**（`api/limitPool.ts:89-93` 丢弃字段）→ R-01/R-11 | `LimitUpScreen.tsx:128` |
| 时间 | `first_seal_time` 直接显示裸 `HHMMSS`，必须格式化为 `HH:mm:ss` | `LimitUpScreen.tsx:26-27`（违反 §8） |

### 3.16 个股深挖（`/research/:code`）— 主任务：一只票的事实、趋势、风险、估值四件套

| 项 | 规范 | 代码依据 |
|---|---|---|
| 入口 | **无侧栏入口**；`/research/:code`（`App.tsx:75`）；站内入口由本屏自产的链接提供（R-05） | `research/links.ts:27` |
| 工具栏 | 标题 = 名称 ?? 代码 ??「个股深挖」；subtitle `code · date · 个股深挖`；meta「六段齐备 · 更新于…」/「部分段落未完成…」；badge = 演示 `Badge` + `DemoStateBadge`；右侧「刷新」+ 主操作「研究报告」 | `ResearchDetailScreen.tsx:507-560` |
| 主区 | `flex flex-col gap-5`；三行 `grid gap-4 desk:grid-cols-12`，每行 7/5 两卡：公司画像 + 盈利预期 / 财务趋势 + 风险清单 / 估值基准面板 + 估值分位与六段状态；底部 `ProvenanceDrawer` | `ResearchDetailScreen.tsx:648-720` |
| 六态 | 整屏 5 态齐全；段内无 artifact → `StageProblem`（含 CLI 提示 + 去报告页）；预测降级 → 内嵌 `Banner warn`；序列/风险/分位各有空态；**分位为空时是裸 `<p>`** | `ResearchDetailScreen.tsx:562-637,162-192,348,457-460` |
| 溯源 | 每卡 `SourceFoot`「来源 N 条 · 已核实 N 条 · 未核实 N 条」+「查看来源」；叙述走 `MarkdownView` + `[n]` 引用；抽屉携带 `citation` | `ResearchDetailScreen.tsx:194-221,146-150,496-500` |

**已合格**：缺失值全链路 `—`（`facts.ts:75-78`），无任何 0 冒充。
**必须整改**：`-mx-4 -mb-4` 负边距出血（破坏卡片内距节奏，R-17）；`gap-0.5` 2px（R-02）；分位卡与六段状态卡**没有**来源脚注（R-11）。

### 3.17 研究报告（`/research/:code/report`）— 主任务：逐段读六段报告并核来源

| 项 | 规范 | 代码依据 |
|---|---|---|
| 入口 | 深挖页「研究报告」按钮 / 段内跳转 / 深链；`?stage=` 选段（默认第一个失败段，否则 `report`） | `App.tsx:76`、`ResearchReportScreen.tsx:117-120` |
| 工具栏 | `PageHeader` subtitle `code · date · 研究报告`；meta「N 段失败 · N 段待跑」；右侧「刷新」+「个股深挖」（左箭头） | `ResearchReportScreen.tsx:152-199` |
| 主区 | `flex flex-col gap-4 desk:flex-row`：左 sticky 段轨卡（`desk:w-[208px] desk:top-4`，`CardBody p-2`，`StageRail`）+ 右 `Card flex-1`（标题 `${ordinal}. ${stage.title}`，副标题复合串「第 N 段 · 状态 · 降级 · mode=… · 模型=…」）→ 正文 `MarkdownView` → 页脚计数 | `ResearchReportScreen.tsx:271-330,427-448` |
| 六态 + 运行态 | 整屏 5 态；段级四分支：`running` → `RunProgress`（`role="status"` + `role="progressbar"` + `aria-valuenow`）、`failed` → `ErrorBlock` + 失败类别 + 「重试本段」+ 等价命令 + 不可重试说明、`pending` → `EmptyState` +「运行本段」+ 等价命令、`ok` → 降级条 + 正文；`run.error` → `Banner danger` +「再试一次」；轮询 1s 至终态 | `ResearchReportScreen.tsx:55-84,330-424`、`useResearchRun.ts:11,56-84` |
| 溯源 | 每段 `[n]` 引用按钮（仅已核实可点，`aria-label 查看来源 n：dataset`）、`〔未核实〕` 胶囊固定 `bg-warn-soft text-warn`、孤儿 `[n]` 转 muted span；抽屉四元组 endpoint / as_of / 取值 / url（仅 http(s)） | `MarkdownView.tsx:50-79`、`ProvenanceDrawer.tsx:57-131` |

**硬约束**：`〔未核实〕` 由后端补齐，前端不改写（`docs/specs/SPEC-RESEARCH-PROVENANCE.md`）；引用角标写在单位之后（`37.2%[2]`）；段轨 `<1280` 换行成 chips，不得横向滚动。
**必须整改**：`desk:w-[208px]` 任意宽（R-02）、`transition-[width] duration-base` 的归属见 §7.5 与 R-19。

### 3.18 设置（`/settings`，P-07）— 主任务：确认本机控制面状态并改少量偏好

| 项 | 规范 | 代码依据 |
|---|---|---|
| 入口 | 侧栏底部固定项（不进 `NAV_SECTIONS`）；四段 peers / ai / ports / about 走 `?tab=`，切段用 `replace` | `Layout.tsx:316-319`、`SettingsScreen.tsx:74,82-91,1068-1078,1075` |
| 工具栏 | 标题「设置」；subtitle 随状态切换（离线 / 控制面未启用 / `N 个模块 · 运行中 M` / 读取中）；右侧主操作「刷新状态」 | `SettingsScreen.tsx:1178-1207` |
| 主区 | 左 176px 竖排二级导航（`w-menu`，手写 `nav`，**不用** C-08 Tabs）+ 右 `min-w-0 flex-1`：peers 为 `SectionCard bodyClassName="p-0"` 内 `ul gap-3 p-4` 的 `PeerCard`；ai 为表单卡；ports 为 `DataTable`（5 列）+ 配置目录卡（`Badge` chips）；about 为两列 `dl`（`desk:grid-cols-2 gap-x-5`）+ 边界声明 + 外观 `Segmented` | `SettingsScreen.tsx:1399-1405,1280-1389,991-1039` |
| 六态 | 整屏 5 态齐全（`?state=` 支持）；**缺段级状态**：`health`/`stateConfig` 的 loading 不 gate about（行显示 `—`+注）；AI 段无自身 loading/empty；「一键体检」永久禁用；抽屉保存错误只有行内文本 | `SettingsScreen.tsx:965-986,740,1350` |
| 反馈 | 保存/无改动/任务完成/动作成败走 `Toasts`；`dirty` 才允许保存（`disabledReason="没有修改"`）；连通性结果走行内块 | `SettingsScreen.tsx:640-677,907-931,1130-1166` |

**必须整改（观感一致性优先级最高）**：同屏挂了**两套** Toast 栈（屏级 `SettingsScreen.tsx:1473` 与 AI 段内部 `:773,934` 各一份 `useToasts`）→ R-20；Toast 无进出场过渡（`ui/Overlay.tsx`、`ui/Feedback.tsx` 内零 `transition`）而 token 注释承诺 180ms → R-19；离线时仍把假定默认端口 `5950/5951` 当真值展示、失败路径显示「耗时 0ms」、PID 缺失显示「?」→ R-07。

## 4. 状态恢复交互

所有恢复相关提示都在**内容区顶部提示条区**，不弹模态，不阻塞输入。

### 4.1 草稿恢复（info）

> 已恢复 10:32 的草稿（情绪 / 资金 2 段，共 1,284 字）。[查看改动] [清空草稿]

- 时间取分片 `updatedAt`，按本地时区 `HH:mm`；跨天则显示 `10-27 10:32`。
- 字数按去空白后的正文字符数，四舍五入到个位，千分位。
- 「清空草稿」走 §4.3 的确认文案，不使用浏览器原生 confirm。
- 提示条 8 秒后不自动消失（与临时 toast 区分），用户关闭后本次会话不再出现。

### 4.2 快照过期 / 需重取（warn）

| 原因（`needs_refetch_reason`） | 文案 | 操作 |
|---|---|---|
| `ttl` | 快照已超过 TTL（8 分 12 秒），旧值已隐藏 | [立即取数] |
| `trade-date-changed` | 上次快照属于 2026-10-24，与今日不符，需重新取数 | [立即取数] |
| `empty` | 还没有可信快照 | [立即取数] |
| `clock-skew` | 本机时间比快照时间早 2 分 10 秒，请检查系统时间 | [重新校准后取数] |

- 过期时**表格不渲染旧数值**（`WatchScreen.tsx:185` 的 `trustedRows` 机制），提示条与占位卡同时出现，避免用户以为"没数据"而不是"数据过期"。

### 4.3 重置分片二次确认（danger，模态）

标题：**重置本机状态分片**

> 将清除「复盘草稿」（`review_draft`，约 2.0 KB，最后写入 2026-10-28 10:32）。
> 只影响本机浏览器中的该分片，无法撤销；交易日志与持仓不受影响。
> ☐ 我明白该操作不可撤销

按钮：`取消`（默认焦点）/ `清除该分片`（危险实心，勾选后启用）。

- 分片名与体积来自 `ldst state inspect` 的索引；拿不到时写「体积未知」，不猜。
- 多分片批量重置时正文列出全部名称与总大小，按钮文案改为「清除 3 个分片」。
- CLI 侧的 `ldst state reset --store <name>` 保持交互 `y/N`；本规范只统一**文案口径**，不改命令。
- 模态实现必须是 C-16 `ConfirmDialog`（`ui/Overlay.tsx:10-13`），禁止 `window.confirm`。

### 4.4 迁移与丢弃

| 事件 | 样式 | 文案 |
|---|---|---|
| v1→v2 迁移成功 | info | 本机草稿已从旧版本迁移到当前版本（N 个分片）。 |
| 某分片迁移失败被丢弃 | **warn** | 分片 `{store}` 迁移失败（v{from} → v{to}）已丢弃，相关内容需重新填写。 |
| 恢复抛错按空态启动 | danger | 分片 `{store}` 恢复失败。原因：{detail ?? '未知原因'}。已按空草稿启动，可以直接继续填写。 |
| 索引上报不可达 | 不提示 | 只影响 `state inspect` 概况，用户无感；开发态在控制台告警 |

- 丢弃类提示与「已恢复」提示互斥：同一分片不可能既恢复又丢弃。
- **v1.1 校正**：「迁移丢弃」由 v1.0 的 danger 改为 warn，与实现一致（`PersistenceBanner.tsx:24-33`）；判据是它丢的是历史草稿、不阻断当前输入，而 danger 保留给「恢复失败导致本轮从空态启动」。理由见 §12.4-C。

### 4.5 全 18 屏的持久化面（v1.1 新增）

只有下表列出的屏有可恢复状态；其余屏刷新即重取，**不得**假装在持久化。

| 屏 | 持久化载体 | 键 | 依据 |
|---|---|---|---|
| 复盘 | `review_draft` | IndexedDB 分片 | DEC-034 / §4.1 |
| 盯盘 | `watch_snapshot`（TTL 300s）、`watch_settings` | 本机 | DEC-037、`WatchScreen.tsx:180-185` |
| 验证 | `validation_draft`（本地）/ 后端 `validation` 缓存 | IndexedDB | `ValidateScreen.tsx:579-582` 双模文案 |
| 日志 / 我的持仓 | 本机 SQLite | 不经 HTTP 读；写走 DEC-033 | §3.4、§3.11 |
| 设置 | `config/state.yaml` + 偏好 | `ldst.theme`（system/light/dark）、`ldst.density`（compact/default/comfortable）、`ldst.nav.expanded` | `tokens.css:9-12,189-191`、`Layout.tsx:128-130` |
| 其余 10 屏 | 只存 URL 状态（`?date=` `?week=` `?tab=` `?stage=`） | 无本机写入 | `App.tsx` 注释、各屏 hook |

**规范**：主题 / 密度 / 导航展开三键只影响呈现，不得触发数据重取；密度只改 `--ldst-row-h`，不得改其它 token（`tokens.css:189-191`）。

## 5. 降级与溯源

### 5.1 `freshness`（数据新鲜度）

| 值 | 标签 | 色调 | 图形 | 悬浮说明 |
|---|---|---|---|---|
| `realtime` | 实时 | info（蓝） | ● | 数据时间 {as_of}，本窗内实时获取 |
| `cached` | 缓存 | warn（黄） | ◐ | 数据时间 {as_of}，本地缓存回放，非实时 |
| `archive` | 存档 | neutral（灰） | ○ | 历史存档，数据时间 {as_of} |

### 5.2 `source_status`（数据完整性）

| 值 | 标签 | 色调 | 说明 |
|---|---|---|---|
| `ok` | 数据完整 | ok（青绿） | 主工具正常返回 |
| `degraded` | 已降级 | warn（黄） | 同 peer 备工具或缓存回放；悬浮说明用了哪条路 |
| `partial` | 部分缺失 | danger（橙红） | 数据集缺项；必须同时列出缺失项，否则不给结论 |

### 5.3 组合与优先级

- 两枚徽标**并排**显示，不合并成一个"健康度"，因为它们是独立事实（数据新 ≠ 数据全）。
- 提示条颜色取二者最严重者：`partial` > `degraded` > `cached` > `realtime`。**色序不得反转**：`partial` 恒为 `danger`、`degraded` 恒为 `warn`（v1.1 实测 `ReviewScreen.tsx:198` 把两者映射反了 → R-12）。
- `confidence`（high / medium / low）以中性徽标显示「高/中/低置信」，不与涨跌色、状态色混用。
- **颜色不是唯一编码**：每枚徽标都有文字与图形符号；色盲模式（`data-ldst-colorblind`）只换色相，不换形状与文案。

### 5.4 AI 四类错误

| 类别 | 标题 | 说明（脱敏摘要） | 主操作 | 次操作 | 可重试 |
|---|---|---|---|---|---|
| `network` | 网络请求失败 | 服务端摘要，如「连接超时（10s）」 | 重试该段 | 切换备用模型 / 查看 `ldst ai status` | 是 |
| `quota` | 额度或频率受限 | 如「HTTP 429」 | 切换备用模型 | 稍后重试 / 查看 `ldst ai status` | 是 |
| `format` | 结构化输出解析失败 | 如「模型返回不是合法 JSON」 | 重试一次 | 换支持 JSON 模式的模型 / 查看脱敏原文 | 是 |
| `truncated` | 输出被截断 | 如「finish_reason=length」 | 缩小该段输入后重试 | 提高 max_tokens（改配置后重启）/ 导出 Markdown | **否**（原样重试必然再截断，主按钮不写"重试"） |
| `internal`（兜底） | AI 调用未归类失败 | 如「鉴权失败」 | 查看 `ldst ai status` | 检查环境变量中的密钥 | 否 |

**呈现规则**

- 段内错误：卡片内嵌面板，保留段标题与「已保留上一次结果」（若有），提供「重试该段」。
- 页面级错误：顶部红条汇总「5 段中 2 段失败（网络 / 截断）」，点击定位到首个失败段。
- 错误摘要最多 2 行，超出省略并提供「查看详情」；详情只展示脱敏摘要，不展示堆栈、密钥、URL 中的凭据。
- 全局失败时「导出 Markdown」始终可用——这是文档要求的降级路径。
- 实现必须走 C-19/AI 错误件（`components/kit/AiErrorNotice.tsx`）；`degraded` 场景同样用它（`WeeklyScreen.tsx:462-468` 已如此）。

### 5.5 溯源徽标只允许一份实现（v1.1 新增）

`freshness` / `source_status` 的标签表、图形符号（●/◐/○）、严重度排序必须集中在**一个**模块（目标文件 `components/ui/SourceBadges.tsx`，待建 → R-01）。任何屏不得自带 label map。

| 现状 | 位置 | 判定 |
|---|---|---|
| 4 份本地 `SourceBadge` | `HomeScreen.tsx:69-82`、`MarketScreen.tsx:105-118`、`WeeklyScreen.tsx:166-179`、`ReviewScreen.tsx:196-205` | 违规（R-01） |
| 徽标形态不一 | `WatchScreen.tsx:340` 用 `StatusDot` 自写「需重新取数/有效期以内」 | 违规（R-26） |
| 整屏无溯源徽标 | `/news` `/calendar` `/pool` `/positions` `/limit-up` `/industry` `/industry/:name` `/signals` 只有小字来源或全无 | 违规（R-11） |

### 5.6 演示数据必须上屏（v1.1 新增，硬约束）

- 任何由 `?demo=1`（`industry/limitup/research/radar` 的 `demo.ts`）渲染的数据，页面必须显示**演示徽标**，文案「演示数据」+ `tone="warn"`，位置在 `PageHeader` 的 badge 槽。
- `DemoStateBadge`（`PageShell.tsx:47-54`）只反映 `?state=`，`state===null` 直接 `return null`——这是 P0 根因，必须扩成同时反映 `demo`（R-18）。
- 13 张假行业、8 只假成分（含贵州茅台）、假信号在没有 `?state=` 时**目前零标识**（`industry/demo.ts:63-324,526,876-908` 由 `useIndustryView.ts:33-37` 注入），页脚 12px 小字「演示数据：…」不算达标。
- 禁止在真实请求失败（404 / 离线）时自动回落 `demo.ts`（现状合格：`industry/demo.ts:8-9`、`useLimitUpView.ts:20`）。

### 5.7 `freshness` 缺省态（v1.1 新增）

后端未给信封或字段缺失时，显示中性徽标「新鲜度未知」（○ + neutral，悬浮说明「响应未携带 freshness」），**不允许静默不渲染**——静默不渲染会让用户以为数据是实时的。现状：`HomeScreen.tsx:70`、`WeeklyScreen.tsx` 等在无信封时直接 `null`。

### 5.8 溯源与口径的呈现层级

- L2 的 `source_detail` / `data_as_of` / `fetched_at` / `formula` 用 `text-caption text-text-secondary`（12px），可用 `truncate` + `title` 收进一行（模板：`PositionsScreen.tsx:338`）。
- 但「是否可信」属 L0，必须有徽标，**不能只靠页脚小字**（R-11）。
- 后端字段名（`price_source`、`mode`、`fetched_at` 这类 key）**不得直显**，一律映射为 §5.1/§5.2/§8 的中文标签。

## 6. 组件契约

原则：组件只接收数据与回调，**不发请求、不读 store、不写业务判断**；颜色只来自 token（以 `bg-*` / `text-*` / `border-*` 语义类形式出现，见 §7.4）。

### 6.1 现存组件（v1.1 按实际文件重写）

| 码 | 组件 | 文件 | 用途 | 主要使用屏 |
|---|---|---|---|---|
| C-01/C-02 | `Button` `IconButton` | `ui/Button.tsx:7` | 主/次/危险操作；`disabledReason` 承载禁用可见原因 | 全 18 屏 |
| C-03/C-04/C-05/C-06 | `Input` `Select` `Switch` `Checkbox` + `Field` | `ui/Field.tsx:6` | 受控输入，label/hint/error 三段结构 | 设置、雷达、矩阵、信号 |
| C-07 | `Segmented` | `ui/Segmented.tsx:6` | 2–4 选项互斥切换（tab 型选择器） | 市场、矩阵、信号、设置侧栏 |
| C-08 | `Tabs` | `ui/Tabs.tsx:6` | 2px 下划线 + 键盘左右切换 | 设置外的分页场景（当前基本闲置） |
| C-09 | `DataTable` `Column` | `ui/Table.tsx:7,16-24` | **全仓唯一表体**：sticky 表头/首列、三档行高、`aria-sort`、表体内 loading/empty/error | 首页 / 市场 / 日志 / 验证 / 候选池 / 持仓 / 涨停 / 矩阵 / 行业 / 信号 / 设置（13 屏真实使用） |
| C-10/C-11 | `Card` `CardHeader` `CardBody` `CardFooter` `SectionCard` `MetricCard` | `ui/Card.tsx:6` | 容器卡 + 指标卡（`h-metric` 96px、`p-4`、值带 `.ldst-num`） | 全 18 屏 |
| C-12/C-13 | `StatusDot` `Badge` `SignedNumber` | `ui/Badge.tsx:5` | 状态点（含 `ldst-pulse`）、徽标、带向数值 | 全 18 屏 |
| C-14 | `Toasts` `useToasts` | `ui/Feedback.tsx:8` | 轻量成功/失败反馈，自动消失 4000ms | 设置 |
| C-17 | `EmptyState` | `ui/Feedback.tsx:8` | 空态 / 需重取 / 未接入 | 全 18 屏 |
| C-18 | `Skeleton` `SkeletonText` `.ldst-skeleton` | `ui/Feedback.tsx:8`、`styles/index.css:70-90` | 骨架与扫光 | 全 18 屏 |
| C-19 | `ErrorBlock` `Banner` | `ui/Feedback.tsx:8` | 段内错误面板 / 顶部提示条 | 全 18 屏 |
| C-15/C-16 | `Drawer` `ConfirmDialog` | `ui/Overlay.tsx:10` | 宽 480 / 640（`lg`），确认框 420；焦点陷阱 + `Esc` + 焦点归还 | 候选池、信号、研究、设置 |
| C-20 / CH-03 | `Sparkline` `HBarList` | `ui/MiniChart.tsx:4` | 迷你趋势与横向条（值带 `.ldst-num`） | 周度、持仓、市场、研究 |
| CH-01/CH-02 | `LineChart` `BarChart` | `charts/Chart.tsx:10,151` | uPlot；色值必须来自 `chartPalette()`/`cssVar()` | 市场（情绪折线 + 梯队柱） |
| — | `AiErrorNotice` | `kit/AiErrorNotice.tsx` | §5.4 五类 AI 错误 | 复盘、周度 |
| — | `PageHeader` `DemoStateBadge` `OfflineBanner` `ScreenSkeleton` | `PageShell.tsx:24,47,56,80` | 页壳与整屏态 | 全 18 屏 |
| — | `Layout` `PersistenceBanner` | `components/Layout.tsx`、`components/PersistenceBanner.tsx` | 外壳与恢复提示 | 全局 |
| — | `MarkdownView` `ProvenanceDrawer` `StageRail` `LinkButton` `PercentileBar` | `components/research/*` | 叙述渲染 / 引用抽屉 / 六段轨 | 研究两屏 |
| — | `ValuationBasisPanel` | `components/valuation/` | 估值基准 | 个股深挖 |

**v1.0 → v1.1 事实修正**：v1.0 §6 登记的 `kit/Badge.tsx`、`kit/SourceBadges.tsx`、`kit/MetricCard.tsx`、`kit/SectionCard.tsx`、`kit/Banner.tsx`、`kit/EmptyState.tsx`、`kit/SignalRow.tsx`、`kit/ThresholdControl.tsx`、`kit/ConditionCard.tsx`、`kit/DisciplineStat.tsx`、`kit/Gallery.tsx` 共 **11 个文件不存在**（`components/kit/` 只有 `AiErrorNotice.tsx` 与 `index.ts`）。对应能力实际落在 `ui/*`（见上表）或由屏内手写（`SignalRow`/`ThresholdControl`/`ConditionCard`/`DisciplineStat` 无对应件）→ R-01。**待建**：`ui/SourceBadges.tsx`（§5.5）。

### 6.2 表体唯一性

- `Table.tsx:160` 的 `<table>` 是全仓唯一合法的 `<table>` 元素。任何屏内出现第二个裸 `<table>` 即为违规：现存 **1 处** —— `radar/PoolScreen.tsx:125`（R-03）。
- 手写网格行（`ValidateScreen.tsx:585-595`、`HomeScreen.tsx:429-447` 的待办列表、`NewsScreen.tsx:209-259` 的时间线、`MarketScreen.tsx:699-724` 的侧栏列表）允许存在，但必须满足：行高取 `--ldst-row-h*`、hover 用 `bg-surface-3` + `transition-colors duration-fast ease-ldst`、列间 gap 取空间刻度；不满足即违规。
- 手写清单型 `dl`（`HomeScreen.tsx:262`、`SettingsScreen.tsx:991-1001`）与 KPI 卡阵**不是**表的替代品，不得为了"消灭手写"而把键值清单换成表体。

### 6.3 契约缺口（先补组件再谈验收）

| 缺口 | 影响 | 处置 |
|---|---|---|
| C-09 无 `summaryRow` | 候选池抽屉只能自己写第二张表 | R-03，阶段二 |
| C-09 无 `expandable` | 验证条件表无法收进 C-09 | R-03，阶段三 |
| C-09 `Column.width` / `maxHeight` 是自由字符串 | 18 屏写了 22 个魔法像素列宽与 6 个 `calc(100vh - Npx)` | §7.3 定列宽刻度 + R-02 |
| `Button` 无 `as` / link 变体 | 13 处"链接长得像按钮"，丢过渡与字重 | R-09 |
| 无 `SourceBadges` | 4 份本地实现 + 7 屏无徽标 | R-01 |
| `Column.width` 无 1024 档名 | `min-[1024px]:` 只能硬写（`tailwind.config.ts:40-44` 未注册 `bp-min`） | R-04 |

### 6.4 页面状态模型（六态，不是四态）

`lib/pageState.ts:14` 定义 6 个状态：`normal / empty / loading / error / partial / offline`，优先级 `error > offline > partial > loading > empty > normal`，演示统一走 `?state=`（`pageState.ts:17,29-38`）。

- **整屏**按六态判定，由 `PageShell` 承接（`ScreenSkeleton` / `OfflineBanner` / `ErrorBlock` / `EmptyState`）。
- **区块（卡 / 表 / 列表）**必须四态齐全且样式同源：正常、加载（骨架，形状同构）、空（C-17 + 明确空因）、降级/错误（C-19 `Banner` 或 `ErrorBlock variant="banner"`）。
- 区块在"不适用/无数据"时不得整块消失而不作说明（条件出现必须给页脚一句解释，模板 `MarketScreen.tsx:500-504` 的反例见 R-14）。

## 7. 视觉 token

唯一真源 `frontend/tokens.json`（v2.0.0）；`styles/tokens.css` 是生成物（`tokens.css:1-3`）。业务样式**只允许引用变量或对应语义类**，不写裸值。

### 7.1 颜色（v1.1 按生成物全量校正）

深色为 `:root` 默认，浅色由 `:root[data-ldst-theme='light']` 覆盖（`tokens.css:136-187`）。

| 变量 | 深色 | 浅色 | 用途 |
|---|---|---|---|
| `--ldst-bg` | `#0e1116` | `#f5f7fa` | 页面底色 |
| `--ldst-surface` | `#151a21` | `#ffffff` | 卡片 / 表格 / 侧栏 |
| `--ldst-surface-2` | `#1b2230` | `#f0f3f7` | 卡内分区 / 表头 |
| `--ldst-surface-3` | `#202836` | `#edf1f7` | 悬停 / 选中行 |
| `--ldst-sunken` | `#0a0d11` | `#e9eef4` | 输入区 / 代码块 |
| `--ldst-border` | `#2a3342` | `#d8dee7` | 装饰分隔（不承担识别职责） |
| `--ldst-border-strong` | `#5d6c82` | `#8791a0` | 输入框 / 勾选 / 分段控件边界，≥3:1 |
| `--ldst-text` | `#e8ecf2` | `#141a22` | 正文与标题 |
| `--ldst-text-secondary` | `#b4becb` | `#46505e` | 表头 / 字段名 / 说明 |
| `--ldst-text-muted` | `#8a96a6` | `#5f6b7a` | 时间戳 / 单位 / 占位 / 轴标签 |
| `--ldst-text-inverse` | `#0e1116` | `#ffffff` | 浅色块上的反白文字 |
| `--ldst-accent` | `#4c8dff` | `#1a5fd0` | 链接、图标强调、焦点环、图表主线 |
| `--ldst-accent-solid` | `#2f6fe0` | `#1a5fd0` | 主操作按钮填充（深一档以保 ≥4.5:1） |
| `--ldst-accent-solid-hover` | `#245ec7` | `#1348a8` | 主操作按下/悬停 |
| `--ldst-accent-soft` | `rgba(76,141,255,.10)` | `rgba(26,95,208,.08)` | 选中底、区间带、信息块底 |
| `--ldst-accent-contrast` | `#ffffff` | `#ffffff` | 实心按钮文字 |
| `--ldst-up` / `-soft` | `#ff5c5c` / `rgba(...,.12)` | `#c42b1c` / `rgba(...,.10)` | 上涨（A 股红） |
| `--ldst-down` / `-soft` | `#2fbf71` / `rgba(...,.12)` | `#0b7a41` / `rgba(...,.10)` | 下跌（A 股绿） |
| `--ldst-flat` / `-soft` | `#9aa4b2` / `rgba(...,.12)` | `#5f6b7a` / `rgba(...,.10)` | 平盘 / 未知 / 不可用 |
| `--ldst-ok` `-warn` `-danger` `-info` | `#3fb6a8` `#f0b429` `#ff7a45` `#4c8dff` | `#0f7a6e` `#8a5a00` `#b23b12` `#1a5fd0` | 系统状态（不用于数字），各有 `-soft` 底 |
| `--ldst-scrim` | `rgba(4,6,9,.68)` | `rgba(16,24,40,.40)` | 抽屉 / 确认框遮罩 |
| `--ldst-skeleton` / `-shine` | `#1d2530` / `#26303d` | `#e7ecf3` / `#f2f5f9` | 骨架底 / 扫光 |
| `--ldst-focus` | `#6ba1ff` | `#1a5fd0` | 焦点环外圈 |
| `--ldst-chart-*` | 见 `tokens.css:51-59` | 见 `tokens.css:173-181` | 图表专用（grid/axis/band/line/bar-up/bar-down/series-2/threshold/empty） |

**两套色互不复用**：涨跌色只作用于数字/箭头；状态色只作用于徽标/提示条。浅色主题通过 `:root[data-ldst-theme='light']` 覆盖；色盲模式 `:root[data-ldst-colorblind='true']` 把涨/跌换成蓝/橙。

**允许出现色字面量的位置（穷举）**：`styles/tokens.css`（生成物）、`charts/cssVar.ts:11-22` 的 jsdom 兜底与 `:24` 的 `TRANSPARENT`、`tailwind.config.ts` 中 `width.sidebar-rail/menu/drawer/dialog` 等结构尺寸（非颜色）。除此之外业务代码出现 hex/rgba 即为违规——**v1.1 全量扫描结果：18 屏 + `industry/heat.ts` + 各 `demo.ts` 零处真颜色字面量**（QUOT-306 待办里那条"硬编码 hex"是 `IndustryDetailScreen.tsx:410` 的 `&#123;` 实体误报，可直接划掉）。

### 7.2 字体与数字

| 变量 | 值 | Tailwind 类 |
|---|---|---|
| `--ldst-font-sans` | 系统中文栈（PingFang / Microsoft YaHei / Noto Sans SC） | `font-sans` |
| `--ldst-font-mono` | `ui-monospace, SFMono-Regular, JetBrains Mono, Consolas…` | `font-mono` |
| `--ldst-num-font` | `= var(--ldst-font-mono)` | `.ldst-num`（`styles/index.css:42-43`） |
| 字号 | 12 / 14 / 16 / 20 / 28 / 36 px | `text-caption` / `text-body` / `text-subtitle` / `text-section` / `text-page` / `text-hero` |
| 行高 | 1.4（tight）/ 1.5（body）/ 1.6（relaxed） | `leading-tight` / `leading-body` / `leading-relaxed` |
| 字重 | 400 / 500 / 600 | `font-regular` / `font-medium` / `font-semibold` |

所有价格、涨跌幅、金额、计数、时间、序号、分位、代码必须使用 `.ldst-num`（tabular-nums），方向色用 `.ldst-num--up|down|flat` 或 `text-up/down/flat`，**只染数字本身**，不染整行。`--ldst-font-size-display` 为 v1.0 别名，指向 `text-hero`，新代码不用别名（`tokens.css:200-207`）。

### 7.3 间距 / 圆角 / 尺寸 / 层级

| 组 | 变量与值 |
|---|---|
| 间距 | `--ldst-space-0…7` = `0 / 4 / 8 / 12 / 16 / 24 / 32 / 48` px（8px 栅格，4px 半步）；对应 `p-0…p-7` |
| **禁止**的间距 | Tailwind 默认档 `0.5 / 1.5 / 2.5 / 3.5`（2 / 6 / 10 / 14 px）**不在刻度上**，因 `theme.extend.spacing` 与默认值合并而泄漏。实测 **48 处**：`gap-1.5` 17、`mt-0.5` 8、`gap-0.5` 8、`px-1.5` 4、`py-1.5` 3、`mx-0.5` 2、`ml-0.5` 2、`py-0.5` / `p-1.5` / `mr-1.5` / `mb-1.5` 各 1 → R-02 |
| 圆角 | **只有三档**：`--ldst-radius: 8px`（卡片、按钮、输入、下拉、弹层、标签页、抽屉、Toast、图表容器）；`--ldst-radius-xs: 4px`（8px 容器内的嵌套元素：行内胶囊、进度条、迷你图、表内徽标）；`--ldst-radius-pill: 999px`（状态点、计数气泡、分段外壳）。v1.0 的 `4/6/10/14/999` 五档已作废 |
| 结构尺寸 | 侧栏 232 / 图标栏 56 / 顶栏 56 / 控件 32 / 大控件 40 / 图标按钮 32 / 行高 36·38·40 / 指标卡高 96 / 阅读宽 1080 / 表格宽 1296 |
| 容器尺寸 | 二级菜单 176（`w-menu`）、抽屉 480 / 640（`w-drawer` `w-drawer-wide`）、确认框 420（`w-dialog`）、空态最小高 200（`min-h-empty`） |
| **列宽与表高刻度（v1.1 新增）** | 列宽只允许 `{56, 88, 112, 144, 176, 208}`（8px 栅格）：代码 88、名称 176、数值 112、时间 144、徽标 88、操作 56；表体滚动高度只允许 `{240, 320, 360, 420}` 或 `calc(100vh - 280px)` **唯一一个**具名值（建议落成 `--ldst-table-h-*`）。现状实测：**63 处** `width: '<N>px'` 魔法像素列宽 + **4 处** `calc(100vh - Npx)`（280/320/360/380）+ 4 处 `maxHeight="<N>px"` 字面量 → R-02 |
| 层级 | `--ldst-z-sticky 10` / `-banner 20` / `-overlay 40` / `-dialog 50` / `-toast 60`；禁止 `z-[1]` `z-[11]` 这类临时层（R-02） |
| 阴影 | 三档且**唯一用法**：`shadow-card`（卡片，卡片之间不叠阴影，靠 1px border 分界）、`shadow-overlay`（浮层 / Tooltip / Toast）、`shadow-dialog`（抽屉 / 确认框） |
| 焦点 | `--ldst-focus-ring: 0 0 0 2px bg, 0 0 0 4px focus` + `--ldst-focus-offset: 2px` |

### 7.4 取用约定

- 入口一次性 `import './styles/tokens.css'`（生产入口与 `components/ui` barrel 带入，不重复）。
- 禁止在业务样式里写 `#ff5c5c`、`14px`、`p-[13px]` 这类裸值；确需新变量，先改 `tokens.json` → 跑 `scripts/gen-tokens.mjs` → 在本文 7.x 登记。
- **v1.1 校正**：v1.0 说"不使用 Tailwind 类名与本套 token 混用"。实际工程是 Tailwind，且 `tailwind.config.ts` 的 `theme.extend` **全部由 `tokens.json` 生成、不写字面量**，语义类与令牌一一对应（`bg-surface-2` ↔ `--ldst-surface-2`）。所以语义类**就是** token 引用方式，不算混用；真正的禁令是：不得用 Tailwind 自带的默认间距/字号/颜色档（它们绕过 `tokens.json`，主题切换时不跟着变），不得在类名里写 `[...]` 任意值。理由见 §12.4-D。
- 图表色只能经 `charts/cssVar.ts` 的 `cssVar()` / `chartPalette()`，且深浅主题切换后必须重绘（不缓存，`cssVar.ts:1-8`）。

### 7.5 动效（DEC-040 裁定项）

QUOT-306 验收要求"hover/press/展开的过渡在 24–160ms"，而 `--ldst-duration-base` 是 180ms。裁定如下：

| 类别 | 时长 | 适用范围 | 是否在 24–160ms 预算内 |
|---|---|---|---|
| `--ldst-duration-fast` = **120ms** | 交互过渡唯一取值 | hover / focus / press / 展开折叠 / 选中态 / 路由内状态切换 / 骨架进出 | ✅ 是。§11 的过渡验收**只测这一档** |
| `--ldst-duration-base` = **180ms** | **登记为明确例外** | 只作用于浮层进出：C-15 抽屉滑入、C-14 Toast 进出；以及连续值填充（进度条宽度） | ❌ 否，不属于交互过渡，不纳入 24–160ms 验收 |
| 环境类动画 | 非 token | 骨架扫光 1200ms（`styles/index.css:81`）、`StatusDot` 呼吸 1600ms（`:121`）、Toast 自动消失 4000ms（`ui/Feedback.tsx:189`） | ❌ 否，循环/提示类，不参与过渡验收 |

- 缓动统一 `--ldst-ease: cubic-bezier(.2,0,0,1)`，业务代码不得写自定义 easing。
- `prefers-reduced-motion: reduce` 下：两档时长归 0（`tokens.css:193-198`），扫光与呼吸改静态（`index.css:93-105,126`）。
- **不得新增第三个时长刻度**。若某处确实需要 160ms 之外的过渡，先按 DEC-040 的例外登记流程改 `tokens.json`，再在本节说明。
- 现状与例外（v1.1 实测，全部 `duration-base` 命中点）：
  1. `tokens.css:132` 定义，注释口径为"抽屉滑入、Toast 进出"；
  2. `styles/index.css:80` 骨架扫光引用它后立刻在 `:81` 被 `1200ms` 覆盖 → 该引用是死代码，R-19；
  3. `SettingsScreen.tsx:340` 任务进度条 `transition-[width] duration-base` → 连续值填充，属本节例外，**保留**；
  4. `research/ResearchReportScreen.tsx:79` 段运行进度 `transition-[width] duration-base` → 同上，**保留**；
  5. 抽屉与 Toast 目前**完全没有**过渡（`ui/Overlay.tsx`、`ui/Feedback.tsx` 内零 `transition`）→ R-19：要么实现 180ms 进出，要么把 `tokens.json` 的 usage 注释改准。
- 其余过渡全部是 `duration-fast`，已在预算内：`ui/Button.tsx:17,101`、`ui/Field.tsx:13,147,154`、`ui/Segmented.tsx:82`、`ui/Table.tsx:135`、`ui/Tabs.tsx:59`、`Layout.tsx:163,249,261`、`HomeScreen.tsx:437`、`CalendarScreen.tsx:153`、`NewsScreen.tsx:220`、`research/StageRail.tsx:59`、`research/bits.tsx:24`、`SettingsScreen.tsx:393,1221`。
- **无过渡的违规点**（§11 逐屏检查）：`WeeklyScreen.tsx` 全页零过渡；13 处"链接长得像按钮"缺 `transition-colors duration-fast` + `font-medium`（R-09）；`HomeScreen.tsx:111` 指针 `-translate-x-1/2` 无过渡，位置跳变。

## 8. 数字与文案规范

| 场景 | 规则 | 示例 |
|---|---|---|
| 涨跌幅 | 两位小数 + 正负号 + `%`；涨红跌绿并带 ↑↓ | `+4.21%`、`-1.08%` |
| 涨跌停价 | 两位小数；涨停标「涨停」，跌停标「跌停」 | `12.34 涨停` |
| 情绪温度 | 0–100 整数；50 为中性 | `62` |
| 晋级率 | 一位小数 + `%` | `38.5%` |
| 梯队断层 | 整数（断层档位数）；0 表示无断层 | `2` |
| 连板数 | 整数 + 「板」 | `5 板` |
| 金额 | 两位小数 + 千分位 + 单位（万 / 亿） | `1,234.56 万` |
| **缺失值** | 一律「—」（`lib/format.ts:10`），并在悬浮或旁注说明原因；**不得显示 0 / 0.00 / ? / 假定默认值** | `—（未取到涨停池）` |
| 计数类 | 无数据时显示「—」而不是 `0`；计数为 0 且语义上真是 0（如"今日 0 只涨停"）才显示 `0`，且必须与空态分支一致 | `共 — 条` vs `共 0 条` |
| 分位 / 占比 | 一位小数 + `%`；热力分位无数据时**不上色**并 title 说明 | `62.5%`、`无数据（未核实）` |
| 时间 | 当日 `HH:mm:ss`，跨日 `MM-DD HH:mm`；全站本地时区；**后端裸值必须格式化**（`HHMMSS`、ISO） | `10:32:15` |
| 日期 | `YYYY-MM-DD`；周 `YYYY-Www` | `2026-10-28`、`2026-W44` |
| 按钮文案 | 动词开头，2–4 字；破坏性动作用「清除/删除/重置」 | `立即取数`、`重试该段`、`清除草稿` |
| 状态文案 | 先结论后原因 | 「需重新取数：快照已超过 TTL」 |
| 禁用原因 | 禁用按钮必须有可见原因（旁注或悬浮），走 C-01 `disabledReason` | 「15:00 后可对账」「没有修改」 |
| 后端 key | 不得直显；一律映射成中文标签（`realtime`→实时、`cached`→缓存、`archive`→存档、`degraded`→已降级、`partial`→部分缺失） | `缓存 · 已降级` |
| 演示数据 | 必须显式「演示数据」徽标（§5.6），不得只在页脚小字出现 | `演示数据` |
| 截断 | 单行截断必须同时给 `title`（或 Tooltip）；多行用 `line-clamp-2` + `title`；代码/规则式允许 `break-all` | `名称截断…` + hover 全称 |

## 9. 可访问性

- 正文对比度 ≥ 4.5:1，大字号 ≥ 3:1；`--ldst-text-muted` 在 `--ldst-surface` 上已按此校验；`--ldst-border-strong` 承担交互件边界识别，≥3:1（`tokens.css:22`）。深浅两主题都要过检。
- 状态不只靠颜色：`freshness` 用 ●/◐/○ + 文字；涨跌用 ↑↓ + 正负号 + 颜色。
- 焦点永远可见（`--ldst-focus-ring`），禁止无替代的 `outline: none`；「跳到主内容」在首焦点可见（`Layout.tsx:191`）。**v1.1 实测违规**：`main` 上挂着 `focus:outline-none`（`Layout.tsx:328`），skip-link 落地后无可见焦点 → R-21。
- 表格必须有 `caption` 与 `scope="col"`，数字列右对齐（C-09 承担；`Table.tsx:160` 起的表体）；行内可点击必须可键盘到达。
- 图标按钮必须有 `aria-label`；纯装饰图标 `aria-hidden="true"`。
- 模态（C-16）与抽屉（C-15）：焦点陷阱 + `Esc` 取消 + 关闭后焦点回到触发元素（`Overlay.tsx:10-13`）。
- 度量类必须带 ARIA：分位条 `role="meter"`（`IndustryDetailScreen.tsx:81-102`）、进度 `role="progressbar"` + `aria-valuenow`（`ResearchReportScreen.tsx:55-84`）、`aria-busy` 给骨架容器（`PageShell.tsx:82`）、运行中提示 `role="status" aria-live`。
- 提示条使用 `role="status"`（信息）/ `role="alert"`（错误），不打断输入；隐私说明用 `role="note"`（`PositionsScreen.tsx:84-99`）。
- 动效遵循 `prefers-reduced-motion`；扫光与呼吸在减弱动效下改静态占位。
- 引用角标可点必须 `aria-label 查看来源 n：{dataset}`；未核实胶囊是文本而非颜色编码（`MarkdownView.tsx:50-79`）。
- 最低支持视口 1024×768；1024–1279 允许内容下移，**不允许横向溢出**（`main` 之外）。

## 10. 全 18 屏与后端契约映射

| 屏 | 路由 | 主要端点 / 数据来源 | 持久化 | 可信规则 |
|---|---|---|---|---|
| 复盘 | `/review` | `/api/review`、`/api/sentiment`、`/api/ladder`、`/api/control/ai/generate` | `review_draft` | 指标永远只读；AI 段独立降级 |
| 盯盘 | `/watch` | `/api/watch/snapshot` | `watch_snapshot`（TTL 300s）、`watch_settings`（DEC-037） | 过期即不渲染旧值 |
| 盯盘·竞价与盘中 | `/intraday` | `/api/intraday`、`/api/intraday/snapshot`（回环独占） | 无（快照走 `data/intraday/`，不进分片） | 逐时点状态只信 `status.json`；文件不在就写「没有快照」，不补 0（SPEC-INTRADAY-READ） |
| 验证 | `/validate` | `/api/validation`、`/api/control/validation/conditions`、`/api/health` | `validation_draft` / 后端缓存 | 15:00 前不可对账；部分缺失只给参考结论 |
| 日志 | `/journal` | `/api/journal/stats`（本机 SQLite） | 本机库 | 不出本机、不进 AI |
| 首页 | `/` | `/api/sentiment`、`/api/ladder`、`/api/validation`、`/api/watch/snapshot` | 无 | 只读聚合，不自算 |
| 市场 | `/market?tab=` | `/api/sentiment`、`/api/market/history?window=`、`/api/ladder`、`/api/market/theme` | 无 | 三段共用同一交易日；404 不得吞成 null（R-14） |
| 周度复盘 | `/weekly` | `/api/weekly?week=` | 无 | 五段 AI + 事实包；降级可见 |
| 资讯雷达 | `/news` | `/api/news?date=&until=&source=&code=&limit=`（DEC-030） | 无 | 事件由后端规则抽取，页面不二次判定 |
| 事件日历 | `/calendar` | `/api/events?month=&code=`（DEC-030） | 无 | 小节是否有源由后端 |
| 候选池 | `/pool` | `/api/pool?date=&limit=`（DEC-030） | 无 | 分项缺失必须列明 |
| 我的持仓 | `/positions` | `/api/positions?date=`、`/api/positions/*`（读写，DEC-033） | 本机 SQLite | 隐私条常驻；导入视图与真持仓分开 |
| 产业矩阵 | `/industry` | `/api/industry`（`api/industry.ts`） | 无 | 分位口径不得页面改算 |
| 行业详情 | `/industry/:name` | `/api/industry/{name}` | 无 | 五维缺项逐个标注 |
| 产业信号 | `/signals` | `/api/signals?industry=&rule=`、`/api/industry` | 无 | 命中数无数据不得为 0（R-07） |
| 涨停样本 | `/limit-up` | `/api/limit-pool?date=` | 无 | 信封字段不得丢弃（R-01/R-11） |
| 个股深挖 | `/research/:code` | `/api/research/{code}` | 无 | 引用四元组齐全才可点 |
| 研究报告 | `/research/:code/report` | `/api/research/{code}`、`/api/research/jobs/*`（轮询 1s） | 无 | `〔未核实〕`由后端补，前端不改写（SPEC-RESEARCH-PROVENANCE） |
| 设置 | `/settings` | `/api/control/peers`、`/api/control/*`、`/api/health`、`/api/state/config` | `config/state.yaml`；偏好 localStorage | 控制面未接入即全禁用 + 可见原因 |
| 全局 | — | `/api/health`、`/__state/index`（DEC-034） | 索引文件 | 索引只含元数据 |

## 11. 逐屏验收清单

用法：走查时按屏勾选，**每条都必须能判真假**（禁止"更精致""再好看一点"类措辞）。每条不通过就在 PR 评论里写 `屏 · 条目 · 文件:行号 · 期望`。所有屏先在 `?state=empty|loading|error|partial|offline` 五态与 `?demo=1` 下各看一遍，再看真实态。

### 11.0 全局条目（每屏都要过）

- [ ] 视口 1024 与 1440 两档下，`main` 无横向滚动条（C-09 内部滚动除外）。
- [ ] 页面标题每屏唯一且为 `text-page`；卡标题 `text-subtitle`；页脚说明 `text-caption`。
- [ ] 全页无 hex / rgba 字面量（§7.1 的三个允许位置除外）。
- [ ] 无 Tailwind 默认泄漏档（`gap-1.5`、`px-1.5`、`py-0.5`、`gap-0.5`、`mt-0.5` 等）与任意值 `[...]`（`min-[...]` 断点算违规）。
- [ ] 间距与圆角只用 §7.3 的刻度；圆角只出现 `rounded` / `rounded-xs` / `rounded-pill`。
- [ ] 数字（含序号、代码、时间、百分比、计数）全部 `.ldst-num`；缺失值全部「—」，无 `0` / `0.00` / `?` / 假定默认值冒充。
- [ ] 后端 key 不直显；状态标签与 §5.1/§5.2 标签表逐字一致。
- [ ] `?demo=1` 有「演示数据」徽标；`?state=` 有 `DemoStateBadge`。
- [ ] 交互过渡只有 `duration-fast`（120ms）；无 >160ms 的 hover/press/展开过渡；`prefers-reduced-motion` 下无动画。
- [ ] 表体只有一处（C-09）；本屏无裸 `<table>`。
- [ ] 单行截断处均有 `title` 或 Tooltip；`min-w-0` 与 `truncate` 成对出现。
- [ ] 键盘可达：可点行/可点卡能 Tab 到、Enter 触发；焦点环可见。
- [ ] 区块四态齐全（正常/骨架/空/降级或错误），骨架与真实内容形状同构（列数一致）。

### 11.1 复盘 `/review`

- [ ] 指标卡三列等宽，`<1024` 单列；数值右上角有溯源徽标，且来自统一实现。
- [ ] `source_status=partial` 显示 `danger`、「部分缺失」；`degraded` 显示 `warn`、「已降级」（当前 `ReviewScreen.tsx:198` 相反 → 修复后本条必须为真）。
- [ ] 五段顺序为情绪→资金→题材→龙虎榜→龙头；`desk:`（≥1280）下每段内左右 40/60（`ReviewScreen.tsx:763`），`rail:` 下上下堆叠且 AI 在上。
- [ ] 三张指标卡在 `rail:` 下为单列、`desk:` 下等宽三列（`ReviewScreen.tsx:666`）。
- [ ] 刷新后草稿恢复出现 §4.1 提示条；`truncated` 类错误不出现「重试」主按钮。
- [ ] 指标卡无可编辑入口；AI 区不出现指标样式的数值。

### 11.2 盯盘 `/watch`

- [ ] `needsRefetch` 为真时表格零旧值，且 §4.2 四类原因文案逐条对得上。
- [ ] 剩余 TTL、取数时间、快照来源三项均 `.ldst-num` + 「—」缺省规则。
- [ ] 阈值控件非法输入：红边 + 行内原因 + 不保存；合法步进 0.5、区间 1.0–10.0。
- [ ] 空流仍显示「最近一次扫描 HH:mm:ss，暂无信号」。
- [ ] 可信状态徽标与 §5.1 同源（同一标签、同一 ●/◐/○），不再是 `StatusDot` 自写文案。

### 11.3 验证 `/validate`

- [ ] 「明日条件」与「收盘对账」两模切换时 L0 文案变化，草稿不丢。
- [ ] 15:00 前对账按钮禁用且原因可见；确认后显示确认时间；未确认行左侧黄条。
- [ ] 条件表行高取 `--ldst-row-h*`；hover 有 `bg-surface-3` + `duration-fast`；表头 sticky。
- [ ] 退出码 8 时黄条列出缺失数据集名，不给结论式断言。

### 11.4 日志 `/journal`

- [ ] 隐私条常驻、无网络请求、无 AI 内容、无分享入口；导出走二次确认并说明文件去向。
- [ ] 样本不足显示「样本不足」而不是 `0%`（少于 5 笔）。
- [ ] 与持仓共用同一 `PrivacyNotice` 实现（R-25 修复后本条为真）。

### 11.5 首页 `/`

- [ ] 摘要文案与当前状态一一对应（四种：数据完整 / 部分降级 / 未连接 / 取数失败），加载中出现的是「读取中…」而非 `—`。
- [ ] 5 张指标卡全部带溯源徽标，包括「主线题材」卡（当前缺失）。
- [ ] 情绪温度条的指针位移带 `transition-*`（当前无过渡，位置跳变）。
- [ ] 待办行、连板行、异动行的 `truncate` 都配了 `title`。
- [ ] `derived==='empty'` 时走明确空态（今日无数据 + 引导），而不是渲染整屏「—」。
- [ ] 重复展示的区块（情绪 / 连板 / 题材 / 异动 / 待办）与来源屏使用同一组件与同一 token，无本地复制样式。

### 11.6 市场 `/market`

- [ ] 三个入口的高亮与 `?tab=` 一致；切 tab 产生历史记录，改日期不产生。
- [ ] `min-[640px]:grid-cols-3` 换成 `rail:`/`desk:` 或新登记的档位；日期字段宽度落在 §7.3 刻度上。
- [ ] 折线线宽 1.5px（非 2）、网格 1px、轴标签 12px、颜色全部来自 `chartPalette()`。
- [ ] 三段各自的「未同步」空态含 `ldst sync --date` 与「重新检查」；theme 段不得跳过空态。
- [ ] 每段有 `SourceLine`（来源 / 数据时间 / 取数）；段级 404 必须有可见降级，不许静默变 null。
- [ ] 指标卡 note 中的数字（含 `—`）走 `.ldst-num`。

### 11.7 周度复盘 `/weekly`

- [ ] `min-[1024px]` / `min-[1100px]` 清零。
- [ ] `SourceBadge` 使用统一标签表：`realtime` 显示「实时」、`archive` 显示「存档」，页面上**不出现英文 key**。
- [ ] 每日序列表空态使用 C-17 `EmptyState`，不是裸文本。
- [ ] empty 分支仍渲染指标卡阵（骨架或「—」），与其它屏同构；不得只剩标题 + 一条横幅。
- [ ] 本周龙头行、待验证项行有 `truncate` + `min-w-0` + `title`。
- [ ] 五段卡的 `AiErrorNotice`、`Badge 降级`、`Banner warn` 三者只在真降级时出现，正常段不显示降级痕迹。
- [ ] 页内至少一处 `duration-fast` 过渡存在（当前整页零过渡）。

### 11.8 资讯雷达 `/news`

- [ ] 三列宽度取自 §7.3 刻度（当前 `184/340/200/400` 为违规）。
- [ ] subtitle 的「共 N 条 · 已抽取事件 N」在无数据时为「—」，非 `0`。
- [ ] 时间线行是行组件或 C-09，行高与 hover 与其余列表一致；标题截断 + `title`。
- [ ] 「原文链接」使用统一链接件（C-01 的 link 变体），有 `transition-colors duration-fast`。
- [ ] `gap-1.5` / `px-1.5` 清零；来源列的降级状态用 §5.2 徽标而不是黄字。

### 11.9 事件日历 `/calendar`

- [ ] 月格高度与内距落在刻度上（`min-h-[76px]`、`p-1.5`、`gap-0.5` 清零）。
- [ ] 空白天显示可辨的"无事件"底纹或占位（当前纯空白）；今日/选中态区分不只靠颜色。
- [ ] 每日面板事件行代码/标题截断 + `title`；chip 上限 2 + `+N` 规则保持。
- [ ] header 的「N/N 个小节有源」在无数据时为「—」。
- [ ] 小节状态徽标三档文案与 §5.2 完全一致（含"降级"）。

### 11.10 候选池 `/pool`

- [ ] 抽屉内不再有第二个裸 `<table>`（改用 C-09 + `summaryRow`，R-03）。
- [ ] 抽屉权重列缺失显示「—」而不是 `0.0%`；「原始输入」单元格数字套 `.ldst-num`。
- [ ] 表体高度不再用 `calc(100vh - 360px)` 魔法值；列宽落在 §7.3 刻度。
- [ ] 名称列 `truncate` 必须配 `title`；行可点（Enter）且 `onRowClick` 有键盘等价。
- [ ] 导出按钮标注"只写本机"，且导出成功/失败有反馈。

### 11.11 我的持仓 `/positions`

- [ ] 隐私条（`role="note"`）常驻且位于所有消息条之上。
- [ ] header「N 只」无数据时为「—」；缺失金额一律「—」，无手写裸 `—` 字符串（应引用 `MISSING`）。
- [ ] 估值 5 卡在 empty/loading 下有骨架或「—」+注，不得空白；导入视图同样给出估值说明。
- [ ] `HBarList` 数值走格式化函数（当前 `v.toFixed(2)` 绕过 `format.ts`）。
- [ ] 行业列 `truncate` 配 `title`；`max-w-[140px]` 落在刻度上。
- [ ] 新增/编辑走 C-03…C-06 受控件 + C-16 确认删除；无原生弹窗。

### 11.12 产业矩阵 `/industry`

- [ ] 热力格颜色只由 `heat.ts` 的 `color-mix` 生成；缺失格不上色且 `title` 为「无数据（未核实）」。
- [ ] 列宽（`176/136/110/96`）落在 §7.3 刻度；表高不再是 `calc(100vh - 280px)`。
- [ ] 行业名列有 `title`（当前只有 `truncate`）；筛选行 `Select w-32` / `Input w-56` 落在刻度。
- [ ] 页脚「演示数据：…」升级为 §5.6 的演示徽标。
- [ ] 图例卡 `读图与口径` 的分位口径描述与 `heat.ts:15-26` 一致（并列取中值、全等 0.5、空为「—」）。

### 11.13 行业详情 `/industry/:name`

- [ ] 左 8 / 右 4 分栏在 `desk:` 生效，`rail:` 下上下堆叠且不横向溢出。
- [ ] 五维卡阵 `grid-cols-2 gap-3` 在窄屏降为单列；卡内数值全部 `.ldst-num`。
- [ ] 分位仪表 `role="meter"` + `aria-valuenow` 存在且值缺失时不显示百分比。
- [ ] 溯源与缺口卡无内容时给出"本维度无数据源"的空态，而不是永远渲一张空壳。
- [ ] 成分股名称/代码 `truncate` + `title`；表高 `360px` 落在刻度。
- [ ] `?demo=1` 时有演示徽标（当前 13 张假行业无任何标识）。

### 11.14 产业信号 `/signals`

- [ ] subtitle「命中 N 条」与三张指标卡数值在无 payload 时为「—」，**不得为 0**（当前 `?? 0` 直接违规）。
- [ ] 表列宽（`96/132/56/56/56/92/72`）与表高（`calc(100vh - 380px)`）落在刻度。
- [ ] 方向/强度徽标文案是中文标签；`line-clamp-1` 的行配 `title`。
- [ ] 证据抽屉里 `Badge 成立/未成立` 与后端字段一一对应，规则式 `code` 用 `.ldst-num`。
- [ ] 「接口未接入」与「没有命中」两种空态文案不同（现状已不同，回归时不得合流）。

### 11.15 涨停样本 `/limit-up`

- [ ] `first_seal_time` 显示为 `HH:mm:ss`，不再是裸 `HHMMSS`。
- [ ] 封板时间列宽/表高落在刻度；涨停原因列有截断 + `title`（当前不截断，长文本溢出）。
- [ ] 信封 `freshness`/`source_status` 上屏（不再被 `api/limitPool.ts:89-93` 丢弃）。
- [ ] 「当日涨停池未同步」空态与表内空态不混用。
- [ ] `?demo=1` 有演示徽标。

### 11.16 个股深挖 `/research/:code`

- [ ] 三行 7/5 分栏在 `desk:` 生效，`rail:` 单列；无 `-mx-4 -mb-4` 负边距出血。
- [ ] 分位卡与六段状态卡补上来源脚注（与其余四张同规格）。
- [ ] 每个 `Fact`/`MetricCard` 数值缺失都是「—」+「本段未提供」（当前已合格，回归项）。
- [ ] `min-[1024px]:` 清零；`gap-0.5` 清零。
- [ ] 分位为空时用 C-17，不用裸 `<p>`。
- [ ] 演示徽标 + `DemoStateBadge` 同时出现时不重叠、不换行错位。

### 11.17 研究报告 `/research/:code/report`

- [ ] 段轨宽 `desk:w-[208px]` 换成刻度值；`<1280` 换行为 chips 且无横向滚动。
- [ ] 六段顺序固定，`aria-current="step"` 在选中段上；段序号 `01`–`06` 用 `.ldst-num`。
- [ ] `running` 时 `role="progressbar"` + `aria-valuenow` 存在，百分比 `.ldst-num`；宽度过渡属连续填充（`duration-base` 允许，见 §7.5）。
- [ ] `failed` 段给出：失败类别、等价命令、「重试本段」；`retryable===false` 时不出现「重试」主按钮。
- [ ] `pending` 段给出「运行本段」+ 等价命令；`ok` 段若有降级必须有 `Banner warn` + `Badge 降级`。
- [ ] `[n]` 仅已核实可点、`aria-label` 完整；孤儿 `[n]` 转 muted 文本；`〔未核实〕` 使用 `bg-warn-soft text-warn`，不新增色。
- [ ] 引用抽屉四元组齐全，缺失项显示「—」；URL 仅 http(s)。

### 11.18 设置 `/settings`

- [ ] 同屏只有一个 Toast 栈（合并 `SettingsScreen.tsx:773,934,1473`）。
- [ ] `?tab=` 四段：切段用 `replace`（现状一致，回归项）；每段有自身 loading/empty/error。
- [ ] 二级导航 176px（`w-menu`）与控件高 32px 全部来自刻度；外观选择器用 C-07。
- [ ] 离线时端口卡片不显示假定默认值 `5950/5951`，改「—（控制面未接入）」；PID 缺失显示「—」不是「?」；失败路径不显示「耗时 0ms」。
- [ ] 所有输入走 `Field` + C-03/C-04/C-05/C-06；`dirty` 才允许保存，禁用原因可见（「没有修改」）。
- [ ] 「一键体检」永久禁用时必须给出可见原因。
- [ ] 主题 / 密度切换后：深浅两主题下本屏对比度与徽标颜色均正确，密度只改行高。
- [ ] 抽屉保存错误从纯行内文本升级为 C-19 `Banner danger` + 保留可编辑内容。

## 12. 遗留与下一步

1. **阈值持久化**：已由 DEC-037 落地 `watch_settings` 分片（`config/state.yaml` + `stores/manifest.json` + 契约测试三处同步），刷新 / 重启 / 多窗口共用同一份。
2. **生产态 `/__state/index`**：已由 DEC-034 落地（`src/ladderstock/web/state_index.py`，`ldst run` 同路径 GET 读回 / POST 上报，注册在 SPA 兜底之前，语义照抄 Vite 中间件），开发态索引不再是唯一通道。
3. **设计还原度**：前端实现后按 §11 **逐屏**走查（不再是四屏抽查）；组件与 token 的偏差需在 PR 评论中说明理由，不静默例外。走查机制见 §12.6。

### 12.4 v1.1 规范变更与理由（逐条）

| 编号 | 原条款（v1.0） | v1.1 裁定 | 理由 |
|---|---|---|---|
| A | 标题「（四屏）」、§0 覆盖四屏 | 扩为 18 屏，§0 出逐屏表 | 产品已 18 屏，后 14 屏无基线即无验收依据（QUOT-306 根因 1） |
| B | §2「<1024 侧栏折叠为顶部横向导航；<720 表格转卡片列表」 | **删除**，最低支持视口定为 `--ldst-bp-min: 1024px` | 代码从未实现（`Layout.tsx:194` 只有 232/56 两档）；保留一个不可能达标的条款会让 §11 无法判真假 |
| C | §4.4 迁移丢弃 = danger | 改 warn，文案对齐实现 | 丢的是历史草稿、不阻断当前输入；danger 保留给"本轮从空态启动"（`PersistenceBanner.tsx:24-33`） |
| D | §7.4「不使用 Tailwind 类名与本套 token 混用」 | 改为「语义类即 token 引用；禁用 Tailwind 默认档与任意值」 | `tailwind.config.ts` 的 `theme.extend` 全量由 `tokens.json` 生成，语义类与令牌一一对应；真风险是默认档泄漏与 `[...]` 任意值 |
| E | §2 标题层级 22px/16px | 改为 token 阶梯 28/20/16/14/12/36 | 与 `tokens.css:67-78` 冲突；按本文"唯一真源"约定，CSS 胜出并回改文档 |
| F | §7.1 色表（`surface-raised`/`surface-sunken`、`border-strong #3c4759`） | 换成 v2 全量色表（surface-2/3、sunken、accent-solid 族、chart 族等） | v1.0 抄的是 token v1；`tokens.css:200-207` 只剩别名兼容 |
| G | §7.3 圆角「4/6/10/14/999 五档」 | 收敛为 8/4/999 三档 | `tokens.json` v2 已收敛；5 档会让卡片圆角各写各的 |
| H | §7.2 字号「12/13/14/16/22/28」、行高含 1.25 | 12/14/16/20/28/36、行高 1.4/1.5/1.6 | 同上，与生成物一致；13px 与 1.25 在系统里不存在 |
| I | §7.3 动效「120ms / 180ms」一句带过 | 拆成 §7.5 三类分工并裁定冲突 | DEC-040：交互过渡只用 120ms；180ms 登记为浮层与连续填充的例外 |
| J | §6 组件清单（`kit/*` 11 个文件、`Gallery.tsx`） | 按实际 `ui/*` + 契约码重写，标出待建件 | 登记的文件不存在；不改文档就等于"规范说 A 代码是 B" |
| K | §9 未规定最低视口与度量 ARIA | 补 1024×768 基线 + `role=meter/progressbar/aria-busy` | 新屏已经在用（`IndustryDetailScreen.tsx:81`、`ResearchReportScreen.tsx:55`），规范不写就无法验收 |
| L | §11「四屏清单」10 条 | 逐屏 18 节 + §11.0 全局 | QUOT-308 交付目标 |
| M | §10 四屏映射 | 18 屏映射 | 新屏的数据来源与可信规则此前无处可查 |
| N | §2「内容最大宽 `--ldst-content-max: 1080px`，超出居中」 | 分层：`main` = `--ldst-table-max: 1296px`（表/图/卡阵），阅读型正文再收 `1080px` | 实际容器是 `max-w-table`（`Layout.tsx:328`）；1080 只该管行长，硬套给整屏会让表格屏白白缩掉 216px |
| O | §3.1 五段「`≥1100px` 内左右两栏」 | 改为 `desk:`（≥1280），并去掉 1024 单列的说法 | 系统里没有 1100 档；实现是 `desk:grid-cols-[2fr_3fr]`（`ReviewScreen.tsx:763`）与 `grid-cols-1 desk:grid-cols-3`（`:666`） |

### 12.5 整改清单（v1.1 登记，代码侧待办；本轮不改代码）

编号 R-01…R-26，每条都是"现状与 §n 冲突"，必须在阶段二/三落地或按 §12.4 反向修订规范。

| 编号 | 冲突 | 现状位置 | 违反条款 | 建议阶段 |
|---|---|---|---|---|
| R-01 | 溯源徽标 4 份本地实现，无统一件 | `HomeScreen.tsx:69-82`、`MarketScreen.tsx:105-118`、`WeeklyScreen.tsx:166-179`、`ReviewScreen.tsx:196-205` | §5.5 | 二 |
| R-02 | 任意值与默认泄漏：**63** 处 `width:'Npx'` 列宽、**4** 处 `calc(100vh - Npx)` 表高、**4** 处 `maxHeight="Npx"`、**19** 处 `[...]` 任意值 utility、**48** 处 Tailwind 默认 `.5` 间距 | 各屏（示例：`PoolScreen.tsx:224-270,431`、`PositionsScreen.tsx:248-336`、`CalendarScreen.tsx:135-167`、`ui/Card.tsx:102` 的 `w-[3px]`、`WeeklyScreen.tsx:381`） | §7.3、§11.0 | 二 |
| R-03 | C-09 缺 `summaryRow` / `expandable`，导致裸表与手写网格 | `PoolScreen.tsx:125`、`ValidateScreen.tsx:585-595` | §6.2、§6.3 | 二 |
| R-04 | `min-[1024px]`×6、`min-[1100px]`×3、`min-[640px]`×1 绕过 `rail:`/`desk:`；`tokens.json` 的 `breakpointMin` 未注册档名 | `WeeklyScreen.tsx:381,506,554`、`MarketScreen.tsx:529`、`ResearchDetailScreen.tsx:94,262,394` | §2、§7.3 | 二 |
| R-05 | 5 屏无导航可达路径（只能改地址栏） | `/industry` `/industry/:name` `/signals` `/research/:code` `/research/:code/report` | §2 | 二 |
| R-06 | 后端 key 直显 | `WeeklyScreen.tsx:176`（`realtime`/`archive`） | §5.8、§8 | 二 |
| R-07 | 用 `0` / 假定值冒充缺失 | `SignalsScreen.tsx:237,347`、`NewsScreen.tsx:437`、`CalendarScreen.tsx:374`、`PoolScreen.tsx:156,289`、`PositionsScreen.tsx:636`、`WeeklyScreen.tsx:520,543`、`SettingsScreen.tsx:541,804,1346` | §8 | 二 |
| R-08 | 数字未套 `.ldst-num` | `PoolScreen.tsx:161-163`、`HomeScreen.tsx:519`、`WeeklyScreen.tsx:339-369`、`MarketScreen.tsx:538-571`、`ResearchDetailScreen.tsx:96,209`、`PositionsScreen.tsx:179,205` | §7.2 | 二 |
| R-09 | 链接长得像按钮，丢过渡与字重（13 处） | `NewsScreen.tsx:359`、`research/bits.tsx`、`PoolScreen.tsx:98` 等 | §7.5 | 二 |
| R-10 | 截断无 `title`（长名称/代码） | `NewsScreen.tsx:244`、`PoolScreen.tsx:237`、`HomeScreen.tsx:149,440,501`、`IndustryMatrixScreen.tsx:236` | §8 | 二 |
| R-11 | 整屏缺溯源徽标 / 只在页脚小字 | `/news` `/calendar` `/pool` `/positions` `/limit-up` `/industry*` `/signals`；`ResearchDetailScreen.tsx:439-469,687-705` 两卡无来源脚 | §5.5、§5.8 | 二 |
| R-12 | 色序反转（`partial`→warn、`degraded`→danger） | `ReviewScreen.tsx:198` | §5.3 | 二 |
| R-13 | 首页重复渲染他屏区块且样式各写一套 | `HomeScreen.tsx:372-514` vs 市场/盯盘/验证 | §1-7 | 二 |
| R-14 | 404 吞成 null；theme 段空态被抑制 | `MarketScreen.tsx:307-322,500-504` | §3.6、§6.4 | 二 |
| R-15 | 整屏 empty 只出横幅，不出卡阵（与其它屏不对称） | `WeeklyScreen.tsx:318-337` | §6.4 | 三 |
| R-16 | 表内空态是裸文本不是 C-17 | `WeeklyScreen.tsx:502` | §6.4 | 二 |
| R-17 | 负边距出血破坏卡片内距节奏 | `ResearchDetailScreen.tsx:280,326,371,432` | §7.3 | 三 |
| R-18 | `?demo=1` 无演示徽标（7 屏），水印只认 `?state=` | `PageShell.tsx:47-54` | §5.6 | 二（P0） |
| R-19 | `duration-base` 三处口径不实：扫光死引用、抽屉/Toast 零过渡 | `styles/index.css:80-81`、`ui/Overlay.tsx`、`ui/Feedback.tsx` | §7.5 | 二 |
| R-20 | 同屏两套 Toast 栈 | `SettingsScreen.tsx:773,934,1473` | §3.18 | 二 |
| R-21 | ~~skip-link 目标无可见焦点（`focus:outline-none`）~~ **实测不成立**：`focus:outline-none` 只压 `outline`，压不住 base 层 `:where([tabindex]):focus-visible` 的 `box-shadow: var(--ldst-focus-ring)`；真实键盘事件（Tab → Enter）下 `#main` 命中 `:focus-visible` 且环已绘制（QUOT-316 取证）。`main` 上的类**保留**，它挡的是非键盘 focus 时的 UA 默认描边 | `Layout.tsx` `main#main`、`styles/index.css:30` | §9 | 已结案（非缺陷） |
| R-22 | `freshness` 缺省静默不渲染 | `HomeScreen.tsx:70`、`WeeklyScreen.tsx` | §5.7 | 二 |
| R-23 | 折线线宽 2px，token 注释口径 1.5px | `charts/Chart.tsx:61` vs `tokens.css:54` | §3.6 | 二 |
| R-24 | 叙述正文无度量上限（`flex-1` 卡内行长不可控） | `MarkdownView.tsx:130`、`ResearchReportScreen.tsx:329` | §2 | 三 |
| R-25 | 隐私说明只持仓有 | `PositionsScreen.tsx:84-99`（日志缺） | §3.4 | 三 |
| R-26 | 盯盘可信状态与徽标不同源 | `WatchScreen.tsx:340-341` | §3.2、§5.5 | 三 |

### 12.6 走查机制（让 §11 真的被执行）

1. **勾选进 PR**：改动任一屏的 PR，描述里必须贴该屏的 §11.x 全清单，逐条 `[x]` 或在下一行写 `FAIL: 文件:行号 + 原因`。缺清单的 PR 视为未过 §12.3。
2. **截图对**：每条视觉项配 before/after（暗 + 亮，1440×900，来自真实 `ldst run` / `npm run dev`）入库 `docs/screenshots/`（由 QUOT-307 建的巡检脚本产出）。
3. **自动化优先**：§11.0 的"零裸表 / 零 hex / 零默认泄漏档 / 零 `min-[...]` / 数字必 `.ldst-num`"五类可由 grep 脚本判定，应做成一条 CI 检查（阶段三，由 QUOT-317 承接）；能自动判的一律不靠人眼。
4. **新增屏幕的门槛**：新屏合入前必须在同一 PR 里补 §0 表行、§3 小节、§10 映射行、§11 清单，否则视为未完成。

### 12.7 与相邻任务的关系

- QUOT-307（视觉巡检最小版 + 18 屏 before 截图）：§11 的截图证据依赖它，分支 `agent/agent/c2e06ff2f397`。
- QUOT-309（逐屏粗糙点清单，315 条）：与本文件**互补**——它按"屏 × 维度"枚举证据，本文件按"条款"给规范。§12.5 的 R 编号是本文件的整改面，不与 QUOT-309 的 M 编号合并。
- QUOT-300 / QUOT-304（导航补入口与分组）：QUOT-304 已按 DEC-044 完成「我的股票」分组，本规范 §2 同步到新结构；后续再动 `NAV_SECTIONS` 必须先改决策与本节。
- `docs/specs/SPEC-RESEARCH-PAGES.md` / `SPEC-RESEARCH-PROVENANCE.md`：§3.16–§3.17 与其口径一致，冲突时以这两个 spec 的**后端契约**为准、以本文件的**视觉 token** 为准。
- 交付位置说明：本文件与 `docs/decisions/DEC-040-*.md` 都在受版本管理的目录内；`docs/tasks/` 被 `.gitignore:48` 排除（DEC-0026），故本次产出不放 `docs/tasks/`。
