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
    from collections.abc import Callable

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
    """一次重拉的结论；`detail` 是失败原因摘要（进日志与通知）。"""

    ok: bool
    pid: int | None = None
    detail: str = ""


class Restarter(Protocol):
    """存活探测 + 重拉通道（生产实现见 restart.CommandTableRestarter）。"""

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
        self._sleeper: Callable[[float], None] = (
            sleeper if sleeper is not None else self._wait
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick_lock = threading.RLock()

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
            for entry in self.ledger.entries():
                try:
                    self._inspect(entry)
                except Exception:
                    _log.exception("自愈巡检模块 %s 异常（已吞，继续下一模块）", entry.module)

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
        if entry.pid is None and not window:
            return  # 从未记账的进程，归属判定是 482-A 的职责，这里不越界。
        if entry.health == HEALTH_NEEDS_HUMAN:
            return  # 超限待人工；只有 reset 能解除，不再自动动作。
        if len(window) >= self.max_restarts:
            self._gave_up(entry, len(window))
            return
        self._pull(entry, window)

    def _pull(self, entry: LedgerEntry, window: list[float]) -> None:
        attempt = len(window) + 1
        delay = self.backoff[min(len(window), len(self.backoff) - 1)]
        if entry.health != HEALTH_RESTARTING:
            self.ledger.update(entry.module, health=HEALTH_RESTARTING)
        self._log(entry, f"第 {attempt}/{self.max_restarts} 次重拉：{delay:g}s 后发起")
        self._sleeper(delay)
        if self._stop_event.is_set():
            return  # 引擎关停中被打断；不登记半截重拉。
        if self._alive(entry):
            # 等待期间进程已被拉活——按恢复处理，不再拉起（防双实例）。
            self.ledger.update(entry.module, health=HEALTH_OK)
            self._log(entry, f"重拉等待期间进程已恢复（pid={entry.pid}），不重复拉起")
            self._safe_notify_recovered(entry)
            return
        outcome = self._restart(entry)
        self.ledger.record_restart(
            entry.module,
            ok=outcome.ok,
            pid=outcome.pid,
            now=self.clock(),
            reason=outcome.detail,
        )
        if outcome.ok:
            self._log(entry, f"第 {attempt}/{self.max_restarts} 次重拉已发起（pid={outcome.pid}）")
        else:
            reason = outcome.detail or "重拉失败"
            self._log(entry, f"第 {attempt}/{self.max_restarts} 次重拉失败：{reason}")
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
            for entry in self.ledger.entries():
                known.setdefault(entry.module, tuple(entry.ports))
            modules = [
                self._module_snapshot(module, ports, self.ledger.get(module), now)
                for module, ports in known.items()
            ]
            modules.sort(key=lambda item: str(item["module"]))
            return {
                "ok": True,
                "engine": {
                    "running": self.running,
                    "window_seconds": self.ledger.window_seconds,
                    "max_restarts": self.max_restarts,
                    "backoff": list(self.backoff),
                },
                "modules": modules,
            }

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
