# DEC-071 首页卡级刷新：卡头常显手动 ⟳（回退「只留顶栏一条刷新」）

- 日期：2026-11-10
- 提出：项目领导人（QUOT-521，拆自 QUOT-519；用户确认要卡级刷新）
- 状态：生效（代码与验收口径见 QUOT-521）
- 编号：`DEC-071`。`docs/decisions/` 已合并的最大号为 DEC-070，顺延一位。
- 关联：DEC-063（单顶栏）/ `docs/specs/SPEC-HOME-V2.md` §2 / §4 / §5 / §6 / §9 /
  `docs/UI-SPEC.md` §3.5 / §11.5 / `frontend/tests/quot-521-card-refresh.test.tsx`
- 参考实现：`C:\Users\Administrator\Desktop\vibe-astock-main\desktop\src\verticals\finance\`
  的首页指数条与自选页卡头（只读参照，未改动）

## 1. 问题

QUOT-457/458（DEC-063）把全平台刷新收敛到顶栏一颗「刷新全部」，首页五块（指数 / 自选 /
持仓 / 验证 / 盯盘快照）只剩整页刷新。两处不合用：

1. **一块坏了要刷五块**：某一路上游慢/抖动时，顶栏刷新会把另外四路也重新取一遍，
   盘中对上游额度不划算，也让人分不清是哪一块在转。
2. **休市没有刷新入口**：指数条休市停 15s 轮询（QUOT-470 的设计），但用户仍想手动补一次
   收盘快照；顶部「刷新全部」语义是整页，不是「补这一块」。

参考页（vibe-astock）的卡头本来就有 ⟳，且休市时可点（点击会强制取一拍）。产品决定回退
「只留顶部一条刷新」，恢复卡级入口。

## 2. 决定

### 2.1 范围

首页 A/C/D/E/F 五张**独立取数**的卡，卡头右侧各挂一颗常显手动 ⟳：

| 卡 | 端点 | 无障碍名 |
|---|---|---|
| A 大盘与全球指数 | `GET /api/indices` | 刷新大盘与全球指数 |
| C 我的自选 | `GET /api/watchlist` | 刷新我的自选 |
| D 我的持仓 | `GET /api/positions` | 刷新我的持仓 |
| E 待办 | `GET /api/validation` | 刷新待办 |
| F 最近异动 | `GET /api/watch/snapshot` | 刷新最近异动 |

B「今天，想研究什么？」是本机检索 + AI 对话，没有可重取的只读块，不挂 ⟳。

### 2.2 与顶栏的关系

顶栏「刷新全部」**保留**。两层语义并存：

- 顶栏 = 整页重取（五路）；
- 卡头 ⟳ = 只重取本块。

这不是二选一：QUOT-457 收敛的是「横栏条数」，不是「刷新粒度」；把 ⟳ 放回卡头不新增横栏。

### 2.3 行为契约

`CardRefreshButton`（`frontend/src/components/ui/Button.tsx`）走
`SectionCard` / `CardHeader` 已有的 `actions` 槽，卡件不给按钮写第二套样式。

- **常显**：卡片渲染出来就有 ⟳；无数据、错误、离线、休市都在。整屏 loading/error 时
  卡片本身不渲染（骨架/错误页），此时没有 ⟳，属于整屏态而非卡级可操作态。
- **只重取本块**：调 `BlockView.onRefresh`，落到该块自己的 `useAsync`；
  其余四路请求数不变。
- **静默优先**：已有数据时用 `refresh({ manual: true })`，不回到骨架、失败保留上一帧；
  无数据（错误 / 空 / 离线）时走 `reload()`（load 模式才会把失败写回 `error`）。
- **休市可用**：`onRefresh` 不受 `useTradingSession` 门控；休市只是停自动轮询，
  手动补拍照发。
- **盘中节拍不变**：15s `usePolling` 照旧；手动 ⟳ 只是插一拍，不重置计时器，
  节拍文案仍由 `INDICES_POLL_SECONDS` 这一个常量供数，不写字面量。
- **取数中禁用**：本块请求在途时 ⟳ 转圈并 `disabled`，避免重复点出两拍。

### 2.4 不动的部分（红线）

- 数据口径与缺数口径零改动：不补 0、不编来源、不改 `freshness` / `source_status` 判定；
- 不新增接口、不改响应契约、不改后端；
- 不硬编码模型清单 / 模块清单；
- 只改视觉与刷新交互。

## 3. 落点

- `frontend/src/components/ui/Button.tsx`：`CardRefreshButton`（`IconButton` + `RefreshCw` /
  `Loader2`，`size-4`，ghost）；
- `frontend/src/screens/home/useHomeSource.ts`：`BlockView` 增加 `onRefresh` / `refreshing`，
  `asBlock` 统一有数据/无数据两支；
- `HomeIndexStrip.tsx`、`HomeWatchlistCard.tsx`、`HomePositionsCard.tsx`、`HomeScreen.tsx`
  （E/F）接入 `actions` 槽。

## 4. 验收

- 单测 `frontend/tests/quot-521-card-refresh.test.tsx`：五卡 ⟳ 常显；点 A 只重取指数；
  C/D/E/F 各刷自己的端点；休市停轮询但 ⟳ 生效且读数前进；手动插一拍后 15s 自动节拍仍在；
  静默重取不闪骨架。
- 浏览器取证：`docs/screenshots/home-quot521-open-dark.png`、
  `home-quot521-open-tall-dark.png`、`home-quot521-closed-dark.png`、
  `home-quot521-closed-tall-dark.png`（含浅色两张），1440×900，横向溢出 0。
- 回归：`npm test` 全绿；`npm run typecheck`、`npm run build` 通过。
