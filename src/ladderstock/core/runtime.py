"""冻结态（打包）运行路径（QUOT-525）。

源码态所有目录都能从 `__file__` 上溯得到；打包后模块文件落在临时解包目录
（PyInstaller `sys._MEIPASS` / py2app `Contents/Resources`），再上溯会指到不存在
的位置。这里集中定义三种根目录，其余模块只许调这些函数：

- `bundle_root()`：随包资源（配置模板、静态页、前端产物）的根；
- `install_root()`：peer 的父目录（`<InstallRoot>/LadderStock/...` 的上一级）；
- `project_root()`：进程工作目录基准（看板 / 看门狗 / 调度器的 cwd）。

打包布局（三平台同一口径）：

| 平台 | 可执行文件 | install_root |
|---|---|---|
| Windows | `<InstallRoot>\\LadderStock\\LadderStock.exe` | `<InstallRoot>` |
| Windows（新 onedir） | `<InstallRoot>\\bin\\LadderStock\\LadderStock.exe` | `<InstallRoot>` |
| Windows（单文件） | `<InstallRoot>\\LadderStock.exe` | `<InstallRoot>` |
| macOS | `<InstallRoot>/LadderStock.app/Contents/MacOS/LadderStock` | `<InstallRoot>` |
| Linux AppImage | `<InstallRoot>/LadderStock-x86_64.AppImage` | `<InstallRoot>` |

`LDST_INSTALL_ROOT` 永远优先，部署侧要用自定义盘符时只改环境变量。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

#: 安装根显式覆盖（与 `module_adapters.INSTALL_ROOT_ENV` 同一口径）
INSTALL_ROOT_ENV = "LDST_INSTALL_ROOT"

#: 打包目录名：Windows/Linux 的 onedir 产物目录、macOS 的 `.app` 主干名
APP_DIRNAME = "LadderStock"

#: AppImage 运行时注入的原始文件路径（不是挂载点）
APPIMAGE_ENV = "APPIMAGE"

#: 便携模式标记：安装根内存在此文件时，config/data 落安装根内（QUOT-592）
PORTABLE_MARKER = ".portable"

#: onedir 新布局的代码目录名：exe 在 `<InstallRoot>/bin/LadderStock/` 下
BIN_DIRNAME = "bin"


def is_frozen() -> bool:
    """是否运行在 PyInstaller / py2app 冻结态。"""
    return bool(getattr(sys, "frozen", False))


def executable() -> Path:
    return Path(sys.executable).resolve()


def _is_macos_app_binary(exe: Path) -> bool:
    return exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents"


def bundle_root() -> Path:
    """随包资源根：冻结态是解包目录/Resources，源码态是仓库根。"""
    if not is_frozen():
        return Path(__file__).resolve().parents[3]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    exe = executable()
    if _is_macos_app_binary(exe):
        resources = exe.parent.parent / "Resources"
        if resources.is_dir():
            return resources
    return exe.parent


def install_root(env: Mapping[str, str] | None = None) -> Path:
    """peer 的父目录（全部 bundle 相对，`LDST_INSTALL_ROOT` 优先）。"""
    values = os.environ if env is None else env
    override = (values.get(INSTALL_ROOT_ENV) or "").strip()
    if override:
        return Path(override)
    if not is_frozen():
        # 源码树：<repo>/src/ladderstock/core/runtime.py 上溯 3 层 = <repo>，
        # 再取父目录——peer 与 LadderStock 仓库同级。
        return Path(__file__).resolve().parents[3].parent
    appimage = (values.get(APPIMAGE_ENV) or "").strip()
    if appimage:
        return Path(appimage).resolve().parent
    exe = executable()
    if _is_macos_app_binary(exe):
        return exe.parent.parent.parent.parent  # .app 所在目录
    if exe.parent.name.lower() == APP_DIRNAME.lower():
        # 旧 onedir：`<InstallRoot>/LadderStock/LadderStock.exe`
        root = exe.parent.parent
        if root.name.lower() == BIN_DIRNAME.lower():
            # 新 onedir：`<InstallRoot>/bin/LadderStock/LadderStock.exe`
            return root.parent
        return root
    if exe.parent.name.lower() == BIN_DIRNAME.lower():
        return exe.parent.parent
    return exe.parent


def is_portable(env: Mapping[str, str] | None = None) -> bool:
    """便携模式判定（QUOT-592）：冻结态恒便携；源码态看工程根有无 `.portable` 标记。

    便携模式下 config/data 落安装根内（`install_root()/config|data`），
    platformdirs 只作非便携兜底；`LDST_CONFIG_DIR` / `LDST_DATA_DIR` 显式
    覆盖仍最高优先（在 `paths.py` 里判定，不在这里）。
    """
    if is_frozen():
        return True
    values = os.environ if env is None else env
    override = (values.get(INSTALL_ROOT_ENV) or "").strip()
    root = Path(override) if override else Path(__file__).resolve().parents[3]
    return (root / PORTABLE_MARKER).is_file()


def app_dir() -> Path:
    """本程序自己的目录：源码树根，或打包后的 exe / `.app` 所在目录。"""
    if not is_frozen():
        return Path(__file__).resolve().parents[3]
    exe = executable()
    if _is_macos_app_binary(exe):
        return exe.parent.parent.parent
    return exe.parent


def project_root() -> Path:
    """进程工作目录基准：源码态是仓库根，冻结态是安装根。

    冻结态没有源码树，子进程的 cwd 只需要一个稳定、可写且相对的位置；
    peer 目录的解析不依赖 cwd（见 `install_root()`）。
    """
    if is_frozen():
        return install_root()
    return Path(__file__).resolve().parents[3]


def source_root() -> Path:
    """源码扫描根（`repo_root()` 用）：源码态是 `<repo>/src`，冻结态是 bundle 根。

    冻结态没有 `.py` 源码，返回 bundle 根即可让扫描自然得到空结果，
    而不是去扫安装盘上的无关目录。
    """
    if is_frozen():
        return bundle_root()
    return Path(__file__).resolve().parents[2]


def package_dir() -> Path:
    """`ladderstock` 包目录：源码态 `<repo>/src/ladderstock`，冻结态解包目录内同名子目录。"""
    if is_frozen():
        return bundle_root() / "ladderstock"
    return Path(__file__).resolve().parents[1]


def bundle_config_dir() -> Path:
    """随包配置模板目录（`ladderstock/assets/config`）。"""
    return package_dir() / "assets" / "config"


def frontend_dist_dir() -> Path:
    """随包前端产物目录（`frontend/dist`）。"""
    return bundle_root() / "frontend" / "dist"


def web_static_dir() -> Path:
    """随包静态页目录（`ladderstock/web/static`）。"""
    return package_dir() / "web" / "static"
