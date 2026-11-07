# DEC-067: 第 9 组「个股深挖」改名「研究工具」并新增 `/skills` 页面

日期：2026-11-07 ｜ 状态：待复核（GM） ｜ 关联：QUOT-474（本单）、
DEC-044（导航分组与页面聚合）、DEC-046（复盘三屏不合并）、
参考数据 `content/skills/index.json`（11 包）+ `content/skills/catalog.json`（70 方向）

编号取值：rebase 到 main `f2874e0` 后重扫 `docs/decisions/`，最大号为 `DEC-066`
（`DEC-065` 已被 QUOT-467 的持仓账本决策占用），故本决策取 `DEC-067`。

## 1. 问题

Skill（可执行工具包）此前只活在后端与 CLI 里，前端没有任何一个页面能回答
「现在有哪些工具、各能做什么、哪些已接通、哪些还没接」。老板要求把
「个股深挖」这个一级组扩成「研究工具」，并补一个能总览全部工具的页面。

二次确认时口径收紧到三条：**组只改名不新增**、**列表按方向条目而非按包**、
**顶栏加一颗全局「问 AI」入口**。本决策把这三条钉死。

## 2. 决定

### 2.1 导航分组规则（修订 DEC-044 的「不许动」部分）

DEC-044 定的分组规则是「组内只放已有路由 + 新页面必须落到某一组内」。本次修订为：

- 规则保留：**不允许为了新页面新增第 10 个一级组**；
- 规则放宽：**允许组内新增页面、允许组内新增二级项**。

具体到本单：第 9 组 key `deep-dive` **整体改名** `research-tools`、label `研究工具`，
**不是新建第 10 组**——避免侧栏同时出现「研究工具」和「个股深挖」两条平级项。
组内两页固定为：

| 顺序 | 页面 | 路径 |
|---|---|---|
| 1 | 工具总览 | `/skills`（新增） |
| 2 | 个股深挖 | `/research`（**路径不变**，深链 `/research/:code`、`/research/:code/report` 全部保留） |

其余 8 组顺序不动，最终顺序：
首页 → 市场 → 盯盘 → 复盘 → 日志 → 我的股票 → 验证 → 产业研究 → 研究工具 →（沉底）设置。

### 2.2 `/skills` 列表是「方向条目」不是「工具包」

数据源 `content/skills/catalog.json`（**目录条目清单**），共 **70 条**，
每条的粒度是「一个包的一段步数」而不是整包。包的清单在 `index.json`（**11 包**）。

- 70 条分 12 个方向组：TickHub-CN「个股」12 /「盘面」8 /「板块」6 /「事件」7 /「宏观」5，
  TickHub-US「个股」6 /「特色」5，QuotaFuse Wire 3、StockSenate 3、LadderStock 4、
  跨项目 8、FinanceCoffer 3。
- 状态分布：ready 41 / partial 2 / unconnected 20 / planned 2 /
  existing_page 2 / not_supported 3。

页面形态：

1. 顶部 12 个方向筛选 chip（横向可滚动），默认「全部」，选中写 `?group=`；
2. 每行：编号(id) · 中文名(label) · 优先级徽标(P0/P1/P2/P3) · 状态徽标 ·
   归属包(package) · 一句话说明(note)；
3. 状态徽标六色分明：
   - ready = 可用（accent）
   - partial = 部分可用（warn）
   - unconnected = 未接通（muted / 灰卡 / 小字写 `blocked_by`）
   - planned = 待建（muted + 虚线边框）
   - existing_page = 已在页面（可点，跳承载页面，如 涨停样本 → `/limit-up`）
   - not_supported = 本阶段不做（muted + note 写原因）
4. 点 ready / partial → `/skills/:id`（**`id` 是 catalog 条目 id，不是包名**）：
   定位到映射的包与步（`steps:[2,3]` → 高亮并展开那几步），
   「问 AI」预填该方向 `steps[0]` 的提示词；
