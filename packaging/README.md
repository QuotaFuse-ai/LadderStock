# LadderStock 打包（QUOT-525）

自带固定解释器与依赖，目标机器**不需要系统 Python / venv**。三平台同一产物口径：
双击即起看板（127.0.0.1:5950）并打开默认浏览器；peer 放在安装根下即可被
supervisor 按相对路径发现。

## 产物布局

| 平台 | 构建命令 | 产物 | install_root |
|---|---|---|---|
| Windows | `packaging/build_win.ps1` | `dist/LadderStock/LadderStock.exe` | `dist/` |
| macOS | `packaging/build_macos.sh` | `dist/LadderStock.app` | `dist/` |
| Linux | `packaging/build_linux_appimage.sh` | `dist/LadderStock-x86_64.AppImage` | `dist/` |

peer 目录示例：

```text
dist/
├── LadderStock/               # Windows onedir（LadderStock.app / *.AppImage 同层）
├── TickHub-CN/
├── TickHub-US/
├── Wire/quotafuse-wire/
├── StockSenate/
└── FinanceCoffer/
```

`LDST_INSTALL_ROOT` 可覆盖安装根；其余路径全部由 `core.runtime` 从 exe / `.app` /
`APPIMAGE` 反推，不含构建机盘符。

## 构建

前置：Node 20 + 一个构建机 Python（3.11+）。构建脚本会自建 venv 并安装
`.[all]` 与打包工具。

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File packaging/build_win.ps1
```

```bash
# macOS（py2app）
bash packaging/build_macos.sh

# Linux（PyInstaller + appimagetool；首次会下载 appimagetool）
bash packaging/build_linux_appimage.sh
```

已有依赖时可用 `SKIP_INSTALL=1` / `SKIP_FRONTEND=1`（Windows 为 `-NoInstall` /
`-SkipFrontend`）跳过重复安装与前端构建。前端必须先构建：缺 `frontend/dist`
时构建期直接失败，不会交付只能看到「前端未构建」提示页的包。

## 验收

```powershell
# Windows：裁剪 PATH、清空 PYTHONPATH/PYTHONHOME、隔离数据目录、非生产端口
powershell -ExecutionPolicy Bypass -File packaging/smoke_win.ps1 `
  -Exe dist/LadderStock/LadderStock.exe -Port 5965
```

```bash
# macOS .app / Linux AppImage：同一脚本，env -i 隔离环境
bash packaging/smoke_posix.sh dist/LadderStock.app/Contents/MacOS/LadderStock 5966
APPIMAGE_EXTRACT_AND_RUN=1 bash packaging/smoke_posix.sh dist/LadderStock-x86_64.AppImage 5967
```

烟测检查四件事：

1. `LadderStock __runtime-info --output json` 报 `frozen=true`，且
   `frontend_dist_ready` / `bundle_config_ready` 均为 true；
2. `/api/health` 返回 `ok=true`（看板真实启动）；
3. `/` 返回真实前端（含 `#root`，不是「前端未构建」提示页）；
4. 退出后端口释放。

CI 的 `Package` workflow 在 Windows / macOS / Linux 三个 runner 上跑同一套构建
与烟测，产物以 artifact 上传。

## 已冻结的运行入口

打包后 exe 本身就是 CLI，supervisor / 看门狗 / 调度器 / 代理池转发器都从同一个
exe 以子命令拉起（不再依赖 `.py` 文件与 `-m`）：

| 子命令 | 用途 |
|---|---|
| `__desktop` | 双击入口：起看板 + 打开浏览器 |
| `__watch-guard` | 页面自启的 watch 扫描守护 |
| `schedule run` | 取数调度器 |
| `watchdog --port 5952` | 看门狗 |
| `proxypool_forwarder` | 按需代理池单映射转发器 |
| `__runtime-info` | 打包自检（冻结态 / 资源 / 安装根） |

## 安装器（Windows，QUOT-525 的「装」这一步）

