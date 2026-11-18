"""FinanceCoffer 增强包的目录布局（DEC-086）。

**全部路径都在 `<安装根>/file/modules/financecoffer/` 之下**，没有 `%LOCALAPPDATA%`
回退：装到 C 盘时由 `installer.plan()` 强提示换盘。用户的一种卸载就对应一次目录删除，
数据跟着走，不留孤儿。

```
<安装根>/file/modules/financecoffer/
├── runtime/                 随包二进制（三端 Redis 在此）
│   ├── pg/
│   ├── minio/
│   └── redis/{win-x64,linux-x64,linux-arm64,macos-arm64}/
├── pgdata/                  数据库数据（卸载即删）
├── miniodata/               对象存储数据
├── redisdata/               Redis 数据
├── conf/                    redis.conf 等配置（三端共用一份）
├── logs/
├── run/                     pid / 状态文件
└── install_state.json       安装状态机（state.py）
```
"""

from __future__ import annotations

from pathlib import Path

MODULE_NAME = "financecoffer"

#: Redis 社区移植版按平台分目录（官方无 Windows 版；三端各一份，共用一份 conf）
REDIS_PLATFORMS: tuple[str, ...] = ("win-x64", "linux-x64", "linux-arm64", "macos-arm64")

#: 数据目录（卸载整删，不可恢复）
DATA_DIRNAMES: tuple[str, ...] = ("pgdata", "miniodata", "redisdata")


def install_root() -> Path:
    """安装根（与 peer 同一套推导：`LDST_INSTALL_ROOT` > 冻结态 > 源码树父目录）。"""
    from ladderstock.core import runtime as core_runtime

    return core_runtime.install_root()


def module_root(root: Path | None = None) -> Path:
    """`<安装根>/file/modules/financecoffer`。"""
    base = Path(root) if root is not None else install_root()
    return base / "file" / "modules" / MODULE_NAME


def runtime_dir(root: Path | None = None) -> Path:
    return module_root(root) / "runtime"


def pg_dir(root: Path | None = None) -> Path:
    return runtime_dir(root) / "pg"


def minio_dir(root: Path | None = None) -> Path:
    return runtime_dir(root) / "minio"


def redis_dir(root: Path | None = None, platform: str | None = None) -> Path:
    """`runtime/redis/<平台>`；不传平台时用当前机器。"""
    plat = platform or current_redis_platform()
    return runtime_dir(root) / "redis" / plat


def conf_dir(root: Path | None = None) -> Path:
    return module_root(root) / "conf"


def logs_dir(root: Path | None = None) -> Path:
    return module_root(root) / "logs"


def run_dir(root: Path | None = None) -> Path:
    return module_root(root) / "run"


def env_suffix() -> str:
    """正式服与测试服的数据目录后缀。

    两个服共用同一个模块目录（`<安装根>/file/modules/financecoffer`），但 PG 的
    data 目录**只能挂一个实例**：共用时正式服 5432 永远起不来，`pg.log` 里只留下
    `role "Administrator" does not exist` 这种连接噪音，看起来像配置错，实际是
    数据目录被测试服锁着。所以按 env 分开：测试服沿用历史名（`pgdata` /
    `redisdata`，不动存量数据），正式服加 `-prod` 后缀。
    """

    import os

    env = (os.environ.get("LDST_ENV") or "").strip().lower()
    return "" if env == "test" else "-prod"


def data_dir(root: Path | None = None, name: str = "pgdata") -> Path:
    return module_root(root) / f"{name}{env_suffix()}"


def state_path(root: Path | None = None) -> Path:
    return module_root(root) / "install_state.json"


def current_redis_platform() -> str:
    """当前机器的 Redis 移植版目录名。未知平台返回 `linux-x64` 之外的显式错误值。"""
    import platform as platform_mod
    import sys

    machine = (platform_mod.machine() or "").lower()
    if sys.platform.startswith("win"):
        return "win-x64" if machine in {"amd64", "x86_64", ""} else "win-x64"
    if sys.platform == "darwin":
        return "macos-arm64" if machine in {"arm64", "aarch64"} else "macos-arm64"
    if machine in {"aarch64", "arm64"}:
        return "linux-arm64"
    return "linux-x64"


__all__ = [
    "DATA_DIRNAMES",
    "MODULE_NAME",
    "REDIS_PLATFORMS",
    "conf_dir",
    "current_redis_platform",
    "data_dir",
    "env_suffix",
    "install_root",
    "logs_dir",
    "minio_dir",
    "module_root",
    "pg_dir",
    "redis_dir",
    "run_dir",
    "runtime_dir",
    "state_path",
]
