"""自愈冷启动（QUOT-526）：bundle 模块表 + 空账本也能把自有模块拉起来。

钉住：

| 场景 | 期望 |
|---|---|
| `modules=` + `cold_start=True`，账本无记录 | 立刻按命令表拉起（delay 0），不等 5s 退避 |
| 未开冷启动 / 没给模块表 | 账本无 pid 的模块原样不动（482-A 归属判定职责） |
| 主动停（`stopping_by_user`） | 冷启动也不碰（D4 intentional） |
| 认领既有实例（`adopted=True`） | pid 记账，但**不算一次重拉**（不占 5 分钟窗口） |
| 冷启动失败 | 第一次 0s，之后走 5s→15s→45s 退避 |
| `needs_human` | 不再自动动作，只有人工 reset |

全部走假件（clock / sleeper / restarter），不碰真实进程。
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
from ladderstock.supervisor.selfheal import RestartOutcome, SelfHealEngine

PORTS = {"wire": (8790, 8791), "watch": (5952,), "ghost": (1,)}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Sleeper:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.naps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.naps.append(seconds)
        self.clock.advance(seconds)


class Restarter:
    def __init__(self) -> None:
        self.alive = False
        self.fail = False
        self.adopt = False
        self.restarts: list[str] = []
        self._pids = itertools.count(200)

    def is_alive(self, entry: Any) -> bool:
        return self.alive

    def restart(self, module: str) -> RestartOutcome:
        self.restarts.append(module)
        if self.fail:
            return RestartOutcome(ok=False, detail="拉起失败：入口不存在")
        if self.adopt:
            return RestartOutcome(ok=True, pid=next(self._pids), adopted=True)
        return RestartOutcome(ok=True, pid=next(self._pids))


def _engine(
    tmp_path: Path,
    *,
    modules: tuple[str, ...] | None,
    cold_start: bool,
) -> tuple[SelfHealEngine, Ledger, Clock, Sleeper, Restarter]:
    clock = Clock()
    sleeper = Sleeper(clock)
    restarter = Restarter()
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json", window_seconds=300.0)
    engine = SelfHealEngine(
        ledger,
        restarter,
        clock=clock,
        sleeper=sleeper,
        modules=modules,
        cold_start=cold_start,
        module_ports=PORTS,
    )
    return engine, ledger, clock, sleeper, restarter


def test_cold_start_launches_unledgered_modules_immediately(tmp_path: Path) -> None:
    engine, ledger, _clock, sleeper, restarter = _engine(
        tmp_path, modules=("wire", "watch"), cold_start=True
    )

    engine.tick()

    assert restarter.restarts == ["wire", "watch"]
    assert sleeper.naps == [0.0, 0.0]  # 冷启动不等退避
    for module in ("wire", "watch"):
        record = ledger.get(module)
        assert record is not None
        assert record.ports == list(PORTS[module])
        assert record.pid is not None
        assert record.restart_count == 1
        assert record.health == HEALTH_RESTARTING  # 发起 ≠ 恢复

    restarter.alive = True
    engine.tick()
    assert ledger.get("wire").health == HEALTH_OK


def test_cold_start_disabled_leaves_unledgered_modules_alone(tmp_path: Path) -> None:
    engine, ledger, _clock, _sleeper, restarter = _engine(
        tmp_path, modules=("wire",), cold_start=False
    )

    engine.tick()

    assert restarter.restarts == []
    record = ledger.get("wire")
    assert record is not None and record.pid is None
    assert record.health == HEALTH_OK


def test_without_module_table_ledger_only_covers_existing_entries(tmp_path: Path) -> None:
    engine, ledger, _clock, _sleeper, restarter = _engine(tmp_path, modules=None, cold_start=True)
    ledger.ensure("ghost", ports=(1,))

    engine.tick()

    assert restarter.restarts == []
    assert ledger.get("ghost").health == HEALTH_OK


def test_cold_start_respects_intentional_stop(tmp_path: Path) -> None:
    engine, ledger, _clock, _sleeper, restarter = _engine(
        tmp_path, modules=("wire",), cold_start=True
    )
    ledger.ensure("wire", ports=(8790, 8791))
    ledger.mark_stop_intent("wire", reason="维护窗口")

    engine.tick()

    assert restarter.restarts == []
    assert ledger.get("wire").health == HEALTH_STOPPED


def test_adoption_does_not_count_as_a_restart(tmp_path: Path) -> None:
    engine, ledger, _clock, _sleeper, restarter = _engine(
        tmp_path, modules=("wire",), cold_start=True
    )
    restarter.adopt = True

    engine.tick()

    record = ledger.get("wire")
    assert record is not None
    assert record.pid == 200
    assert record.restart_count == 0
    assert record.restart_window == []
    assert record.health == HEALTH_OK


def test_cold_start_failure_walks_the_backoff_ladder(tmp_path: Path) -> None:
    engine, ledger, _clock, sleeper, restarter = _engine(
        tmp_path, modules=("wire",), cold_start=True
    )
    restarter.fail = True

    engine.tick()
    assert sleeper.naps == [0.0]
    assert ledger.get("wire").restart_count == 1

    engine.tick()
    assert sleeper.naps == [0.0, 15.0]
    assert ledger.get("wire").restart_count == 2
    assert ledger.get("wire").last_exit_reason == "拉起失败：入口不存在"


def test_cold_start_does_not_override_needs_human(tmp_path: Path) -> None:
    engine, ledger, _clock, _sleeper, restarter = _engine(
        tmp_path, modules=("wire",), cold_start=True
    )
    ledger.ensure("wire", ports=(8790, 8791))
    ledger.update("wire", health=HEALTH_NEEDS_HUMAN, restart_count=3)

    engine.tick()

    assert restarter.restarts == []
    assert ledger.get("wire").health == HEALTH_NEEDS_HUMAN


def test_module_table_ignores_unmanaged_ledger_entries(tmp_path: Path) -> None:
    engine, ledger, _clock, _sleeper, restarter = _engine(
        tmp_path, modules=("wire",), cold_start=True
    )
    ledger.ensure("ghost", ports=(1,))
    ledger.update("ghost", pid=999)

    engine.tick()

    assert restarter.restarts == ["wire"]
    assert ledger.get("ghost").restart_count == 0


def test_snapshot_lists_managed_modules_before_first_tick(tmp_path: Path) -> None:
    engine, _ledger, _clock, _sleeper, _restarter = _engine(
        tmp_path, modules=("wire",), cold_start=True
    )

    modules = {item["module"]: item for item in engine.snapshot()["modules"]}

    assert modules["wire"]["ports"] == [8790, 8791]
    assert modules["wire"]["pid"] is None
