# SPEC-HOME-V2 首页 v2 版式（一页工作台：两行五卡 · 合并待办异动 · 研究面板 · 紧凑自选持仓）

- 版本：v2.2（2026-11-13，QUOT-573 / DEC-083：§3.1 对话通道改为 `POST /api/page/assistant` + 单轮无状态 + `pending_research` 边界）：
- 前版：v2.1（2026-11-11，QUOT-552 版式 v2.0 + QUOT-560 / DEC-081 港股两格接真实源合流）：
  QUOT-552 一页工作台改版——两行五卡（8+4 / 4+4+4），1280×800 一屏装下；
  待办+异动合并 1 表（类型列区分）；研究面板三页签（快捷入口 / 研究股票 / 搜索历史）+ 底部唯一输入框
  回车路由（问句→AI、代码→行情直达、名称→搜索面板）；自选 / 持仓首页压 3 列摘要 + 合计行 + 跳转全表页；
  隐私长文案收进卡头「仅本机」徽标（全状态常驻）；持仓「毛额·未扣费」徽标。
  QUOT-560（DEC-081）港股两格接入真实源——`index_quote_hk` / `tickhub_us` 逐 symbol 取
  （`HSI.HK` / `HSTECH.HK`），零 key Tier C 非商用；「源未接入」占位与「`ok` 不可达」
  两条 QUOT-471 临时口径废止，8 格全有值时 `ok` 首次可达。
  只改首页版式与装配、港股两格数据源接入：数据口径、接口契约、搜索栈公开签名零改动。
  v2.0 为 QUOT-552，v1.5 为 QUOT-560（DEC-081），v1.4 为 2026-11-11 QUOT-551（B 块三路径 clarity），
  v1.3 为 QUOT-531 / QUOT-521，v1.2 为 2026-11-07 QUOT-471 / QUOT-472）
- 修订：2026-11-11 QUOT-552 —— §1 骨架改两行五卡（行距 gap-3）、§3 改研究面板（三页签 + 唯一输入框）、
  §4 / §5 改 3 列摘要（隐私徽标在卡头操作区、快照 / 估值时间收进一行页脚）、§6 改合并待办异动表、
  §5.4 隐私改徽标承载、§9 验收重写、§12-L06 / L07 裁定更新、新增 §12-L09 / L10 / L11
- 修订：2026-11-11 QUOT-560 —— §2.1 线框、§2.2 字段表、§2.3 状态矩阵、§10 交付表随港股接源更新
- 历史修订：2026-11-07 QUOT-469（页头只报屏级态）/ QUOT-472（AI 研究对话入口）；
  2026-11-10 QUOT-531（工具快捷入口）/ QUOT-521（卡级 ⟳）；2026-11-11 QUOT-551（三路径 clarity）
- 日期：2026-11-03（v1.0 首版）/ 2026-11-11（v2.1）
- 归属：UI/UX（版式与状态规格）；**本文件不改数据口径、不改接口契约**
- 实现落点：`frontend/src/screens/HomeScreen.tsx`、`frontend/src/screens/home/`
  （沿用现有拆分方式：屏体只装配，块级件与取数 hook 各自独立文件）
- 代码事实基准：QUOT-552 分支（基于 main `c62e84a`，含 QUOT-551 PR #211）
- 视觉唯一真源：`frontend/tokens.json` → `styles/tokens.css`（`--ldst-*`）。
  **本规格零新增 token、零新依赖、零新断点**；引用不到刻度上的尺寸一律改用现有刻度或改结构。
- 相关：`docs/UI-SPEC.md` §2 / §3.5 / §5 / §6 / §7 / §8 / §11.5、DEC-033（持仓边界）、
  DEC-038（成本法口径）、DEC-045（变更记录合并规则）、DEC-006（生态边界闸门 4：无源不自采）、
  `SPEC-MY-POSITION`、`SPEC-RADAR-PAGES` §6.1

### 变更记录

| 版本 | 日期 | 变更 | 任务 |
|---|---|---|---|
| v1.0 | 2026-11-03 | 首版：A 块按 4 格（A 股四指数）画，美股 / 港股标注「待核实」 | QUOT-378 |
| v1.1 | 2026-11-07 | A 块改为恒渲染 8 格：新增 2 美股格（`index_quote_us` / `tickhub_us`）、2 港股格（无合规源 →「源未接入」）；§2.1 线框、§2.2 字段表、§2.3 状态矩阵随实现更新；`ok` 状态在港股接入前不可达 | QUOT-471 |
| v1.2 | 2026-11-07 | B 块改为 AI 研究对话入口（起步问题 / 多行输入 / 发送·停止·清空 / 本轮来源）；搜索能力完整保留在「不经过 AI，直接搜」分区；§9 B 块验收清单重写；§12-L01 裁定、新增 §12-L08（无 research-start 接口） | QUOT-472 |
| v1.3 | 2026-11-10 | B 块对话下方新增「研究工具快捷入口」：从 `content/skills/catalog.json` 按组随机 4 条可点条目（组内优先 ready / existing_page），点击直达 `/skills/:id` 或已承载页面，支持「换一批」；起步问题与直接搜零回退 | QUOT-531 |
| v1.3 | 2026-11-10 | A/C/D/E/F 五张取数卡的卡头加常显手动 ⟳（`CardRefreshButton`，走 `actions` 槽），点击只重取本块、休市也可补拍；盘中 15s 轮询与节拍文案不变。产品上回退 QUOT-457/458「只留顶部一条刷新」，顶栏「刷新全部」保留（DEC-071）。§2.1 线框、§2.3 状态矩阵、§4.1 / §5.1 线框、§6 E/F、§9 验收同步 | QUOT-521 |
| v1.4 | 2026-11-11 | B 块三条研究路径 clarity：统一序号标签（`HomePathLabel`）①问 AI·自由提问 / ②开现成工具·点一下直达 / ③直接搜·不经过 AI 摆明主次；起步问题从与工具卡同款的 2×2 卡片改为 tinted 问句 pill（`rounded-pill` + 填入图标，暗示「填入输入框」），工具卡保留带箭头矩形卡（暗示「跳转」），两组不再同款；只改呈现 / 文案 / 视觉区分，三条路径行为口径零回退。§3.1 线框、§9 验收同步 | QUOT-551 |
| v1.5 | 2026-11-11 | A 块港股两格接真实源：新增 `index_quote_hk` 数据集（`tickhub_us` / `th_us_get_quote`，逐 symbol `HSI.HK` / `HSTECH.HK`，零 key、Tier C 非商用，DEC-081）；QUOT-471 的「港股无合规源 → 源未接入占位」「`ok` 不可达」两条临时口径废止——8 格全有值时 `ok` 首次可达，取不到仍按格级降级诚实标原因。§2.1 线框、§2.2 字段表、§2.3 状态矩阵、§10 交付表同步；前端渲染逻辑零改动（通用格自动升级） | QUOT-560 |
| v2.1 | 2026-11-13 | §3.1「通道只有一条」改为 `POST /api/page/assistant`（`page_key: '/'`，`module_for('/')` → `tickhub`），废止 `/api/research/home/assistant` 语义错配路径；新增「会话语义 = 单轮无状态」与「`pending_research` 允许不渲染、禁止起任务按钮」两条；§12-L08 补该边界；§9 验收输入框路由同步。决策见 DEC-083 | QUOT-573 |
| v2.0 | 2026-11-11 | 一页工作台：两行五卡（大盘 8 + 待办异动 4 / 自选 4 + 持仓 4 + 研究 4），desk 1280×800 一屏装下（实测内容 728px ≤ 滚动区 744px）、1024–1279 单栏保底零横向溢出；待办+异动合并 1 表（类型列区分，待办按优先级在前、异动按 \|涨跌\| 前 3 在后）；研究面板三页签 + 底部唯一输入框回车路由（`homeQueryRoute.ts`）；自选 / 持仓首页 3 列摘要（持仓带合计行），全表留 `/watchlist`、`/positions`；隐私长文案收进卡头 `PrivacyBadge`（全状态常驻，悬停 / 点击展开全文）；持仓「毛额·未扣费」徽标；`useHomeSearch.remember` 同步写 localStorage 修复（公开签名不变）；`Card`/`SectionCard` 默认内距让位修复（调用方带 p-* 时不再叠 p-4，§12-L11）；行距 gap-3、页脚一行化（口径短句 + h-5 全表入口）；实机走查修复三处：研究页签 `Tabs dense`（325px 卡宽不折行）、隐私 popover `bg-surface` 不透明打底（tint 不透字）、`describeApiError(null/undefined)` 不再把 "undefined" 印上屏（强制 error 演示态）。§1 / §3 / §4.1 / §5.1 / §5.4 / §6 / §9 / §12 重写 | QUOT-552 |

## 0. 一句话与范围

