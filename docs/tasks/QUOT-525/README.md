# QUOT-525 · 捆绑固定解释器跨平台：Win EXE 先行 + mac / Linux 同口径

状态：Windows 产物已构建并冒烟通过；macOS / Linux 流水线已就绪，待对应 runner 出包。
父单：QUOT-519（六根支柱之一）

## 交付内容

### 运行时路径层

| 文件 | 作用 |
|---|---|
| `src/ladderstock/core/runtime.py` | 冻结态四个根目录（bundle/install/app/project）唯一来源 |
| `src/ladderstock/cli/paths.py` | 冻结态禁用 cwd config/data 兜底；随包模板走 bundle |
| `src/ladderstock/supervisor/module_adapters.py` | `install_root`/`launcher_entry`/proxypool 启动委托 runtime |
| `src/ladderstock/supervisor/page_boot.py` | 自有子进程入口冻结态用裸 exe（不拼 `-m`） |
| `src/ladderstock/web/server.py` | 前端产物与静态页从 bundle 解析 |
| `src/ladderstock/cli/schedule.py` | 调度 schema 回退到随包模板 |
| `src/ladderstock/core/boundary.py` | 源码扫描根委托 runtime |
| `src/ladderstock/cli/run.py` | `launch(open_browser=)`：端口就绪后开浏览器 |
| `src/ladderstock/cli/main.py` | 隐藏入口 `__desktop` / `proxypool_forwarder` / `__runtime-info` |

### 打包流水线

| 文件 | 作用 |
|---|---|
| `packaging/ladderstock_entry.py` | PyInstaller/py2app 入口；无参数默认 `__desktop` |
| `packaging/bundle_manifest.py` | 随包资源/隐藏导入/排除清单，目标必须相对 |
| `packaging/LadderStock.spec` | Windows onedir（Linux AppDir 复用） |
| `packaging/build_win.ps1` | venv + 依赖 + 前端构建 + PyInstaller |
| `packaging/smoke_win.ps1` | 模拟无 Python 干净机器冒烟 |
| `packaging/macos/setup_py2app.py` | py2app `.app` 配置 |
| `packaging/build_macos.sh` | macOS 构建与冒烟入口 |
| `packaging/build_linux_appimage.sh` | onedir + AppDir + appimagetool |
| `packaging/smoke_posix.sh` | macOS/Linux 干净环境冒烟 |
| `packaging/linux/*` | desktop 文件与图标 |
| `.github/workflows/package.yml` | 三平台构建 + 冒烟 + 产物上传 |
| `packaging/README.md` | 构建/验真速查 |

### 测试与文档

- `tests/unit/core/test_frozen_runtime.py`：Win onedir / onefile、mac `.app`、
  AppImage、`LDST_INSTALL_ROOT`、冻结态 cwd 隔离、冻结态子进程入口。
- `tests/unit/test_package_manifest.py`：资源目标相对、缺失资源构建期失败、
  打包入口文件齐全。
- `docs/specs/SPEC-PACKAGING.md`、`docs/decisions/DEC-076-bundled-interpreter-packaging.md`。

## Windows 验真结果

产物：`dist/LadderStock/LadderStock.exe`（onedir，约 111 MB，含前端与配置模板）。

冒烟命令：

```powershell
powershell -ExecutionPolicy Bypass -File packaging/smoke_win.ps1 `
  -Exe dist\LadderStock\LadderStock.exe -Port 5965 -KeepTemp
```

结果（原始证据在 `evidence/`）：

- PATH 中无 `python`（`python_on_path: null`）；
- `/api/health` → `ok=true`、`db_ok=true`、数据目录落在隔离临时目录；
- `/` 返回 2211 字节真实看板，含 `id="root"`，非「前端尚未构建」提示页；
- `__runtime-info` → `frozen=true`、`frontend_dist_ready=true`、
  `bundle_config_ready=true`，`install_root` = `dist/`（peer 同级）。
- 进程退出后端口释放（脚本 `finally` 校验 + 端口复查无服务）。

## 复现步骤

```powershell
cd frontend; npm ci; npm run build; cd ..
python -m pip install "pyinstaller>=6.11,<7"
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build/pack-win/work packaging/LadderStock.spec
powershell -ExecutionPolicy Bypass -File packaging/smoke_win.ps1 -Exe dist\LadderStock\LadderStock.exe -Port 5965
```

macOS / Linux 分别运行 `packaging/build_macos.sh`、`packaging/build_linux_appimage.sh`
后接 `packaging/smoke_posix.sh`；CI 的 `Package` workflow 已按同一命令矩阵配置。

## CI 修复（#197 linux AppImage smoke）

首轮 CI 中 linux 失败在 `FAIL: port still served after shutdown`，构建与看板本身都成功：

- 根因：`APPIMAGE_EXTRACT_AND_RUN=1` 下 `$!` 是 AppImage 运行时包装进程，真正的
  看板是它的子孙；只 `kill $PID` 会留下仍在监听 5967 的 python 子进程。
- 修复：`packaging/smoke_posix.sh` 改为递归终止进程树（`pgrep -P`），并在退出后
  用最多 30s 的轮询确认端口释放；超时仍占用依旧判失败，未放宽断言。
- macOS 在 1s 内 `cancelled` 且 `runner_name` 为空、`steps` 为空（未分配到 runner）。
  check-run 注解给出原因：`The configuration 'macos-14-arm64-us-default' is not
  supported`——组织侧未启用该 macOS runner 配置。修复是把 macOS job 的
  `runs-on` 从 `macos-14` 换成 `macos-latest`（走 macos-15 池，已在
  `test/macos13-runner` 验证可分到机器）。
- 分到真机后暴露第二处问题：py2app 产物缺 `anyio._backends._asyncio`，
  首个 ASGI 请求即 `ModuleNotFoundError: No module named 'anyio._backends'` /
  `KeyError: 'asyncio'`，`/api/health` 轮询超时。根因是 anyio 用
  `importlib.import_module("anyio._backends._asyncio")` 动态选后端，静态分析抓不到；
  PyInstaller 自带 contrib hook，py2app 没有。修复：在共用的
  `packaging/bundle_manifest.py` 的 `HIDDEN_IMPORTS` 增加该子模块，三平台同一份清单。
- 最终 head `322c62c`：linux / windows / macOS / frontend / backend 五绿。macOS
  smoke 日志含 `runtime-info: frozen + bundled resources OK`、
  `health: {"ok":true,...}`、`OK: packaged app starts the dashboard without system Python`。

## 已知问题与边界

- 本次 Windows 冒烟 stderr 有一条**既有**告警：`ProxyFleetAdapter.identify() takes 1
  positional argument but 2 were given`（page_boot 认领 peer 时被吞，不影响看板启动）。
  与本任务路径改动无关，未在本 PR 修改，建议单独提单。
- 未做代码签名 / 公证；macOS 首启需右键打开，Windows 可能提示未知发布者。
- 三平台产物均已由 CI 在对应 runner 上实际构建并通过同格式冒烟；产物仅验证到
  「双击/直接执行起看板」，未做跨机器分发验证。
- 托盘常驻 / 开机自启归 QUOT-527；生产端口未参与任何冒烟。
