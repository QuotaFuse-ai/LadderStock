"""自愈引擎——崩溃识别 + 退避重拉 + needs_human（QUOT-491 / 482-C）。

D4/D5 定稿的执行口径：

- **崩溃 vs 主动关**：只有「非主动退出」才触发自愈。账本 `stopping_by_user=True`
  的退出是主动关（D4 intentional），落 `stopped`，绝不重拉；
- **退避重拉**：5 分钟滑动窗口内最多 `len(backoff)` 次（默认 3），等待
  5s → 15s → 45s；每次重拉发起前再次确认进程仍死着，防止双实例；
- **超限即停**：窗口内重拉次数达上限后不再重拉，health 置 `needs_human`、
  发「已停止重拉，需人工处理」通知，只有人工 `reset` 能解除；
- **发起 ≠ 恢复**：重拉发起后置 `restarting`，下一轮巡检确认新进程活着才回 `ok`；
- **日志有上限**：每模块滚动保留最近 `log_max_lines` 条（append_capped），绝不
  无限膨胀（C 盘只剩十几个 GB，D5 明令）。

冷启动（QUOT-526）：给了 `modules=` 且 `cold_start=True` 时，巡检对象是这份
bundle 模块表而不是账本里已有的条目；某模块账本无 pid（从没拉起过 / 账本丢了）
就按命令表立刻拉起一次（delay 0），之后失败才走 5s→15s→45s 退避。重拉通道
换成 `restart.AdapterRestarter`（适配器 = bundle 自带命令表），不再依赖空
`restart-commands.json`。认领既有实例（`RestartOutcome.adopted`）不记重拉次数。

clock / sleeper / restarter / notifier 全部可注入，引擎本身零 I/O 依赖注入点，
pytest 用假件即可覆盖全部路径。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from ladderstock.supervisor.health_events import (
    DEFAULT_PORT_PROBE_INTERVAL,
    HealthEventBus,
    HealthEventMonitor,
    PortInspector,
)
from ladderstock.supervisor.ledger import (
    HEALTH_NEEDS_HUMAN,
    HEALTH_OK,
    HEALTH_RESTARTING,
    HEALTH_STOPPED,
    PUBLIC_HEALTH,
    Ledger,
    LedgerEntry,
)
from ladderstock.supervisor.logroll import append_capped, utc_line
from ladderstock.supervisor.modules import MODULE_PORTS

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

_log = logging.getLogger(__name__)

BACKOFF_SECONDS: tuple[float, ...] = (5.0, 15.0, 45.0)
WINDOW_SECONDS = 300.0
DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_LOG_MAX_LINES = 200

# 484 前端契约：self_heal.status ∈ ok | healing | gave_up（与 health 三值一一对应）。
SELF_HEAL_STATUS: dict[str, str] = {
    HEALTH_OK: "ok",
    HEALTH_RESTARTING: "healing",
    HEALTH_NEEDS_HUMAN: "gave_up",
}


@dataclass(frozen=True)
class RestartOutcome:
    """一次重拉的结论；`detail` 是失败原因摘要（进日志与通知）。

    `adopted=True` = 没有真正拉起，而是认领了一个已在运行的匹配实例（pid 不变），
    自愈据此不累计重拉次数（它不是一次崩溃恢复）。
    """

    ok: bool
    pid: int | None = None
    detail: str = ""
    adopted: bool = False


class Restarter(Protocol):
    """存活探测 + 重拉通道（生产实现见 restart.AdapterRestarter）。"""

    def is_alive(self, entry: LedgerEntry) -> bool: ...

    def restart(self, module: str) -> RestartOutcome: ...


class Notifier(Protocol):
    """失败/放弃/恢复通知（生产实现见 notify.MultiNotifier）。"""

    def failure(
        self, *, module: str, ports: tuple[int, ...], attempt: int, max_attempts: int, reason: str
    ) -> None: ...

    def gave_up(
        self, *, module: str, ports: tuple[int, ...], attempts: int, reason: str
    ) -> None: ...

    def recovered(self, *, module: str, pid: int | None) -> None: ...


class SelfHealEngine:
    """单进程内巡检全部账本模块；后台线程周期 tick，tick 亦可手动驱动（测试）。"""

    def __init__(
        self,
        ledger: Ledger,
        restarter: Restarter,
        notifier: Notifier | None = None,
        *,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        backoff: tuple[float, ...] = BACKOFF_SECONDS,
        log_dir: str | Path | None = None,
        log_max_lines: int = DEFAULT_LOG_MAX_LINES,
        modules: Sequence[str] | None = None,
        cold_start: bool = False,
        module_ports: Mapping[str, Sequence[int]] | None = None,
        event_bus: HealthEventBus | None = None,
        port_inspector: PortInspector | None = None,
        port_probe_interval: float = DEFAULT_PORT_PROBE_INTERVAL,
    ) -> None:
        self.ledger = ledger
        self.restarter = restarter
        self.notifier = notifier
        self.clock = clock
        self.poll_interval = poll_interval
        self.backoff = tuple(backoff)
        self.max_restarts = len(self.backoff)
        self.log_dir = Path(log_dir) if log_dir is not None else None
        self.log_max_lines = log_max_lines
        #: 给了 modules = 托管这份 bundle 模块表（冷启动的前提）；None = 只看账本旧口径。
        self.modules = tuple(modules) if modules is not None else None
        #: 没有模块表就没有「该拉起谁」的依据，冷启动一律不生效。
        self.cold_start = bool(cold_start) and self.modules is not None
        self._module_ports: dict[str, tuple[int, ...]] = {
            name: tuple(ports) for name, ports in (module_ports or MODULE_PORTS).items()
        }
        self._sleeper: Callable[[float], None] = (
            sleeper if sleeper is not None else self._wait
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick_lock = threading.RLock()
        #: peer 级健康事件通道（QUOT-556）：给了 bus 才启用观测，旧装配零影响。
        self.event_bus = event_bus
        self._monitor = (
            HealthEventMonitor(
                event_bus,
                port_inspector=port_inspector,
                clock=clock,
                port_probe_interval=port_probe_interval,
            )
            if event_bus is not None
            else None
        )

    # ---------------------------------------------------------------- 生命周期

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="supervisor-selfheal", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def _wait(self, seconds: float) -> None:
        self._stop_event.wait(seconds)

    def _loop(self) -> None:
        while not self._stop_event.wait(self.poll_interval):
            self.tick()

    # ------------------------------------------------------------------- 巡检

    def tick(self) -> None:
        """单轮巡检：逐模块看账本、判死活、按需重拉。异常逐模块吞掉，不拖垮整轮。"""
        with self._tick_lock:
            modules = self._managed_modules()
            if self._monitor is not None:
                # 先观测再处置：crash 事件不被本轮重拉的退避等待延迟；
                # 处置结果（恢复/放弃）下一轮观测如实上报。
                entries = [
                    entry
                    for module in modules
                    if (entry := self.ledger.get(module)) is not None
                ]
                try:
                    self._monitor.observe(entries, is_alive=self._alive)
                except Exception:
                    _log.exception("健康事件观测异常（已吞，不影响自愈主流程）")
            for module in modules:
                entry = self.ledger.get(module)
                if entry is None:
                    continue
                try:
                    self._inspect(entry)
                except Exception:
                    _log.exception("自愈巡检模块 %s 异常（已吞，继续下一模块）", module)

    def _managed_modules(self) -> list[str]:
        """本轮巡检对象：给了 bundle 模块表就按表（缺的账本条目就地补齐）。"""
        if self.modules is None:
            return [entry.module for entry in self.ledger.entries()]
        for module in self.modules:
            self.ledger.ensure(module, self._module_ports.get(module, ()))
        return list(self.modules)

    def _inspect(self, entry: LedgerEntry) -> None:
        now = self.clock()
        window = self.ledger.prune_window(entry, now)
        alive = self._alive(entry)
        if alive and entry.health == HEALTH_RESTARTING:
            # 重拉发起 ≠ 恢复：下一轮确认新进程活着，才落回 ok。
            self.ledger.update(entry.module, health=HEALTH_OK)
            self._log(entry, f"重拉成功，健康回到 ok（pid={entry.pid}）")
            self._safe_notify_recovered(entry)
            return
        if alive:
            return
        if entry.stopping_by_user:
            # 主动关（D4 intentional）：落 stopped，绝不自愈。
            if entry.health != HEALTH_STOPPED:
                self.ledger.update(entry.module, health=HEALTH_STOPPED)
                self._log(entry, f"进程退出（主动停：{entry.stop_reason or '用户停止'}），不自愈")
            return
        if entry.health == HEALTH_NEEDS_HUMAN:
            return  # 超限待人工；只有 reset 能解除，不再自动动作。
        if entry.pid is None and not window:
            if self.cold_start:
                # 从没记账 / 账本丢了：按 bundle 命令表立刻拉一次（失败才进退避）。
                self._pull(entry, window, cold_start=True)
            return
        if len(window) >= self.max_restarts:
            self._gave_up(entry, len(window))
            return
        self._pull(entry, window)

    def _pull(self, entry: LedgerEntry, window: list[float], *, cold_start: bool = False) -> None:
        attempt = len(window) + 1
        label = "冷启动" if cold_start else "重拉"
        delay = (
            0.0
            if cold_start and not window
            else self.backoff[min(len(window), len(self.backoff) - 1)]
        )
        if entry.health != HEALTH_RESTARTING:
            self.ledger.update(entry.module, health=HEALTH_RESTARTING)
        self._log(entry, f"第 {attempt}/{self.max_restarts} 次{label}：{delay:g}s 后发起")
        self._sleeper(delay)
        if self._stop_event.is_set():
            return  # 引擎关停中被打断；不登记半截重拉。
        if self._alive(entry):
            # 等待期间进程已被拉活——按恢复处理，不再拉起（防双实例）。
            self.ledger.update(entry.module, health=HEALTH_OK)
            self._log(entry, f"{label}等待期间进程已恢复（pid={entry.pid}），不重复拉起")
            self._safe_notify_recovered(entry)
            return
        outcome = self._restart(entry)
        if outcome.adopted:
            # 认领既有实例不是一次崩溃恢复：pid 不变，不占重拉窗口。
            # 账本必须换绑到认领到的 pid，否则下一轮会把它当「从没拉起」再冷启动。
            fields: dict[str, Any] = {"health": HEALTH_OK}
            if outcome.pid is not None:
                fields.update(pid=int(outcome.pid), state="running", stopping_by_user=False)
            self.ledger.update(entry.module, **fields)
            self._log(entry, f"{label}认领已在运行的实例（pid={outcome.pid}），不重复拉起")
            return
        self.ledger.record_restart(
            entry.module,
            ok=outcome.ok,
            pid=outcome.pid,
            now=self.clock(),
            reason=outcome.detail,
        )
        if outcome.ok:
            self._log(
                entry, f"第 {attempt}/{self.max_restarts} 次{label}已发起（pid={outcome.pid}）"
            )
        else:
            reason = outcome.detail or f"{label}失败"
            self._log(entry, f"第 {attempt}/{self.max_restarts} 次{label}失败：{reason}")
            self._safe_notify_failure(entry, attempt, reason)

    def _gave_up(self, entry: LedgerEntry, attempts: int) -> None:
        reason = entry.last_exit_reason or f"5 分钟内已重拉 {attempts} 次，进程仍退出"
        self.ledger.update(entry.module, health=HEALTH_NEEDS_HUMAN)
        self._log(entry, f"重拉达上限 {self.max_restarts} 次（5 分钟内），停止自愈，需人工处理")
        if self.notifier is None:
            return
        try:
            self.notifier.gave_up(
                module=entry.module, ports=tuple(entry.ports), attempts=attempts, reason=reason
            )
        except Exception:
            _log.warning("自愈通知失败（已吞，不影响自愈主流程）", exc_info=True)

    # ------------------------------------------------------- 探测 / 重拉 / 通知

    def _alive(self, entry: LedgerEntry) -> bool:
        if entry.pid is None:
            return False
        try:
            return bool(self.restarter.is_alive(entry))
        except Exception:
            _log.exception("存活探测异常（按不存活处理）：%s", entry.module)
            return False

    def _restart(self, entry: LedgerEntry) -> RestartOutcome:
        try:
            return self.restarter.restart(entry.module)
        except Exception as exc:
            _log.exception("重拉调用异常：%s", entry.module)
            return RestartOutcome(ok=False, pid=None, detail=f"重拉调用异常：{exc}")

    def _safe_notify_failure(self, entry: LedgerEntry, attempt: int, reason: str) -> None:
        if self.notifier is None:
            return
        try:
            self.notifier.failure(
                module=entry.module,
                ports=tuple(entry.ports),
                attempt=attempt,
                max_attempts=self.max_restarts,
                reason=reason,
            )
        except Exception:
            _log.warning("自愈通知失败（已吞，不影响自愈主流程）", exc_info=True)

    def _safe_notify_recovered(self, entry: LedgerEntry) -> None:
        if self.notifier is None:
            return
        try:
            self.notifier.recovered(module=entry.module, pid=entry.pid)
        except Exception:
            _log.warning("自愈通知失败（已吞，不影响自愈主流程）", exc_info=True)

    # ------------------------------------------------------------------- 日志

    def _log(self, entry: LedgerEntry, message: str) -> None:
        logging.getLogger(f"{__name__}.{entry.module}").info(message)
        if self.log_dir is None:
            return
        try:
            path = self.log_dir / f"{entry.module}.log"
            append_capped(path, utc_line(message), max_lines=self.log_max_lines)
        except OSError:
            _log.warning("自愈日志写盘失败（已吞）：%s", entry.module)

    # ------------------------------------------------------------------ 快照

    def snapshot(self) -> dict[str, Any]:
        """GET /api/supervisor/state 的载荷：每模块 health 三值 + restarts_5min。"""
        with self._tick_lock:
            now = self.clock()
            known: dict[str, tuple[int, ...]] = {
                module: tuple(ports) for module, ports in MODULE_PORTS.items()
            }
            for module in self.modules or ():
                known.setdefault(module, self._module_ports.get(module, ()))
            for entry in self.ledger.entries():
                known.setdefault(entry.module, tuple(entry.ports))
            modules = [
                self._module_snapshot(module, ports, self.ledger.get(module), now)
                for module, ports in known.items()
            ]
            modules.sort(key=lambda item: str(item["module"]))
            payload: dict[str, Any] = {
                "ok": True,
                "engine": {
                    "running": self.running,
                    "window_seconds": self.ledger.window_seconds,
                    "max_restarts": self.max_restarts,
                    "backoff": list(self.backoff),
                },
                "modules": modules,
            }
            if self.event_bus is not None:
                # 前端拿既有 /state 轮询即可发现「有没有新事件」，再按需拉 /events。
                payload["events_latest"] = self.event_bus.latest_seq
            return payload

    def _module_snapshot(
        self, module: str, ports: tuple[int, ...], entry: LedgerEntry | None, now: float
    ) -> dict[str, Any]:
        if entry is None:
            return {
                "module": module,
                "ports": list(ports),
                "pid": None,
                "health": HEALTH_OK,
                "state": "unknown",
                "restarts_5min": 0,
                "restart_count": 0,
                "stopping_by_user": False,
                "origin": None,
                "adopted_peer": False,
                "cmdline_hash": None,
                "last_exit_reason": None,
                "self_heal": {"attempts": 0, "max": self.max_restarts, "status": "ok"},
            }
        window = self.ledger.prune_window(entry, now)
        alive = self._alive(entry)
        if alive:
            state = "running"
        elif entry.stopping_by_user:
            state = "stopped"
        elif entry.health == HEALTH_NEEDS_HUMAN:
            state = "needs_human"
        elif entry.health == HEALTH_RESTARTING:
            state = "restarting"
        else:
            state = "down"
        # stopped 是「主动关」的静息态，对外折叠为 ok；原始生命周期看 state。
        public_health = entry.health if entry.health in PUBLIC_HEALTH else HEALTH_OK
        return {
            "module": entry.module,
            "ports": list(entry.ports or ports),
            "pid": entry.pid,
            "health": public_health,
            "state": state,
            "restarts_5min": len(window),
            "restart_count": entry.restart_count,
            "stopping_by_user": entry.stopping_by_user,
            "origin": entry.origin,
            "adopted_peer": entry.adopted_peer,
            "cmdline_hash": entry.cmdline_hash,
            "last_exit_reason": entry.last_exit_reason,
            "self_heal": {
                "attempts": len(window),
                "max": self.max_restarts,
                "status": SELF_HEAL_STATUS.get(public_health, "ok"),
            },
        }

    # ------------------------------------------------------------------- 人工

    def reset(self, module: str) -> dict[str, Any] | None:
        """人工解除 needs_human：清重拉窗口、健康回 ok。账本无此模块返回 None。"""
        entry = self.ledger.get(module)
        if entry is None:
            return None
        self.ledger.reset_heal(module)
        self._log(entry, "人工解除：重拉窗口清零，健康回 ok")
        return self._module_snapshot(
            module, tuple(entry.ports), self.ledger.get(module), self.clock()
        )


__all__ = [
    "BACKOFF_SECONDS",
    "DEFAULT_LOG_MAX_LINES",
    "DEFAULT_POLL_INTERVAL",
    "RestartOutcome",
    "SELF_HEAL_STATUS",
    "SelfHealEngine",
    "WINDOW_SECONDS",
]