首页是「开盘前 5 分钟的一页工作台」：**第一行看大盘在哪 + 今天要处理什么（待办 / 异动合并 1 表），
第二行只看自己的票（自选摘要 + 持仓摘要）+ 一个研究入口（三页签 + 唯一输入框）**。
desk（≥1280）两行五卡一屏装下、无多余滚动；1024–1279 单栏保底、允许内容下移但零横向溢出。
原有三块（情绪温度 / 连板梯队 Top 10 / 题材强度 Top 5）仍不在首页（落点 `/market`，§1.3）；
持仓 / 自选全表仍在 `/positions`、`/watchlist`，首页只摘要、不丢任何列与信息。

**覆盖**：页面骨架与顺序、逐块线框、逐块字段表（要哪些字段 / 从哪来 / 拿不到显示什么）、
逐块状态矩阵、整屏六态与块级四态的关系、验收清单、需上报的决策点。

**不覆盖**：`GET /api/indices` 与 `GET /api/watchlist` 的实现与字段命名（QUOT-377 S2 后端子任务）、
`frontend/src/lib/search*.ts` 的接口形状（硬约束：零改动）、`/market` `/positions` 等来源屏自身的版式。

## 1. 页面骨架

### 1.1 块顺序与栅格（v2.0）

```
PageHeader：首页 · 摘要 · [刷新全部]                       （唯一主操作在最右，无搜索入口）
├─ offline / partial 提示条（整屏级，条件出现）
└─ grid gap-3 desk:grid-cols-12（外层 flex flex-col gap-3）
    ├─ A 大盘与全球指数          desk:col-span-8   SectionCard（8 格）
    ├─ B 待办与异动（合并 1 表）  desk:col-span-4   SectionCard（DataTable 3 列）
    ├─ C 我的自选（3 列摘要）     desk:col-span-4   SectionCard（DataTable 3 列）
    ├─ D 我的持仓（3 列摘要）     desk:col-span-4   SectionCard（DataTable 3 列 + 合计行）
    └─ E 研究面板（三页签）       desk:col-span-4   Card（Tabs + 唯一输入框 + SearchPanel 模态）
```

- **两行五卡**：第一行 A(8) + B(4)，第二行 C(4) + D(4) + E(4)。五张卡都是**直接的网格子项**
  （不包 wrapper），行内等高由 grid stretch + 卡内 `flex flex-col` + body `grow` + footer `mt-auto` 保证。
- **<1280 单栏堆叠**（顺序 A→B→C→D→E），允许纵向滚动，**零横向溢出**（`scrollWidth ≤ innerWidth`）。
- 旧版「A/B/C/D 满宽 + E/F 并排（7/5）」作废。v1.x 的 824px 不并排算式不再约束首页——
  首页持仓改 3 列摘要（§5），全 6 列双表原样保留在 `/positions`（§12-L06 更新）。
- **不再出现**：`TemperatureBar`、5 张 `MetricCard`、`今日连板梯队 Top 10` 表、`题材强度 Top 5` 卡
  （落点见 §1.3）；页脚口径行取消——口径随各卡自己的 caption / footer 走，不再重复一遍。
- **不再出现**：`PageHeader` 右侧的搜索入口——搜索收进 E 研究面板底部唯一输入框（§3），
  页头右侧恢复为「刷新全部」唯一主操作。

### 1.2 几何（可复核，不是估的）

**宽度**（`main` = 视口 − 侧栏 − `px-5` 48；12 列 `gap-3`）：

| 视口 | 侧栏 | `main` 可用宽 | `col-span-8`(A) | `col-span-4`(B/C/D/E) | 指数格（A 内 4 列） |
|---|---|---|---|---|---|
| 1440 | 232 | 1160 | ≈769 | ≈379 | ≈175 |
| 1280（`--ldst-bp-base`） | 232 | 1000 | ≈663 | ≈325 | ≈147 |
| 1024–1279（`rail:` 档侧栏 56） | 56 | ≈920 | — 单栏满宽 | — 单栏满宽 | ≈212 |

- **~325px 是 v2.0 压缩列数的硬约束依据**：持仓 6 列下限 824px（名称 176 + 176 + 112×4 + 内距 24）
  在 325px 里放不下 → 首页只放 3 列摘要（名称剩余 / 112 / 112），全 6 列双表留 `/positions`（§5）。
  自选同理压 3 列（名称剩余 / 88 / 88）。
- 指数格内容下限：点位 `12,641.86` = 9 字符 × 12.05px（20px 等宽）= 108px + `p-2` 16 = **124px**；
  A 卡 4 列档最窄 ≈147px（1280），富余成立。单排 8 格仍不做（§12-L02）。

**高度**（desk 1280×800 一屏预算，实测值）：

| 量 | 值 | 说明 |
|---|---|---|
| topbar | 56 | `h-topbar` |
| 主滚动区高 | 744 | 800 − 56 |
| page-shell 上下 padding | 40 | 20 × 2 |
| PageHeader + 外层 gap-3 | 36 | 24 + 12 |
| 行间距 | 12 | `gap-3` |
| 两行卡高上限 | ≤656 | 744 − 40 − 36 − 12 |
| 实测两行卡高 | 302 + 338 = 640 | 演示态内容总高 728 ≤ 744，富余 16px；partial 态 717 |

- 卡内垂直结构：卡头（min-h-topbar 56；C/D 副标题只放溯源 / 口径徽标一行，隐私徽标在操作区）
  + body `grow`（表 maxHeight 240 内滚动 / 研究面板内容区 160 内滚动）
  + footer `mt-auto` 沉底（一行：口径短句 truncate + `h-5` 全表入口按钮，py-1，共 33px）。
  行高取行内最高卡，另一卡 footer 沉底补齐。
- 1024–1279 单栏：卡宽 ≈920，所有表 maxHeight 内滚动，不横向压缩、零横向溢出。

### 1.3 移除项的去向（必须逐条有落点）

| 移除块 | 信息落点 | 首页是否留出口 |
|---|---|---|
| 情绪温度条 + 情绪温度 / 最高连板 / 主线题材 / 盯盘异动 / 待验证 5 卡 | `/market?tab=sentiment`（同三指标 + CH-01 折线） | 不留。首页页头副标题**只报屏级态**（加载 / 断连 / 取数失败 / 空态），不再给「数据完整 / 部分数据降级 · 更新于 …」——参考页口径是「标题 + 面包屑」，降级走待办条目 + 块级 `SourceStatusBadge`，取数时刻留在拥有该数据的块里（QUOT-469） |
| 今日连板梯队 Top 10 | `/market?tab=ladder` | 不留。待办里若出现「N 条验证条件等待收盘对账」仍链 `/validate` |
| 题材强度 Top 5 | `/market?tab=theme` | 不留 |
| 待办、最近异动 | 首页独有 | **保留**，v2.0 合并为 B 卡「待办与异动」1 表（§6），不再 E/F 并排 |

## 2. 块 A：大盘与全球指数（desk:col-span-8）

> v2.0 只改承载宽度（满宽 → col-span-8 ≈656px@1280）；数据口径 / 字段表 / 状态矩阵与 v1.x 完全一致。
> 视觉主次分层沿用 QUOT-479：A 股四格主卡（`min-h-index-cell` 72px 三行），海外四格缺数时降为
> 「名称 · 源未接入」胶囊行（`h-7` 一行），拿到点位才升级为正常 cell。

### 2.1 线框

