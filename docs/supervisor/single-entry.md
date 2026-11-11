# 单入口启停（QUOT-523）

> 范围：把生产从 `start_board_main.cmd` / `start_watchdog.cmd` / 散落计划任务
> 收敛到一个入口——`ldst supervisor`（看板内的 `/api/supervisor/*` 是同一个引擎）。
> 账本、归属判定、状态机见 [ledger-and-lifecycle.md](ledger-and-lifecycle.md)，
> 模块与端口见 [module-matrix.md](module-matrix.md)。

## 1. 入口与命令

| 入口 | 用途 |
|------|------|
| `ldst supervisor start` | 按 `MODULE_ORDER` 全起（`--only 模块` 只起指定模块，单模块失败不挡后续） |
| `ldst supervisor stop` | **缺省是预演**：打印 `dry_run / would_stop / order`，不动任何进程 |
| `ldst supervisor stop --force` | 反向全停（`--only` 只停指定模块） |
| `ldst supervisor stop --owned-only --force` | 应用退出全停：只停本控制面 spawn 的模块，adopted peer 保留（QUOT-527） |
| `ldst supervisor restart --only 模块` | 单模块重启，其它模块 pid 必须不变 |
| `ldst supervisor status` | 每模块 `state / ownership / evidence / listening` + 账本 PID |
| `ops/host/Invoke-SingleEntry.ps1` | 计划任务 / 桌面调用的包装器：`-Action start\|stop\|restart\|status`，`-Modules`、`-Force`、`-DryRun` |
| 看板 `POST /api/supervisor/start\|restart\|stop` | 与 CLI 同一引擎、同一本账；只走回环 `_guard` |

退出码：全部成功 `0`；有模块失败 `EXIT_PARTIAL`；`restart` 不带 `--only` 为 `EXIT_CONFIG`。

包装器把 `python` 解释器形态的 `LDST_LAUNCHER` 自动补成
`python -m ladderstock.cli.main`，所以源码树与安装布局是同一套调用。

## 2. 托管范围

托管模块 = 顺序模块组（tickhub / tickhub_us / wire / senate / financecoffer /
proxyfleet）+ watch（5952 看门狗，`inherit_chain` 识别 venv `ldst.exe` 启动器的
后代）。**5950 看板本身不在七个模块里**：停模块组不会关掉看板，看板仍由自己的
任务 / 快捷方式管理，直到切换窗口被批准。

### 2.1 自愈冷启动（QUOT-526）

`LDST_SUPERVISOR_OWN_PROCESSES=1` 时，自愈引擎同时托管 `MODULE_ORDER` 全表
（`modules=` + `cold_start=True`）：账本里没有 pid 的模块按 bundle 命令表
（适配器，不读 `restart-commands.json`）立刻拉起；已在监听的匹配实例先
`adopt_module()` 认领（pid 不变，不 spawn）。默认附加模式只看账本旧口径、只认领
不拉起。主动 `stop` 会落 `stopping_by_user`，自愈不会把它撤销；要恢复用显式
`start`。决策见 [DEC-075](../decisions/DEC-075-self-heal-cold-start-bundle-command-table.md)。

## 3. 安全边界（DEC-014）

- 只停止**账本能证明由本控制面拉起**的进程树；`external`（`cmdline_mismatch` /
  `port_taken_by_unknown`）一律不碰，启动前端口被外人占用则拒绝启动该模块、如实报错。
- 外部 peer 认领（账本 `adopted_peer` 标记，QUOT-545 / DEC-078）同样绝不终止：
  `stop_all` / 单模块停 / 重启一律跳过，退出后原样保留；前任 supervisor 遗留的
  自有进程（对账续托管、无标记）仍可被全停正常停止。
- `stop` 默认预演，真正执行要求 `--force`；包装器对应 `-Force`。
- 外部占了本模块端口时整套启停是**失败关闭**的（单入口不再假装全起）。

## 4. 环境开关

| 变量 | 含义 |
|------|------|
| `LDST_LAUNCHER` | 启动器：`ldst.exe` 路径，或 `python*.exe`（自动补 `-m ladderstock.cli.main`） |
| `LDST_INSTALL_ROOT` | 安装根：定位 `LadderStock\.venv\Scripts\ldst.exe` |
| `LDST_PEER_ROOT` / `LDST_PEER_DIR_<MODULE>` | peer 仓库根 / 逐模块覆盖入口目录（bundle 相对，不写死盘符） |
| `LDST_SINGLE_ENTRY_ALLOW_EXTERNAL` | 运维验收用：`1` 时外部占口降级为只观察，不阻断 |
| `LDST_SINGLE_ENTRY_DWELL_SEC` | 运维验收常驻时长（默认 600 秒） |

## 5. 计划任务收编（只报告，不动定义）

`ops/host/Get-LegacyTaskReceipt.ps1`（缺省只读）逐条给出计划任务分类：

| 类别 | 任务 |
|------|------|
| `legacy-autostart` | `Peer Startup Check`、`LadderStock Modules`、`LadderStock Watchdog`、6 条旧双实例任务（已 Disabled） |
| `stat-monitor` | `LadderStock Watch Scan` |
| `keep-board-loop` | `LadderStock API`（5950 看板，**不动定义**）、`LadderStock Schedule` |
| `peer-peer` | `TickHub CN 8000`、`TickHub CN 8000 Watchdog`、`QF-Peer-*` |

- `-Json` 输出机读回执；`-Apply` **只导出 XML**（`%LOCALAPPDATA%\LadderStock\task-receipts`），不改任何 State。
- 脚本结尾固定回显：仍启用的旧启停链路条数 + 回滚方式（`Restore-ScheduledTasks.ps1`）。
- 禁用死任务另有 `Disable-DeadScheduledTasks.ps1`（缺省干跑、三道护栏），**本轮不执行**。

## 6. 切换与回滚

1. 在**用户批准的切换窗口**内停旧任务（`LadderStock Modules` / `LadderStock Watchdog`），
   之后由 `Invoke-SingleEntry.ps1 -Action start` 单一入口接管。
2. 切换前先 `-Action stop -DryRun` 看预演清单；回滚 = `Restore-ScheduledTasks.ps1` 恢复旧任务，
   再停模块组。
3. **本轮交付不改生产进程、不停用任何计划任务**：5950 / 8000 / 9100 / 9000 现有进程
   与 `LadderStock API` 任务定义原样保留。

> QUOT-527 起，**新的安装/开机自启入口是 `ldst service install`**（Windows 任务
> 计划程序 / macOS launchd / Linux systemd --user 各自原生单元），本节的旧任务
> 清单只用于识别与回滚，不再作为新装机的安装路径。机制与安全边界见
> [cross-platform-service.md](cross-platform-service.md)。

## 7. 验收证据

| 证据 | 用例 |
|------|------|
| 单入口打开全起、常驻不丢子进程、关闭全停 + 端口释放 + 零残留 + adopted 不碰，两轮幂等 | `tests/ops/test_lifecycle_single_entry.py::test_single_entry_open_all_up_close_all_down`（peer 入口在部署树时设 `LDST_PEER_ROOT`，如 `D:\workplace`；常驻时长用 `LDST_SINGLE_ENTRY_DWELL_SEC` 压短） |
| 自愈快照与启停引擎共用一本账 | `tests/ops/test_lifecycle_single_entry.py::test_selfheal_and_process_engine_share_one_ledger` |
| 包装器 BOM、dry-run 只打印、stop 缺省预演 | `tests/ops/test_single_entry_ops.py` |
| 计划任务回执分类、`-Apply` 不改 State | `tests/ops/test_single_entry_ops.py` |
