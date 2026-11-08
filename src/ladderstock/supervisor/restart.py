"""重拉通道 + 进程存活检测（QUOT-491 / 482-C）。

**不写模块启动命令**（那是 482-B adapters 的职责）。482-A/482-B 落地前，重拉由一个
外部命令表驱动（`data/supervisor/restart-commands.json`：`{"模块名": ["exe", "arg", ...]}`，
`shell=False`）——命令内容全部来自部署侧，本仓代码里没有一条模块启动命令。

命令表为空/缺条目时，`restart()` 如实返回失败（「无可用重拉通道」），自愈引擎会照常
走退避与上限判定、最终置 `needs_human`——**绝不假装重拉成功**。482-B 的 adapters
落地后，把这里的实现换成其 `Adapter.start()` 即可，`SelfHealEngine` 无需改动。
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ladderstock.supervisor.ledger import LedgerEntry

SYNCHRONIZE = 0x00100000
WAIT_TIMEOUT = 0x00000102
INFINITE = 0xFFFFFFFF


def windows_pid_alive(pid: int) -> bool:
    """Windows 下按 pid 判活：OpenProcess + WaitForSingleObject(0)。

    进程已退出 → 句柄被触发（WAIT_OBJECT_0）；还活着 → WAIT_TIMEOUT。
    打不开句柄（权限/不存在）一律按不存活处理——自愈只会「多探一次」，不会误标成功。
    """
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]  # windll 仅 win32 平台存在
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def posix_pid_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 进程在，只是没权限发信号
    return True


def pid_alive(pid: int | None) -> bool:
    """跨平台按 pid 判活（pid 复用的最终防线是 482-A 的 cmdline_hash，这里先按 pid）。"""
    if pid is None or int(pid) <= 0:
        return False
    if os.name == "nt":
        return windows_pid_alive(int(pid))
    return posix_pid_alive(int(pid))


class CommandTableRestarter:
    """命令表驱动的重拉通道（482-B 落地前的过渡实现）。"""

    def __init__(self, commands: Mapping[str, Sequence[str]] | None = None) -> None:
        self._commands: dict[str, list[str]] = {
            str(module): [str(part) for part in argv]
            for module, argv in (commands or {}).items()
        }

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> CommandTableRestarter:
        target = Path(path)
        if not target.exists():
            return cls({})
        try:
            raw: Any = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls({})
        if not isinstance(raw, dict):
            return cls({})
        commands = {str(k): v for k, v in raw.items() if isinstance(v, list)}
        return cls(commands)

    @property
    def known_modules(self) -> tuple[str, ...]:
        return tuple(self._commands)

    def is_alive(self, entry: LedgerEntry) -> bool:
        return pid_alive(entry.pid)

    def restart(self, module: str) -> Any:
        """按命令表拉起模块；返回 `RestartOutcome`（见 selfheal，避免循环导入用 Any 标注）。"""
        from ladderstock.supervisor.selfheal import RestartOutcome

        argv = self._commands.get(module)
        if not argv:
            return RestartOutcome(ok=False, detail="无可用重拉通道（重启命令表未配置该模块）")
        try:
            process = subprocess.Popen(  # noqa: S603 — argv 白名单来自本地命令表，无 shell
                argv,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            return RestartOutcome(ok=False, detail=f"重拉启动失败：{exc}")
        return RestartOutcome(ok=True, pid=process.pid)


__all__ = ["CommandTableRestarter", "pid_alive", "posix_pid_alive", "windows_pid_alive"]
