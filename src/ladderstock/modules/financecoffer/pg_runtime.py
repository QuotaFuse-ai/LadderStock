"""PostgreSQL 运行时取件（方案 A：conda-forge 同一编译链）。

**为什么需要这个文件**：增强包原来随包的 PG 是 EDB 官方二进制，**不含 pgvector**
（pgvector 不是 contrib 扩展）。FinanceCoffer 的迁移第一步就是
`CREATE EXTENSION vector`，拿不到扩展就直接 `E_STORAGE migration failed`，
于是 ODS/DWD/DWS/vector 四层表一张都没建出来，`/v1/structured` 与 MCP
`query_structured_data` 全程 `E_INTERNAL`。

把 conda-forge 的 `vector.dll` 单独塞进 EDB 的 PG 里是**错的**（实测报
`The specified procedure could not be found`）：那是按 conda 的 PG 编的，
跟 EDB 的 17.6 不是同一条编译链。

所以这里改成整套取件：用 micromamba（单文件、三端都有）在 `runtime/pg`
建一个 conda 环境，一次装 `postgresql=17` + `pgvector`，两者同链、ABI 必然匹配。

**不做运行期热替换**：这个函数只应在「安装 / 修复」步骤里、服务已停的前提下调用；
运行期只做体检（`pgvector_status`）。
"""

from __future__ import annotations

import json
import os
import platform as platform_mod
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from ladderstock.modules.financecoffer import layout

#: conda 频道（只有 conda-forge，不引第三方频道）
CHANNEL = "https://conda.mamba.pm/conda-forge"
CONDA_FORGE = "conda-forge"

#: 取件超时（秒）：国内网络拉 micromamba 单文件很快，装环境第一次要下几十 MB
DOWNLOAD_TIMEOUT = 120
CREATE_TIMEOUT = 1800


class PgRuntimeError(RuntimeError):
    """取件或建环境失败（不假装成功）。"""


def platform_key() -> str:
    """conda 的 subdir 名。未知平台显式报错，不猜。"""
    machine = (platform_mod.machine() or "").lower()
    if sys.platform.startswith("win"):
        return "win-64"
    if sys.platform == "darwin":
        return "osx-arm64" if machine in {"arm64", "aarch64"} else "osx-64"
    if machine in {"aarch64", "arm64"}:
        return "linux-aarch64"
    return "linux-64"


def micromamba_url(subdir: str | None = None) -> str:
    return f"https://micro.mamba.pm/api/micromamba/{subdir or platform_key()}/latest"


def _micromamba_exe(root: Path | None = None) -> Path:
    name = "micromamba.exe" if sys.platform.startswith("win") else "micromamba"
    return layout.runtime_dir(root) / "micromamba" / name


