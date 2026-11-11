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

## 已知边界

- 本仓代码不签名。Windows SmartScreen 与 macOS Gatekeeper 会提示「未知开发者」；
  正式分发需另加代码签名/公证（不在本单范围）。
- macOS `.app` 为未公证的本地构建；如需分发给其他机器，需要 `xattr` 或公证流程。
- Linux AppImage 在无 FUSE 的环境可用 `APPIMAGE_EXTRACT_AND_RUN=1` 运行。
- 托盘 / 开机自启属于 QUOT-527，不在本单。
