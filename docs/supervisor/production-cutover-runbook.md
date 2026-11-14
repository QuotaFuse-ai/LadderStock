# 生产切换 Runbook：旧模型 → 单入口 supervisor（QUOT-550）

> 目标：把生产宿主从旧模型（`start_board_main.cmd` + `start_watchdog.cmd` +
> 散落计划任务）切换到单入口 `ldst supervisor`（QUOT-523/526 已落地）。
> 本文档是切换窗口的操作手册：步骤、验证清单、回滚路径、peer adopt-only 保全。
> 架构与安全边界见 [single-entry.md](single-entry.md)，模块与端口见
> [module-matrix.md](module-matrix.md)。

## 0. 切换前提（全部满足才开窗口）

- [ ] 六个 peer 部署就位（TickHub-CN / TickHub-US / Wire / StockSenate /
      FinanceCoffer / ProxyFleet），WSL 默认发行版可用，ProxyFleet 计划任务存在。
- [ ] 宿主上有管理员权限（`schtasks /change` 需要）。
- [ ] `ldst supervisor status` 能跑通、显示全部七模块。
- [ ] 验收证据：`LDST_SELFHEAL_COLD_START=1 python -m pytest
      tests/ops/test_lifecycle_selfheal_cold_start.py` 已在**同一台宿主**跑绿
      （全停后自愈全拉回、adopted PID 不变）。QUOT-550 另附 ephemeral 自愈验收
      （`tests/ops/test_lifecycle_quot550_selfheal_ephemeral.py`，不占生产端口）。
- [ ] 通知相关方切换窗口时间（预计停机 ≈ 模块全停 + 全起的时长，分钟级）。

## 1. 环境边界（红线，全程生效）

| 资源 | 约束 |
|------|------|
| 生产数据目录（`D:\QuotaFuseModules\LadderStock` 等） | 只读观察；不写入、不清理、不删除 |
| peer 模块（TickHub-CN 8000 / mem0、Wire、StockSenate 等） | 只 **adopt**（认领），绝不终止 |
| 外部占口进程（在听但不命中识别关键字） | 绝不终止（DEC-014）；单入口失败关闭，先人工让口再切 |
| 9000 / 9100（宿主 mem0 / MCP 服务） | 一律不碰，不属于托管范围 |

`LDST_SUPERVISOR_OWN_PROCESSES=1` 才允许冷启动拉起；默认附加模式只认领。

## 2. 切换步骤

### 第 1 步：切换前快照（回滚依据）

```powershell
# 记录当前在听端口与归属 PID（逐条存档）
ldst supervisor status | Tee-Object -FilePath logs\cutover\status-before.txt
Get-NetTCPConnection -State Listen |
  Where-Object { $_.LocalPort -in 8000,8002,8787,8765,8786,8790,8791,8788,8789,8100,5950,5951,5952 } |
  Select-Object LocalPort, OwningProcess |
  Export-Csv logs\cutover\ports-before.csv -NoTypeInformation
```

### 第 2 步：停旧模型（在批准的窗口内）

```powershell
# 2a. 停看门狗（先停它，避免旧 watchdog 在切换中途拉起旧形态模块）
schtasks /change /tn "LadderStockWatchdog" /disable   # 任务名以宿主实际为准
schtasks /end   /tn "LadderStockWatchdog"             # 若在运行

# 2b. 停旧看板入口
schtasks /end /tn "LadderStockBoard"                  # start_board_main.cmd 对应任务

# 2c. 确认 5950/5951/5952 已释放、各模块端口当前归属已存档
Get-NetTCPConnection -State Listen -LocalPort 5950,5951,5952 -ErrorAction SilentlyContinue
```

> 若计划任务名与宿主不一致：`schtasks /query /fo csv | findstr /i ladder`
> 找实际名字；只 disable/end，不删除任务（回滚要用）。

### 第 3 步：单入口首次拉起（认领为主）