产物是「解压就能用」，但普通人拿到 zip 仍然不知道该把 `LadderStock\` 放哪、
快捷方式怎么建。`packaging/installer/` 补上这一步：

| 文件 | 作用 |
|---|---|
| `Install-LadderStock.cmd` | 双击入口（自举 PowerShell，带 `-ExecutionPolicy Bypass`） |
| `install.ps1` | 拷贝程序、建开始菜单/桌面快捷方式、写卸载注册项、写 `install.json` |
| `Uninstall-LadderStock.cmd` / `uninstall.ps1` | 卸载；默认保留 `data\` 与 `config\`，`-PurgeData` 才一起删 |

安装根默认 `%LOCALAPPDATA%\LadderStock`（`-Root` 可改），冻结态是 **portable 布局**，
所以 data/config 与程序目录同级：

```text
%LOCALAPPDATA%\LadderStock\
├── LadderStock\       程序（升级时整体替换，旧的改名为 LadderStock.old-<时间戳>）
├── data\              用户数据（永不动）
├── config\            用户配置（含 ai.yaml，永不动）
├── install.json       安装记录（含版本）
└── TickHub-CN\ ...    peer 数据服务（installer 不代管）
```

**升级不丢数据**：升级只替换 `LadderStock\`，`data\` 与 `config\` 原地保留——
`research_plans.json`（跟过的任务）、`preferences.json`（偏好）都还在。实机验证过：
装 → 写入 `data/keep-me.json` → 再装一次 → 文件仍在、旧程序目录留下 `*.old-*`。

`release_win.ps1` 会把 bundle 与安装器打进同一个 zip，并生成 `version.txt`
（`__runtime-info` 不报版本，installer 靠它显示版本）与两份说明
（`安装说明.txt` 面向用户、`README-dist.txt` 面向开发）：

```powershell
powershell -ExecutionPolicy Bypass -File packaging/release_win.ps1
# dist/release/LadderStock-<version>-win-x64.zip
```

**还差的一步由页面承担**：AI Key 不写进安装器，装完启动后在页面点「接入 AI」填一次
（走 `PUT /control/ai/modules` 写 `ai.yaml` 的 `modules:` 段）。安装器只负责提示，
不伪造配置——否则用户会以为已经在用某个模型。

### 实机验证（2026-11-16）

```powershell
# 1. 装到临时根（不碰真实 LOCALAPPDATA）
powershell -ExecutionPolicy Bypass -File installer/install.ps1 -Root D:\Temp\x -NoDesktop
# 2. 冻结态自检
D:\Temp\x\LadderStock\LadderStock.exe __runtime-info --output json
#    frozen=true / frontend_dist_ready=true / bundle_config_ready=true
#    install_root 正确反推为 D:\Temp\x
# 3. 卸载
powershell -ExecutionPolicy Bypass -File installer/uninstall.ps1 -Root D:\Temp\x -PurgeData
#    开始菜单项 / 注册表项 / 程序目录 / data / config 全部清掉
```

**打包曾缺件（已修）**：首版 onedir 里没有 `_internal\agents\`，双击启动直接崩在
`memory_consolidation_prompt.md` 的 `FileNotFoundError`——`LadderStock.spec` 里的
`collect_data_files("agents")` 是后加的，旧产物没重建。凡是改 spec 后必须重建再验，
烟测脚本（`smoke_win.ps1`）只查 `/api/health` 与首页，查不到这类「启动即崩」。

## 便携包的端口：启动器自动选，不让用户配

开发机上是 5950（正式）/ 5951（测试）两套并行，peer 端口按 `LDST_PORT_OFFSET`
整体平移（测试服 +10000 → 18000 系）。**这套是开发约定，不是给终端用户的。**

便携包只跑一个实例，端口冲突时应当自动避让。`packaging/port_probe.py` 提供：

- `pick_base_port(preferred=5950, step=10, attempts=60)`：从 5950 起每 10 个探测一次，
  返回第一个能 bind 的端口；候选全被占时直接抛错，不静默乱选。
- `offset_for(base_port)`：基端口 → `LDST_PORT_OFFSET`，与
  `boundary.port_offset()` 同一语义（5950 → 0，后移两档 5970 → 20）。

启动器接入就三行：

```python
base = pick_base_port()
os.environ["LDST_PORT_OFFSET"] = str(offset_for(base))
argv += ["--port", str(base)]
```

派生链已全链路吃这个变量：控制面端口展示、peer `--port` 启动命令、
`sources.yaml` 的数据源地址（`provider_registry._offset_url`）。用户不需要知道
任何端口号，也不需要改任何配置文件。

尚未接入：`installer/install.ps1` 与 `ladderstock_entry.py` 仍是固定 `--port 5950`，
接线放在便携包启动器改造那一单里做；本轮只交付探测函数与 5 条单测。

## 已知边界

- 本仓代码不签名。Windows SmartScreen 与 macOS Gatekeeper 会提示「未知开发者」；
  正式分发需另加代码签名/公证（不在本单范围）。
- macOS `.app` 为未公证的本地构建；如需分发给其他机器，需要 `xattr` 或公证流程。
- Linux AppImage 在无 FUSE 的环境可用 `APPIMAGE_EXTRACT_AND_RUN=1` 运行。
- 托盘 / 开机自启属于 QUOT-527，不在本单。
