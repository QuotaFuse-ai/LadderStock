"""进程树操作（QUOT-482-A）：supervisor 对进程的全部接触面都在这里。

链路是 `cmd → python → worker` 多层，只杀直接子进程必留孤儿——
所以所有终止一律**整树**：Windows 走 `taskkill /PID <root> /T /F`，
POSIX 按 `/proc` 的 PPid 递归先杀子孙。

引擎不直接碰 subprocess：`ProcessOps` 全部可注入，测试给假实现。
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

#: 进程查询类子进程的兜底超时（与 control.PROCESS_QUERY_TIMEOUT_SEC 同档）
QUERY_TIMEOUT_SEC = 10.0


class ProctreeError(RuntimeError):
    """整树终止后目标仍在，或查询通道全部不可用且无法给出安全结论。"""


def cmdline_hash(cmdline: Sequence[str]) -> str:
    """命令行指纹：归一 JSON 后 sha256。落账本、对账、归属判定共用。

    统一小写归一：Windows 路径大小写不敏感，CIM 回读的 exe 路径与 spawn 时
    给的大小写可能不同；指纹只用于「是不是同一个进程」判定，不做凭据比对。
    """
    normalized = [str(part).lower() for part in cmdline]
    payload = json.dumps(normalized, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def split_windows_commandline(raw: str) -> tuple[str, ...]:
    """Windows 命令行串 → argv（CommandLineToArgvW 的够用近似）。

    CIM 返回的 `CommandLine` 是拼接串（`"D:\\...\\th-cn.exe" start-all`），
    账本里存的是参数数组——指纹比对前必须先拆回 argv。处理：空白分割、
    双引号成段、`\\"` 转义；不追求覆盖全部极端转义（够区分「同一进程」即可）。
    """
    args: list[str] = []
    current: list[str] = []
    in_quotes = False
    escaped_quote = False
    i = 0
    while i < len(raw):
        ch = raw[i]
        if escaped_quote:
            current.append(ch)
            escaped_quote = False
        elif ch == "\\" and i + 1 < len(raw) and raw[i + 1] == '"':
            escaped_quote = True
        elif ch == '"':
            in_quotes = not in_quotes
        elif ch in (" ", "\t") and not in_quotes:
            if current:
                args.append("".join(current))
                current = []
        else:
            current.append(ch)
        i += 1
    if current:
        args.append("".join(current))
    return tuple(args)


class ProcessOps(Protocol):
    """进程操作面（测试注入假实现；生产用 `default_process_ops()`）。"""

    def self_pid(self) -> int: ...

    def alive(self, pid: int) -> bool: ...

    def cmdline(self, pid: int) -> tuple[str, ...] | None:
        """实时命令行；进程已死 / 查不到返回 None（调用方按无证据处理）。"""

    def tree_pids(self, root_pid: int) -> list[int]:
        """root 及全部子孙（含隔代）；root 已死返回空表。"""

    def kill_tree(self, root_pid: int) -> None:
        """整树强杀；杀完不回读（确认由引擎的 probe 负责）。"""


class WindowsProcessOps:
    """tasklist / Get-CimInstance / taskkill 的真实实现（本项目以 Windows 为主）。"""

    def self_pid(self) -> int:  # pragma: no cover - 平台直通
        return os.getpid()

    def alive(self, pid: int) -> bool:  # pragma: no cover - 子进程查询
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            shell=False,
            timeout=QUERY_TIMEOUT_SEC,
            capture_output=True,
            text=True,
            errors="replace",
        )
        return "No tasks" not in (result.stdout or "")

    def cmdline(self, pid: int) -> tuple[str, ...] | None:  # pragma: no cover - 子进程查询
        raw = self._cim_query(
            f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine'
        )
        if raw is None:
            raw = self._wmic_value("CommandLine", pid)
        if raw is None:
            return None
        return split_windows_commandline(raw)

    def tree_pids(self, root_pid: int) -> list[int]:  # pragma: no cover - 子进程查询
        edges = self._parent_edges()
        if not edges:
            return [root_pid] if self.alive(root_pid) else []
        children: dict[int, list[int]] = {}
        for child, parent in edges:
            children.setdefault(parent, []).append(child)
        tree: list[int] = []
        queue = [root_pid]
        seen: set[int] = set()
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            if not self.alive(current):
                continue
            tree.append(current)
            queue.extend(children.get(current, []))
        return tree

    def kill_tree(self, root_pid: int) -> None:  # pragma: no cover - 子进程终止
        result = subprocess.run(
            ["taskkill", "/PID", str(root_pid), "/T", "/F"],
            shell=False,
            timeout=QUERY_TIMEOUT_SEC,
            capture_output=True,
            text=True,
            errors="replace",
        )
        if result.returncode == 0:
            return
        # 「找不到进程」等于目标已死，不算失败；其余情况让引擎在 probe 上看到真相
        combined = f"{result.stdout or ''}{result.stderr or ''}"
        if "not found" in combined.lower() or "找不到" in combined:
            return

    def _cim_query(self, expression: str) -> str | None:  # pragma: no cover - 子进程查询
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                expression,
            ],
            shell=False,
            timeout=QUERY_TIMEOUT_SEC,
            capture_output=True,
            text=True,
            errors="replace",
        )
        if result.returncode != 0:
            return None
        return (result.stdout or "").strip() or None

    def _wmic_value(self, prop: str, pid: int) -> str | None:  # pragma: no cover - 兜底通道
        result = subprocess.run(
            [
                "wmic",
                "process",
                "where",
                f"ProcessId={pid}",
                "get",
                prop,
                "/format:list",
            ],
            shell=False,
            timeout=QUERY_TIMEOUT_SEC,
            capture_output=True,
            text=True,
            errors="replace",
        )
        for line in (result.stdout or "").splitlines():
            text = line.strip()
            if text.lower().startswith(f"{prop.lower()}="):
                value = text.split("=", 1)[1].strip()
                return value or None
        return None

    def _parent_edges(self) -> list[tuple[int, int]]:  # pragma: no cover - 子进程查询
        """全系统 (child_pid, parent_pid) 表：CIM 优先，wmic /format:csv 兜底。"""
        raw = self._cim_query(
            "Get-CimInstance Win32_Process | "
            'ForEach-Object { "$($_.ProcessId),$($_.ParentProcessId)" }'
        )
        edges = _parse_pid_pairs(raw or "")
        if edges:
            return edges
        result = subprocess.run(
            ["wmic", "process", "get", "ProcessId,ParentProcessId", "/format:csv"],
            shell=False,
            timeout=QUERY_TIMEOUT_SEC,
            capture_output=True,
            text=True,
            errors="replace",
        )
        return _parse_pid_pairs(result.stdout or "")


def _parse_pid_pairs(raw: str) -> list[tuple[int, int]]:  # pragma: no cover - 纯解析
    """从 CSV / `child,parent` 行流里抠 (child, parent)；wmic CSV 首列是节点名。"""
    edges: list[tuple[int, int]] = []
    for line in (raw or "").splitlines():
        parts = [p.strip() for p in line.strip().split(",")]
        numbers: list[int] = []
        for part in parts:
            if part.isdigit():
                numbers.append(int(part))
        if len(numbers) >= 2:
            child, parent = numbers[-2], numbers[-1]
            if child > 0 and parent > 0:
                edges.append((child, parent))
    return edges


class PosixProcessOps:
    """`/proc` 递归 + SIGKILL 的 POSIX 实现（开发/CI 用，生产以 Windows 为主）。"""

    def self_pid(self) -> int:  # pragma: no cover - 平台直通
        return os.getpid()

    def alive(self, pid: int) -> bool:  # pragma: no cover - 非 Windows
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def cmdline(self, pid: int) -> tuple[str, ...] | None:  # pragma: no cover - 非 Windows
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return None
        parts = tuple(part.decode("utf-8", "replace") for part in raw.split(b"\0") if part)
        return parts or None

    def tree_pids(self, root_pid: int) -> list[int]:  # pragma: no cover - 非 Windows
        children: dict[int, list[int]] = {}
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            stat_text = (entry / "stat").read_text(errors="replace")
            tail = stat_text.rsplit(")", 1)[-1].split()
            if len(tail) < 2:
                continue
            child = int(entry.name)
            parent = int(tail[1])
            children.setdefault(parent, []).append(child)
        tree: list[int] = []
        queue = [root_pid]
        seen: set[int] = set()
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            if not self.alive(current):
                continue
            tree.append(current)
            queue.extend(children.get(current, []))
        return tree

    def kill_tree(self, root_pid: int) -> None:  # pragma: no cover - 非 Windows
        # SIGKILL 是 POSIX 专属常量（Windows 的 signal 模块没有）；这个方法只在
        # 非 Windows 走到，回退值仅为让跨平台静态检查通过，运行时不会用到
        sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
        for pid in reversed(self.tree_pids(root_pid)):
            try:
                os.kill(pid, sigkill)
            except OSError:
                continue


def default_process_ops() -> ProcessOps:
    if os.name == "nt":
        return WindowsProcessOps()
    return PosixProcessOps()  # pragma: no cover - 平台分流
