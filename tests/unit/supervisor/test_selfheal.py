"""自愈引擎的验收语义（QUOT-491 / 482-C，验收证据 ①②③⑤）。

钉住：

| 场景 | 期望 |
|---|---|
| kill 托管模块 | 5s 后重拉 1 次；新进程确认活着 → health 回 `ok` |
| 连续 kill 4 次 | 退避 5s→15s→45s 共重拉 3 次；第 4 次崩溃不再重拉，置 `needs_human` + 放弃通知 |
| 主动 stop（`mark_stop_intent`） | 落 `stopped`，**零重拉**（对外 health 折叠为 `ok`） |
| 通知通道炸掉 | 只记一行日志，自愈主流程照常 |
| 日志滚动 | 刷 500 行只保留上限条数（验收证据 ⑤） |
| 窗口清零 | 5 分钟过后窗口清空，退避从头开始 |
| 人工 reset | `needs_human` 解除：窗口清零、health 回 `ok` |

全部走假件（clock / sleeper / restarter / notifier），不碰真实进程。
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

from ladderstock.supervisor.ledger import (
    HEALTH_NEEDS_HUMAN,
    HEALTH_OK,
    HEALTH_RESTARTING,
    HEALTH_STOPPED,
    Ledger,
)
from ladderstock.supervisor.logroll import append_capped, utc_line
from ladderstock.supervisor.selfheal import (
    RestartOutcome,
    SelfHealEngine,
)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeSleeper:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.naps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.naps.append(seconds)
        self.clock.advance(seconds)


class FakeRestarter:
    def __init__(self) -> None:
        self.alive = True
        self.fail = False
        self.raise_on_restart = False
        self.restarts: list[str] = []
        self._pids = itertools.count(100)

    def is_alive(self, entry: Any) -> bool:
        return self.alive

    def restart(self, module: str) -> RestartOutcome:
        if self.raise_on_restart:
            raise OSError("boom")
        self.restarts.append(module)
        if self.fail:
            return RestartOutcome(ok=False, detail="拉起失败：退出码 1")
        return RestartOutcome(ok=True, pid=next(self._pids))


class RecordingNotifier:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.raise_on: str | None = None

    def failure(self, **kwargs: Any) -> None:
        self._record("failure", kwargs)

    def gave_up(self, **kwargs: Any) -> None:
        self._record("gave_up", kwargs)

    def recovered(self, **kwargs: Any) -> None:
        self._record("recovered", kwargs)

    def _record(self, kind: str, kwargs: dict[str, Any]) -> None:
        if self.raise_on == kind:
            raise RuntimeError("通知炸了")
        self.events.append((kind, kwargs))


def _engine(
    tmp_path: Path, *, notifier: RecordingNotifier | None = None, log_dir: Path | None = None
) -> tuple[SelfHealEngine, Ledger, FakeClock, FakeSleeper, FakeRestarter, RecordingNotifier]:
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    restarter = FakeRestarter()
    active_notifier = notifier if notifier is not None else RecordingNotifier()
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json", window_seconds=300.0)
    engine = SelfHealEngine(
        ledger,
        restarter,
        active_notifier,
        clock=clock,
        sleeper=sleeper,
        log_dir=log_dir,
    )
    return engine, ledger, clock, sleeper, restarter, active_notifier


def _kill(restarter: FakeRestarter) -> None:
    restarter.alive = False


def _snap(engine: SelfHealEngine, module: str) -> dict[str, Any]:
    modules = engine.snapshot()["modules"]
    return next(item for item in modules if item["module"] == module)


# --------------------------------------------------------------- 验收证据 ①


def test_crash_restarts_after_five_seconds_and_health_returns_ok(tmp_path: Path) -> None:
    engine, ledger, _clock, sleeper, restarter, notifier = _engine(tmp_path)
    ledger.ensure("tickhub", ports=(8000, 8787))
    ledger.update("tickhub", pid=123)

    _kill(restarter)
    engine.tick()  # 第 1 次巡检：等待 5s 后重拉

    assert sleeper.naps == [5.0]
    assert restarter.restarts == ["tickhub"]
    assert ledger.get("tickhub").restart_count == 1
    assert ledger.get("tickhub").health == HEALTH_RESTARTING  # 发起 ≠ 恢复

    restarter.alive = True  # 新进程活着
    engine.tick()  # 第 2 次巡检：确认恢复

    assert ledger.get("tickhub").health == HEALTH_OK
    assert ("recovered", {"module": "tickhub", "pid": 100}) in notifier.events

    snap = _snap(engine, "tickhub")
    assert snap["module"] == "tickhub"
    assert snap["health"] == "ok"
    assert snap["state"] == "running"
    assert snap["restarts_5min"] == 1
    assert snap["restart_count"] == 1
    assert snap["self_heal"] == {"attempts": 1, "max": 3, "status": "ok"}


# --------------------------------------------------------------- 验收证据 ②


def test_four_crashes_in_a_row_stop_at_three_and_mark_needs_human(tmp_path: Path) -> None:
    engine, ledger, _clock, sleeper, restarter, notifier = _engine(tmp_path)
    ledger.ensure("tickhub", ports=(8000, 8787))
    ledger.update("tickhub", pid=123)

    for _round in range(3):  # kill1 → 5s 重拉1；kill2 → 15s 重拉2；kill3 → 45s 重拉3
        _kill(restarter)
        engine.tick()
        restarter.alive = False  # 每次重拉出来的进程又被 kill

    assert sleeper.naps == [5.0, 15.0, 45.0]
    assert ledger.get("tickhub").restart_count == 3

    _kill(restarter)  # 第 4 次 kill：窗口已满，不再重拉
    engine.tick()

    assert restarter.restarts.count("tickhub") == 3
    assert ledger.get("tickhub").health == HEALTH_NEEDS_HUMAN
    gave_up_events = [event for event in notifier.events if event[0] == "gave_up"]
    assert len(gave_up_events) == 1
    assert gave_up_events[0][1]["module"] == "tickhub"
    assert gave_up_events[0][1]["attempts"] == 3

    snap = _snap(engine, "tickhub")
    assert snap["health"] == "needs_human"
    assert snap["state"] == "needs_human"
    assert snap["self_heal"] == {"attempts": 3, "max": 3, "status": "gave_up"}

    engine.tick()  # needs_human 之后不再有任何自动动作
    assert restarter.restarts.count("tickhub") == 3


def test_manual_reset_clears_needs_human(tmp_path: Path) -> None:
    engine, ledger, _clock, _sleeper, _restarter, _notifier = _engine(tmp_path)
    ledger.ensure("tickhub")
    ledger.update("tickhub", pid=123, health=HEALTH_NEEDS_HUMAN, restart_count=3)
    ledger.get("tickhub").restart_window = [1000.0, 1020.0, 1040.0]

    result = engine.reset("tickhub")

    assert result is not None
    assert result["health"] == "ok"
    assert ledger.get("tickhub").health == HEALTH_OK
    assert ledger.get("tickhub").restart_window == []
    assert engine.reset("ghost-module") is None  # 账本无此模块


# --------------------------------------------------------------- 验收证据 ③


def test_intentional_stop_never_self_heals(tmp_path: Path) -> None:
    engine, ledger, _clock, _sleeper, restarter, notifier = _engine(tmp_path)
    ledger.ensure("senate", ports=(8765, 8786))
    ledger.update("senate", pid=555)
    ledger.mark_stop_intent("senate", reason="维护窗口")

    _kill(restarter)
    engine.tick()

    assert ledger.get("senate").health == HEALTH_STOPPED
    assert ledger.get("senate").restart_count == 0
    assert restarter.restarts == []
    assert notifier.events == []

    snap = _snap(engine, "senate")
    assert snap["state"] == "stopped"
    assert snap["stopping_by_user"] is True
    assert snap["health"] == "ok"  # 主动关对外折叠为 ok，原始生命周期看 state


# ------------------------------------------------------- 通知失败 / 重拉失败


def test_notifier_exception_is_swallowed_and_heal_continues(tmp_path: Path) -> None:
    notifier = RecordingNotifier()
    notifier.raise_on = "failure"
    engine, ledger, _clock, _sleeper, restarter, _notifier = _engine(
        tmp_path, notifier=notifier
    )
    ledger.ensure("tickhub", ports=(8000,))
    ledger.update("tickhub", pid=123)
    restarter.fail = True  # 重拉必失败 → 触发 failure 通知

    _kill(restarter)
    engine.tick()  # 通知炸了也不能把巡检带走

    assert ledger.get("tickhub").health == HEALTH_RESTARTING
    assert ledger.get("tickhub").restart_count == 1
    assert ledger.get("tickhub").last_exit_reason == "拉起失败：退出码 1"


def test_failing_restarts_still_walk_the_backoff_ladder(tmp_path: Path) -> None:
    engine, ledger, _clock, sleeper, restarter, notifier = _engine(tmp_path)
    ledger.ensure("tickhub", ports=(8000,))
    ledger.update("tickhub", pid=123)
    restarter.fail = True

    _kill(restarter)
    engine.tick()
    _kill(restarter)
    engine.tick()

    assert sleeper.naps == [5.0, 15.0]
    assert ledger.get("tickhub").restart_count == 2
    failures = [event[0] for event in notifier.events if event[0] == "failure"]
    assert failures == ["failure", "failure"]


def test_restarter_exception_is_treated_as_failure(tmp_path: Path) -> None:
    engine, ledger, _clock, _sleeper, restarter, notifier = _engine(tmp_path)
    ledger.ensure("tickhub", ports=(8000,))
    ledger.update("tickhub", pid=123)
    restarter.raise_on_restart = True

    _kill(restarter)
    engine.tick()

    assert ledger.get("tickhub").restart_count == 1
    assert "重拉调用异常" in (ledger.get("tickhub").last_exit_reason or "")
    assert notifier.events and notifier.events[0][0] == "failure"


# --------------------------------------------------------------- 验收证据 ⑤


def test_log_roll_keeps_only_the_cap(tmp_path: Path) -> None:
    path = tmp_path / "roll.log"
    for index in range(500):
        append_capped(path, utc_line(f"line-{index}"), max_lines=200)

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 200
    assert lines[-1].endswith("line-499")
    assert lines[0].endswith("line-300")


def test_engine_log_dir_also_rolls(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    engine, ledger, _clock, _sleeper, restarter, _notifier = _engine(
        tmp_path, log_dir=log_dir
    )
    ledger.ensure("tickhub", ports=(8000,))
    ledger.update("tickhub", pid=123)

    for _ in range(210):  # 制造 210 次崩溃重拉
        _kill(restarter)
        engine.tick()

    lines = (log_dir / "tickhub.log").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) <= 200


# ------------------------------------------------------------------- 窗口语义


def test_window_expires_and_backoff_starts_over(tmp_path: Path) -> None:
    engine, ledger, clock, sleeper, restarter, _notifier = _engine(tmp_path)
    ledger.ensure("tickhub", ports=(8000,))
    ledger.update("tickhub", pid=123)

    _kill(restarter)
    engine.tick()  # 重拉 1（5s）
    clock.advance(301.0)  # 窗口外

    _kill(restarter)
    engine.tick()  # 窗口已清零 → 退避从头：5s

    assert sleeper.naps == [5.0, 5.0]
    assert ledger.get("tickhub").restart_count == 2
    assert len(ledger.get("tickhub").restart_window) == 1
    assert _snap(engine, "tickhub")["restarts_5min"] == 1


def test_unknown_module_without_pid_is_left_alone(tmp_path: Path) -> None:
    engine, ledger, _clock, _sleeper, restarter, _notifier = _engine(tmp_path)
    ledger.ensure("ghost", ports=(1,))  # 从未记账 pid，也无窗口

    engine.tick()

    assert restarter.restarts == []
    assert ledger.get("ghost").health == HEALTH_OK


# ------------------------------------------------------------------- 后台线程


def test_start_stop_lifecycle(tmp_path: Path) -> None:
    engine, _ledger, _clock, _sleeper, _restarter, _notifier = _engine(tmp_path)

    engine.start()
    assert engine.running
    engine.start()  # 幂等
    assert engine.running
    engine.stop(timeout=2.0)
    assert not engine.running
