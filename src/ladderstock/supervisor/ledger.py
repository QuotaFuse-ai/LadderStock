"""supervisor 进程账本——最小承载版（QUOT-491 / 482-C）。

QUOT-482-A（账本结构 + 顺序启停引擎 + 归属判定）两次运行失败、未产出代码。
本模块只承载 482-C 自愈**必需**的字段，落盘路径沿用 482-A 的约定
（`data/supervisor/ledger.json`，原子写、进程重启后可恢复），482-A 落地后由其账本平替。

本模块**只做三件事**，其余一律不碰：

1. 记「主动停意图」（`stopping_by_user`）——自愈据此区分「自己崩了」与「你主动关的」；
2. 记自愈计数与滑动窗口（`restart_count` / `restart_window` / `restarts_5min`）；
3. 记健康态（`health`：ok / restarting / needs_human / stopped）。

`origin` / `cmdline_hash` 等归属字段**原样透传**——归属判定是 482-A 的职责，本模块
只存不判；启停顺序、模块启动命令同样不在本模块（分别属 482-A、482-B）。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HEALTH_OK = "ok"
HEALTH_RESTARTING = "restarting"
HEALTH_NEEDS_HUMAN = "needs_human"
HEALTH_STOPPED = "stopped"

# 对外（GET /api/supervisor/state）只认这三种健康态；stopped 是「主动关」的静息态，
# 对外折叠为 ok（无病态），原始生命周期另由 `state` 字段给出。
PUBLIC_HEALTH: tuple[str, ...] = (HEALTH_OK, HEALTH_RESTARTING, HEALTH_NEEDS_HUMAN)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class LedgerEntry:
    module: str
    ports: tuple[int, ...] = ()
    pid: int | None = None
    started_at: str | None = None
    # origin / cmdline_hash 由 482-A 填写；本模块只透传、不判定归属。
    origin: str | None = None
    cmdline_hash: str | None = None
    stopping_by_user: bool = False
    stop_reason: str | None = None
    restart_count: int = 0
    # 滑动窗口：窗口内每次重拉的时刻（epoch 秒）；`restarts_5min` = len(窗口内时刻)。
    restart_window: list[float] = field(default_factory=list)
    health: str = HEALTH_OK
    last_exit_reason: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "ports": list(self.ports),
            "pid": self.pid,
            "started_at": self.started_at,
            "origin": self.origin,
            "cmdline_hash": self.cmdline_hash,
            "stopping_by_user": self.stopping_by_user,
            "stop_reason": self.stop_reason,
            "restart_count": self.restart_count,
            "restart_window": list(self.restart_window),
            "health": self.health,
            "last_exit_reason": self.last_exit_reason,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LedgerEntry:
        ports = data.get("ports") or []
        window = data.get("restart_window") or []
        return cls(
            module=str(data.get("module") or ""),
            ports=tuple(int(port) for port in ports),
            pid=int(data["pid"]) if data.get("pid") is not None else None,
            started_at=data.get("started_at"),
            origin=data.get("origin"),
            cmdline_hash=data.get("cmdline_hash"),
            stopping_by_user=bool(data.get("stopping_by_user")),
            stop_reason=data.get("stop_reason"),
            restart_count=int(data.get("restart_count") or 0),
            restart_window=[float(tick) for tick in window],
            health=str(data.get("health") or HEALTH_OK),
            last_exit_reason=data.get("last_exit_reason"),
            updated_at=data.get("updated_at"),
        )


class Ledger:
    """线程安全的进程账本；落盘为原子写（临时文件 + os.replace）。"""

    def __init__(self, path: str | os.PathLike[str], *, window_seconds: float = 300.0) -> None:
        self.path = Path(path)
        self.window_seconds = window_seconds
        self._lock = threading.RLock()
        self._entries: dict[str, LedgerEntry] = {}
        self.load()

    # ------------------------------------------------------------------ 读写

    def load(self) -> None:
        with self._lock:
            self._entries = {}
            if not self.path.exists():
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            records = raw.get("modules") if isinstance(raw, dict) else None
            if not isinstance(records, list):
                return
            for record in records:
                if not isinstance(record, dict):
                    continue
                entry = LedgerEntry.from_dict(record)
                if entry.module:
                    self._entries[entry.module] = entry

    def save(self) -> None:
        with self._lock:
            payload = {
                "version": 1,
                "updated_at": now_iso(),
                "modules": [entry.to_dict() for entry in self._entries.values()],
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)

    # ------------------------------------------------------------------ 查询

    def entries(self) -> list[LedgerEntry]:
        with self._lock:
            return list(self._entries.values())

    def get(self, module: str) -> LedgerEntry | None:
        with self._lock:
            return self._entries.get(module)

    def ensure(self, module: str, ports: tuple[int, ...] = ()) -> LedgerEntry:
        with self._lock:
            entry = self._entries.get(module)
            if entry is None:
                entry = LedgerEntry(module=module, ports=tuple(ports))
                self._entries[module] = entry
            elif ports and not entry.ports:
                entry.ports = tuple(ports)
            return entry

    # ------------------------------------------------------------------ 变更

    def mark_stop_intent(self, module: str, reason: str | None = None) -> LedgerEntry:
        """写下「主动停」意图；自愈看到它一律不重拉（D4 的 intentional 路径）。"""
        with self._lock:
            entry = self.ensure(module)
            entry.stopping_by_user = True
            entry.stop_reason = reason
            entry.health = HEALTH_STOPPED
            entry.updated_at = now_iso()
            self.save()
            return entry

    def record_restart(
        self,
        module: str,
        *,
        ok: bool,
        pid: int | None,
        now: float,
        reason: str | None = None,
    ) -> LedgerEntry:
        """登记一次重拉：累计 +1，并压入滑动窗口；成功则换绑新 pid。"""
        with self._lock:
            entry = self.ensure(module)
            entry.restart_count += 1
            entry.restart_window.append(float(now))
            if ok:
                entry.pid = pid
                entry.started_at = now_iso()
                entry.stopping_by_user = False
                entry.stop_reason = None
                entry.last_exit_reason = None
            else:
                entry.last_exit_reason = reason
            entry.updated_at = now_iso()
            self.save()
            return entry

    def prune_window(self, entry: LedgerEntry, now: float) -> list[float]:
        """丢弃窗口外的重拉时刻，返回仍在窗口内的时刻列表。"""
        with self._lock:
            window = [tick for tick in entry.restart_window if now - tick < self.window_seconds]
            if window != entry.restart_window:
                entry.restart_window = window
            return list(window)

    def update(self, module: str, **fields: Any) -> LedgerEntry:
        with self._lock:
            entry = self.ensure(module)
            for key, value in fields.items():
                if hasattr(entry, key):
                    setattr(entry, key, value)
            entry.updated_at = now_iso()
            self.save()
            return entry

    def reset_heal(self, module: str) -> LedgerEntry:
        """人工解除 needs_human：清窗口与失败痕迹，健康回 ok（不动 pid / origin）。"""
        with self._lock:
            entry = self.ensure(module)
            entry.restart_window = []
            entry.health = HEALTH_OK
            entry.last_exit_reason = None
            entry.updated_at = now_iso()
            self.save()
            return entry


__all__ = [
    "HEALTH_NEEDS_HUMAN",
    "HEALTH_OK",
    "HEALTH_RESTARTING",
    "HEALTH_STOPPED",
    "PUBLIC_HEALTH",
    "Ledger",
    "LedgerEntry",
    "now_iso",
]
