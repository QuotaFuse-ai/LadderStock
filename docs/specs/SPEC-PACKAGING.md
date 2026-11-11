# SPEC-PACKAGING · 自带解释器跨平台打包

状态：生效（QUOT-525）
适用范围：Windows onedir EXE（先行）、macOS `.app`（py2app）、Linux AppImage
关联：DEC-071（platformdirs 落点）、DEC-076（本方案决策）、QUOT-524（bundle 相对路径）

## 1. 目标

双击启动即用，不要求机器上安装 Python、不要求 venv、不要求源码树：

1. 干净机器（PATH 里没有 `python`）双击产物即可打开看板；
2. 三个平台同一套路径口径（bundle 内全部相对，不硬编码盘符）；
3. 产物与 peer 同级摆放，supervisor 的 peer 解析无需额外配置。

## 2. 产物与安装布局

| 平台 | 可执行文件 | `install_root`（peer 父目录） |
|---|---|---|
| Windows（onedir） | `<InstallRoot>\LadderStock\LadderStock.exe` | `<InstallRoot>` |
| Windows（onefile，可选） | `<InstallRoot>\LadderStock.exe` | `<InstallRoot>` |
| macOS | `<InstallRoot>/LadderStock.app/Contents/MacOS/LadderStock` | `<InstallRoot>` |
| Linux | `<InstallRoot>/LadderStock-x86_64.AppImage` | `<InstallRoot>` |

peer 目录按既有 `PEER_SIBLING_DIRS` 放在 `<InstallRoot>/<同级名>`。
`LDST_INSTALL_ROOT` 永远优先于自动推导（部署侧换盘符只改环境变量）。

随包资源（解包目录内，全部相对目标）：

```
ladderstock/web/static          # 兜底静态页
ladderstock/assets/config       # 首跑 seed 的配置模板（含 schema/）
frontend/dist                   # 看板前端产物（缺它就只能看到提示页）
```

## 3. 运行时路径层

一切根目录只经 `src/ladderstock/core/runtime.py`：

- `bundle_root()`：PyInstaller `sys._MEIPASS`；py2app `Contents/Resources`；AppImage 为解包目录；
- `install_root()`：`LDST_INSTALL_ROOT` > AppImage 原文件目录 > `.app` 上级 > onedir 的 `LadderStock/` 上级；
- `app_dir()`：exe 目录（macOS 是 `.app`）；
- `project_root()`：子进程 cwd 基准，冻结态等于 `install_root()`；
- `bundle_config_dir()` / `frontend_dist_dir()` / `web_static_dir()`：随包资源定位。

**禁止**在新的代码里直接写 `Path(__file__).parents[N]` 推目录；源码态与冻结态
的映射统一放在 runtime。

## 4. 进程入口

打包后 exe 本身就是 CLI，**子进程命令不能再拼 `-m ladderstock.cli.main`**：

| 子命令 | 用途 |
|---|---|
| `__desktop`（默认） | 双击入口：起看板，端口就绪后开浏览器；`LDST_NO_BROWSER=1` 关闭 |
| `__watch-guard` | 网页打开自启的看门狗（QUOT-522） |
| `proxypool_forwarder` | 按需代理池单映射转发器，由 supervisor 拉起 |
| `__runtime-info` | 输出 JSON：`frozen`、各根目录、随包资源 ready 标记 |

`packaging/ladderstock_entry.py` 在无参数时自动补 `__desktop`，保证双击可用。
`launcher_entry()` / `PageBoot._self_exe_command()` 冻结态返回裸 exe 路径。

## 5. 用户数据落点

冻结态**不读 cwd 下的 `config/`、`data/`**（那是安装目录，不是用户配置）：

- Windows：`%LOCALAPPDATA%\QuotaFuse\LadderStock`
- macOS：`~/Library/Application Support/LadderStock`
- Linux：`$XDG_DATA_HOME/LadderStock`（默认 `~/.local/share/LadderStock`）

首跑从随包 `assets/config` 补齐完整配置，不覆盖已存在文件（DEC-071）。

## 6. 构建

```bash
# Windows
powershell -ExecutionPolicy Bypass -File packaging/build_win.ps1
# macOS
packaging/build_macos.sh
# Linux（AppImage）
packaging/build_linux_appimage.sh
```

公共开关：`SKIP_INSTALL=1`（依赖已装）、`SKIP_FRONTEND=1`（已构建前端）。
构建脚本会在 `frontend/dist` 缺失时直接失败，不产出“只能看到提示页”的包。

## 7. 验真

- Windows：`packaging/smoke_win.ps1 -Exe dist\LadderStock\LadderStock.exe -Port 5965`
- macOS / Linux：`packaging/smoke_posix.sh <exe> 5966`

冒烟环境：PATH 只留系统目录、清空 `PYTHONHOME`/`PYTHONPATH`、隔离
`LOCALAPPDATA`/data/config、`LDST_PAGE_AUTOSTART=0`、`LDST_NO_BROWSER=1`、
非生产端口。通过标准：

1. `/api/health` 返回 `ok=true`；
2. `/` 返回真实看板（含 `id="root"`），不是“前端尚未构建”提示页；
3. `__runtime-info` 的 `frozen`、`frontend_dist_ready`、`bundle_config_ready` 均为 `true`；
4. 退出后端口释放。

CI 见 `.github/workflows/package.yml`（三平台矩阵，构建 + 冒烟 + 产物上传）。

## 8. 约束与已知限制

- 未做代码签名 / 公证：macOS Gatekeeper 首次需右键打开；Windows SmartScreen 可能提示未知发布者。
- Linux AppImage 冒烟用 `APPIMAGE_EXTRACT_AND_RUN=1` 规避 CI 无 FUSE。
- 托盘常驻、开机自启属于 QUOT-527，不在本规格内。
- 生产端口 5950/8000/9100/9000 不参与任何冒烟。