def _download(url: str, dest: Path, *, attempts: int = 3) -> int:
    """分块下载 + 重试。

    `micro.mamba.pm` 直连在国内经常中途断流（实测 `IncompleteRead`），
    一次性 `resp.read()` 会整包作废；分块写盘并允许重试，才不会让安装卡在第一步。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as resp:  # noqa: S310
                total = 0
                with dest.open("wb") as fh:
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        fh.write(chunk)
                        total += len(chunk)
            if total > 1_000_000:
                return total
            last_error = PgRuntimeError(f"下载内容过小：{total} 字节")
        except Exception as exc:  # noqa: BLE001 - 网络抖动要重试，不能一次就放弃
            last_error = exc
        if attempt < attempts:
            time.sleep(1.5 * attempt)
    raise PgRuntimeError(f"下载 micromamba 失败（{url}）：{last_error}")


def ensure_micromamba(root: Path | None = None, *, force: bool = False) -> Path:
    """下载并解出 micromamba（单文件，约 5MB）。已存在直接复用。"""
    exe = _micromamba_exe(root)
    if exe.is_file() and not force and exe.stat().st_size > 1_000_000:
        return exe

    exe.parent.mkdir(parents=True, exist_ok=True)
    archive = exe.parent / "micromamba.tar.bz2"
    _download(micromamba_url(), archive)

    try:
        with tarfile.open(archive) as tf:
            member = next(
                (
                    m
                    for m in tf.getmembers()
                    if Path(m.name).name in {"micromamba", "micromamba.exe"}
                ),
                None,
            )
            if member is None:
                raise PgRuntimeError("micromamba 压缩包里没有可执行文件")
            data = tf.extractfile(member)
            if data is None:
                raise PgRuntimeError("micromamba 解包失败")
            exe.write_bytes(data.read())
        if not sys.platform.startswith("win"):
            exe.chmod(0o755)
    except PgRuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PgRuntimeError(f"解出 micromamba 失败：{exc}") from exc
    finally:
        archive.unlink(missing_ok=True)

    if not exe.is_file():
        raise PgRuntimeError("micromamba 解开后不存在")
    return exe


def _base(root: Path | None = None, target: Path | None = None) -> Path:
    """PG 目录：显式 target 优先（安装中可能指向临时目录），否则按布局解析。"""
    return Path(target) if target is not None else layout.pg_dir(root)


def pg_bin_candidates(root: Path | None = None, *, target: Path | None = None) -> tuple[Path, ...]:
    """两种布局都要认：EDB 官方包是 `pg/bin`，conda 环境是 `pg/Library/bin`（win）/ `pg/bin`。"""
    base = _base(root, target)
    return (base / "Library" / "bin", base / "bin")


def locate_binary(root: Path | None, name: str, *, target: Path | None = None) -> Path | None:
    exe = f"{name}.exe" if sys.platform.startswith("win") else name
    for folder in pg_bin_candidates(root, target=target):
        candidate = folder / exe
        if candidate.is_file():
            return candidate
    return None


def sharedir(root: Path | None = None, *, target: Path | None = None) -> Path | None:
    base = _base(root, target)
    for folder in (base / "Library" / "share" / "extension", base / "share" / "extension"):
        if (folder / "vector.control").is_file():
            return folder
    return None


def pgvector_status(root: Path | None = None, *, target: Path | None = None) -> dict[str, Any]:
    """体检：`vector.control` + `vector` 动态库是否都在，版本能读就读。"""
    base = _base(root, target)
    ext = sharedir(root, target=target)
    if ext is None:
        return {
            "ok": False,
            "status": "missing",
            "detail": f"{base} 的 share/extension 下没有 vector.control",
        }
    lib_dir = base / "lib"
    if not lib_dir.is_dir():
        cand = base / "Library" / "lib"
        lib_dir = cand if cand.is_dir() else lib_dir

    libs = sorted(p.name for p in lib_dir.glob("vector.*")) if lib_dir.is_dir() else []
    version = ""
    control = ext / "vector.control"
    try:
        for line in control.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("default_version"):
                version = line.split("=", 1)[1].strip().strip("'")
                break
    except OSError:
        pass
    if not libs:
        return {
            "ok": False,
            "status": "missing_lib",
            "detail": f"{lib_dir} 里没有 vector 库文件（control 有、库没有）",
            "control": str(control),
        }
    return {
        "ok": True,
        "status": "ok",
        "version": version,
        "extension_dir": str(ext),
        "libs": libs,
    }


def install(
    root: Path | None = None, *, with_pgvector: bool = True, target: Path | None = None
) -> dict[str, Any]:
    """在 `runtime/pg` 建 conda 环境：postgresql 17 + pgvector（同编译链）。

    调用前提：PG/MinIO/Redis 等服务已停（文件被占用会失败）。
    失败一律抛 `PgRuntimeError`，不写 ready。
    """
    exe = ensure_micromamba(root)
    target = Path(target) if target is not None else layout.pg_dir(root)

    # 旧目录来自 EDB 官方包时先挪走，不要混装两个 PG（同名 DLL 会互相覆盖）。
    # 备份名带时间戳：不覆盖、不删除用户可能还在用的旧运行时。
    if target.is_dir() and not (target / "conda-meta").is_dir():
        backup = target.with_name(f"{target.name}-edb-backup-{time.strftime('%Y%m%d-%H%M%S')}")
        target.rename(backup)

    # **不要 pin 17**：conda-forge 的 pgvector（win-64）按自己的 libpq 版本段 pin 了
    # postgresql，写成 `postgresql=17` 会和它冲突（实测 solver 报 libpq 版本冲突）。
    # 只写 `postgresql>=16`（FC 要求 16+），让 solver 从 pgvector 的 pin 反推，
    # 最终拿到的是 16.15 + pgvector 0.8.6 这一对同链组合。
    spec = ["postgresql>=16"]
    if with_pgvector:
        spec.insert(0, "pgvector")

    cmd = [
        str(exe),
        "create",
        "-y",
        "-p",
        str(target),
        "-c",
        CONDA_FORGE,
        *spec,
    ]
    env = dict(os.environ)
    env.setdefault("MAMBA_ROOT_PREFIX", str(layout.runtime_dir(root) / "mamba"))
    env.setdefault("MAMBA_NO_BANNER", "1")
    try:
        done = subprocess.run(
            cmd, capture_output=True, text=True, timeout=CREATE_TIMEOUT, env=env, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PgRuntimeError(f"micromamba create 调用失败：{exc}") from exc

    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-6:]
        raise PgRuntimeError("micromamba create 失败：" + " / ".join(tail))

    binary = locate_binary(root, "postgres", target=target)
    if binary is None:
        raise PgRuntimeError(f"装完仍找不到 postgres 可执行文件：{target}")

    vector = pgvector_status(root, target=target)
    if with_pgvector and not vector.get("ok"):
        raise PgRuntimeError(f"pgvector 未就位：{vector.get('detail') or vector.get('status')}")

    return {
        "ok": True,
        "pg_target": str(target),
        "postgres": str(binary),
        "pgvector": vector,
    }


def version_of(root: Path | None = None, *, target: Path | None = None) -> str | None:
    """读 `postgres --version`（失败返回 None，不抛）。"""
    binary = locate_binary(root, "postgres", target=target)
    if binary is None:
        return None
    try:
        out = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (out.stdout or out.stderr or "").strip() or None


def as_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)


__all__ = [
    "PgRuntimeError",
    "ensure_micromamba",
    "install",
    "locate_binary",
    "micromamba_url",
    "pg_bin_candidates",
    "pgvector_status",
    "platform_key",
    "sharedir",
    "version_of",
]
