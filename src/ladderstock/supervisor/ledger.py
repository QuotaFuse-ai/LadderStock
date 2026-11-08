"""进程账本（QUOT-482-A）：「我们启动过谁」的唯一真源。

归属判定的铁律（QUOT-482 派单正文）：**只认账本 + cmdline 匹配**，
不许扫端口猜谁起的。账本每模块一条记录：

    {module, ports[], pid, parent_pid, started_at, origin, cmdline_hash,
     state, forced_kill, stopped_at, stop_reason, health, restart_*}

落盘 `data/supervisor/ledger.json`，**原子写**（tmp + `os.replace`）：
主程序崩溃在写一半也不会留下半个 JSON；重启后 `load()` + `reconcile()`
把「已死进程」标回 stopped，绝不误判 running。

本文件**平替** QUOT-491（482-C）的最小承载账本（其文档字符串明说
「482-A 落地后由其账本平替」）：482-A 的状态机与对账之外，原样保留 482-C
自愈内核（`selfheal.SelfHealEngine`）依赖的全部字段与方法——`health` 三值、
`stopping_by_user` 停止意图、`restart_count` / `restart_window` 重拉窗口、
`record_restart` / `mark_stop_intent` / `reset_heal` 等。落盘格式沿用
482-C 的 `{"version": 1, "updated_at", "modules": [...]}`（运行中升级无缝），
并兼容读取 482-A 开发期的 dict 形态。

状态机（`ModuleState`）：starting → running → stopping → stopped，
任何一步失败进 failed；failed 不自动流转（自愈是 482-C 的事）。
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Collection, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

#: 账本里的模块状态（设计文档《ledger-and-lifecycle.md》§状态机）
ModuleState = Literal["starting", "running", "stopping", "stopped", "failed"]

MODULE_STATES: tuple[str, ...] = ("starting", "running", "stopping", "stopped", "failed")

#: origin：spawned = 本 supervisor 生命周期内自己拉起；
#: adopted = 崩溃重启后对账确认仍存活、继续托管的前任进程。
LedgerOrigin = Literal["spawned", "adopted"]

#: stop_reason 取值（写进账本给人看，不做 if 分支）
STOP_BY_REQUEST = "stopped_by_request"
STOP_PROCESS_DEAD = "process_dead_on_reconcile"
STOP_PID_REUSE = "suspected_pid_reuse_on_reconcile"

# ---- 482-C 健康态（自愈内核依赖，语义原样保留） ---------------------------- #

HEALTH_OK = "ok"
HEALTH_RESTARTING = "restarting"
HEALTH_NEEDS_HUMAN = "needs_human"
HEALTH_STOPPED = "stopped"

#: 对外（GET /api/supervisor/state）只认这三种健康态；stopped 是「主动关」的静息态，
#: 对外折叠为 ok（无病态），原始生命周期另由 `state` 字段给出。
PUBLIC_HEALTH: tuple[str, ...] = (HEALTH_OK, HEALTH_RESTARTING, HEALTH_NEEDS_HUMAN)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class ModuleRecord:
    """一个模块在账本里的全部状态（482-A 生命周期 + 482-C 自愈字段）。"""

    module: str
    ports: list[int] = field(default_factory=list)
    pid: int | None = None
    parent_pid: int | None = None
    started_at: str | None = None
    origin: LedgerOrigin = "spawned"
    #: 拉起时命令行的 sha256；对账与归属判定时与实时 cmdline 比对（堵 PID 复用）
    cmdline_hash: str = ""
    state: ModuleState = "stopped"
    #: 优雅停超时被强杀过（QUOT-482-A：升级强杀必须记账）
    forced_kill: bool = False
    stopped_at: str | None = None
    stop_reason: str | None = None
    # ---- 482-C 自愈字段（平替承载，语义与 QUOT-491 版一致） ----
    stopping_by_user: bool = False
    restart_count: int = 0
    #: 滑动窗口：窗口内每次重拉的时刻（epoch 秒）；`restarts_5min` = len(窗口内时刻)
    restart_window: list[float] = field(default_factory=list)
    health: str = HEALTH_OK
    last_exit_reason: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ModuleRecord:
        state = str(raw.get("state") or "stopped")
        if state not in MODULE_STATES:
            state = "failed"
        origin = str(raw.get("origin") or "spawned")
        if origin not in ("spawned", "adopted"):
            origin = "spawned"
        ports_raw = raw.get("ports")
        ports = [int(p) for p in ports_raw] if isinstance(ports_raw, (list, tuple)) else []
        pid_raw = raw.get("pid")
        parent_raw = raw.get("parent_pid")
        window = raw.get("restart_window") or []
        return cls(
            module=str(raw.get("module") or ""),
            ports=ports,
            pid=int(pid_raw) if pid_raw is not None else None,
            parent_pid=int(parent_raw) if parent_raw is not None else None,
            started_at=raw.get("started_at"),
            origin=origin,  # type: ignore[arg-type]
            cmdline_hash=str(raw.get("cmdline_hash") or ""),
            state=state,  # type: ignore[arg-type]
            forced_kill=bool(raw.get("forced_kill")),
            stopped_at=raw.get("stopped_at"),
            stop_reason=raw.get("stop_reason"),
            stopping_by_user=bool(raw.get("stopping_by_user")),
            restart_count=int(raw.get("restart_count") or 0),
            restart_window=[float(tick) for tick in window],
            health=str(raw.get("health") or HEALTH_OK),
            last_exit_reason=raw.get("last_exit_reason"),
            updated_at=raw.get("updated_at"),
        )


#: 482-C（QUOT-491）侧的类型名；同一实现，账本平替后两边代码零改动。
LedgerEntry = ModuleRecord


class Ledger:
    """账本：内存 dict + 原子落盘。全部方法线程安全（引擎的启停互斥在外层）。"""

    def __init__(self, path: str | os.PathLike[str], *, window_seconds: float = 300.0) -> None:
        self.path = Path(path)
        #: 重拉滑动窗口宽度（秒）；自愈内核按它判定 `restarts_5min`
        self.window_seconds = float(window_seconds)
        self._records: dict[str, ModuleRecord] = {}
        self._lock = threading.RLock()
        self.load()

    # ------------------------------------------------------------------ 读

    def record(self, module: str) -> ModuleRecord | None:
        with self._lock:
            return self._records.get(module)

    #: 482-C 侧的读取名（`SelfHealEngine` 用）；与 `record()` 同一实现。
    get = record

    def records(self) -> dict[str, ModuleRecord]:
        with self._lock:
            return dict(self._records)

    def entries(self) -> list[ModuleRecord]:
        with self._lock:
            return list(self._records.values())

    # ------------------------------------------------------------------ 写

    def update(self, target: ModuleRecord | str, **fields: Any) -> ModuleRecord | None:
        """双形态写入（482-A × 482-C 平替）：

        - `update(record)` —— 整条替换（482-A 引擎路径）；
        - `update(module, health=..., ...)` —— 按 name 补丁式改字段
          （482-C `SelfHealEngine` 路径），未知名静默忽略。
        """
        with self._lock:
            if isinstance(target, ModuleRecord):
                self._records[target.module] = target
                self.save()
                return None
            entry = self.ensure(target)
            for key, value in fields.items():
                if hasattr(entry, key):
                    setattr(entry, key, value)
            entry.updated_at = now_iso()
            self.save()
            return entry

    def remove(self, module: str) -> None:
        with self._lock:
            self._records.pop(module, None)
            self.save()

    def ensure(self, module: str, ports: Collection[int] = ()) -> ModuleRecord:
        with self._lock:
            entry = self._records.get(module)
            if entry is None:
                entry = ModuleRecord(module=module, ports=list(ports))
                self._records[module] = entry
            elif ports and not entry.ports:
                entry.ports = list(ports)
            return entry

    def mark_stop_intent(self, module: str, reason: str | None = None) -> ModuleRecord:
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
    ) -> ModuleRecord:
        """登记一次重拉：累计 +1，并压入滑动窗口；成功则换绑新 pid。

        平替融合点：482-C 只换绑 pid，这里同步维护 482-A 的生命周期状态机
        （成功 → `running`，清停止痕迹）。
        """
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
                entry.stopped_at = None
                entry.state = "running"
            else:
                entry.last_exit_reason = reason
            entry.updated_at = now_iso()
            self.save()
            return entry

    def prune_window(self, entry: ModuleRecord, now: float) -> list[float]:
        """丢弃窗口外的重拉时刻，返回仍在窗口内的时刻列表。"""
        with self._lock:
            window = [tick for tick in entry.restart_window if now - tick < self.window_seconds]
            if window != entry.restart_window:
                entry.restart_window = window
            return list(window)

    def reset_heal(self, module: str) -> ModuleRecord:
        """人工解除 needs_human：清窗口与失败痕迹，健康回 ok（不动 pid / origin）。"""
        with self._lock:
            entry = self.ensure(module)
            entry.restart_window = []
            entry.health = HEALTH_OK
            entry.last_exit_reason = None
            entry.updated_at = now_iso()
            self.save()
            return entry

    # ------------------------------------------------------------------ 落盘

    def save(self) -> None:
        """原子写：先写同目录 tmp，再 `os.replace` 原子换名（Windows 同卷可用）。

        格式沿用 482-C（QUOT-491）的 `{"version": 1, "updated_at", "modules": [...]}`
        ——两代账本读写同一份 `ledger.json`，运行中升级无缝。
        """
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "updated_at": now_iso(),
                "modules": [
                    rec.to_dict()
                    for rec in sorted(self._records.values(), key=lambda item: item.module)
                ],
            }
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)

    def load(self) -> None:
        """从盘上恢复；文件缺失 / 损坏一律按空账本起（损坏文件改名留证）。

        兼容两代格式：482-C 的 `{"modules": [...]}`、482-A 开发期的 dict 形态。
        """
        with self._lock:
            self._records = {}
            if not self.path.is_file():
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.path.replace(self.path.with_suffix(".json.corrupt"))
                return
            entries: Any
            if isinstance(raw, dict):
                modules = raw.get("modules")
                if isinstance(modules, list):
                    entries = modules
                else:
                    entries = [value for value in raw.values() if isinstance(value, dict)]
            else:
                self.path.replace(self.path.with_suffix(".json.corrupt"))
                return
            for entry in entries:
                if isinstance(entry, dict):
                    record = ModuleRecord.from_dict(entry)
                    if record.module:
                        self._records[record.module] = record

    # ------------------------------------------------------------------ 对账

    def reconcile(
        self,
        probe: Callable[[ModuleRecord], str],
        *,
        at: str | None = None,
        spawned_this_session: Collection[str] = (),
    ) -> dict[str, str]:
        """崩溃恢复对账：只信活进程，不信账本上的旧状态。

        `probe(record)` 返回三态：
        - `"alive"` —— PID 存活且 cmdline_hash 匹配 → 继续托管；
          `origin` 按调用方给的会话身份定：本次会话自己拉起的保持 `spawned`，
          只有上一任 supervisor 留下的活进程才标 `adopted`；
        - `"reuse"` —— PID 存活但 cmdline 对不上（疑似 PID 复用）→
          标 stopped，原因写明疑似复用；
        - `"dead"` —— PID 已不存在 → 标 stopped（**绝不误判 running**）。

        stopping/stopped/failed 一律不动。返回每模块动作摘要（日志与测试断言用）。
        """
        summary: dict[str, str] = {}
        stamp = at or now_iso()
        with self._lock:
            dirty = False
            for record in self._records.values():
                if record.state not in ("starting", "running"):
                    summary[record.module] = f"kept:{record.state}"
                    continue
                verdict = probe(record)
                if verdict == "alive":
                    if record.module in spawned_this_session:
                        record.origin = "spawned"
                        summary[record.module] = "alive:spawned_this_session"
                    else:
                        record.origin = "adopted"
                        summary[record.module] = "adopted:still_running"
                elif verdict == "reuse":
                    record.state = "stopped"
                    record.stopped_at = stamp
                    record.stop_reason = STOP_PID_REUSE
                    summary[record.module] = "stopped:suspected_pid_reuse"
                else:
                    record.state = "stopped"
                    record.stopped_at = stamp
                    record.stop_reason = STOP_PROCESS_DEAD
                    summary[record.module] = "stopped:process_dead"
                dirty = True
            if dirty:
                self.save()
        return summary


__all__ = [
    "HEALTH_NEEDS_HUMAN",
    "HEALTH_OK",
    "HEALTH_RESTARTING",
    "HEALTH_STOPPED",
    "MODULE_STATES",
    "PUBLIC_HEALTH",
    "STOP_BY_REQUEST",
    "STOP_PID_REUSE",
    "STOP_PROCESS_DEAD",
    "Ledger",
    "LedgerEntry",
    "ModuleRecord",
    "now_iso",
]
