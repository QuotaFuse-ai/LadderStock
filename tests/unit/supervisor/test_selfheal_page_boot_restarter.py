"""QUOT-607 回归：watch_guard / scheduler 死后自愈能拉回来，不出现「未知模块」。

根因：PageBoot（页面自启）把自有子进程 `watch_guard` / `scheduler` 记进账本
（origin=spawned, state=running），但这两个名字不在 MODULE_ORDER 适配器注册表
里。自愈巡检（生产默认 modules=None，按账本条目巡检）探到它们死后，把名字递给
AdapterRestarter → SupervisorEngine._adapter() → SupervisorError「未知模块：
scheduler」→ 三次退避烧完即 needs_human，永远拉不回来。

修法：生产重拉通道前加一条 PageBootRestarter 路由（`CompositeRestarter`），
这两个名字走 `PageBoot.ensure_module()` 的幂等 ensure（按命令行指纹找活进程），
其余模块原样走适配器。MODULE_ORDER / MODULE_PORTS 不动。

全部假件，不碰真进程、不占真端口。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ladderstock.supervisor.ledger import (
    HEALTH_NEEDS_HUMAN,
    HEALTH_OK,
    Ledger,
    ModuleRecord,
    now_iso,
)
from ladderstock.supervisor.page_boot import (
    SCHEDULER_MODULE,
    WATCH_GUARD_MODULE,
    PageBoot,
    WatchScanner,
)
from ladderstock.supervisor.proctree import cmdline_hash
from ladderstock.supervisor.restart import (
    CompositeRestarter,
    PageBootRestarter,
    pid_alive,
)
from ladderstock.supervisor.selfheal import RestartOutcome, SelfHealEngine
from tests.unit.supervisor.test_page_boot import SCHED_CMD, FakeHost

GUARD_CMD = ("python.exe", "-m", "ladderstock.cli.main", "__watch-guard", "--tick", "30")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

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


class RecordingNotifier:
    def __init__(self) -> None:
        self.gave_up_events: list[dict[str, Any]] = []

    def failure(self, **kwargs: Any) -> None:
        pass

    def gave_up(self, **kwargs: Any) -> None:
        self.gave_up_events.append(kwargs)

    def recovered(self, **kwargs: Any) -> None:
        pass


def _boot(tmp_path: Path, host: FakeHost) -> PageBoot:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    return PageBoot(
        data_root=tmp_path,
        ledger=ledger,
        host=host,
        adapters={},
        scanner=WatchScanner(tmp_path, threshold=3.0),
        scheduler_argv=list(SCHED_CMD),
        run_cwd=tmp_path,
        log_dir=tmp_path / "logs",
        env={"LDST_PAGE_AUTOSTART": "1"},
        tick_sec=30.0,
    )


def _channel(boot: PageBoot) -> CompositeRestarter:
    return CompositeRestarter([PageBootRestarter(boot), _FakeAdapterRestarter()])


class _FakeAdapterRestarter:
    """AdapterRestarter 的替身：known_modules 与生产一致（MODULE_ORDER）。"""

    @property
    def known_modules(self) -> tuple[str, ...]:
        return ("tickhub", "tickhub_us", "wire", "senate", "financecoffer", "proxyfleet", "watch")

    def is_alive(self, entry: Any) -> bool:
        return pid_alive(entry.pid)

    def restart(self, module: str) -> RestartOutcome:
        raise AssertionError(f"自有子进程 {module} 不应路由到适配器重拉通道")


def _seed_self_children(boot: PageBoot) -> None:
    """页面自启一次：拉起两个自有子进程并记账（模拟用户打开网页）。"""
    boot.ensure()


def _kill_child(boot: PageBoot, module: str, host: FakeHost) -> None:
    """模拟子进程崩溃：从假宿主进程表里删掉账本上的 pid。"""
    record = boot.ledger.record(module)
    assert record is not None and record.pid is not None
    host.procs.pop(int(record.pid), None)


def test_scheduler_crash_is_restarted_next_tick(tmp_path: Path) -> None:
    """验收①：scheduler 退出一次 → 下一 tick 重拉成功、restart_count=1、health 回 ok。"""
    host = FakeHost()
    boot = _boot(tmp_path, host)
    _seed_self_children(boot)
    channel = _channel(boot)
    clock = FakeClock()
    engine = SelfHealEngine(
        boot.ledger,
        channel,
        RecordingNotifier(),
        clock=clock,
        sleeper=FakeSleeper(clock),
    )
    before = boot.ledger.record(SCHEDULER_MODULE)
    assert before is not None and before.pid is not None
    original_pid = int(before.pid)

    _kill_child(boot, SCHEDULER_MODULE, host)
    engine.tick()  # 发起重拉（退避 5s 由 FakeSleeper 推进）
    engine.tick()  # 下一轮确认新进程活着，health 回 ok

    after = boot.ledger.record(SCHEDULER_MODULE)
    assert after is not None
    assert after.restart_count == 1
    assert after.health == HEALTH_OK
    assert after.state == "running"
    assert after.pid is not None and after.pid != original_pid
    assert "未知模块" not in json.dumps(after.last_exit_reason or "")
    # 新 pid 在假宿主里真的活着，命令行与账本指纹一致
    snap = host.procs[after.pid]
    assert cmdline_hash(snap.cmdline) == after.cmdline_hash


def test_watch_guard_crash_is_restarted_next_tick(tmp_path: Path) -> None:
    """watch_guard 同样路径：死一次 → 下一 tick 拉回，无「未知模块」。"""
    host = FakeHost()
    boot = _boot(tmp_path, host)
    _seed_self_children(boot)
    channel = _channel(boot)
    clock = FakeClock()
    engine = SelfHealEngine(
        boot.ledger,
        channel,
        RecordingNotifier(),
        clock=clock,
        sleeper=FakeSleeper(clock),
    )
    _kill_child(boot, WATCH_GUARD_MODULE, host)
    engine.tick()
    engine.tick()

    after = boot.ledger.record(WATCH_GUARD_MODULE)
    assert after is not None
    assert after.restart_count == 1
    assert after.health == HEALTH_OK
    assert "未知模块" not in json.dumps(
        [rec.last_exit_reason or "" for rec in boot.ledger.entries()]
    )


def test_ledger_never_records_unknown_module(tmp_path: Path) -> None:
    """验收②：整个崩溃-恢复周期落盘的 ledger.json 里不出现「未知模块」。"""
    host = FakeHost()
    boot = _boot(tmp_path, host)
    _seed_self_children(boot)
    channel = _channel(boot)
    clock = FakeClock()
    engine = SelfHealEngine(
        boot.ledger,
        channel,
        RecordingNotifier(),
        clock=clock,
        sleeper=FakeSleeper(clock),
    )
    _kill_child(boot, SCHEDULER_MODULE, host)
    _kill_child(boot, WATCH_GUARD_MODULE, host)
    engine.tick()
    engine.tick()

    raw = json.loads((tmp_path / "supervisor" / "ledger.json").read_text(encoding="utf-8"))
    text = json.dumps(raw, ensure_ascii=False)
    assert "未知模块" not in text
    modules = {entry["module"] for entry in raw["modules"]}
    assert {SCHEDULER_MODULE, WATCH_GUARD_MODULE} <= modules
    for entry in raw["modules"]:
        if entry["module"] in (SCHEDULER_MODULE, WATCH_GUARD_MODULE):
            assert entry["restart_count"] == 1


def test_repeated_crashes_still_gives_up_with_needs_human(tmp_path: Path) -> None:
    """持续崩溃仍走退避上限：3 次重拉后 needs_human + 放弃通知（不无限循环）。"""
    host = FakeHost()
    boot = _boot(tmp_path, host)
    _seed_self_children(boot)
    channel = _channel(boot)
    clock = FakeClock()
    notifier = RecordingNotifier()
    engine = SelfHealEngine(
        boot.ledger,
        channel,
        notifier,
        clock=clock,
        sleeper=FakeSleeper(clock),
    )

    def crash_and_burn() -> None:
        record = boot.ledger.record(SCHEDULER_MODULE)
        assert record is not None and record.pid is not None
        # 每次重拉出来的新进程也立刻崩：从宿主进程表删掉账本绑定的 pid
        host.procs.pop(int(record.pid), None)

    for _ in range(3):
        crash_and_burn()
        engine.tick()
    # 窗口已满：再崩一次，下一 tick 置 needs_human（不再自动重拉）
    crash_and_burn()
    engine.tick()
    entry = boot.ledger.record(SCHEDULER_MODULE)
    assert entry is not None
    assert entry.health == HEALTH_NEEDS_HUMAN
    assert len(notifier.gave_up_events) == 1


def test_page_boot_restarter_routes_and_rejects(tmp_path: Path) -> None:
    """PageBootRestarter 通道本身的语义：幂等认领、失败如实、非自有名字拒绝。"""
    host = FakeHost()
    boot = _boot(tmp_path, host)
    _seed_self_children(boot)
    restarter = PageBootRestarter(boot)

    # 自有名字路由到 PageBoot：进程活着 → adopted（不重复拉起）
    outcome = restarter.restart(SCHEDULER_MODULE)
    assert outcome.adopted is True
    assert outcome.ok is True
    assert len(host.spawns) == 2  # ensure() 拉过两个，重拉没有再加

    # 非自有名字如实拒绝（不抛「未知模块」那种异常）
    outcome = restarter.restart("tickhub")
    assert outcome.ok is False and outcome.adopted is False
    assert "tickhub" in (outcome.detail or "")


def test_ensure_module_is_idempotent(tmp_path: Path) -> None:
    """ensure_module 幂等：活着不重复拉起；死了按同一命令行重新拉。"""
    host = FakeHost()
    boot = _boot(tmp_path, host)
    _seed_self_children(boot)
    spawns_after_ensure = len(host.spawns)

    report = boot.ensure_module(SCHEDULER_MODULE)
    assert report.state == "already_running"
    assert len(host.spawns) == spawns_after_ensure

    _kill_child(boot, SCHEDULER_MODULE, host)
    report = boot.ensure_module(SCHEDULER_MODULE)
    assert report.state == "spawned"
    assert len(host.spawns) == spawns_after_ensure + 1
    record = boot.ledger.record(SCHEDULER_MODULE)
    assert record is not None and record.pid is not None
    assert host.procs[record.pid].cmdline == tuple(SCHED_CMD)


def test_ensure_module_rejects_foreign_names(tmp_path: Path) -> None:
    boot = _boot(tmp_path, FakeHost())
    report = boot.ensure_module("tickhub")
    assert report.state == "skipped"


def test_stale_ledger_entry_with_dead_pid_recovers(tmp_path: Path) -> None:
    """复刻线上现场：账本里 scheduler 已是「未知模块」败迹 + pid 已死 → 也能拉回。"""
    host = FakeHost()
    boot = _boot(tmp_path, host)
    ledger = boot.ledger
    ledger.update(
        ModuleRecord(
            module=SCHEDULER_MODULE,
            ports=[],
            pid=424242,  # 假宿主里不存在的 pid
            parent_pid=1,
            started_at=now_iso(),
            origin="spawned",
            cmdline_hash=cmdline_hash(tuple(SCHED_CMD)),
            state="running",
            health=HEALTH_OK,
            last_exit_reason="未知模块：scheduler",
            updated_at=now_iso(),
        )
    )
    channel = _channel(boot)
    clock = FakeClock()
    engine = SelfHealEngine(
        ledger,
        channel,
        RecordingNotifier(),
        clock=clock,
        sleeper=FakeSleeper(clock),
    )
    engine.tick()
    engine.tick()

    after = ledger.record(SCHEDULER_MODULE)
    assert after is not None
    assert after.restart_count == 1
    assert after.health == HEALTH_OK
    assert after.pid is not None
    assert after.pid != 424242
    assert "未知模块" not in (after.last_exit_reason or "")
