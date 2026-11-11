"""peer 级健康事件通道（QUOT-556）：crash / offline / port_conflict / needs_human / recovered。

前端 S5 弹窗的订阅源。两条约定：

- **只发跃迁，不刷屏**：`HealthEventMonitor` 把每轮巡检的账本状态翻译成「条件」，
  只在条件变化时发事件；恢复（recovered）只在「先坏过」之后发一次，主动停
  （stopping_by_user）与从未拉起（unknown）全程静默。
- **观测不动作**：本模块只读账本 + 注入的存活/端口探针，绝不终止、绝不拉起、
  绝不改账本——处置走引擎的既有闸门（DEC-078 / DEC-079 原样保留）。

事件经 `HealthEventBus`（线程安全环形缓冲 + 单调 seq）供
`GET /api/supervisor/events?since=<seq>` 定时轮询；端口归属分类口径与
QUOT-547 一致（own_child / adopted_peer / foreign / free），由装配侧注入
（本层不依赖 web 层，避免反向导入）。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from ladderstock.supervisor.ledger import (
    HEALTH_NEEDS_HUMAN,
    HEALTH_OK,
    HEALTH_RESTARTING,
    PUBLIC_HEALTH,
    STOP_BY_REQUEST,
    LedgerEntry,
    now_iso,
)

_log = logging.getLogger(__name__)

#: 事件类型（前端弹窗契约，QUOT-556 派单正文）
EVENT_CRASH = "crash"
EVENT_OFFLINE = "offline"
EVENT_PORT_CONFLICT = "port_conflict"
EVENT_NEEDS_HUMAN = "needs_human"
EVENT_RECOVERED = "recovered"

EVENT_TYPES: tuple[str, ...] = (
    EVENT_CRASH,
    EVENT_OFFLINE,
    EVENT_PORT_CONFLICT,
    EVENT_NEEDS_HUMAN,
    EVENT_RECOVERED,
)

#: 端口占用分类（与 web/port_diagnostics.py 同一套取值；本层只消费字符串）
OWNER_FOREIGN = "foreign"
OWNER_FREE = "free"

#: 会触发弹窗的「坏条件」；recovered 只在从其中之一恢复后发
_BAD_CONDITIONS = frozenset({EVENT_CRASH, EVENT_OFFLINE, EVENT_PORT_CONFLICT, EVENT_NEEDS_HUMAN})

DEFAULT_BUS_CAPACITY = 500
DEFAULT_EVENTS_PAGE_LIMIT = 200
#: 端口归属扫描的节流间隔：psutil 连接表扫描有成本，存活巡检（每 tick）不受影响
DEFAULT_PORT_PROBE_INTERVAL = 5.0

#: 白话原因（不打印 argv / 环境变量 / 技术栈，前端可直接展示）
_REASONS: dict[str, str] = {
    EVENT_CRASH: "子模块进程已退出（崩溃或被外部停止），当前没有在运行",
    EVENT_OFFLINE: "子模块进程还在，但服务端口没有监听（掉线、无响应）",
    EVENT_PORT_CONFLICT: "子模块的服务端口被其它进程占用（不会自动换端口，也不会终止对方）",
    EVENT_NEEDS_HUMAN: "自动恢复多次失败，已停止重试，需要确认后一键重启",
    EVENT_RECOVERED: "子模块已恢复运行",
}


@dataclass(frozen=True)
class HealthEvent:
    """一条 peer 级健康事件：机器键（module/event/health/state）+ 白话原因。"""

    seq: int
    ts: str
    module: str
    event: str
    health: str
    state: str
    reason: str
    pid: int | None
    ports: tuple[int, ...]
    origin: str | None
    adopted_peer: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ports"] = list(self.ports)
        return payload


class HealthEventBus:
    """线程安全事件总线：单调 seq + 容量上限（环形缓冲，绝不无限膨胀）。

    发布方是自愈巡检线程，消费方是 HTTP 轮询——全部操作持同一把锁。
    客户端落后超过容量时旧事件被挤掉：seq 出现空洞即提示客户端重新全量对齐。
    """

    def __init__(self, capacity: int = DEFAULT_BUS_CAPACITY) -> None:
        self._events: deque[HealthEvent] = deque(maxlen=max(1, int(capacity)))
        self._lock = threading.Lock()
        self._seq = 0

    @property
    def latest_seq(self) -> int:
        with self._lock:
            return self._seq

    def publish(
        self,
        *,
        module: str,
        event: str,
        health: str,
        state: str,
        reason: str,
        pid: int | None = None,
        ports: Sequence[int] = (),
        origin: str | None = None,
        adopted_peer: bool = False,
    ) -> HealthEvent:
        with self._lock:
            self._seq += 1
            item = HealthEvent(
                seq=self._seq,
                ts=now_iso(),
                module=module,
                event=event,
                health=health,
                state=state,
                reason=reason,
                pid=pid,
                ports=tuple(int(port) for port in ports),
                origin=origin,
                adopted_peer=bool(adopted_peer),
            )
            self._events.append(item)
            return item

    def since(
        self,
        seq: int = 0,
        *,
        module: str | None = None,
        limit: int = DEFAULT_EVENTS_PAGE_LIMIT,
    ) -> list[HealthEvent]:
        """seq 之后的事件（按 seq 升序，最多 limit 条）；module 给出时只取该模块。"""
        with self._lock:
            items = [item for item in self._events if item.seq > seq]
        if module is not None:
            items = [item for item in items if item.module == module]
        return items[: max(1, int(limit))]


@dataclass(frozen=True)
class PortObservation:
    """一个端口的观测结果：在听与否 + 占用者归属（QUOT-547 四分类字符串）。"""

    port: int
    listening: bool
    owner: str


#: 端口观测器：一次调用看全部托管模块（装配侧实现可做一次连接表扫描摊薄成本）。
PortInspector = Callable[[Sequence[LedgerEntry]], Mapping[str, Sequence[PortObservation]]]


class HealthEventMonitor:
    """把账本巡检翻译成健康事件（只发跃迁）。全部依赖注入，测试不起真进程。

    - `is_alive`：账本条目的存活探针（生产 = 自愈的 pid 判活），每轮都查；
    - `port_inspector`：可选。给了才检测 offline / port_conflict；观测结果按
      `port_probe_interval` 节流复用，存活类事件（crash/recovered）不受节流影响。
    """

    def __init__(
        self,
        bus: HealthEventBus,
        *,
        port_inspector: PortInspector | None = None,
        clock: Callable[[], float] = time.time,
        port_probe_interval: float = DEFAULT_PORT_PROBE_INTERVAL,
    ) -> None:
        self._bus = bus
        self._inspector = port_inspector
        self._clock = clock
        self._port_probe_interval = float(port_probe_interval)
        self._prev: dict[str, str] = {}
        self._pending_recovery: set[str] = set()
        self._port_obs: Mapping[str, Sequence[PortObservation]] = {}
        self._port_checked_at: float | None = None

    def observe(
        self,
        entries: Sequence[LedgerEntry],
        *,
        is_alive: Callable[[LedgerEntry], bool],
    ) -> None:
        """一轮观测：逐模块定条件、发跃迁事件。单模块异常吞掉，不拖垮整轮。"""
        self._refresh_ports(entries)
        for entry in entries:
            try:
                self._observe_one(entry, is_alive)
            except Exception:
                _log.exception("健康事件观测模块 %s 异常（已吞，继续下一模块）", entry.module)

    def _refresh_ports(self, entries: Sequence[LedgerEntry]) -> None:
        if self._inspector is None:
            return
        now = self._clock()
        if (
            self._port_checked_at is not None
            and now - self._port_checked_at < self._port_probe_interval
        ):
            return
        self._port_checked_at = now
        try:
            self._port_obs = self._inspector(entries)
        except Exception:
            # 观测失败按「本轮无端口信息」处理：只检测存活类事件，不误报端口类
            _log.warning("端口健康观测失败（已吞，本轮只看存活）", exc_info=True)
            self._port_obs = {}

    def _observe_one(
        self, entry: LedgerEntry, is_alive: Callable[[LedgerEntry], bool]
    ) -> None:
        alive = bool(is_alive(entry))
        obs = self._port_obs.get(entry.module) if self._inspector is not None else None
        condition = self._condition(entry, alive, obs)
        state = self._state(entry, alive)
        health = entry.health if entry.health in PUBLIC_HEALTH else HEALTH_OK
        prev = self._prev.get(entry.module, "ok")
        if condition == prev:
            return
        self._prev[entry.module] = condition
        if condition in _BAD_CONDITIONS:
            self._pending_recovery.add(entry.module)
            self._publish(entry, condition, health=health, state=state)
        elif condition == "ok" and entry.module in self._pending_recovery:
            self._pending_recovery.discard(entry.module)
            self._publish(entry, EVENT_RECOVERED, health=health, state=state)

    def _condition(
        self,
        entry: LedgerEntry,
        alive: bool,
        obs: Sequence[PortObservation] | None,
    ) -> str:
        if entry.stopping_by_user:
            return "stopped"
        if entry.state == "starting":
            return "restarting"  # 引擎拉起中（pid 未换绑）：静默过渡态
        foreign_listening = obs is not None and any(
            item.listening and item.owner == OWNER_FOREIGN for item in obs
        )
        if not alive:
            if entry.pid is None and entry.started_at is None and entry.restart_count == 0:
                return "unknown"  # 从没拉起过，不是崩溃
            if entry.stop_reason == STOP_BY_REQUEST:
                # 主动停的静息态；也覆盖 restart 的 stop→start 间隙（stop_reason
                # 与 state=starting 同笔落账，之前仍是 stopped_by_request）。
                return "stopped"
            if entry.health == HEALTH_NEEDS_HUMAN:
                return "needs_human"
            if foreign_listening:
                return "port_conflict"
            # health=restarting 不掩盖 crash：正在自愈的崩溃也是崩溃，弹窗要弹。
            return EVENT_CRASH
        if entry.health == HEALTH_NEEDS_HUMAN:
            return "needs_human"
        if foreign_listening:
            return EVENT_PORT_CONFLICT
        if obs is not None and entry.ports and not any(item.listening for item in obs):
            return EVENT_OFFLINE
        return "ok"

    @staticmethod
    def _state(entry: LedgerEntry, alive: bool) -> str:
        """与自愈快照同一套对外 state 口径（484 前端契约）。"""
        if alive:
            return "running"
        if entry.stopping_by_user:
            return "stopped"
        if entry.health == HEALTH_NEEDS_HUMAN:
            return "needs_human"
        if entry.health == HEALTH_RESTARTING:
            return "restarting"
        return "down"

    def _publish(self, entry: LedgerEntry, event: str, *, health: str, state: str) -> None:
        self._bus.publish(
            module=entry.module,
            event=event,
            health=health,
            state=state,
            reason=_REASONS[event],
            pid=entry.pid,
            ports=tuple(entry.ports),
            origin=entry.origin,
            adopted_peer=entry.adopted_peer,
        )


__all__ = [
    "DEFAULT_BUS_CAPACITY",
    "DEFAULT_EVENTS_PAGE_LIMIT",
    "DEFAULT_PORT_PROBE_INTERVAL",
    "EVENT_CRASH",
    "EVENT_NEEDS_HUMAN",
    "EVENT_OFFLINE",
    "EVENT_PORT_CONFLICT",
    "EVENT_RECOVERED",
    "EVENT_TYPES",
    "OWNER_FOREIGN",
    "OWNER_FREE",
    "HealthEvent",
    "HealthEventBus",
    "HealthEventMonitor",
    "PortInspector",
    "PortObservation",
]