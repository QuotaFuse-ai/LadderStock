# QUOT-522：网页打开即自启自有子进程 + 全页 fetch-on-open 复核

关联：QUOT-519（拆单来源）、QUOT-482（supervisor 内核）、DEC-069（崩溃自愈口径）
状态：已交付，待验收

## 1. 目标

看板（`ldst run`，127.0.0.1:5950）一打开，**不依赖任何 CLI 手动命令、不依赖计划任务**，
两个自有子进程自动在跑：

| 模块 | 进程 | 职责 |
|------|------|------|
| `watch_guard` | `python -m ladderstock.cli.main __watch-guard --tick <N>` | 周期跑 `watch start`（盯盘扫描，离线单遍） |
| `scheduler` | `python -m ladderstock.cli.main schedule run` | 取数调度器（APScheduler 长驻） |

peer（tickhub / tickhub_us / wire / senate / financecoffer / proxyfleet）维持 **adopt-only**：
只认领已在监听的实例，绝不自动拉起、绝不自动终止。

## 2. 实现

新增 `src/ladderstock/supervisor/page_boot.py`，在 `web/server.py` 里用
`mount_page_boot(app, boot)` 挂两处：

- **startup** → `boot.ensure()`：adopt peer → 起自有子进程（幂等，已在跑则 `already_running`，
  pid 不变）；
- **shutdown** → `boot.stop_children()`：只停 `origin == "spawned"` 的自有子进程。

复用 QUOT-482 的 `Ledger`（`data/supervisor/ledger.json`）与 `HostPivot`
（`WindowsHostPivot.spawn` 用 `CREATE_NO_WINDOW`，日志落 `logs/<module>.log`）。
**没有新建 autostart 机制**：没有 Run 键、没有计划任务、没有启动文件夹项。

开关：`LDST_PAGE_AUTOSTART=0` 关闭（默认开）。`ensure()` 同时被
`POST /api/supervisor/page-boot`（看板刷新时前端触发）调用，实现「打开/刷新页面即拉齐」。

### 2.1 watch 守护的启动空档

启动时若本机还没有分钟样本，`watch start` 以退出码 4 退出。守护按
`GUARD_RETRY_DELAYS = 5s → 15s → 45s → 120s → 300s` 退避重试，之后回到固定 tick；
这是正常形态，不是故障。

## 3. Windows 无控制台进程的优雅停止（关键事实）

实证：由看板（`CREATE_NO_WINDOW`）拉起的 python **收不到优雅终止**——
`taskkill /PID <pid> /T` 只能报「只能强制终止」（rc 128），没有任何信号能被捕获。

因此关闭看板时**两个自有子进程都走「退出标记」协作停止**：

1. 看板写 `logs/<module>.stop`（带 ISO 时间戳）；
2. 子进程侧的检查：
   - `schedule run`：`cli/main.py::_stop_when_marked` 在调度器 `start()` 前挂一个 2 秒间隔的
     内部 job，标记检查到点后由**独立线程**调用 `scheduler.shutdown(wait=False)`
     （在 job 线程里 shutdown 会 `RuntimeError: cannot join current thread`），主循环正常返回；
   - `__watch-guard`：`watch_guard_main` 在每个扫描空档（0.2s 粒度）检查标记，出现即返回 0；
3. 看板侧 `_request_stop` 先等 `STOP_GRACE_SEC = 5.0` 秒子进程自退，**等不到才**
   `terminate_tree(pid, force=True)` 兜底；
4. 无论结果如何都清理标记文件，下一次打开从干净状态开始。

标记路径通过子进程环境变量传入（`LDST_PAGE_BOOT_STOP` / `LDST_WATCH_GUARD_STOP`），
不写死在代码里，避免多实例/多 data_root 串台。

## 4. 前端 fetch-on-open 复核

只读页（首页/自选/持仓/盘中/涨停池等）进入即取数，不需要先跑 CLI 命令：
`frontend/tests/readonly-mount-fetch.test.tsx` 6 项全过（含 `/positions` 挂载后立即
`GET /api/positions`）。本单未改动前端代码，复核结论：**保持**。

## 5. 验收证据

| 检查 | 结果 |
|------|------|
| `tests/unit/supervisor/test_page_boot.py` + `tests/unit/web/test_page_boot_endpoint.py` | 全过 |
| `tests/unit/cli/test_page_boot_stop_hook.py`（新） | 全过 |
| `tests/ops/test_lifecycle_page_boot.py`（真实宿主，仅 Windows） | 全过：两个自有子进程存活、二次 ensure pid 不变、peer pid 不变、关闭后自有子进程全死、peer 存活 |
| 真实子进程探针 | `schedule run` 写标记后正常退出 rc 0，日志无 traceback；`stop_children()` 两个子进程均 `stopped` |
| `frontend/tests/readonly-mount-fetch.test.tsx` | 6 passed |
| 残留进程 | 测试结束无新增 `schedule run` / `__watch-guard` 进程（既有 PID 4416 为用户先前手动起的，未动） |

## 6. 边界与已知取舍

- **不覆盖**：看板被强杀（`taskkill /F` 看板本身）时来不及写标记，自有子进程会留下。
  下一次打开看板时 `_find_by_cmdline` 会认领同一命令行的进程（`already_running`，不重复拉起），
  不会出现双实例；要彻底清掉请用 `/api/supervisor` 的停止面。
- **peer 永不代停**：`stop_children` 只处理 `origin == "spawned"` 记录。
- 单实例假设：同一 `log_dir` 只应有看板在写标记；不同 data_root 的看板互不影响。