```
┌─ SectionCard（tone=neutral，desk:col-span-8，bodyClassName="flex grow flex-col p-3"）────┐
│  大盘与全球指数     交易时段 · 每 15 秒刷新  ● 数据完整   13:41              ⟳           │
│ ┌────────────────────┬────────────────────┬ … 4 列（≈146px@1280）… ┐                   │
│ │ 上证指数  A 股      │ 沪深300  A 股      │ 深证成指 / 创业板指     │  ← 名称 caption    │
│ │ 3,813.79            │ 4,317.25           │                        │  ← 点位 section 600│
│ │ +0.05% ↑           │ +0.16% ↑           │                        │  ← SignedNumber    │
│ └────────────────────┴────────────────────┴────────────────────────┘                   │
│ ┌──────────────┬──────────────┬──────────────┬──────────────┐                          │
│ │ 道琼斯 美股   │ 纳斯达克综合  │ 恒生指数 港股 │ 恒生科技 港股 │  ← 港股 QUOT-560 起有源  │
│ │ 51,570.09    │ 24,707.81    │ 24,315.92    │ 4,027.06     │                          │
│ │ +0.09% ↑     │ +0.14% ↑     │ -0.98% ↓     │ -1.41% ↓     │                          │
│ └──────────────┴──────────────┴──────────────┴──────────────┘                          │
│ （任一海外格取数失败 → 该格降为 h-7 胶囊行「名称 · 原因」，不占主格高）                   │
│ 脚注（mt-auto 沉底）：指数为行情快照，不是逐笔实时；缺数格显示「—」与原因，不补 0。       │
│       源：指数快照 + 美股指数快照 + 港股指数快照                                        │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

**卡头刷新口径（QUOT-470）**：盘中说「交易时段 · 每 15 秒刷新 <as_of 时刻>」，休市说
「休市 · 显示最近交易日读数 <as_of 时刻>」——两段文案由**交易所挂钟 + 竞价窗口**的真判定
（`lib/tradingSession.ts`，与后端 `core/intraday.py:in_trading_session` 同口径：周一至周五
09:30–11:30 / 13:00–15:00，左闭右开，`Asia/Shanghai`）驱动，**禁止拿本机钟点或后端恒发的
`freshness='realtime'` 当休市信号**。盘中指数条按 15s 静默轮询（`lib/usePolling.ts`，带请求
计数与连续失败指数退避，封顶 120s，后台标签页跳过），休市立即停轮询；其余四块维持一次性取数。

**卡头 ⟳（QUOT-521 / DEC-071）**：A/C/D/E/F 五张取数卡的卡头右侧常显手动 ⟳
（`CardRefreshButton`，走 `SectionCard` / `CardHeader` 已有的 `actions` 槽）。点击只重取本块
（`BlockView.onRefresh`）：有数据时静默替换、不闪骨架；无数据（错误 / 空 / 离线）时走整块加载，
错误态仍有出口。**手动 ⟳ 不受交易时段门控**——休市也能补一拍；盘中 15s 自动轮询照旧，
手动只是插一拍、不重置节拍，节拍文案仍由 `INDICES_POLL_SECONDS` 这一个常量供数。
顶栏「刷新全部」保留，与卡级 ⟳ 并存（整页刷新 × 块级刷新，不是二选一）。

- 每格：`div` 非卡（不叠第二层阴影，靠 1px `border-border` 分界，§7.3 阴影唯一用法），
  `rounded` + `border border-border bg-surface` + `p-2` + `min-h-index-cell`（72px，QUOT-479）+
  `flex flex-col justify-between`；海外缺数格为 `h-7` 胶囊（`rounded-full bg-surface-2`，
  「名称 · 源未接入」一行），不占主卡高度。
- 网格：`grid grid-cols-2 gap-4 desk:grid-cols-4`（desk 起 4 列；8 格自然回流，海外胶囊行矮、
  整卡比 v1.x 满宽版更矮，正好适配两行工作台的第一行）。**不做单排 8 格**，算术见 §1.2 / §12-L02。
- v2.0 装配：`SectionCard className="desk:col-span-8 flex flex-col"`，body `flex grow flex-col p-3`，
  脚注 `mt-auto pt-2`（内层 `p` `pt-1`）沉底——与右侧 B 卡等高时不悬空。
- 市场后缀（`A 股` / `港股` / `美股`）是 `text-caption text-text-muted` 的行内标签，
  不是 `Badge`——它是身份说明不是状态，用 Badge 会把 8 格变成 8 个胶囊，噪声大于信息。
- 顺序固定为白名单顺序：上证 / 沪深300 / 深证成指 / 创业板指 → 道琼斯 / 纳斯达克综合 →
  恒生指数 / 恒生科技。顺序由后端 `cells[]` 给出，前端不排序（§1-1「程序算数」）；
  前端只保证「给几格渲染几格」。

### 2.2 字段表

> 字段名以 `GET /api/indices` 实际契约（`frontend/src/api/radar.ts:343` `IndicesPayload`）为准：
> 八格在 `cells[]` 里，每格 `{ code, market, name, available, price, change, change_pct, prev_close, as_of, reason }`。
> **QUOT-471 起**：`cells[]` 恒为 8 格（A 股 4 + 美股 2 + 港股 2），美股两格由 `tickhub_us` peer 的
> `th_us_get_quote` 供数（每首页取数 = 2 次调用，一 symbol 一次，无按格扇出）。
> **QUOT-560 起（DEC-081）**：港股两格同样由 `th_us_get_quote` 供数（`index_quote_hk`，
> 规范形 `HSI.HK` / `HSTECH.HK`，再 +2 次调用，全块共 5 次）；零 key、Tier C 非商用，
> 「无合规源 → 源未接入占位」的临时口径废止，取不到时按格级降级并诚实标原因。

| 上屏项 | 需要的字段 | 从哪来 | 拿不到时显示 |
|---|---|---|---|
| 指数名 | `name` | `GET /api/indices` → `cells[].name`（后端白名单写死 8 格：4 A 股 + 2 美股 + 2 港股） | 该格整体降级为「源未接入」占位格（§2.3），不显示名字猜指数 |
| 市场后缀 | `market`（枚举 `SH` / `SZ` / `BJ` / `HK` / `US`） | 同上 `cells[].market` | 不显示后缀行（后缀是修饰信息，缺失不阻塞读数） |
| 点位 | `price` | 同上 `cells[].price`（A 股来自 `index_quote`，美股来自 `index_quote_us`，港股来自 `index_quote_hk`） | `—`（`MISSING`），旁注「点位未取到」；**不得显示 0**（§8） |
| 涨跌幅 | `change_pct` | 同上 `cells[].change_pct` | `—` + 无箭头；点位仍照常显示（两者是独立事实，不互相抹掉） |
| 缺格原因 | `available` / `reason` | 同上 `cells[].available` / `cells[].reason`；`available=false` → 「源未接入」，否则「点位未取到」 | 第三行落到二者之一，不留白（§2.3） |
| 「快照，不是逐笔实时」 | `freshness` / `data_as_of` | 信封公共字段 → `SourceBadges` + `formatTime` | `freshness` 未给 → 中性徽标「新鲜度未知」（§5.7，不得静默不渲染） |
| 脚注里的源名 | `datasets[]`（数据口径）与 `price_source` | 信封 → 中文标签映射（§5.8，后端 key 不直显）；QUOT-560 起脚注列 `指数快照 + 美股指数快照 + 港股指数快照` | 脚注只保留固定口径句，不编源名 |

### 2.3 状态矩阵（块级四态，`StatePanel` 承接）

| 状态 | 判据 | 表现 |
|---|---|---|
| normal（`ok`） | 8 格都有 `price` | 满格 + 组级 `● 数据完整` 徽标。**QUOT-560 起可达**：港股两格接 `index_quote_hk` 后 8 格可同时有值（QUOT-471 期间因港股无源恒不可达的临时口径废止） |
| loading | 请求在飞 | 4 列 × 2 行**同形状**骨架格（`Skeleton`，8 格，不跳列） |
| empty | 端点 404 / 未接入 | `EmptyState compact`：标题「指数数据源未接入」，说明「本机已登记数据集，指数不在其中；先运行 `ldst run`，接入后此块自动出现」，出口「重新检查」 |
| 部分格缺（某格取数失败 / 端点上该格无行） | 该格 `price === null` 且 `available === false` | 该格点位与涨跌都是 `—`，第三行改文案「源未接入」（`text-caption text-text-muted`），组级 `SourceStatusBadge partial`（橙红 + 列明哪几格缺，§5.2「不列缺失项就不给结论」）。**QUOT-560 起是降级态而非常态**：8 格都有源，只有 peer 取数失败 / 无行时才落到这里，格级 `reason` 带具体原因（如 `index_quote_hk 取数失败（HSI.HK）：…`） |
| 休市 / 非交易日 | **不在连续竞价时段**（`lib/tradingSession.ts:isTradingSession`，交易所挂钟 + 竞价窗口），**不是** `freshness`——`freshness` 只决定是否给 `◐ 缓存` 徽标 | 数值照常显示 + 卡头文案「休市 · 显示最近交易日读数」；**停止 15s 轮询**（请求计数不再增长），**但卡头 ⟳ 常显可用**，点击照常发本块请求（QUOT-521）；`freshness` 为 `cached` / `archive` 时另给 `◐ 缓存` 徽标；**不灰化整块**（灰化会让人以为坏了） |
| 整屏 offline | `isApiError.kind === 'not-connected'` | 走 §7 整屏 `OfflineBanner`，本块不渲染任何数字（旧值一律不上屏） |

## 3. 块 E：研究面板（desk:col-span-4，三页签 + 唯一输入框）

> QUOT-552 起，v1.x 的「B 块研究入口」（对话 + 工具快捷入口 + 直接搜三段纵排）合并为一个 tab 面板。
> 三条能力一条不少，只是从「三段同屏」改为「三页签 + 底部唯一输入框」。
> 旧约束继续有效：**是对话框不是假输入框、不承诺通道没有的能力、搜索栈公开签名零改动**。

### 3.1 版式与边界

```
┌─ Card（desk:col-span-4，flex flex-col）──────────────────────────────────┐
│ CardHeader：今天，想研究什么？                                            │
│   subtitle：[🔒 仅本机]（PrivacyBadge，detail=研究上下文口径）             │
│             问句 / 代码 / 名称，一个入口                                  │
│ Tabs（C-08，dense）：[快捷入口] [研究股票 ·回答中?] [搜索历史]              │
│ ┌ 内容区（grow，height 160，三个面板同挂 hidden 切换，各自 overflow-y-auto）┐ │
│ │ 快捷入口：HomeResearchShortcuts embedded（2×2 工具卡 + 换一批）         │ │
│ │ 研究股票：HomeResearchChat embedded（起步问题 pill + 对话 log           │ │
│ │           max-height 128 + 本轮来源；不自带输入框）                     │ │
│ │ 搜索历史：model.recent 全部 8 枚 chip / 空态「还没有搜索历史」           │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│ ── 底部唯一输入框（border-t，px-4 py-2）──                                │
│ [🔍 问一句，或输入代码 / 名称 · 回车直达                        ]         │
│ 问句 → AI · 代码 → 行情 · 名称 / 关键词 → 搜索（一行 truncate）            │
│ SearchPanel（既有模态 overlay：createPortal + useModalChrome，一份实现）   │
└──────────────────────────────────────────────────────────────────────────┘
```

- **通道只有一条**：`POST /api/page/assistant`（页面助手，`assistant.py:441` `PAGE_PREFIX`），请求带 `page_key: '/'`。
  首页**不再走** `/api/research/home/assistant`——那条路把 `home` 当个股代码，固定 `module="senate"` +
  个股深挖提示词 + `symbol='home'` 进 mem0 召回，语义错配（DEC-083 §2.2）。页面助手按
  `module_for('/')` 回落 `tickhub`（首页数据就是行情/自选），与 QUOT-454 / `describeAssistantError` /
  `ErrorBlock` / `AbortController` 同一套降级与停止口径。**不新起第二套对话实现、不新起第二个模型调用。**
- **会话语义：单轮无状态**（DEC-083 §4）。请求不带 `session_id`，后端不落会话；多轮会话由任务书阶段 C
  的 `chat_session / chat_turn`（`chat.sqlite`）承接，**不在 `assistant.py` 上加内存会话**。
  mem0 跨会话召回保持现状，不属于会话内记忆。
- **`pending_research` 边界**（DEC-083 §5）：本通道不返回该字段。若未来任何通道返回，
  **允许不渲染，禁止渲染成「起任务」按钮**（§12-L08 理由不变）。
- **上下文白名单**在 `screens/home/homeAssistant.ts`：指数四格、自选最多 20 行、盯盘最多 10 行、
  验证条件**只给条数**；**持仓永不进入**。取不到的块写进 `unavailable[]`。
- **本轮来源**取发送那一刻的上下文快照（`assistant.sources`），每条 label + 项数 + `取数 HH:MM` +
  页面链接；没有任何来源时显示「本轮未取数」，不造默认来源。
- **搜索零回退**：`useHomeSearch` / `SearchPanel` / `lib/searchCore.ts` / `searchIntent.ts` /
  `searchRoutes.ts` 的公开入参与返回形状零改动；结果面板仍是既有模态 overlay（焦点陷阱 / Esc /
  滚动锁 / 关闭后焦点回入口，一份实现）；`?search=` 仍是唯一搜索深链入口，`/` 全局快捷键保留。
- **研究工具快捷入口**（QUOT-531 口径不变）：数据只读 `content/skills/catalog.json`（`readCatalog()`），
  抽样在 `screens/home/homeShortcuts.ts`：一组最多一条、组内优先 `ready` / `existing_page`，
  组数不足 4 时才从剩余可点条目补齐；`ready` / `partial` / `unconnected` → `/skills/:id`，
  `existing_page` → `existingPageTarget(item)`，`planned` / `not_supported` 不参与。
  「换一批」重抽、每次进入首页重新挂载即换一批；embedded 只去掉分隔线与序号标签，
  抽样 / 跳转 / 工具卡 `span` 结构不变。
- **起步问题 pill**（QUOT-551 视觉不变）：tinted 问句 pill（`rounded-pill` + 填入图标），
  点击**只填底部唯一输入框并聚焦**（`onPickSuggestion`），不自动发送。
- **页签用 `Tabs dense`**（v2.0 实测约束）：~325px 卡宽里默认档（px-3 + text-body）三个页签
  放不下会折行；dense = `px-2 + text-caption + gap-1`，按钮恒 `whitespace-nowrap`。
  dense 是 C-08 的 additive 可选 prop，其余屏默认档渲染不变。

### 3.2 唯一输入框与回车路由（`home/homeQueryRoute.ts`，QUOT-552 新增）

- 底部输入框绑定 `useHomeSearch` 的 `model.query`——与搜索模态里的输入框是**同一个 controller
  同一个 query**；面板关着时打字不触发任何 fetch（fetch effect 只在 `open=true` 时跑）。
- 回车（非 IME 合成中）经 `routeHomeQuery(input)` 统一判定，不写第二份解析：

| 输入 | 判定 | 去向 |
|---|---|---|
| 空 | `empty` | 不动 |
| 整串 6 位代码（`600519` / `sh600000` / `600000.SH` 归一） | `research` | `actions.onExit(researchHref(code))`：记最近搜索 + 跳 `/research/:code` |
| 含问号（？?）或疑问 / 祈使词（为什么 / 怎么 / 哪些 / 帮我 / 分析 / 对比 / 值得 …） | `assistant` | 切到「研究股票」页签，问句经 `incoming{ text,seq }` 送进既有对话通道发送；输入框清空 |
| 其余名称 / 关键词 | `search` | `openPanel()` 打开既有搜索模态（名称命中个股还是行业由数据决定，不臆造代码） |

- 归一化与代码判定**复用 QUOT-356 `searchIntent` / `searchRoutes`**（NFKC + 空白折叠 +
  市场前后缀剥离）；疑问词表是本次新增的唯一判定，只服务「问句进 AI」这一条路由。
  含代码的问句（`600519 怎么样`）按问句走 AI，不抢成代码直达。
- 问句路由时若上一轮还在回答：只切页签、保留输入框文字，不堆叠请求
  （「研究股票」页签 badge「回答中」提示，底部 hint 文案同步）。
- IME 合成中（`isComposing`）回车不路由；搜索模态内的回车仍走既有 `submit` 逻辑，不受影响。

### 3.3 三页签

| 页签 | 承载 | 口径 |
|---|---|---|
| 快捷入口 | `HomeResearchShortcuts embedded` | §3.1 快捷入口口径（QUOT-531） |
| 研究股票 | `HomeResearchChat embedded` | 同一条 assistant 通道；embedded 不自带输入框（唯一输入框在面板底部），对话 log max-height 128 内滚动；停止 / 清空 / 本轮来源 / 降级全保留 |
| 搜索历史 | `model.recent`（最多 8 枚） | 点 chip → `actions.onRecent` 回填 query + 打开搜索面板；空态「还没有搜索历史」 |

- 三个页签面板**都保持挂载**，用 `hidden` 切换——对话记录 / 在飞请求跨页签不丢。
- 默认页签「快捷入口」；对话 pending 时「研究股票」页签显示「回答中」badge。

### 3.4 文案红线（沿用 v1.x §3.2，不变）

- 不写「联网搜索」「实时抓取」「自动取数」：通道只发送页面已取到的上下文，不做检索；
- 不写「后台研究」「自动跑数」：没有「先问一句就起任务」的通道（§12-L08）；
- 不把模型输出的数字写回页面任何数据位；固定口径「行情数字以页面为准，不构成投资建议」；
- 不出现提示词、密钥、环境变量名；上下文只含页面已展示字段（§3.1 白名单）。
- 隐私口径收进卡头 PrivacyBadge（§5.4），detail 文本：私有数据不出本机，持仓成本 / 数量 / 盈亏
  一律不进 AI 上下文；对话只携带首页已展示的公开行情（指数 / 自选 / 盯盘 / 验证条件计数），
  不写 URL、日志或本机存储。

### 3.5 状态矩阵

| 状态 | 判据 | 表现 |
|---|---|---|
| normal | 本机服务在 + AI 通道已配置 | 「研究股票」页签内每条 assistant 气泡下带「本轮来源」 |
| 无 Key | 后端 503 `ai_not_configured` | 页签内 `ErrorBlock`：「研究助手未配置模型」+ 后端原文 + 「重试」；搜索面板照常可用 |
| 本机服务未连接 | `fetch` 网络失败（`kind === 'not-connected'`） | 「本机服务未连接」+「启动 ldst run 后重试」+「重试」；页面数据不丢，搜索面板照常可用 |
| 超时 / 上游失败 / 422 | 对应 HTTP 状态 | 各自的结论句与可重试标记；绝不静默失败 |
| 停止 | 用户点「停止」或组件卸载 | 追加「已停止本次回答。」气泡，不渲染来源清单 |
| 清空 | 有对话时点「清空」 | 中止在飞请求，清空记录与错误，焦点回底部输入框 |
| 无搜索历史 | `model.recent.length === 0` | 「搜索历史」页签出空态，不留空胶囊轨道 |
| 面板内各组状态 | 沿用 QUOT-357 既有规格 | **本规格不改，不重复定义** |

## 4. 块 C：我的自选（desk:col-span-4，3 列摘要）

### 4.1 线框

```
┌─ SectionCard（title=我的自选 · (N)，actions=[🔒 仅本机] + ⟳）─────────────────┐
│ subtitle：● 数据完整 ● 实时（FreshnessBadge + SourceStatusBadge，一行）        │
│ ┌ DataTable（density=用户偏好，maxHeight="240px"，表头可排序）┐               │
│ │ caption：我的自选（N 只），现价与涨跌来自本机行情快照，非逐笔实时│           │
│ │ 名称（剩余，含代码行内副标）│ 现价 88 numeric │ 涨跌 88 numeric │           │
│ │ 贵州茅台 600519            │ 1,263.00       │ +0.57% ↑        │           │
│ └─────────────────────────────────────────────────────────────┘             │
│ ── 一行页脚（border-t px-4 py-1，mt-auto 沉底）──                             │
│ 源：实时行情快照 · 13:41（truncate，全文在 title）      [管理自选 → h-5]      │
└──────────────────────────────────────────────────────────────────────────────┘
```

- **首页只 3 列**（QUOT-552）：名称（主行名称 + 行内代码 `ldst-num text-caption`）/ 现价 / 涨跌，
  列宽 `剩余 / 88 / 88`。~325px 卡宽放不下 v1.x 的 4 列分列（88 + 剩余 + 112×2），
  全列宽表原样保留在 `/watchlist`（→ `/my-stocks?tab=watch`），「管理自选 →」直达，不丢信息只收起来。
- 名称列 `truncate` + `cellTitle`（名称 + 代码全文）；数值列 `numeric: true`（右对齐 + `.ldst-num`）。
- 排序：现价、涨跌两列给 `sort` 比较函数，表头点击即 `aria-sort` 三态（C-09 内建）。
  **不做「调整顺序」按钮**——那是同一功能的第二套入口。
- 行数：maxHeight 240 内滚动，不分页；骨架 5 行与真实表同构。
- 隐私长文案不再印在脚注 → 卡头 `PrivacyBadge`（§5.4，**actions 槽**），**加载中 / 失败 / 空态常驻**。
- **卡头一行放徽标（v2.0 实测约束）**：subtitle 只放 `FreshnessBadge + SourceStatusBadge`；
  快照时间不上卡头——演示 / 跨日数据 `formatInboxTime` 出 `MM-DD HH:mm`（≈73px），
  会把 subtitle 挤成两行（卡头 63→85px）。时间收进**一行页脚**：`源：{中文标签} · {formatInboxTime(price_as_of)}`，
  `truncate` + `title` 全文（源口径 + 非逐笔实时 + 全表去向）；「非逐笔实时」同时进表 caption 常显。
  页脚右侧「管理自选 →」是 h-5（24px，token space 5）纯文本按钮，不用 `Button`——
  `Button` 的 `h-control` 在 `cn` 不消解冲突下永远压过 className 尺寸覆盖（§12-L11）。

### 4.2 字段表

| 上屏项 | 需要的字段 | 从哪来 | 拿不到时显示 |
|---|---|---|---|
| 代码 | `code` | `GET /api/watchlist` → `items[].code`（**S2 交付，当前无读接口**；本机隐私库已有 `watchlist` 表） | 行不渲染（代码是主键，缺主键的行是脏数据不是缺数） |
| 名称 | `name` | 同上；由后端从 `spot_quote` / `daily_k` 带出 | 「名称未接入」（沿用 `searchCore` §6.4 口径），**不拿代码顶替** |
| 现价 | `latest_price` | `spot_quote` → `tencent_quote_batch`（`ProviderRegistry.fetch_live()`，非交易日水位） | `—` + 行内注「现价未取到」；不得回落到成本价 |
| 涨跌 | `change_pct` | 同上 | `—` + 无箭头 + `text-flat` |
| 只数「(5)」 | `position_count` 类计数字段 | 信封 payload | `—`（`countOrMissing`），**不得 `?? 0`**（R-07 家族） |
| 快照口径 | `freshness` / `data_as_of` / `source_detail` | 信封公共字段 | 同 §2.2 |

### 4.3 状态矩阵

| 状态 | 判据 | 表现 |
|---|---|---|
| normal | `items.length > 0` | 表格 |
| loading | 请求在飞 | `SkeletonRows rows={5}`，列数与真实表一致（§11.0 骨架同构） |
| empty（池子为空） | 接口 200 且 `items.length === 0` | `EmptyState compact`「还没有自选」+ 说明「自选只写本机 SQLite；在自选页加一只，这里就会出现」+ 出口按钮 |
| 接口未接入 | 404 / 端点不存在 | `EmptyState compact`「自选接口未接入」+ 说明「`GET /api/watchlist` 由 QUOT-377 S2 交付；本机隐私库已有 `watchlist` 表，缺的是读接口」+「重新检查」。**不得回落演示数据**（§5.6） |
| 字段为 null | 单行 `latest_price === null` | 该行数值列 `—` + 原因；名称列照常显示 |
| 取数失败 | 该路 `status === 'error'` | 块级 `ErrorBlock variant="banner"`（结论 + 原因 + 重试），**其余块照常**（§3.1 成功内容保留） |
| 整屏 offline | `not-connected` | 走 §7 |

### 4.4 依赖与边界

- 「管理自选 →」落 `/watchlist`（重定向 `/my-stocks?tab=watch`，全列宽表 + 增删改都在那里）。
  v1.x「没有自选页、不放链接」的登记已被 QUOT-552 取代（§12-L07 更新）：自选页已存在，
  首页摘要 + 跳转是正式口径。
- 隐私边界沿用 DEC-033：`GET /api/watchlist` 与持仓同闸门口径（回环独占 + `Host` 白名单 +
  写方法同源校验），**不新增对外暴露**；自选数据不进信封、不进 MCP、不进 AI 上下文。

## 5. 块 D：我的持仓（desk:col-span-4，3 列摘要 + 合计行 + 毛额徽标）

### 5.1 线框

```
┌─ SectionCard（title=我的持仓 · (N)，actions=[🔒 仅本机] + ⟳）──────────────────┐
│ subtitle：[毛额·未扣费] ● 数据完整（SourceStatusBadge，一行）                   │
│ ┌ DataTable（density=用户偏好，maxHeight="240px"，表头可排序，列级 summary 合计行）┐ │
│ │ caption：持仓摘要（N 只），合计行与逐条同源                                    │ │
│ │ 名称（剩余）│ 市值 112 numeric │ 持仓盈亏 112 numeric │                        │ │
│ │ 浦发银行    │ 63,000.00       │ +3,000.00           │                        │ │
│ │ 合计        │ 126,300.00      │ +6,300.00           │  ← tfoot（Column.summary）│ │
│ └──────────────────────────────────────────────────────────────────────────────┘ │
│ ── 一行页脚（border-t px-4 py-1，mt-auto 沉底）──                               │
│ 移动加权平均 · 不含分红 · 13:41（truncate，全文在 title）   [完整持仓 → h-5]     │
└──────────────────────────────────────────────────────────────────────────────────┘
```

- **首页只 3 列摘要**（QUOT-552）：名称 / 市值 / 持仓盈亏，列宽 `剩余 / 112 / 112`，
  全部落在 §7.3 列宽刻度上。**合计行**走 C-09 列级 `Column.summary`（tfoot 内建），
  合计值取 `valuation.market_value` / `valuation.unrealized_pnl`（后端同源，前端不重算）。
- **全 6 列双表（币种汇总 + 逐条）原样保留在 `/positions`，不删列不丢信息**——
  收益率 / 当日盈亏 / 代码·股数 / 现价都在那边，「完整持仓 →」直达。
  v1.x 的 824px 算式仍是 `/positions` 满宽双表的依据，不再约束首页（§12-L06 更新）。
- **「毛额·未扣费」徽标**：持仓盈亏是毛额口径（`fee_included=false`，trades 无费用列，
  未扣佣金 / 印花税 / 过户费），subtitle 首位显式 `Badge tone="flat"` + `title` 全文，不靠脚注小字暗示；
  空 / 加载 / 失败态同样常驻。
- 名称列 `truncate` + `cellTitle`（代码 + 名称全文）；市值 / 盈亏 `null` → `—` 不补 0；
  盈亏走 `formatSignedAmount` + `SignedNumber`（只染数字本身）。
- 排序：市值 / 持仓盈亏两列给 `sort`；`initialSort` 不设（默认按后端 `items[]` 顺序）。
  合计行不参与排序（C-09 `summary` 语义）。
- **卡头一行放徽标 + 一行页脚（v2.0 实测约束，同 §4.1）**：估值时间不上卡头（跨日 `MM-DD HH:mm`
  会把 subtitle 挤成两行），收进页脚 `移动加权平均 · 不含分红 · {formatInboxTime(as_of)}`；
  无 `as_of` 时该位写 `· 毛额`。页脚 `title` 全文含：成本法 DEC-038 / 不含分红 / 毛额·未扣费 /
  `估值时间 {as_of}` / （有未定价成本时）`其中 X 成本未定价，市值不含它们` / 当日盈亏与完整六列去向。
  有未定价成本时页脚可见句尾追加 `· 未定价成本 {formatMoney(unpriced_cost)}`，**不把未定价当 0 市值**。
  右侧「完整持仓 →」同 C 卡为 h-5 纯文本按钮（§12-L11）。

### 5.2 字段表（含「当前给不到」的三处，逐条写明）

> v2.0 首页只上屏其中三列（名称 / 市值 / 持仓盈亏）+ 合计；其余字段（代码·股数 / 现价 / 收益率 /
> 币种 / 当日盈亏）的口径仍是 `/positions` 全表的契约，此处保留不删。

| 上屏项 | 需要的字段 | 从哪来 | 拿不到时显示 |
|---|---|---|---|
| 名称 | `items[].name` | **`positions` 表没有名称列**（schema v2 只有 `code/cost/qty/entry_date/industry/note`）；需后端从 `spot_quote` 带出 | 「名称未接入」（不拿代码顶替、不拿行业顶替） |
| 代码 · 股数 | `code` + `qty` | `GET /api/positions` 既有 | 代码是主键，缺则行不渲染；`qty` 为 null → 该格写 `—`，不写 `0 股` |
| 现价 | `latest_price` | 既有（`price_source` 映射为中文标签） | `—` + 注「现价不可用：本机 `daily_k` 无该日缓存」（`price_lookup.reason`） |
| 市值 | `market_value` | 既有 | `—`（未定价持仓本来就可能为 null，不补 0） |
| 持仓盈亏 | `unrealized_pnl` | 既有 | `—` + 注「未定价」 |
| 收益率 | `unrealized_pnl_pct`（逐条）/ `valuation.unrealized_pnl_pct`（汇总） | 既有 | 汇总行照常显示；逐条列**不设收益率**（§5.1），拿不到时该格 `—` |
| **币种** | `items[].currency` | **当前无此字段** → 汇总卡无法分组 | 汇总卡退化为**单行「合计」**（币种列写「合计」，`text-caption` 注「持仓表无 `currency` 列，未按币种拆分」），**不猜币种、不按代码前缀推** |
| **当日盈亏** | `day_pnl`（逐条 + 汇总） | **当前无此字段**（需昨收价，`daily_k` 缓存里有前一交易日就有） | 列保留、值全 `—` + 脚注「当日盈亏需昨收价，本机未取到」；**不得用持仓盈亏或浮动盈亏的差冒充** |
| 成本法口径 | `realized.method` | DEC-038 出口字段（`moving_weighted_average`） | 后端没给 → 脚注仍写「移动加权平均（DEC-038）」，因为这是**口径**不是读数 |

### 5.3 状态矩阵

| 状态 | 判据 | 表现 |
|---|---|---|
| normal | `position_count > 0` | 3 列摘要表 + 合计行（v2.0；币种汇总 / 逐条 6 列在 `/positions`） |
| loading | 请求在飞 | 3 行骨架（形状同构） |
| empty（无持仓） | `position_count === 0` 且 200 | `EmptyState compact`「本机还没有持仓」+ 出口「去我的持仓新增」→ `/positions`（导入 / 新增都在那里，首页**不放**新增表单） |
| 字段为 null | 见 §5.2 各行 | 逐格 `—` + 原因，行不消失 |
| 未定价（`coverage_pct < 100`） | 既有 | 页脚追加 `· 未定价成本 {formatMoney(unpriced_cost)}`（title 全文「其中 X 成本未定价，市值不含它们」），**不把未定价当 0 市值** |
| 取数失败 | `status === 'error'` | 块级 `ErrorBlock variant="banner"` + 重试；**「仅本机」与「毛额·未扣费」徽标仍常驻**（隐私与口径不随失败消失） |
| 整屏 offline | `not-connected` | 走 §7，不渲染任何金额 |

### 5.4 隐私承载：卡头「仅本机」徽标（v2.0 改徽标，硬约束不变）

- **不再印整段隐私长文案**（QUOT-552）：首页持仓 / 自选 / 研究三卡卡头各放一枚紧凑
  `PrivacyBadge`（`components/ui/PrivacyBadge.tsx`：Lock 图标 + 「仅本机」，h-5 rounded-xs border）。
  **落位**：自选 / 持仓在卡头 **actions 槽**（与 ⟳ 并排，subtitle 让位给溯源徽标保一行）；
  研究面板在 CardHeader **subtitle**（与「问句 / 代码 / 名称，一个入口」同行）。
  完整口径**信息不丢，只是收起**：悬停 `title` 出全文；点击 `createPortal` 弹出 `role="dialog"`
  popover（宽 320，视口内钳位 / 空间不足翻上方，Esc / 外部点击 / 滚动 / resize 关闭，
  关闭后焦点回徽标）。popover 容器必须 `bg-surface` 不透明打底——`PrivacyNotice` 的
  `bg-accent-soft` 是 tint，直接浮在表格上会透字（v2.0 实测修复）。
- **默认 popover 渲染 `PrivacyNotice` 本体**（`privacyNoticeText(subject, storeCommand)` 单一口径源，
  与 `/positions` 同一实现，R-25 同源），完整承诺一字不丢；`detail` / `detailText` 可覆盖
  （研究面板用研究上下文口径，§3.4）。`PrivacyNotice` 组件本身不删，`/positions` 等仍用整段。
- **常驻红线（DEC-033）**：加载中 / 失败 / 空态徽标都在——隐私承诺不随数据状态消失。
  持仓卡另常驻「毛额·未扣费」口径徽标（§5.1），同样不随状态消失。
- 首页不因聚合而放宽边界：持仓 / 自选只经 127.0.0.1 回环读取，不进信封、不进 MCP、不进 AI；
  页面上不出现「同步 / 分享 / 云端」类入口。

## 6. 块 B：待办与异动（desk:col-span-4，合并 1 表）

> QUOT-552 起，v1.x 的 E 待办（col-span-7 手写 ul）与 F 最近异动（col-span-5）合并为一张表，
> 不再并排两卡。来源端点不变（`/api/watch/snapshot`、`/api/validation` + 两个 IndexedDB 草稿），
> **不因改版新增调用**；待办按处理优先级、异动按 `|change_pct|` 取前 3 的口径不变。

### 6.1 线框

```
┌─ SectionCard（title=待办与异动，actions=[⟳ 待办] [⟳ 异动]）────────────────────┐
│ ┌ DataTable（maxHeight="240px"，表头不可排序）┐                                │
│ │ 类型 88 │ 事项（剩余，truncate）│ 涨跌 / 去向 112（右对齐，非 numeric）│    │
│ │ 待对账  │ N 条验证条件等待收盘对账 │ 去验证 → │                            │
│ │ 待写    │ 今日复盘草稿还是空的     │ 去复盘 → │                            │
│ │ 数据降级│ ×× 源降级               │ 看数据源 → │                          │
│ │ 异动    │ 300308 中际旭创         │ +9.98% ↑ │                            │
│ └──────────────────────────────────────────────┘                              │
│ caption：待办按处理优先级 · 异动按涨跌幅绝对值取前 3 · 仅供扫读，不构成建议       │
└────────────────────────────────────────────────────────────────────────────────┘
```

### 6.2 合并规则与列口径

- **行序**：待办在前（顺序 = HomeScreen 既有 todos 组装顺序：待对账 → 待写 → 数据降级），
  异动在后（按 `|change_pct|` 降序取前 3；`change_pct === null` 的行不进前 3，现有 `.filter` 契约保留）。
- **「类型」列**（width 88）：`Badge` 区分——待对账 `warn` / 待写 `info` / 数据降级 `danger` /
  异动 `flat`。类型是状态不是身份，用 Badge 与指数格的市场后缀（行内标签）区分开。
- **「事项」列**（剩余宽，`truncate` + `cellTitle`）：待办 = 标题链接（去对应页）；
  异动 = 代码前缀（`ldst-num text-caption`）+ 名称，整行链接去 `/research/:code`；名称缺 → `—`，
  不拿代码顶替名称。
- **loading**：无行且任一路在飞 → `DataTable loading` 骨架（5 行同构）；单路失败不打块级错误，
  降级经「数据降级」待办条目与整屏 partial banner 表达（§7）。
- **「涨跌 / 去向」列**（width 112，`align:'right'` 但**不标 `numeric`**）：混合列——
  待办行渲染「{action} →」链接，异动行渲染 `SignedNumber(formatChangePct)`。
  `.ldst-num` 会把中文也套等宽，故整列不标 numeric，SignedNumber 自带 ldst-num 只染数字。
- **卡头两枚 ⟳ 常显**（QUOT-521 口径）：「刷新待办」重取 `/api/validation`、
  「刷新最近异动」重取 `/api/watch/snapshot`，各自只重取本块、互不影响，同一颗 `CardRefreshButton`。
- **空态**：无待办且无异动 → `EmptyState compact`「今日无待办」；两枚 ⟳ 仍可用。
  整屏 offline / 单块 error 沿用 §7 / 块级四态。

## 7. 整屏六态与块级四态的关系

优先级沿用 `lib/pageState.ts`：`error > offline > partial > loading > empty > normal`。

| 整屏态 | 判据（v2，五路：indices / watchlist / positions / watch / validation） | 表现 |
|---|---|---|
| loading | 五路全在飞 | `PageHeader` + `ScreenSkeleton metrics={2} rows={8}`（**不再 `metrics=5`**：首页已无 5 卡阵，骨架形状要跟着真实版式走，§11.0） |
| error | 五路全失败 | `PageHeader` + `ErrorBlock`（结论 / 原因 / 代码 / 重试） |
| offline | 全失败且都是 `not-connected` | `OfflineBanner` + 块级一律不渲染数字 |
| partial | 部分失败 | 顶部 `ErrorBlock variant="banner"` 列明失败块名（沿用 `failedLabels` 机制，扩到五路）+ **成功块照常渲染** |
| empty | 五路都 200 且全空 | 保留 `EmptyState`（今日无数据 + `ldst sync --date` + 重新检查 / 看数据源），但**A / E 两块仍渲染**：A 走自己的「源未接入」空态、E 是入口不是数据 |
| normal | 其余 | 两行五卡（A/B/C/D/E） |

**块级四态由 `StatePanel`（`components/ui/Feedback.tsx`）承接**，不在屏内手写第三套分支：
整屏态决定「有没有必要渲染骨架 / 离线条」，块级态决定「这一块自己缺了怎么说话」。
判据写在屏侧（`PageState` 值），呈现收在组件里——与 §6.4 同口径。

`?state=empty|loading|error|partial|offline` 与 `?demo=1` 必须逐块可复现；
`DemoStateBadge` 继续挂 `PageHeader` 的 badge 槽（§5.6），块内不打水印（QUOT-348 口径）。

## 8. 组件与 token 契约

**复用清单（不新建语义组件）**：`PageHeader` `DemoStateBadge` `OfflineBanner` `ScreenSkeleton`
（`PageShell.tsx`）；`Card` `CardHeader` `CardBody` `CardFooter` `SectionCard`（`ui/Card.tsx`）；
`DataTable` + `Column`（`ui/Table.tsx`，合计行走列级 `summary`）；`Tabs`（`ui/Tabs.tsx`，C-08）；
`EmptyState` `ErrorBlock` `Banner` `Skeleton` `SkeletonRows` `StatePanel`（`ui/Feedback.tsx`）；
`Badge` `StatusDot` `SignedNumber`（`ui/Badge.tsx`）；`PrivacyBadge` `PrivacyNotice`（`ui/`）；
`SourceBadges` `FreshnessBadge` `SourceStatusBadge`（`ui/SourceBadges.tsx`）；
`SearchPanel` `HomeSearch` `useHomeSearch`（QUOT-357，公开签名零改动）；
`Button` `Link` `Input`。

**结构性新增（v2.0）**：① 块件 `home/HomeTodoMoversCard.tsx`（合并表）、
`home/HomeIndexStrip.tsx` / `HomeWatchlistCard.tsx` / `HomePositionsCard.tsx` / `HomeSearch.tsx`
（研究面板装配）+ `home/HomeResearchChat.tsx` / `HomeResearchShortcuts.tsx` 的 `embedded` 模式
（不自带输入框 / 去分隔线，独立件契约不变）；② `home/homeQueryRoute.ts`（回车路由纯函数，§3.2）；
③ `components/ui/PrivacyBadge.tsx`（徽标 + popover，默认渲染 `PrivacyNotice` 本体，§5.4）。
沿用 `screens/home/` 既有拆分方式，不是新组件语义。

**token 用法（逐块，全部是既有变量）**：

| 用途 | 类 / 变量 | 出处 |
|---|---|---|
| 卡底 / 页面底 / 表头底 / 悬停 | `bg-surface` `bg-bg` `bg-surface-2` `bg-surface-3` | `tokens.css:16-19` |
| 格与卡的分界 | `border-border`（装饰）/ `border-border-strong`（交互件边界） | `tokens.css:21-22` |
| 点位、现价、市值 | `text-section`（20px）+ `.ldst-num` + `font-semibold` | `tokens.css:73`、§7.2 |
| 指数名、字段名、表头 | `text-caption text-text-secondary` | `tokens.css:67,24` |
| 时间戳、单位、脚注 | `text-caption text-text-muted` | `tokens.css:66,25` |
| 涨跌 | `SignedNumber` → `text-up` / `text-down` / `text-flat` + ↑↓ | §7.2「只染数字本身」 |
| 卡内距 / 块间距 / 页内距 | `p-2`(8) `p-3`(12) `p-4`(16) `gap-3`(12) `gap-4`(16) `px-5 py-5`(24) | §7.3 刻度；**space 是自定义刻度**（`tokens.json`：1=4 / 2=8 / 3=12 / 4=16 / 5=24 / 6=32），`h-5`=24px 页脚按钮、`py-1`=4px 页脚纵向内距都取自它 |
| 指数格高 | `min-h-metric`(96) | `tokens.css` `h-metric` |
| 行高 | `min-h-row`(38) / `h-row-compact`(36) / `h-row-comfortable`(40)，由 `useDensity()` 决定 | §4.5 密度只改行高 |
| 控件高 | `h-control`(32) / `h-control-lg`(40，搜索触发条) | `tokens.css:105-106` |
| 圆角 | 只有 `rounded`(8) / `rounded-xs`(4，行内胶囊) / `rounded-pill` | §7.3 |
| 过渡 | 只有 `transition-colors duration-fast ease-ldst`(120ms) | §7.5 |
| 缺失值 | `MISSING`（`lib/format.ts:10`）常量，**不写字面 `—`** | §8 |
| 表高 | `maxHeight="240px"`（§7.3 允许集合 `{240,320,360,420}` 内） | §7.3 |

**禁止清单**：新 token / 新依赖 / 新断点（`min-[...]` 一律算违规，R-04）；Tailwind 默认泄漏档
（`gap-1.5` `p-1.5` `mt-0.5` 等）；`[...]` 任意值；裸 hex / rgba；第二份 `SourceBadge` label map；
第二个 `<table>`；`0` / `0.00` / `?` 冒充缺失；后端 key 直显（`tencent_quote_batch`、`cached`、
`price_source` 一律映射中文）；裸 ISO / `HHMMSS` 上屏；自动刷新定时器。

## 9. 验收清单（逐条可判真假）

先跑 `npm run check:ui`（不新增违规），再按 `docs/UI-SPEC.md` §11.0 全局条目走查，最后逐条：

**版式（QUOT-552 核心）**
- [ ] desk 1280×800：两行五卡（A8+B4 / C4+D4+E4）一屏装下，主滚动区无多余纵向滚动（真机 5950 截图取证）。
- [ ] 1024×900：单栏保底（A→B→C→D→E），`scrollWidth ≤ innerWidth`，零横向溢出。
- [ ] 首页 DOM 里**没有** `TemperatureBar`、`MetricCard`、连板梯队表、题材强度卡；
      没有 E/F 并排两卡，没有页脚口径行。
- [ ] 五张卡行内等高（grid stretch + footer 沉底），暗 / 浅两主题截图各一张入库 `docs/screenshots/`。

**A 指数条（口径零回退）**
- [ ] 8 格恒渲染；A 股 4 主格 + 海外缺数 4 胶囊（QUOT-479）；缺数 `—` 不补 0，第三行「源未接入」/「点位未取到」二者之一。
- [ ] 点位 `text-section` + `.ldst-num`，涨跌 `SignedNumber`；脚注含「不是逐笔实时」；`freshness` 缺失出「新鲜度未知」。
- [ ] 休市停 15s 轮询但卡头 ⟳ 仍可补拍；盘中手动插一拍不重置节拍；节拍文案读 `INDICES_POLL_SECONDS`。

**B 合并表**
- [ ] 一张表：类型列 Badge 区分待对账 / 待写 / 数据降级 / 异动；待办在前按优先级、异动前 3 按 |涨跌|。
- [ ] 待办行「涨跌 / 去向」列出「去验证 → / 去复盘 → / 看数据源 →」链接；异动行出 SignedNumber 且整行链 `/research/:code`。
- [ ] 卡头两枚 ⟳ 各自只重取本块端点（validation / watch），另一路请求数不变。
- [ ] 无待办无异动 → 「今日无待办」空态，两枚 ⟳ 仍在。

**E 研究面板**
- [ ] 三页签切换：快捷入口（4 条 4 组、换一批只换工具）/ 研究股票 / 搜索历史；对话记录跨页签不丢。
- [ ] 全卡只有一个输入框（底部）；问句回车 → 切「研究股票」页签并发送到 `POST /api/page/assistant`（`page_key: '/'`，DEC-083）；
      `600519` 回车 → `/research/600519` 且记最近搜索；名称回车 → 既有搜索模态；IME 合成中回车不路由。
- [ ] 起步问题 pill 点击只填底部输入框 + 聚焦，不自动发请求。
- [ ] 上下文 JSON 键只有 `page / date / market / watchlist / watch / validation / unavailable`，不含持仓。
- [ ] 回答下「本轮来源」列来源名 + 项数 + `取数 HH:MM`，无来源显示「本轮未取数」；停止追加「已停止本次回答。」。
- [ ] 无 Key / 未连接有可读结论 + 重试，搜索面板照常可用；`?search=` 深链、`/` 快捷键、Esc 焦点回归三条回归用例仍绿。
- [ ] 搜索历史 chip 点击回填 query + 打开搜索面板；空态「还没有搜索历史」。

**C/D 摘要与全表反向核验**
- [ ] 自选首页 3 列（名称含代码 / 现价 88 / 涨跌 88），表头可排序，maxHeight 240；「管理自选 →」落 `/watchlist`。
- [ ] 持仓首页 3 列（名称 / 市值 112 / 持仓盈亏 112）+ 合计行（valuation 同源）；「完整持仓 →」落 `/positions`。
- [ ] `/positions` 全 6 列双表（币种汇总 + 逐条）原样保留，不删列不丢信息（反向核验）。
- [ ] 缺数口径不变：名称缺「名称未接入」不拿代码顶替；现价 / 市值 / 盈亏缺 `—` 不补 0；只数 `countOrMissing`。
- [ ] 隐私徽标三卡常驻（加载 / 失败 / 空态都在），悬停出全文、点击展开 `PrivacyNotice` 本体，
      Esc / 外点关闭且焦点回徽标；首页不再印整段隐私长文案。
- [ ] 持仓「毛额·未扣费」徽标可见，title 含 `fee_included=false` 口径全文。

**整屏与工程**
- [ ] 五路取数各自失败只打块级 banner / 数据降级待办，不把整屏打成 error；`partial` 时成功块内容保留。
- [ ] `?state=empty|loading|error|partial|offline` 五态逐块可复现；真实请求失败不回落演示数据。
- [ ] 首页取数仍是只读聚合：无新增写请求、无页面侧计算（盈亏 / 排序 / 分位一律取后端值）。
- [ ] 卡体 `bodyClassName` 带内距类时默认 `p-4` 让位（`bodyClasses()`），`p-0` 调用点真正贴边
      （§12-L11）；全仓既有 `p-0` / `p-2` / `p-3` 调用点随之按作者本意渲染。
- [ ] `npm run typecheck` + 前端测试全绿；`check:ui` 不新增违规。

## 10. 与后端的依赖（不等，按「给不到」设计）

| 依赖 | 状态 | 首页 v2 的处置 |
|---|---|---|
| `GET /api/indices`（8 指数快照） | **已交付**：A 股 4 格走 `index_quote`，美股 2 格走 `index_quote_us`、港股 2 格走 `index_quote_hk`（均为 `tickhub_us` peer 的 `th_us_get_quote`，QUOT-471 / QUOT-560） | A 块恒渲染 8 格，全部为真实读数；任一格取不到走 §2.3 格级降级（不复用 A 股占位、不自采、不补 0） |
| `GET /api/watchlist`（自选读接口） | S2 交付；本机隐私库有 `watchlist` 表，缺读接口；UI-SPEC §0 原登记为不覆盖项 | C 块走 §4.3「接口未接入」空态，文案直说缺的是读接口 |
| `items[].name`（持仓名称） | 需后端从 `spot_quote` 带出 | 显示「名称未接入」 |
| `items[].currency`（币种） | 需 `positions` schema 增列或推导规则（DEC-033 §2.2 六字段白名单要一起改） | 汇总卡退化单行「合计」 |
| `day_pnl`（当日盈亏） | 需昨收价，`daily_k` 缓存可算 | 列保留、值 `—` + 脚注 |
| 自选管理页（增删改入口） | **已存在**：`/watchlist` → `/my-stocks?tab=watch`（全列宽表 + 增删改） | 首页 3 列摘要 + 「管理自选 →」直达（QUOT-552，§12-L07 更新） |

## 11. 线框图

v1.x 的 1440×1680 高保真线框（`home-v2-dark.png` / `home-v2-light.png`）画的是旧版满宽堆叠版式，
仅作历史参考——其中恒生科技指数格「源未接入」的降级形态自 QUOT-560（港股接 `index_quote_hk`）起
仅在取数失败时出现，不再是常态。v2.0 两行五卡以真机（5950）渲染截图为准：1280×800 一屏 + 1024×900 单栏，
暗 / 浅两主题入库 `docs/screenshots/`，与本文 §1–§6 线框逐块对齐。
图内数字全部是夹具（`?demo=1`），不是真实行情；演示水印只挂页头（`DemoStateBadge`，§7）。

## 12. 需上报 / 已裁定的决策点

| 编号 | 事项 | 默认口径（老板未否决即生效） | 需谁确认 |
|---|---|---|---|
| L01 | 参考页的「AI 联网问答 + 请接入 AI」按钮要不要照抄 | **已裁定（2026-11-07）**：对话入口照做（A 方案），但**不承诺联网搜索**。QUOT-454 的 `POST /api/research/{scope}/assistant` 只发送页面已取到的上下文，不做检索；文案红线改为「不承诺通道没有的能力」（§3.2 / DEC-067） | 老板（已拍板） |
| L08 | 首页对话里的「起后台研究任务前确认 / 取消写原因」 | **不做**：本仓没有 `pending_research` 类接口，`POST /api/research/{code}/run` 只能对已存在的 `(code, day)` 续跑单段，前端无法凭一句话起任务。真正的研究任务入口保持在个股深挖页；要这条能力需先立「意图 → 任务」接口单。**2026-11-13 补充（DEC-083 §5）**：首页通道改用 `POST /api/page/assistant` 后仍不返回该字段；若未来任何通道返回 `pending_research[]`，允许不渲染，**禁止渲染成起任务按钮**。§3.1「通道只有一条」同步改为页面助手端点 | 项目领导人（QUOT-472 已上报；QUOT-573 对通道与字段边界复核定稿） |
| L02 | 指数条单排 8 格（参考页做法） | **4 列 × 2 行**。参考页能单排是因为它的点位是 14px：9 字符 × 8.4 + `p-3` 24 = 100px，1280 档每格 108px 刚好放得下；我们的点位按 §7 层级用 `text-section`（20px），需要 132px，1440 档单排只有 128px、1280 档 108px，两档都放不下。二选一：要么点位降为 `text-body`（L0 主读数被弱化），要么新增 1440 档断点（= 新 token，本规格禁止）。默认取 4×2 保住 20px 读数 | 老板（要单排就得批「降字号」或「新断点」之一） |
| L03 | `PrivacyNotice` 提为 `components/ui/` 共用件 | 按 §5.4 执行（R-25 收口，不是新语义） | 前端架构师 |
| L04 | 首页是否保留情绪温度 | **不保留**（父任务默认口径）。开盘前 5 分钟的问题是「大盘在哪 + 我的票怎么样」，情绪温度属「今天市场热不热」，`/market?tab=sentiment` 回答它 | 老板（已在 QUOT-377 默认口径里，未否决即生效） |
| L05 | 自选是否上首页（隐私边界扩一次） | 上。沿用 DEC-033 同口径回环独占，不新增对外暴露；需要一次与持仓同规格的边界决定 | 项目领导人（后端 S2 落接口时一并登记 DEC） |
| L06 | 自选 / 持仓是否照参考页并排 | **已裁定（2026-11-11 QUOT-552 更新）**：首页改 3 列摘要并排（col-span-4 ≈322px），全 6 列双表留 `/positions`、全列宽自选表留 `/watchlist`。v1.x「不并排、各自满宽」的 824px 算式仍是 `/positions` 满宽双表的依据，不再约束首页——摘要不是两行格，不新增行高档、不写第二份表体 | 老板（已拍板，QUOT-552） |
| L07 | 卡头「管理自选 →」入口 | **已裁定（2026-11-11 QUOT-552 更新）**：放。自选页已存在（`/watchlist` → `/my-stocks?tab=watch`），首页摘要 + 跳转是正式口径；v1.x「没有自选页、不放链接」的登记作废 | 老板（已拍板，QUOT-552） |
| L09 | 三搜索合一是否新起第二套搜索实现 | **不新起**：底部输入框与搜索模态共用 `useHomeSearch` 一个 controller（同一个 query）；回车路由只新增 `homeQueryRoute.ts` 一个纯函数（疑问词表），`searchCore` / `searchIntent` / `searchRoutes` / `SearchPanel` 公开签名零改动；名称命中哪边仍由数据决定，不臆造代码 | 前端架构师（已执行，QUOT-552） |
| L10 | 最近搜索在代码直达跳转后丢失 | **已修复（QUOT-552）**：`useHomeSearch.remember` 先同步写 localStorage 再 setState——跳转会在同一批次卸载组件，只写在 setState updater 里的副作用会随卸载被 React 丢弃。属内部实现修复，公开入参与返回形状不变；同时修好既有面板 → 跳转流程 | 前端架构师（已执行） |
| L11 | `cn` 不消解 Tailwind 冲突导致卡体默认内距压过调用方 | **已修复（2026-11-11 QUOT-552）**：`cn` 是纯 join，`p-4` 在 CSS 里后生成于 `p-0`，所以 `bodyClassName="p-0"` 长期无效（全仓 ~17 处调用点都被幻影内距撑高，首页一屏预算因此超 124px）。`Card.tsx` 新增 `bodyClasses()`：调用方 className 含任一 `p*/px*/py*/pt*/pr*/pb*/pl-` 类时不再补默认 `p-4`，`CardBody` / `SectionCard` 同规则。同理 `Button` 的 `h-control` 会压过 className 的 `h-5` 等尺寸覆盖——首页页脚导航按钮因此用纯 `<button>`（与搜索历史 chip 同先例），不给 `Button` 传尺寸类。属共享件行为修复，全仓 `p-0` / `p-2` / `p-3` 调用点随之按作者本意渲染；不改 token、不加依赖 | 前端架构师（已执行） |