5. 点 unconnected → 仍可看步骤与提示词，但**不给执行按钮**，只展示 `offline_notice`；
6. 点包名 → `/skills/pkg/:name`，看整包 7/6/5 步总览。

`/skills` 不写 mock / 演示兜底：`index.json` 或 `catalog.json` 读不出来时给显式空态
（`ErrorBlock` + 错误码），不拿编的条目糊弄。

### 2.3 顶栏全局「问 AI」入口

> 实现归属：本单拆分后由 **QUOT-475** 交付，已随 #144 合入 main。本节只记录 474 侧
> 「/skills 三屏必须走同一个抽屉」的接缝约束，重复实现部分以 main 为准。

- 位置：顶栏最右，`Sparkles` 图标 + 文案「**问 AI**」，accent 描边、圆角、hover 变色；
- 保留原来的「接入 AI」为**纯图标次级按钮**（配置入口），**不合并**；
- 按钮挂在 **Layout 级、全页面可用**；
- 点击打开**页级研究助手抽屉**：按当前路由取绑定 skill，
  标题 `研究助手 · {页面名} · {skill 中文名}`；快捷问题取该 skill 第一步的引导；
  输入框默认空；发送时上下文 = `页面 + skill + 当前对象`；
- 绑定是 `unconnected` 时抽屉顶部展示 `offline_notice`，发送前先返回该提示、不吐数字；
- 页面 ↔ skill 绑定表落在 `frontend/src/lib/skills/pageBindings.ts`；
- **必须复用 `ResearchAiDock`**（不写第二个抽屉），只允许新增可选 props：
  `pageKey` / `boundSkill` / `initialDraft` / `offlineNotice`。
  Layout 那份用 `trigger="drawer-only"`，因此不会渲染出第二颗按钮 / 第二个抽屉。

### 2.4 数据落地

`ladderstock-skills/` 的 11 个包目录 + `index.json` + `catalog.json`
原样拷进仓库 `content/skills/`。其中：

- `index.json` = 包清单（11）；
- `catalog.json` = 方向清单（70）。

本地校验：`cn=134 us=41`；所有 `tools` 引用命中真实工具名；未知引用 0。

## 3. 被否掉的替代方案

- **新增第 10 组「研究工具」**：侧栏会同时出现「研究工具」与「个股深挖」两条平级项，
  用户分不清谁是谁——否掉，改为整组改名。
- **`/skills` 按 8 个可执行包列表**：8 只是能跑的包，回答不了「这个方向能不能用」；
  老板要的是 70 条方向条目——否掉按包列表。
- **每个 skill 各占一条侧栏项**：侧栏会被 70 条撑爆——否掉，侧栏只留一颗
  「工具总览」入口。
- **在代码里再写一份 skill 清单**：会与 `content/skills/*.json` 双写漂移——
  否掉，运行时读 `index.json` / `catalog.json`。
- **重写 `steps.json` 的工具名或步骤文案**：包是数据源，前端只读不改——否掉。
- **再写一个 AI 抽屉**：与既有 `ResearchAiDock` 重复——否掉，扩可选 props 复用。
- **合并「接入 AI」进「问 AI」**：一个是配置入口、一个是提问入口，语义不同——否掉，两者并存。

## 4. 代价与已知边界

- `/skills/:id` 与 `/skills/pkg/:name` 是深链，由 `/skills` 列表页逐条链入，
  不单独挂侧栏入口，按 `NON_PAGE_ROUTES` 处理（与 `/research/:code` 同口径）。
- 70 条目录一次性渲染，不分页；条目量再上一个数量级时需要补虚拟滚动（本期不做）。
- `unconnected` 方向只读，执行链路未通；等对应包接通后状态改 `ready` 即可，前端无需改动。
- 绑定表是手工维护的 16 条规则（最长前缀匹配），新增页面要记得补一条，
  否则该页顶栏「问 AI」只给出兜底标题。
