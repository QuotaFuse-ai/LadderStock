"""默认服务定义：把当前运行形态翻译成一条平台无关的启动命令（QUOT-527）。

打包态（PyInstaller / py2app / AppImage）启动自身 exe 的隐藏 `__service` 入口；
源码态优先同目录 console-script（`ldst` / `ldst.exe`），再退回
`python -m ladderstock.cli.main __service`。这与 QUOT-522 的自有子进程入口
同一套判定，安装后的服务不依赖 cwd、PATH 或系统 Python。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from ladderstock.core import runtime
from ladderstock.service.model import SERVICE_NAME, ServiceSpec

#: 服务进程的内部入口（看板 + 自愈 + 自有子进程，不打开浏览器）
SERVICE_SUBCOMMAND = "__service"


def entry_argv(subcommand: str = SERVICE_SUBCOMMAND) -> tuple[str, ...]:
    """当前形态下可被 OS 服务直接执行的绝对命令。"""
    if runtime.is_frozen():
        return (str(runtime.executable()), subcommand)
    console_name = "ldst.exe" if os.name == "nt" else "ldst"
    console = Path(sys.executable).with_name(console_name)
    if console.is_file():
        return (str(console), subcommand)
    return (sys.executable, "-m", "ladderstock.cli.main", subcommand)


def default_spec(
    *,
    data_root: Path | None = None,
    env: Mapping[str, str] | None = None,
    name: str = SERVICE_NAME,
) -> ServiceSpec:
    """按当前安装形态组装服务定义；`data_root` 显式钉住数据目录。"""
    from ladderstock.cli.paths import data_dir

    root = Path(data_root) if data_root is not None else data_dir()
    resolved_env: dict[str, str] = dict(env or {})
    if data_root is not None:
        resolved_env.setdefault("LDST_DATA_DIR", str(root))
    spec = ServiceSpec(
        name=name,
        argv=entry_argv(),
        workdir=runtime.project_root(),
        log_dir=root / "service" / "logs",
        env=resolved_env,
    )
    spec.validate()
    return spec


__all__ = ["SERVICE_SUBCOMMAND", "default_spec", "entry_argv"]
