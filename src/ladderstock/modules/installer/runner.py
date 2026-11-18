"""子模块安装执行器：建独立环境 → 从 PyPI 镜像装包 → 验入口 → 落状态。

设计要点：
- **每个 peer 一个独立 venv**，落在 `<安装根>/file/modules/<target>/.venv`。
  不共用主程序的 site-packages，避免依赖冲突（TickHub-CN 的 mcp 2.x 和
  LadderStock 的 openai-agents 对 httpx 的要求就不一致）。
- **解释器优先取随包 Python**（`<安装根>/python/python.exe`，embeddable 版）。
  拿不到才回落到当前进程的 `sys.executable`——那是开发态才成立，便携包必须自带。
- **取件只走 PyPI**，镜像按 `manifest.PYPI_MIRRORS` 顺序试，首个成功即停。
  不碰 GitHub：普通用户机器不保证能连，第三方加速站不保证长期可用。
- **进度按阶段加权上报**，签名 `on_progress(pct: float, message: str)`，
  pct 为 0.0–1.0。pip 阶段按输出行解析「已收集 / 已下载」计数，给的是
  可信的阶段进度而非假装精确的百分比。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ladderstock.modules.installer import manifest, state

ProgressFn = Callable[[float, str], None]


class InstallError(RuntimeError):
    """安装失败，带可展示给用户的原因。"""


@dataclass(frozen=True)
class InstallResult:
    ok: bool
    peer: str
    target: Path
    state: str
    detail: str = ""
    log_tail: str = ""

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "peer": self.peer,
            "target": str(self.target),
            "state": self.state,
            "detail": self.detail,
            "log_tail": self.log_tail,
        }


# --------------------------------------------------------------------------- #
# 环境定位
# --------------------------------------------------------------------------- #


def portable_python(root: Path | None = None) -> Path | None:
    """随包解释器：`<程序目录>/python/python.exe`（Windows）或 `.../bin/python3`。

    **必须以程序目录为基准**：`runtime.install_root()` 在开发态会向上跑到 workspace 根
    （`D:\\workplace`），随包运行时却跟 `LadderStock.exe` 同级（`D:\\workplace\\LadderStock`）。
    早期版本只探 `install_root()`，于是目录建好了、状态里 `portable_python` 仍为空。
    打包后两者通常重合，所以 `install_root()` 保留为第二顺位兜底。
    """
    from ladderstock.core import runtime

    if root is not None:
        bases = [Path(root)]
    else:
        bases = [runtime.app_dir(), runtime.install_root()]

    for base in bases:
        candidates = (
            base / "python" / "python.exe",
            base / "python" / "bin" / "python3",
            base / "bin" / "python" / "python.exe",
        )
        for path in candidates:
            if path.is_file():
                return path
    return None


def resolve_interpreter(root: Path | None = None) -> tuple[Path, str]:
    """返回 (解释器路径, 来源说明)。来源用于在向导上如实告诉用户。"""
    bundled = portable_python(root)
    if bundled is not None:
        return bundled, "随包 Python"
    return Path(sys.executable), "本机 Python（开发态回落，便携包不应出现）"


def venv_python(target: Path) -> Path:
    """venv 内的解释器。"""
    if os.name == "nt":
        return target / ".venv" / "Scripts" / "python.exe"
    return target / ".venv" / "bin" / "python3"


def venv_scripts(target: Path) -> Path:
    return target / ".venv" / ("Scripts" if os.name == "nt" else "bin")


# --------------------------------------------------------------------------- #
# 进度
# --------------------------------------------------------------------------- #


class _Progress:
    """把各阶段折算成统一 0–1 进度，同时保留一句人能看懂的话。"""

    def __init__(self, callback: ProgressFn | None) -> None:
        self._cb = callback
        self._floor = 0.0
        self._ceil = 1.0

    def stage(self, floor: float, ceil: float, message: str) -> None:
        self._floor, self._ceil = floor, ceil
        self.emit(0.0, message)

    def emit(self, fraction: float, message: str) -> None:
        if self._cb is None:
            return
        span = max(self._ceil - self._floor, 0.0)
        value = min(max(self._floor + span * fraction, 0.0), 1.0)
        try:
            self._cb(value, message)
        except Exception:  # 回调异常不能打断安装
            pass


# pip 输出里的阶段信号
_COLLECT_RE = re.compile(r"Collecting\s+([A-Za-z0-9_.\-]+)")
_DL_PCT_RE = re.compile(r"Downloading\s+\S+\s+\(([\d.]+)\s*(k|M|G)B\)")
_INSTALLED_RE = re.compile(r"Successfully installed")


def _run_pip(
    interpreter: Path,
    args: list[str],
    *,
    progress: _Progress,
    log: list[str],
) -> None:
    """跑一次 pip，边跑边解析进度；失败抛 InstallError 并带上输出尾部。"""
    cmd = [str(interpreter), "-m", "pip", *args, "--disable-pip-version-check", "--no-input"]
    env = dict(os.environ)
    env.setdefault("PIP_NO_COLOR", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.pop("PIP_REQUIRE_VIRTUALENV", None)

    collected = 0
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        log.append(line)
        if len(log) > 400:
            del log[:200]
        if m := _COLLECT_RE.search(line):
            collected += 1
            # 单个 peer 的直接+传递依赖多在 30–70 个，按 55 估即可给出可读的推进
            progress.emit(min(collected / 55.0, 0.95), f"已收集依赖 {collected} 个（{m.group(1)}）")
        elif m := _DL_PCT_RE.search(line):
            size = f"{m.group(1)}{m.group(2)}B"
            progress.emit(0.5, f"下载中 {size}")
        elif _INSTALLED_RE.search(line):
            progress.emit(0.98, "写入磁盘")
    code = proc.wait()
    if code != 0:
        tail = "\n".join(log[-12:])
        raise InstallError(f"pip 退出码 {code}\n{tail}")


def _pip_index_args(mirrors: Iterable[str]) -> list[list[str]]:
    """每个镜像一组参数；调用方按顺序试。"""
    return [["--index-url", url] for url in mirrors]


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def _prepare_target(target: Path) -> None:
    """目录不存在则建；存在则保留（可能是上次装了一半）。"""
    target.mkdir(parents=True, exist_ok=True)


def _ensure_venv(target: Path, interpreter: Path, *, progress: _Progress, log: list[str]) -> Path:
    """建 venv 并升级 pip；已存在则复用。"""
    py = venv_python(target)
    if py.is_file():
        progress.emit(0.4, "复用已有环境")
        return py

    progress.emit(0.1, "创建独立运行环境")
    cmd = [str(interpreter), "-m", "venv", str(target / ".venv")]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    log.append(" ".join(cmd))
    if proc.returncode != 0:
        raise InstallError(f"创建 venv 失败：{(proc.stderr or proc.stdout or '').strip()[-400:]}")
    if not py.is_file():
        raise InstallError(f"venv 建完但找不到解释器：{py}")

    progress.emit(0.3, "升级 pip")
    try:
        _run_pip(py, ["install", "--upgrade", "pip"], progress=progress, log=log)
    except InstallError:
        # 升级 pip 失败不该阻断安装（很多内网镜像不提供最新 pip）
        log.append("pip 升级失败，继续用 venv 自带版本")
    return py


def _verify_entry(target: Path, item: manifest.PeerManifest, *, progress: _Progress, log: list[str]) -> None:
    """跑一次入口命令，确认装完真能起。"""
    if not item.entry:
        progress.emit(1.0, "无需入口验证")
        return
    script_name = item.entry.split()[0]
    scripts = venv_scripts(target)
    exe = scripts / (f"{script_name}.exe" if os.name == "nt" else script_name)
    if not exe.is_file():
        raise InstallError(f"找不到入口 {exe}（包可能装失败或 console script 名不符）")
    args = item.entry.split()[1:]
    progress.emit(0.2, f"验证 {script_name}")
    proc = subprocess.run(
        [str(exe), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    log.append(f"{item.entry} → exit {proc.returncode}")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise InstallError(f"入口验证失败（{item.entry}）：{tail}")
    progress.emit(1.0, f"{script_name} 可用")


def install_peer(
    key: str,
    *,
    root: Path | None = None,
    mirrors: Iterable[str] | None = None,
    on_progress: ProgressFn | None = None,
) -> InstallResult:
    """装一个 peer。失败落到 `failed` 并带原因，成功落 `ready`。"""
    item = manifest.BY_KEY.get(key)
    if item is None:
        raise InstallError(f"未知子模块：{key}")

    from ladderstock.core import boundary

    target = boundary.modules_root(root) / item.target if root is None else Path(root) / "file" / "modules" / item.target
    progress = _Progress(on_progress)
    log: list[str] = []

    try:
        state.write_state(key, "planning", root, detail=f"目标 {target}")
        _prepare_target(target)

        if item.kind == "bundle":
            # 增强包交给已有的 financecoffer 安装器；进度回调原样透传，
            # 它会按组件体积加权上报（PG 848MB 比 API 40MB 占更多进度）
            from ladderstock.modules.financecoffer import installer as fc_installer

            progress.stage(0.0, 1.0, "转交增强包安装器")
            result = fc_installer.install(
                root, with_embedding=False, confirm=True, on_progress=on_progress
            )
            if not result.get("ok"):
                raise InstallError(str(result.get("detail") or "增强包安装未成功"))
            state.write_state(key, "ready", root, detail="增强包安装完成")
            return InstallResult(True, key, target, "ready")

        interpreter, origin = resolve_interpreter(root)
        progress.stage(0.0, 0.2, f"使用{origin}")
        py = _ensure_venv(target, interpreter, progress=progress, log=log)

        state.write_state(key, "fetching", root, step="pip")
        packages = item.package or item.key
        # 版本约束直接后缀在主包后：`tickhub-cn[sources]>=1.1,<2`。
        # 早期版本在这里插过一个逗号，pip 会当成两个包（`tickhub-cn[sources]` 和 `>=1.1`）而报错。
        constraint = f"{packages}{item.version}"
        targets = [constraint, *item.extra_requirements]
        progress.stage(0.2, 0.92, f"安装 {constraint}")
        last_error = ""
        for index_args in _pip_index_args(list(mirrors or manifest.PYPI_MIRRORS)):
            try:
                _run_pip(py, ["install", *targets, *index_args], progress=progress, log=log)
                last_error = ""
                break
            except InstallError as exc:
                last_error = str(exc)
                log.append(f"镜像失败，换下一个：{index_args[-1]}")
                continue
        if last_error:
            raise InstallError(f"所有镜像都装不上，最后一次错误：{last_error}")

        state.write_state(key, "verifying", root, step="entry")
        progress.stage(0.92, 1.0, "验证入口")
        _verify_entry(target, item, progress=progress, log=log)

        state.write_state(key, "ready", root, detail="安装完成")
        return InstallResult(True, key, target, "ready")

    except InstallError as exc:
        detail = str(exc)
        try:
            state.write_state(key, "failed", root, detail=detail)
        except Exception:
            pass
        return InstallResult(False, key, target, "failed", detail=detail, log_tail="\n".join(log[-12:]))
    except Exception as exc:  # 未预期异常也要落 failed，不留「装了一半没人知道」
        detail = f"{type(exc).__name__}: {exc}"
        try:
            state.write_state(key, "failed", root, detail=detail)
        except Exception:
            pass
        return InstallResult(False, key, target, "failed", detail=detail, log_tail="\n".join(log[-12:]))


def uninstall_peer(key: str, *, root: Path | None = None, confirm: bool = False) -> InstallResult:
    """删掉 peer 目录并回到 not_installed。`confirm` 必须显式为 True。"""
    item = manifest.BY_KEY.get(key)
    if item is None:
        raise InstallError(f"未知子模块：{key}")
    if not confirm:
        return InstallResult(False, key, Path(""), "not_installed", detail="需要 confirm=True")

    target = boundary_modules_target(item, root)
    state.write_state(key, "uninstalling", root, detail="删除中")
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    state.write_state(key, "not_installed", root, detail="已删除")
    return InstallResult(True, key, target, "not_installed")


def boundary_modules_target(item: manifest.PeerManifest, root: Path | None = None) -> Path:
    from ladderstock.core import boundary

    base = boundary.modules_root(root) if root is None else Path(root) / "file" / "modules"
    return base / item.target


def missing_peers(root: Path | None = None) -> tuple[str, ...]:
    """按目录存在性判断「还没装」的 peer（不走状态文件，避免状态与实际不一致）。"""
    missing: list[str] = []
    for item in manifest.PEERS:
        target = boundary_modules_target(item, root)
        if not target.exists():
            missing.append(item.key)
    return tuple(missing)


__all__ = [
    "InstallError",
    "InstallResult",
    "install_peer",
    "missing_peers",
    "portable_python",
    "resolve_interpreter",
    "venv_python",
    "venv_scripts",
]
