# DEC-077：开机自启改用三平台原生服务单元（QUOT-527）

日期：2026-11-11
关联：QUOT-519（伞单）、QUOT-523（单入口 supervisor）、DEC-014（只动自有进程）、
DEC-071（peer adopt-only）、DEC-075（冷启动命令表）
状态：已定稿

## 背景

单入口 supervisor（QUOT-523）解决了「谁拉起谁、怎么停」的问题，但**怎么在
开机/登录时拉起单入口**仍假设 Windows：`scripts/install-service.ps1` 注册两个
S4U 计划任务（`LadderStock API` / `LadderStock Watchdog`），macOS 与 Linux 没有
对应能力，且与「不依赖散落计划任务」的目标冲突。托盘退出也没有统一契约：
直接杀 supervisor 会连同 adopted peer 一起进风险区。

## 决策

1. **三平台各自原生机制，不引入自研守护进程**：
   - Windows：任务计划程序 XML + `schtasks`（登录触发、交互令牌、`IgnoreNew`、
     无执行时限、失败重启 `PT1M × 3`）；
   - macOS：launchd LaunchAgent `.plist` + `launchctl bootstrap gui/<uid>`
     （`RunAtLoad=true`、`KeepAlive=false`）；
   - Linux：systemd `--user` unit + `systemctl --user enable --now`
     （`Restart=on-failure`、`KillMode=control-group`）。
   注册单元名固定为本应用专属（`LadderStock Single Entry` /
   `com.quotafuse.ladderstock` / `ladderstock.service`），**只增删自己这一条**，
   不扫描、不改写任何旧计划任务。
2. **服务入口统一为隐藏命令 `ldst __service`**：看板 + 自愈 + 自有子进程，不打开
   浏览器；打包态跑自身 exe，源码态优先 console-script，再退回
   `python -m ladderstock.cli.main`。服务定义不依赖 cwd / PATH / 系统 Python。
3. **安装/卸载默认 dry-run，`--apply` 才动 OS**；`--apply` 还要求目标平台等于
   当前平台（平台闸门）。跨平台只在 `service render` 生成产物，带到目标机器再装。
   命令一律 argv 列表、`shell=False`；失败关闭，反注册未成功绝不删产物。
4. **密钥不进服务定义**：`ServiceSpec` 渲染前拒绝 `TOKEN / SECRET / PASSWORD /
   API_KEY / DSN` 等环境变量名。密钥只进进程环境或系统凭据存储。
5. **「应用退出」= 只停自有子进程**：新增 `SupervisorEngine.stop_owned()`，只停
   `origin="spawned"` 的模块；`origin="adopted"` 的 peer（tickhub:8000 /
   mem0:9100 / mem0-dev:9000 / financecoffer:8100）原样保留并回报
   `kept_adopted`。托盘退出、看板关停钩子、服务停止统一走这条路径；
   `supervisor stop --force`（显式全停）仍保留停全部账本记录的语义。
6. **托盘是可选能力**：`pystray + pillow` 作为 extra，不进默认依赖；无 GUI 或 CI
   用 `service tray --headless --quit-after 0` 验同一条退出契约。

## 后果

- 三平台都能用同一条 `ldst service install` 装成登录自启；卸载只作用于本单元。
- Windows 旧的 S4U 双任务脚本不再被新安装流程使用；旧任务定义**不由本单删除**，
  切换窗口由运维负责（回滚方式见 `ops/host/Restore-ScheduledTasks.ps1`）。
- 托盘退出不会误杀 adopted peer；但也不会替用户停掉外部实例，面板会显示
  `kept_adopted` 供人工确认。
- 未装 `pystray` 时服务照常运行，只是没有托盘；退出用 OS 服务停止 / Ctrl+C。
- Linux 无登录会话时需 `loginctl enable-linger`，属于宿主配置，不在本单自动修改。

## 验收

| 场景 | 用例 |
|---|---|
| 三平台产物（XML/plist/unit）内容、编码、注册命令 | `tests/unit/service/test_backends.py` |
| dry-run 零副作用；平台闸门；失败关闭；清单 | `tests/unit/service/test_manager.py` |
| 托盘退出幂等 / 失败可读 / headless | `tests/unit/service/test_tray.py` |
| `stop_owned` 只停 spawned、保留 adopted | `tests/unit/supervisor/test_stop_owned.py` |
| CLI 参数、退出码、tray 契约 | `tests/unit/cli/test_service.py` |
| 关停钩子改走 `stop_owned` | `tests/unit/web/test_supervisor_process_endpoints.py` |
| `--output` 契约与 argv 矩阵 | `tests/unit/test_cli_output_option.py`、`tests/unit/test_cli_surface.py`、`tests/compat/test_legacy_entry.py` |