```powershell
$env:LDST_SUPERVISOR_OWN_PROCESSES = "1"
ldst supervisor start
ldst supervisor status
```

预期：

- 已在监听且命中识别关键字的实例 → `ownership=adopted_peer`，**PID 与
  ports-before.csv 一致**（tickhub 8000 等既有 peer 进程被认领，不重启）；
- 没有实例的模块 → 按 bundle 命令表拉起，`ownership=managed`；
- 外部占口 → 该模块失败关闭并报「占用」，**不动外部进程**：先人工确认并让口，
  再 `ldst supervisor start --only <模块>` 补起。

### 第 4 步：验证清单（逐条勾）

- [ ] `ldst supervisor status`：七模块全部 `state=running`（或 `adopted_peer`），
      无 `needs_human`。
- [ ] 端口归属比对：各模块端口 PID 与 ports-before.csv 中 adopted 项一致
      （**认领进程 PID 一个都不许变**）。
- [ ] 看板 5950 可访问；`GET /api/supervisor/state` 与 CLI status 同账。
- [ ] 看门狗收编验证：`watch` 模块 `ownership=managed` 且 5952 在听——
      旧 watchdog 已停，由单入口接管。
- [ ] **保活真实恢复**：任选一个 managed 模块（建议 tickhub_us），
      `ldst supervisor restart --only tickhub_us`；确认其它模块 PID 全部不变、
      该模块换新 PID 且端口恢复。
- [ ] 自愈验证（可选，窗口内做）：强杀一个 managed 模块进程（如 watch），
      等 ≤60s 轮询确认端口被自愈拉回监听、账本换绑新 PID。
- [ ] 自愈上限验证（可选）：连续 3 次崩溃后 health 置 `needs_human` 并通知，
      `ldst supervisor reset --only <模块>`（或看板 reset）解除后能再拉起。

### 第 5 步：收尾

- [ ] 旧入口的两个计划任务保持 `disabled`（不删除），在 runbook 附录登记任务名。
- [ ] `logs\cutover\`（status-before.txt / ports-before.csv / status-after.txt）归档。
- [ ] 观察一个巡检周期（≥15 分钟）无 `needs_human` / 无端口冲突弹窗后宣布切换完成。

## 3. 回滚路径（任一验证项不可恢复时）

```powershell
# 3a. 单入口全停：只停本控制面 spawn 的进程，adopted peer 原样保留（PID 不变）
ldst supervisor stop --owned-only --force

# 3b. 确认 adopted peer 仍在听（对照 ports-before.csv）
ldst supervisor status

# 3c. 恢复旧入口
schtasks /change /tn "LadderStockWatchdog" /enable
schtasks /change /tn "LadderStockBoard" /enable
schtasks /run /tn "LadderStockBoard"
```

回滚验收：5950 看板恢复可访问、各 peer 端口 PID 与切换前一致
（单入口全程未终止任何 adopted 进程）、旧 watchdog 回到 5952。

## 4. 已知边界与失败模式

- **外部占口**：单入口失败关闭是设计行为（DEC-014）。处理方式永远是「让口」，
  不是「杀掉」；占用者身份不明时先 `Get-CimInstance Win32_Process` 核实。
- **FinanceCoffer / ProxyFleet**：形态是 WSL compose / 计划任务，适配器按形态停起；
  首次切到 managed 后旧计划任务（ProxyFleet）可能被单入口与任务双重管理——
  切换完成后将 ProxyFleet 旧任务 disable，托管交给单入口。
- **账本丢失**：冷启动会按 bundle 命令表全拉一遍；已在运行的匹配实例走认领，
  不会双实例（`AdapterRestarter` 先 adopt 后 spawn）。
- **`needs_human`**：5 分钟窗口 3 次重拉超限后自愈停止动作，必须人工 reset；
  这是「不无限重试也不静默放弃」的设计口径，不是故障。
