"""端口自动避让（QUOT-594）：foreign 占用时自动换端口，用户无感。

DEC-004「绝不改端口」在本单被替换为「foreign 占用时自动避让，own/adopted
不动」——商业安装包的用户不知道什么是端口冲突，应用必须自己解决。红线不变：
DEC-014 绝不杀外来进程；避让只是换**自己**监听的端口，不碰任何运行中进程。

判定口径与既有四分类（`web/port_diagnostics.classify_port_owner`）一致：
- `own_child` / `adopted_peer` 在听 → 正常 adopt / 续跑，绝不避让；
- `foreign` 在听 → 从 base+1 起线性扫描空闲端口，上限 +100；
- 超限仍无空闲 → `AllocationFailed`，由引擎翻成 needs_human（极端情况，交人工）。

避让结果持久化在安装根 `config/ports.yaml`（相对路径，QUOT-591 便携口径）：
首次避让写入，之后启动直接复用——用户重启应用后端口稳定，不漂移。
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ladderstock.supervisor.modules import current_env

#: 避让上限：base+100 仍无空闲则报 needs_human，绝不无限扫描
MAX_SHIFT = 100

#: ports.yaml 顶层键
_KEY_BOARD = "board"
_KEY_MODULES = "modules"
_KEY_ENV = "env"

#: 在听即不避让的占用者分类（与 classify_port_owner 四分类同口径）
_KEEP_OWNERS = ("own_child", "adopted_peer")


class AllocationFailed(RuntimeError):
    """避让失败：上限内无空闲端口（引擎翻成 needs_human）。"""


@dataclass(frozen=True)
class PortAllocation:
    """一个模块（或看板）的端口避让结果。"""

    #: 逐端口避让明细：base → actual（无冲突时 base == actual）
    assignments: tuple[tuple[int, int], ...]

    @property
    def actual(self) -> tuple[int, ...]:
        return tuple(a for _, a in self.assignments)

    @property
    def shifted(self) -> bool:
        return any(b != a for b, a in self.assignments)


def load_port_overrides(config_dir: Path) -> dict[str, tuple[int, ...]]:
    """读 config/ports.yaml 的 modules 段；无文件 / 损坏回空（基准表兜底）。"""
    path = Path(config_dir) / "ports.yaml"
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - 无文件 / YAML 损坏一律回落基准表
        return {}
    modules = data.get(_KEY_MODULES)
    # 环境切换（8000 系 ↔ 18000 系，QUOT-593）时旧记录整段失效：面数可能相同
    # 但端口段不同，必须回落该环境的基准表，而不是复用别的环境避让结果。
    env = str(data.get(_KEY_ENV) or "").strip().lower()
    if env and env != current_env():
        return {}
    if not isinstance(modules, dict):
        return {}
    out: dict[str, tuple[int, ...]] = {}
    for name, value in modules.items():
        if isinstance(value, int) and value > 0:
            out[str(name)] = (int(value),)
        elif isinstance(value, (list, tuple)):
            ports = tuple(int(p) for p in value if isinstance(p, int) and p > 0)
            if ports:
                out[str(name)] = ports
    return out


def load_board_override(config_dir: Path) -> int | None:
    """读 config/ports.yaml 的 board 端口；无记录返回 None。"""
    path = Path(config_dir) / "ports.yaml"
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return None
    board = data.get(_KEY_BOARD)
    if isinstance(board, int) and board > 0:
        return board
    return None


def save_port_overrides(
    config_dir: Path, board: int, modules: Mapping[str, tuple[int, ...]]
) -> Path:
    """把看板 + 模块端口写入 config/ports.yaml（临时文件 + 原子替换）。"""
    path = Path(config_dir) / "ports.yaml"
    import yaml

    payload = {
        _KEY_ENV: current_env(),
        _KEY_BOARD: int(board),
        _KEY_MODULES: {name: list(ports) for name, ports in modules.items()},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    os.replace(tmp, path)
    return path


def allocate_ports(
    base_ports: tuple[int, ...],
    *,
    owner_classifier: Callable[[int], str],
    free_probe: Callable[[int], bool],
    max_shift: int = MAX_SHIFT,
) -> PortAllocation:
    """对一组基准端口逐个避让，返回实际可用端口组。

    `owner_classifier(port)`：端口在听时由调用方给出占用者分类
    （`own_child` / `adopted_peer` / `foreign` / `free`，与
    `classify_port_owner` 同口径）。own/adopted → 不避让（保留原端口，
    正常 adopt / 续跑）；foreign → 从 base+1 起扫描，首个空闲端口即避让
    结果；上限内无空闲 → `AllocationFailed`。

    `free_probe(port)`：端口无监听即 True（生产 = socket bind 探测）。
    """
    assignments: list[tuple[int, int]] = []
    for base in base_ports:
        if free_probe(base):
            assignments.append((base, base))
            continue
        owner = owner_classifier(base)
        if owner in _KEEP_OWNERS:
            # 本方进程在听：正常 adopt / 续跑，绝不避让（QUOT-545 / DEC-014）
            assignments.append((base, base))
            continue
        chosen: int | None = None
        for candidate in range(base + 1, base + max_shift + 1):
            if free_probe(candidate):
                chosen = candidate
                break
        if chosen is None:
            raise AllocationFailed(
                f"端口 {base} 被外部程序占用，{max_shift} 个候选端口"
                f"（{base + 1}~{base + max_shift}）内没有空闲，需要人工处理"
            )
        assignments.append((base, chosen))
    return PortAllocation(assignments=tuple(assignments))


class PortOverrideStore:
    """避让结果的进程内缓存 + ports.yaml 读写（线程安全，引擎持锁调用）。

    读路径：启动时 `refresh()` 一次，之后 `module_ports()` / `board_port()`
    直接给实际端口（有记录用记录，否则基准表）；写路径：避让发生时
    `set_module()` / `set_board()` 更新缓存并落盘，下次启动复用。
    """

    def __init__(self, config_dir: Path, *, default_board: int = 5950) -> None:
        self._dir = Path(config_dir)
        self._default_board = int(default_board)
        self._lock = threading.Lock()
        self._board: int | None = None
        self._modules: dict[str, tuple[int, ...]] = {}

    def refresh(self) -> None:
        """重新读 ports.yaml（引擎启动时调一次）。"""
        with self._lock:
            self._modules = load_port_overrides(self._dir)
            self._board = load_board_override(self._dir)

    def module_ports(self, name: str, base: tuple[int, ...]) -> tuple[int, ...]:
        """模块实际端口：ports.yaml 有合理记录用记录，否则基准表。

        记录长度必须与基准表一致（面数不变才可复用），损坏回落基准表。
        """
        with self._lock:
            override = self._modules.get(name)
        if override and len(override) == len(tuple(base)):
            return override
        return tuple(base)

    def board_port(self) -> int:
        with self._lock:
            return self._board if self._board is not None else self._default_board

    def set_board(self, port: int) -> None:
        with self._lock:
            self._board = int(port)
            self._persist_locked()

    def set_module(self, name: str, ports: tuple[int, ...]) -> None:
        with self._lock:
            self._modules[str(name)] = tuple(int(p) for p in ports)
            self._persist_locked()

    def snapshot(self) -> dict[str, Any]:
        """当前实际端口表（设置页只读展示用）。"""
        with self._lock:
            return {
                "board": self.board_port(),
                "modules": dict(self._modules),
            }

    def _persist_locked(self) -> None:
        save_port_overrides(
            self._dir,
            self._board if self._board is not None else self._default_board,
            self._modules,
        )


__all__ = [
    "AllocationFailed",
    "MAX_SHIFT",
    "PortAllocation",
    "PortOverrideStore",
    "allocate_ports",
    "load_board_override",
    "load_port_overrides",
    "save_port_overrides",
]
