# QUOT-484 退出停止清单确认 + 模块归属 / 自愈状态展示

- Issue：QUOT-484（拆自 QUOT-482）
- 范围：前端 `frontend/`（`SettingsScreen` 增量改动），不改后端数据语义
- 分支：`agent/agent/8e8cc4b21a2f` → PR base `main`

## 交付内容

### A. 「将停清单」确认

- 退出（子模块区「退出（停止清单）」）与单模块「停止」都先进入清单弹层，
  不再直接执行停止。
- 清单是**可见文本列表**（非图标）：每行 = 模块名 / PID / 端口 / 来源 / 命令行摘要。
  来源分两档并视觉区分：`本程序启动`（中性）与 `外部接管`（warn 令牌）。
- 命令行摘要默认截断，可「展开完整命令行」；后端若只回传 `cmdline_hash`（当前实现），
  如实显示「命令行摘要：后端本次未提供（只回传 cmdline_hash）」。
- 确认需二次确认：勾选「我确认停止以上 N 个进程（含外部接管的 M 个）」后按钮才可用；
  Esc / 取消 = 不停止。
- 取消后，清单里的外部进程在卡片上保留可见标记
  「未停止（外部进程）：上次停止清单已取消，该进程仍在运行」，并 toast 说明。
- 全是本程序启动（无外部进程）时**不弹冗余确认**，直接停止并给出可见说明；
  清单为空（全部未运行）时提示「当前离线：没有正在运行的模块」。
- 后端尚未提供 `/api/supervisor/stop` 时（404），如实提示「482-B 尚未接入……
  本次没有停止任何进程」，不假装已停。

### B. 模块归属 + 自愈状态

- 每张 Peer / Component 卡片显示归属徽标，**只翻译后端字段**：
  - `origin=spawned` 或 `ownership=managed` → `主程序托管`（ok）
  - `origin=adopted` / `spawned-via-task` → `外部接管`（warn，停止前需确认）
  - `ownership=external` → `非托管（观测）`（neutral，不列入停止清单；
    该结论优先于账本里可能过期的 `origin`）
  - 无数据 → `归属未知（supervisor 未接入）`
- 自愈展示：`self_heal.status=healing` → `正在重拉（第 N/3 次）`；
  `gave_up` / `health=needs_human` → 红色卡片 + `需人工处理（已停止自愈）` + 「重试」
  （调用既有 `POST /api/supervisor/state/{module}/reset`）。
- 前端不扫端口、不推断归属；数据只来自 `GET /api/supervisor/state`。

## 本次对接的契约版本

对接的是 **已合入 main 的 QUOT-482（482-A 归属 + 482-C 自愈）**：

| 端点 | 状态 | 用途 |
| --- | --- | --- |
| `GET /api/supervisor/state` | 存在（482-A + 482-C 合并载荷） | 归属 / 自愈 / 停止候选的唯一数据源 |
| `POST /api/supervisor/state/{module}/reset` | 存在（482-C） | 「重试」解除 needs_human |
| `POST /api/supervisor/stop` | **不存在（482-B 未落地）** | 前端按 `{confirm: true, modules: string[]}` 声明契约，404 时如实降级 |

合并载荷：`selfheal.snapshot()` 提供每模块 `health/state/self_heal/origin/cmdline_hash…`，
`engine.state()` 叠加 `ownership/evidence/listening/ports`，顶层含 `configured/order/supervisor_pid`。

**后端当前不提供 `cmdline` 明文**，只回传 `cmdline_hash`。前端已兼容该字段
（若将来后端补上 `cmdline` 会自动显示）。

## 归属来自账本的证据

前端不做任何端口扫描或归属推断，`origin` 在服务端由账本直出：

- `src/ladderstock/supervisor/selfheal.py:293-298` — `snapshot()` 逐个读
  `self.ledger.get(module)`；
- `src/ladderstock/supervisor/selfheal.py:352` — 快照里的 `"origin": entry.origin`
  直接取自账本条目；
- `src/ladderstock/supervisor/ledger.py:41-44,75` — `origin` 只有
  `spawned`（本任 supervisor 拉起）/ `adopted`（崩溃重启后对账确认仍存活的前任进程）；
- `src/ladderstock/supervisor/engine.py:295-348` — `state()` / `_ownership()`
  只依据「账本 + 命令行指纹 + 是否在听」，注释明确「不扫端口猜归属」；
  `ownership=external` 的证据为 `cmdline_mismatch` / `port_taken_by_unknown`。

`supervisor-state.sample.json` 是**按上述契约字段构造的前端联调样例**
（由 `frontend/scripts/quot-484-mock-api.mjs --dump` 生成，用于截图与单测），
不是生产环境抓包；它只用于证明前端消费的字段形状与后端一致。

## 截图（浅色 + 深色）

全部位于 `docs/screenshots/quot-484/`：

| 场景 | 浅色 | 深色 |
| --- | --- | --- |
| 将停清单弹层（本程序启动 + 外部接管同屏，PID/端口/命令行，勾选二次确认） | `settings-484-dialog-light.png` | `settings-484-dialog-dark.png` |
| 取消后外部进程保留「未停止（外部进程）」标记 | `settings-484-cancelled-light.png` | `settings-484-cancelled-dark.png` |
| 全部本程序启动：不弹冗余确认，仅可见说明 | `settings-484-empty-light.png` | `settings-484-empty-dark.png` |
| 归属三档 + 自愈进行中 + 红色需人工处理卡片 | `settings-484-cards-light.png` | `settings-484-cards-dark.png` |

截图使用 `frontend/scripts/visual-shot.mjs`（无头 Chrome，1440×1500）对
`frontend/scripts/quot-484-mock-api.mjs` 的三个场景（`dialog` / `empty` / `cards`）拍摄。

## 验证

- `npx tsc --noEmit`：通过
- `npx vitest run tests/quot-484-supervisor-panel.test.tsx`：8/8 通过
- 前端全量测试：877 个用例中 876 通过；`tests/nav-routes.test.tsx` 一条在并发高负载下单次超时，
  单独重跑 41/41 通过（与本改动无关的既有时序用例）
- `npm run build`：通过（仅既有 >500 kB chunk 体积 warning，与本次改动无关）

## 本次未覆盖

- `POST /api/supervisor/stop` 的后端实现（QUOT-482-B）；前端在 404 时如实报未执行。
- `cmdline` 明文回传（当前只有 `cmdline_hash`）。
- 托盘 / 关闭到后台（按派单要求不做）。
