# 跨平台服务/开机自启/托盘（QUOT-527）

> 范围：把「装成服务 / 开机自启 / 托盘退出全停」从 Windows-only 的计划任务
> 假设里抽出来，三平台各自用原生机制，统一到一个 `ldst service` 面。
> 启停顺序与归属判定仍归 supervisor（见 [single-entry.md](single-entry.md)）；
> 本文只讲**注册单元、产物、退出契约**。

## 1. 统一入口

| 形态 | 启动命令 |
|---|---|
| 打包态（PyInstaller / py2app / AppImage） | `<安装目录>/LadderStock(.exe) __service` |
| 源码态 | 同目录 `ldst` / `ldst.exe`（存在时），否则 `python -m ladderstock.cli.main __service` |

`__service` 是隐藏长驻入口：设置 `LDST_SUPERVISOR_OWN_PROCESSES=1`、
`LDST_NO_BROWSER=1`，构建看板 + 自愈 + 自有子进程；有 GUI 时在进程内挂托盘，
托盘退出先 `stop_owned()` 再通过 `threading.Event` 让 uvicorn 优雅退出。

服务定义（`ServiceSpec`）只描述一条 argv、工作目录、日志目录和**非密钥**
环境变量；平台差异全部收在 `ladderstock/service/backends.py`。

## 2. 三平台后端

| 平台 | 机制 | 产物 | 安装态落点 | 编码 |
|---|---|---|---|---|
| Windows | 任务计划程序 | Task Scheduler XML | `<data>/service/LadderStock-SingleEntry.xml` | UTF-16（含 BOM） |
| macOS | launchd LaunchAgent | `.plist` | `~/Library/LaunchAgents/com.quotafuse.ladderstock.plist` | UTF-8 |
| Linux | systemd --user | `.service` unit | `${XDG_CONFIG_HOME:-~/.config}/systemd/user/ladderstock.service` | UTF-8 |

**注册/反注册命令**（全部 argv 列表、`shell=False`）：

| 动作 | Windows | macOS | Linux |
|---|---|---|---|
| 安装 | `schtasks /Create /TN "LadderStock Single Entry" /XML <xml> /F`，随后 `schtasks /Run ...`（可选步骤） | `launchctl bootout`（可选）→ `launchctl bootstrap gui/<uid> <plist>` → `launchctl kickstart -k`（可选） | `systemctl --user daemon-reload` → `systemctl --user enable --now ladderstock.service` |
| 卸载 | `schtasks /End`（可选）→ `schtasks /Delete /TN ... /F` | `launchctl bootout gui/<uid>/com.quotafuse.ladderstock` | `systemctl --user disable --now`（可选）→ 删除 unit → `systemctl --user daemon-reload` |
| 状态 | `powershell Get-ScheduledTask`（JSON） | `launchctl print gui/<uid>/<label>` | `systemctl --user show --property=LoadState,...` |

开机/登录自启语义：

- Windows：`LogonTrigger` + `InteractiveToken` + `LeastPrivilege`，延迟 `PT15S`；
  `MultipleInstancesPolicy=IgnoreNew`，`ExecutionTimeLimit=PT0S`（常驻），
  失败重启 `PT1M × 3`。
- macOS：`RunAtLoad=true`、`KeepAlive=false`（托盘主动退出后不被拉回）、
  `LimitLoadToSessionType=Aqua`、`ProcessType=Interactive`、`ThrottleInterval=10`。
- Linux：`Restart=on-failure` + `RestartSec=5`（用户主动 stop 不复活）、
  `TimeoutStopSec=30`、`KillMode=control-group`（只清本 unit cgroup，碰不到
  外部 adopted peer）、`WantedBy=default.target`。

## 3. 安全模型（红线）

1. **默认 dry-run**：`install` / `uninstall` 不传 `--apply` 只打印产物路径、哈希和
   将执行的命令；`render` 永远只写文本，不碰 OS 注册表。
2. **平台闸门**：`--apply` 前校验目标平台 == 当前平台（`ServiceManager._require_current`）。
   跨平台安装直接 `EXIT_CONFIG`，不会调用任何系统命令。要在别的机器安装，先
   `service render` 生成产物再带过去。
3. **失败关闭**：任一必需命令失败即停止后续步骤；安装失败不写清单；卸载时状态
   查询不可用仍尝试反注册，但**反注册未成功绝不删产物**（避免「注册还在、文件没了」）。
4. **只碰自己的注册单元**：任务名 / label / unit 名固定为本应用专属值，不扫描、
   不改写、不删除任何旧计划任务。
5. **密钥守卫**：环境变量名命中 `TOKEN / SECRET / PASSWORD / API_KEY / DSN …` 时
   `ServiceSpec.validate()` 直接拒绝。密钥只进当前进程环境或系统凭据存储。
6. **绝对路径**：argv[0]、工作目录、日志目录必须是绝对路径（launchd/systemd 不经过
   shell，也不按 PATH 解析）。

## 4. 托盘退出契约

- 退出唯一入口是 `TrayController.quit()`；生产处理器 = `SupervisorEngine.stop_owned()`。
- `stop_owned` 只停账本里 `origin="spawned"` 且正在运行的模块；`origin="adopted"`
  （tickhub:8000 / mem0:9100 / mem0-dev:9000 / financecoffer:8100 等外部实例）
  **不终止、不重启**，并在返回里给出 `kept_adopted`。
- 重复退出幂等；停止失败也返回结构化结果（`ok=false` + `error`），不会让托盘卡死。
- 同一语义已接到看板关停钩子（`web/supervisor_api.py`）：应用退出 = 只停自有子进程。

托盘图标依赖可选 extra：

```bash
pip install 'ladderstock[tray]'   # pystray + pillow
ldst service tray --check         # 报告是否可用
```

无 GUI / CI 用 `ldst service tray --headless --quit-after 0` 验证同一条退出契约。

## 5. 常用命令

```bash
# 1) 任何平台：先看三平台产物长什么样（只写文本）
ldst service render --out ./service-units

# 2) 预演安装（不写文件、不调用系统命令）
ldst service install
ldst service uninstall

# 3) 在当前平台真正安装/卸载（需要确认）
ldst service install --apply
ldst service uninstall --apply

# 4) 只读状态
ldst service status --output json

# 5) 托盘退出契约（无 GUI 环境）
ldst service tray --headless --quit-after 0 --output json
```

## 6. 未覆盖 / 后续

- 托盘图标资源与打包器集成（PyInstaller `--icon` / macOS `.icns`）属于打包单的
  工作面；本单只提供抽象与运行时行为，`pystray` 保持**可选**依赖，不影响
  `pip install .` 的默认安装。
- Linux 无登录会话时 `systemd --user` 需要 `loginctl enable-linger`；本单不替用户
  修改系统配置，只在文档说明。
- 生产宿主的真实安装/卸载不在本单执行；验收只使用临时数据目录 + 注入执行器。

## 7. 验收对照

| 场景 | 用例 |
|---|---|
| 三平台产物内容与编码 | `tests/unit/service/test_backends.py` |
| dry-run 零副作用 / 平台闸门 / 失败关闭 / 清单 | `tests/unit/service/test_manager.py` |
| 托盘退出幂等、失败可读、headless 契约 | `tests/unit/service/test_tray.py` |
| `stop_owned` 保留 adopted peer | `tests/unit/supervisor/test_stop_owned.py` |
| CLI dry-run / render / tray 参数与退出码 | `tests/unit/cli/test_service.py` |
| 关停钩子改走 `stop_owned` | `tests/unit/web/test_supervisor_process_endpoints.py` |
