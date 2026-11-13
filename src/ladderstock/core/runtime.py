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

#: peer 根显式覆盖（与 `module_adapters.DEFAULT_PEER_ROOT_ENV` 同一口径）：
#: 只影响 peer / 组件的同级目录探测，不改变 bundle/install 根本身。
PEER_ROOT_ENV = "LDST_PEER_ROOT"

#: 已知的部署根（按优先级探测）。源码态 install_root 解析到「仓库的父目录」
# （如 D:\workplace），而真实部署可能落在别的根（如 D:\QuotaFuseModules）——
#: 这里只探测「根/<同级名>」目录是否真实存在，绝不写死成解析结果。
KNOWN_DEPLOY_ROOTS_ENV = "LDST_KNOWN_DEPLOY_ROOTS"
KNOWN_DEPLOY_ROOTS: tuple[str, ...] = (r"D:\QuotaFuseModules",)

#: 打包目录名：Windows/Linux 的 onedir 产物目录、macOS 的 `.app` 主干名
APP_DIRNAME = "LadderStock"

#: AppImage 运行时注入的原始文件路径（不是挂载点）
APPIMAGE_ENV = "APPIMAGE"


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
        return exe.parent.parent
    return exe.parent


def app_dir() -> Path:
    """本程序自己的目录：源码树根，或打包后的 exe / `.app` 所在目录。"""
    if not is_frozen():
        return Path(__file__).resolve().parents[3]
    exe = executable()
    if _is_macos_app_binary(exe):
        return exe.parent.parent.parent
    return exe.parent


def peer_root(env: Mapping[str, str] | None = None) -> Path:
    """peer 同级目录探测的基准根。

    优先级：`LDST_PEER_ROOT` 显式覆盖 > 源码态/冻结态的 `install_root()` > 已知
    部署根探测（`LDST_KNOWN_DEPLOY_ROOTS` 补充清单 + 内置 `KNOWN_DEPLOY_ROOTS`）。

    源码态 install_root = 仓库的父目录（如 D:\\workplace）；当真实部署落在别的
    根（如 D:\\QuotaFuseModules）时，控制面会把「目录在位的 peer」误判成未配置
    （QUOT-569）。这里按顺序探测已知根下是否真的存在部署痕迹（LadderStock 或
    任一已知 peer 同级目录），命中才回退——绝不盲目把不存在的路径当根。
    """
    values = os.environ if env is None else env
    override = (values.get(PEER_ROOT_ENV) or "").strip()
    if override:
        return Path(override)
    base = install_root(values)
    sibling_names = {
        APP_DIRNAME.lower(),
        *(name.lower() for name in DEPLOY_SIBLING_DIRS),
    }
    roots: list[Path] = []
    extra = (values.get(KNOWN_DEPLOY_ROOTS_ENV) or "").strip()
    for raw in ([extra] if extra else []) + list(KNOWN_DEPLOY_ROOTS):
        for part in raw.split(";"):
            part = part.strip()
            if part:
                roots.append(Path(part))
    for root in roots:
        try:
            if any(child.name.lower() in sibling_names for child in root.iterdir()):
                return root
        except OSError:
            continue
    return base


#: 已知 peer 同级目录名（部署根探测的「痕迹」清单；与 boundary.PEER_SIBLING_DIRS
#: 对齐，但本模块不 import boundary，避免循环依赖）。
DEPLOY_SIBLING_DIRS: tuple[str, ...] = (
    "TickHub-CN",
    "TickHub-US",
    "Wire",
    "StockSenate",
    "FinanceCoffer",
    "Mem0",
    "ProxyFleet",
    "Qdrant",
)


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
