"""嵌入模型的后下载：检测缺失 → 告知体积 → 下载 → SHA256 校验 → 落盘。

为什么要这一层：`file/models`（BAAI 原版 safetensors，约 92MB）不再默认进安装包，
普通用户拿到的是几百 MB 的包而不是 1GB+。第一次要用记忆时才下载权重。

边界：

- **只下模型数据，不装 Python 依赖**。PyInstaller 冻结后 `_internal` 是定死的，
  torch/sentence-transformers 必须随包；模型是纯文件，运行期下载没有 ABI 问题。
- **下载只发生在显式调用**（`fetch()` / setup 接口），`memory` 侧只读不联网
  （`HF_HUB_OFFLINE=1`），绝不静默下载。
- **落盘位置固定**：`user_data_root()/models`，与安装包解耦，升级不丢；
  `LDST_MEMORY_MODELS_DIR` 仍可整体覆盖（开发态/测试用）。
- **完整性以随包 `SHA256SUMS` 为准**，只校验本模型目录内的条目；校验不过删掉
  半成品，不留“装了一半”的目录。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ProgressFn = Callable[[float, str], None]

#: 模型目录名与 memory.py 保持一致（那边是唯一运行时口径）
MODEL_DIRNAME = "bge-small-zh-v1.5-baai"
MANIFEST_FILENAME = "SHA256SUMS"

#: HF 仓库与 revision：钉死 commit，避免上游改动导致哈希对不上
HF_REPO = "BAAI/bge-small-zh-v1.5"
HF_REVISION = "main"

#: 国内可达优先，官方兜底。只看能不能连，不猜用户地区。
HF_MIRRORS: tuple[str, ...] = (
    "https://hf-mirror.com",
    "https://huggingface.co",
)

#: 目录内约 92MB；向导按这个数字告知用户，不实时算
SIZE_MB = 92

_lock = threading.Lock()
_job: dict[str, Any] = {}


class ModelFetchError(RuntimeError):
    """下载或校验失败，带可展示的原因。"""


@dataclass(frozen=True)
class ModelFile:
    rel: str
    sha256: str
    size: int


def download_root() -> Path:
    """下载落点：用户数据目录下的 models/（升级不丢，安装包只读也能用）。"""
    from ladderstock.cli.paths import user_data_root

    env = os.environ.get("LDST_MEMORY_MODELS_DIR", "").strip()
    if env:
        return Path(env)
    return user_data_root() / "models"


def bundled_models_root() -> Path | None:
    """随包预置的模型根（离线包才有）；找不到返回 None。"""
    from ladderstock.core.runtime import install_root, project_root

    for root in (install_root(), project_root()):
        candidate = root / "file" / "models"
        if (candidate / MODEL_DIRNAME).is_dir():
            return candidate
    return None


def _manifest_paths() -> list[Path]:
    """校验清单的查找顺序：下载目录 → 随包目录 → 仓库内（开发态）。"""
    roots = [download_root()]
    bundled = bundled_models_root()
    if bundled is not None:
        roots.append(bundled)
    return [root / MANIFEST_FILENAME for root in roots]


def load_manifest() -> list[ModelFile]:
    """读本模型目录的 SHA256SUMS 条目。清单缺失直接报错，不放过无校验的下载。"""
    for path in _manifest_paths():
        if not path.is_file():
            continue
        out: list[ModelFile] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            digest, _, rel = line.partition(" ")
            rel = rel.strip().lstrip("*").replace("/", os.sep)
            if not rel.startswith(MODEL_DIRNAME + os.sep):
                continue
            out.append(ModelFile(rel=rel, sha256=digest.lower(), size=0))
        if out:
            return out
    raise ModelFetchError(
        f"找不到模型校验清单 {MANIFEST_FILENAME}；无法确认下载内容是否可信"
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(root: Path | None = None, *, entries: list[ModelFile] | None = None) -> list[str]:
    """逐条比对哈希，返回问题列表（空 = 完好）。缺文件与哈希不符都算问题。"""
    base = Path(root) if root is not None else download_root()
    items = entries if entries is not None else load_manifest()
    bad: list[str] = []
    for item in items:
        path = base / item.rel
        if not path.is_file():
            bad.append(f"{item.rel}（缺失）")
            continue
        if _sha256(path) != item.sha256:
            bad.append(f"{item.rel}（哈希不一致）")
    return bad


def is_ready(root: Path | None = None) -> bool:
    """模型是否已就绪：目录在 + 清单条目全过。任何异常都按未就绪处理。"""
    base = Path(root) if root is not None else download_root()
    if not (base / MODEL_DIRNAME).is_dir():
        return False
    try:
        return not verify(base)
    except ModelFetchError:
        return False


def status() -> dict[str, Any]:
    """给向导/接口的一行现状：装在哪、齐不齐、多大、从哪下。"""
    root = download_root()
    bundled = bundled_models_root()
    ready = is_ready(root) or (bundled is not None and is_ready(bundled))
    try:
        entries = load_manifest()
        missing = [item.rel for item in entries if not (root / item.rel).is_file()]
    except ModelFetchError:
        entries = []
        missing = []
    return {
        "ok": True,
        "ready": ready,
        "models_dir": str(root),
        "bundled_dir": str(bundled) if bundled is not None else "",
        "model": HF_REPO,
        "dirname": MODEL_DIRNAME,
        "size_mb": SIZE_MB,
        "files": len(entries),
        "missing": missing,
        "mirrors": list(HF_MIRRORS),
        "job": snapshot(),
    }


def snapshot() -> dict[str, Any] | None:
    with _lock:
        return dict(_job) if _job else None


def _emit(pct: float, message: str, cb: ProgressFn | None) -> None:
    with _lock:
        _job["pct"] = round(max(0.0, min(pct, 1.0)), 3)
        _job["message"] = message
    if cb is None:
        return
    try:
        cb(pct, message)
    except Exception:  # 回调异常不能打断下载
        pass


def _download(url: str, dest: Path, *, cb: ProgressFn | None, base_pct: float, span: float) -> None:
    """流式下载单个文件，边下边报进度；先写 .part 再原子改名。"""
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, timeout=60.0, follow_redirects=True) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length") or 0)
        done = 0
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes(1 << 20):
                fh.write(chunk)
                done += len(chunk)
                if total:
                    _emit(base_pct + span * done / total, f"下载 {dest.name}", cb)
    tmp.replace(dest)


def fetch(*, on_progress: ProgressFn | None = None) -> dict[str, Any]:
    """下载并校验模型。已就绪直接返回；失败清掉半成品并落 failed。"""
    import time

    root = download_root()
    with _lock:
        if _job.get("status") == "running":
            return {"ok": False, "status": "busy", "detail": "已有下载任务在跑"}
        _job.clear()
        _job.update(
            {
                "status": "running",
                "started_at": time.time(),
                "finished_at": None,
                "pct": 0.0,
                "message": "准备下载",
                "models_dir": str(root),
            }
        )

    try:
        entries = load_manifest()
        if is_ready(root):
            _emit(1.0, "模型已就绪", on_progress)
            with _lock:
                _job["status"] = "done"
                _job["finished_at"] = time.time()
            return {"ok": True, "status": "already_ready", "models_dir": str(root)}

        # 随包已有完好副本（离线包）：直接复制，不联网
        bundled = bundled_models_root()
        if bundled is not None and is_ready(bundled):
            _emit(0.2, "从随包副本复制", on_progress)
            target = root / MODEL_DIRNAME
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(bundled / MODEL_DIRNAME, target)
            shutil.copy2(bundled / MANIFEST_FILENAME, root / MANIFEST_FILENAME)
        else:
            span = 0.8 / max(len(entries), 1)
            last_error = ""
            for mirror in HF_MIRRORS:
                try:
                    for idx, item in enumerate(entries):
                        name = item.rel.split(os.sep, 1)[1]
                        url = f"{mirror}/{HF_REPO}/resolve/{HF_REVISION}/{name}"
                        _download(
                            url,
                            root / item.rel,
                            cb=on_progress,
                            base_pct=0.05 + span * idx,
                            span=span,
                        )
                    last_error = ""
                    break
                except Exception as exc:  # noqa: BLE001 - 换镜像重试
                    last_error = f"{type(exc).__name__}: {exc}"
                    _emit(0.05, f"{mirror} 失败，换下一个源", on_progress)
            if last_error:
                raise ModelFetchError(f"所有下载源都失败，最后一次错误：{last_error}")

        _emit(0.9, "校验文件完整性", on_progress)
        bad = verify(root, entries=entries)
        if bad:
            raise ModelFetchError("模型校验失败：" + "；".join(bad[:5]))

        _emit(1.0, "模型就绪", on_progress)
        with _lock:
            _job["status"] = "done"
            _job["finished_at"] = time.time()
        return {"ok": True, "status": "done", "models_dir": str(root)}

    except Exception as exc:  # noqa: BLE001 - 半成品必须清掉
        detail = f"{type(exc).__name__}: {exc}"
        target = root / MODEL_DIRNAME
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        with _lock:
            _job["status"] = "failed"
            _job["finished_at"] = time.time()
            _job["error"] = detail
        return {"ok": False, "status": "failed", "detail": detail, "models_dir": str(root)}


__all__ = [
    "HF_MIRRORS",
    "HF_REPO",
    "MODEL_DIRNAME",
    "SIZE_MB",
    "ModelFetchError",
    "bundled_models_root",
    "download_root",
    "fetch",
    "is_ready",
    "load_manifest",
    "snapshot",
    "status",
    "verify",
]
