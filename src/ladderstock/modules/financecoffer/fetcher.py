"""增强包取件器（DEC-086 决策 1 / 2 / 3）。

每个组件一条取件路径，**本地来源优先、其次下载**，两者都拿不到就明确报缺，
绝不写占位文件冒充装好：

| 组件 | 首选 | 兜底 |
|---|---|---|
| pg | `LDST_FC_PG_ZIP` 本地 zip | 下载 EDB Windows 二进制包（含 initdb/pg_ctl） |
| redis | `LDST_FC_REDIS_ZIP` / `LDST_FC_REDIS_DIR` | 本机移植版 zip 自动探测；都没有就报缺 |
| minio | `LDST_FC_MINIO_EXE` | 本机已有 minio.exe；再不行官方下载 |
| api | `LDST_FC_SRC`（FinanceCoffer 源码目录） | ——（源码必须显式给，不猜） |
| embedding | `LDST_FC_EMBED_MODELS`（bge-m3 目录） | ——（模型权重不做自动下载，体积太大） |

「本机自动探测」只是开发机便利，生产/便携包靠环境变量或随包 runtime 提供。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from ladderstock.modules.financecoffer import layout

#: PG Windows 二进制（EDB 官方 zip，含 initdb / pg_ctl / postgres）
DEFAULT_PG_URL = "https://get.enterprisedb.com/postgresql/postgresql-17.6-1-windows-x64-binaries.zip"
#: MinIO 官方 Windows 二进制
DEFAULT_MINIO_URL = "https://dl.min.io/server/minio/release/windows-amd64/minio.exe"

#: 本机便捷探测路径（仅开发机；打包产物里应通过环境变量显式指定）
_PROBE_REDIS_ZIPS = (r"D:\workplace\ruanjian\dl\Redis-8.10.2-cygwin.zip",)
_PROBE_MINIO = (r"D:\workplace\ruanjian\minio\minio.exe",)
_PROBE_FC_SRC = (r"D:\workplace\FinanceCoffer",)
#: 已装好的 conda PG 运行时（含 pgvector）。便携包随包带一份即可，不必每次走网络。
_PROBE_PG_DIRS = (r"D:\workplace\LadderStock\file\modules\financecoffer\runtime\pg",)


def _pgvector_present(directory: Path) -> bool:
    """目录内是否自带 pgvector（看产物，不看全局 root）。

    `pg_runtime.pgvector_status(root)` 是从 `<root>/runtime/pg` 推导的，复制到任意位置
    时推不出来；校验复制结果只能直接看文件。
    """
    if not (directory / "Library" / "lib" / "vector.dll").is_file() and not (
        directory / "lib" / "vector.dll"
    ).is_file():
        return False
    for candidate in (
        directory / "Library" / "share" / "extension" / "vector.control",
        directory / "share" / "extension" / "vector.control",
    ):
        if candidate.is_file():
            return True
    return False
_PROBE_EMBED = (
    r"D:\workplace\ruanjian\embedding\models\bge-m3",
    r"D:\workplace\LadderStock\file\models\bge-small-zh-v1.5",
)


class FetchError(RuntimeError):
    """某个组件取件失败（缺来源、下载中断、包结构不对）。"""


def _copy_progress(src: Path, dst: Path) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst.stat().st_size


def _tree_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _copy_tree(src: Path, dst: Path) -> int:
    """复制目录；目标已存在则先判定为「已有内容」避免半份混装。"""
    if dst.exists() and any(dst.iterdir()):
        return _tree_size(dst)  # 幂等：重复安装不重拷
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return _tree_size(dst)


def _download(url: str, dest: Path, *, timeout: float = 20.0) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "LadderStock/1.0"})
    total = 0
    with urllib.request.urlopen(request, timeout=timeout) as resp, tmp.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            total += len(chunk)
    tmp.replace(dest)
    return total


def _unzip(zip_path: Path, dest: Path, *, inner_prefix: str | None = None) -> None:
    """解压；`inner_prefix` 用来把 zip 里的 `pgsql/` 一层展平到目标目录。"""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        if inner_prefix:
            prefix = inner_prefix.rstrip("/") + "/"
            members = [n for n in names if n.startswith(prefix) and n != prefix]
            if not members:
                raise FetchError(f"{zip_path.name} 里没有 {inner_prefix}/ 目录")
            for name in members:
                rel = name[len(prefix) :]
                if not rel:
                    continue
                target = dest / rel
                if name.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out)
        else:
            archive.extractall(dest)


def _first_env(*names: str) -> str | None:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


def _first_existing(candidates: tuple[str, ...]) -> Path | None:
    for item in candidates:
        path = Path(item)
        if path.exists():
            return path
    return None


# ------------------------------------------------------------------ 各组件


def fetch_pg(target: Path, *, root: Path | None = None) -> dict[str, Any]:
    """PostgreSQL 运行时取件。

    **默认走 conda-forge（方案 A）**：`postgresql 17` 与 `pgvector` 同一条编译链，
    一次装齐，`CREATE EXTENSION vector` 才可能成功。EDB 官方 zip 里**没有 pgvector**，
    单独塞一个 conda 的 `vector.dll` 会因为 ABI 不匹配报
    `The specified procedure could not be found`（实测），所以只作为降级保留
    （`LDST_FC_PG_MODE=edb`），装完体检会直接把 pgvector 标成 missing。
    """
    from ladderstock.modules.financecoffer import pg_runtime

    mode = (_first_env("LDST_FC_PG_MODE") or "conda").lower()

    if mode != "edb":
        if (target / "conda-meta").is_dir() and pg_runtime.pgvector_status(root).get("ok"):
            return {
                "bytes": _tree_size(target),
                "source": "already-present",
                "detail": "conda 环境与 pgvector 均已就位，跳过",
            }
        # 复用：本机/随包已经有一份 conda PG（含 pgvector）时直接复制，
        # 不再重跑 conda 解析与下载。pgvector 必须同链编译，所以只认 conda-meta 结构的源。
        dir_env = _first_env("LDST_FC_PG_DIR")
        candidates = [Path(dir_env)] if dir_env else [Path(p) for p in _PROBE_PG_DIRS]
        for src in candidates:
            if not (src / "conda-meta").is_dir():
                continue
            if src.resolve() == Path(target).resolve():
                continue
            if dir_env and not src.is_dir():
                raise FetchError(f"LDST_FC_PG_DIR 指向的目录不存在：{src}")
            shutil.copytree(src, target, dirs_exist_ok=True)
            if not _pgvector_present(target):
                raise FetchError(f"从 {src} 复制后目标里没有 pgvector（vector.dll / vector.control），目录结构不匹配")
            return {
                "bytes": _tree_size(target),
                "source": f"local-dir:{src}",
                "detail": "从既有 conda PG 运行时复制（含 pgvector），未走网络",
            }
        result = pg_runtime.install(root, target=target)
        return {
            "bytes": _tree_size(target),
            "source": "conda-forge:postgresql=17,pgvector",
            "detail": f"conda 同链安装完成：{result['postgres']}",
            "pgvector": result["pgvector"],
        }

    if (target / "bin" / "pg_ctl.exe").is_file() or (target / "bin" / "pg_ctl").is_file():
        return {"bytes": _tree_size(target), "source": "already-present", "detail": "已有 pg_ctl，跳过"}

    local_zip = _first_env("LDST_FC_PG_ZIP")
    if local_zip:
        zip_path = Path(local_zip)
        if not zip_path.is_file():
            raise FetchError(f"LDST_FC_PG_ZIP 指向的文件不存在：{zip_path}")
        source = str(zip_path)
        archive = zip_path
    else:
        cache = target.parent / "_cache"
        archive = cache / "postgresql-windows-x64-binaries.zip"
        if not archive.is_file() or archive.stat().st_size < 100 * 1024 * 1024:
            _download(DEFAULT_PG_URL, archive)
        source = DEFAULT_PG_URL

    _unzip(archive, target, inner_prefix="pgsql")
    if not (target / "bin" / "pg_ctl.exe").is_file() and not (target / "bin" / "pg_ctl").is_file():
        raise FetchError(f"解压后 {target} 里没有 pg_ctl，包结构不符合预期")
    return {
        "bytes": _tree_size(target),
        "source": source,
        "detail": "PostgreSQL 二进制已就位（EDB 包，不含 pgvector）",
    }


def fetch_redis(target: Path, *, root: Path | None = None) -> dict[str, Any]:
    if any((target / name).is_file() for name in ("redis-server.exe", "redis-server")):
        return {"bytes": _tree_size(target), "source": "already-present", "detail": "已有 redis-server，跳过"}

    zip_env = _first_env("LDST_FC_REDIS_ZIP")
    dir_env = _first_env("LDST_FC_REDIS_DIR")
    if dir_env:
        src = Path(dir_env)
        if not src.is_dir():
            raise FetchError(f"LDST_FC_REDIS_DIR 不是目录：{src}")
        _copy_tree(src, target)
        return {"bytes": _tree_size(target), "source": str(src), "detail": "从本地目录复制 Redis"}

    zip_path: Path | None = Path(zip_env) if zip_env else None
    if zip_path is None:
        zip_path = _first_existing(_PROBE_REDIS_ZIPS)
    if zip_path is None or not zip_path.is_file():
        raise FetchError(
            "没有可用的 Windows Redis 移植版：设置 LDST_FC_REDIS_ZIP（官方 zip 移植版）"
            "或 LDST_FC_REDIS_DIR（已解压目录）；不做自动下载，移植版来源需人工确认"
        )

    with tempfile.TemporaryDirectory() as tmp:
        _unzip(zip_path, Path(tmp))
        extracted = Path(tmp)
        entries = list(extracted.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            extracted = entries[0]  # 去掉单层目录
        _copy_tree(extracted, target)
    if not any((target / name).is_file() for name in ("redis-server.exe", "redis-server")):
        raise FetchError(f"{zip_path.name} 里没有 redis-server，移植版包结构不符合预期")
    return {"bytes": _tree_size(target), "source": str(zip_path), "detail": "从移植版 zip 解压 Redis"}


def fetch_minio(target: Path, *, root: Path | None = None) -> dict[str, Any]:
    name = "minio.exe" if os.name == "nt" else "minio"
    if (target / name).is_file():
        return {"bytes": (target / name).stat().st_size, "source": "already-present", "detail": "已有 minio，跳过"}

    exe_env = _first_env("LDST_FC_MINIO_EXE")
    src: Path | None = Path(exe_env) if exe_env else _first_existing(_PROBE_MINIO)
    if src is not None and src.is_file():
        size = _copy_progress(src, target / name)
        return {"bytes": size, "source": str(src), "detail": "从本地复制 minio"}

    cache = target.parent / "_cache" / name
    size = _download(DEFAULT_MINIO_URL, cache)
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cache, target / name)
    return {"bytes": size, "source": DEFAULT_MINIO_URL, "detail": "从官方地址下载 minio"}


def fetch_api(target: Path, *, root: Path | None = None) -> dict[str, Any]:
    """复制 FinanceCoffer 源码（不复制 .git / .venv / var 数据）。"""
    src_env = _first_env("LDST_FC_SRC")
    src: Path | None = Path(src_env) if src_env else _first_existing(_PROBE_FC_SRC)
    if src is None or not (src / "pyproject.toml").is_file():
        raise FetchError(
            "找不到 FinanceCoffer 源码目录：设置 LDST_FC_SRC 指向含 pyproject.toml 的目录"
        )
    if target.exists() and (target / "pyproject.toml").is_file():
        return {"bytes": _tree_size(target), "source": str(src), "detail": "已有源码，跳过"}

    target.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns(
        ".git", ".venv", "venv", "__pycache__", "var", "*.pyc", ".pytest_cache", ".mypy_cache"
    )
    shutil.copytree(src, target, dirs_exist_ok=True, ignore=ignore)
    return {"bytes": _tree_size(target), "source": str(src), "detail": "已复制 FinanceCoffer 源码"}


def fetch_embedding(target: Path, *, root: Path | None = None) -> dict[str, Any]:
    """复制模型权重目录（bge-m3）。权重不做自动下载：2GB 级，来源必须显式。"""
    if target.exists() and any(target.iterdir()):
        return {"bytes": _tree_size(target), "source": "already-present", "detail": "已有模型，跳过"}

    models_env = _first_env("LDST_FC_EMBED_MODELS")
    src: Path | None = Path(models_env) if models_env else _first_existing(_PROBE_EMBED)
    if src is None or not src.is_dir():
        raise FetchError(
            "找不到 embedding 模型目录：设置 LDST_FC_EMBED_MODELS 指向 bge-m3（含 pytorch_model.bin）"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    # 目录复制在 NTFS 上走硬链接更快更省盘；跨盘会自动退化成复制
    try:
        shutil.copytree(src, target, dirs_exist_ok=True)
    except OSError as exc:
        raise FetchError(f"复制模型失败：{exc}") from exc
    return {"bytes": _tree_size(target), "source": str(src), "detail": "已复制 bge-m3 模型权重"}


FETCHERS = {
    "pg": fetch_pg,
    "redis": fetch_redis,
    "minio": fetch_minio,
    "api": fetch_api,
    "embedding": fetch_embedding,
}


def fetch(component: str, target: Path, *, root: Path | None = None) -> int:
    """`installer.Fetcher` 签名实现：取件并返回字节数。"""
    handler = FETCHERS.get(component)
    if handler is None:
        raise FetchError(f"未知组件：{component!r}")
    result = handler(target, root=root)
    return int(result.get("bytes") or 0)


def describe(component: str, target: Path, *, root: Path | None = None) -> dict[str, Any]:
    """取件并返回完整结果（含来源说明），给 CLI/页面展示用。"""
    handler = FETCHERS.get(component)
    if handler is None:
        raise FetchError(f"未知组件：{component!r}")
    started = time.time()
    result = handler(target, root=root)
    return {**result, "component": component, "target": str(target), "elapsed_s": round(time.time() - started, 1)}


__all__ = [
    "DEFAULT_MINIO_URL",
    "DEFAULT_PG_URL",
    "FETCHERS",
    "FetchError",
    "describe",
    "fetch",
    "fetch_api",
    "fetch_embedding",
    "fetch_minio",
    "fetch_pg",
    "fetch_redis",
]
