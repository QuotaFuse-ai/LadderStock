"""顺序启停引擎测试（QUOT-482-A）：全走 fake，覆盖派单判过证据 1/3/4。

- 冷启动进程树：账本记录入口 pid + parent_pid（= supervisor 自己）；
- 只重启 wire：其它模块 pid 一个不变；
- 崩溃恢复：进程已死 → stopped，绝不误判 running，停时绝不误杀。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeProcess, build_engine

from ladderstock.supervisor import Ledger, SupervisorEngine
from ladderstock.supervisor.adapters import MODULE_ORDER
from ladderstock.supervisor.engine import (
    EVIDENCE_CMDLINE_MISMATCH,
    EVIDENCE_LEDGER,
    EVIDENCE_NOT_RUNNING,
    EVIDENCE_PORT_UNKNOWN,
    SupervisorError,
)
from ladderstock.supervisor.ledger import STOP_PID_REUSE, STOP_PROCESS_DEAD
from ladderstock.supervisor.proctree import cmdline_hash


def second_generation(tmp_path: Path, adapters: Any, procs: Any, clock: Any) -> SupervisorEngine:
    """崩溃恢复测试用：进程死光后，从盘上账本重建第二任 supervisor。"""
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    ledger.load()
    return SupervisorEngine(
        adapters=adapters,
        ledger=ledger,
        procs=procs,
        start_grace_sec=1.0,
        stop_grace_sec=1.0,
        kill_grace_sec=1.0,
        poll_interval_sec=0.25,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


# --------------------------------------------------------------------- 启动


def test_start_all_follows_registered_order(tmp_path: Path) -> None:
    engine, adapters, _procs, _clock = build_engine(tmp_path)
    report = engine.start_all()
    assert report["ok"] is True
    assert list(report["modules"]) == list(MODULE_ORDER)
    for name in MODULE_ORDER:
        assert report["modules"][name] == {"ok": True, "error": None}
        assert adapters[name].entry_pid() is not None


def test_started_process_tree_is_recorded_with_parentage(tmp_path: Path) -> None:
    """判过证据 1 的账本面：入口 pid + parent_pid（= supervisor 自己）都进账本。"""
    engine, adapters, procs, _clock = build_engine(tmp_path)
    engine.start_all()
    for name, adapter in adapters.items():
        record = engine._ledger.record(name)
        assert record is not None
        assert record.pid == adapter.entry_pid()
        assert record.parent_pid == procs.self_pid()
        assert record.state == "running"
        assert record.origin == "spawned"
        assert record.cmdline_hash == cmdline_hash(adapter.recorded_cmdline())
        assert record.ports == list(adapter.ports)


def test_start_all_is_idempotent(tmp_path: Path) -> None:
    engine, adapters, _procs, _clock = build_engine(tmp_path)
    engine.start_all()
    pids_before = {name: adapter.entry_pid() for name, adapter in adapters.items()}
    again = engine.start_all()
    assert again["ok"] is True
    for name, adapter in adapters.items():
        assert adapter.entry_pid() == pids_before[name]
        record = engine._ledger.record(name)
        assert record is not None and record.state == "running"


def test_start_failure_does_not_block_other_modules(tmp_path: Path) -> None:
    engine, adapters, _procs, _clock = build_engine(tmp_path)
    adapters["wire"].probe_always_false = True
    report = engine.start_all()
    assert report["ok"] is False
    assert report["modules"]["wire"]["ok"] is False
    for name in MODULE_ORDER:
        if name != "wire":
            assert report["modules"][name]["ok"] is True
    wire = engine._ledger.record("wire")
    assert wire is not None and wire.state == "failed"


def test_start_timeout_force_kills_half_started_chain(tmp_path: Path) -> None:
    """拉起后端口一直不听：超时整树终止，不留半死进程占端口。"""
    engine, adapters, procs, _clock = build_engine(tmp_path)
    adapters["wire"].probe_always_false = True
    with pytest.raises(SupervisorError):
        engine.start_module("wire")
    chain = adapters["wire"].chain_pids()
    assert chain, "spawn 应已发生"
    for pid in chain:
        assert pid not in procs.processes, f"孤儿 {pid} 未被清掉"
    record = engine._ledger.record("wire")
    assert record is not None
    assert record.state == "failed"
    assert record.stop_reason == "start_timeout+forced_kill"
    assert record.forced_kill is True


def test_start_refuses_when_port_taken_by_stranger(tmp_path: Path) -> None:
    """端口被外部进程占着：拒绝启动（DEC-004 不换端口），不碰那个进程。"""
    engine, adapters, procs, _clock = build_engine(tmp_path)
    adapters["senate"].take_port_externally()
    stranger_pids = set(procs.processes)
    with pytest.raises(SupervisorError, match="已被其它进程占用"):
        engine.start_module("senate")
    record = engine._ledger.record("senate")
    assert record is None or record.state != "running"
    assert set(procs.processes) == stranger_pids


def test_start_unknown_module_raises(tmp_path: Path) -> None:
    engine, _adapters, _procs, _clock = build_engine(tmp_path)
    with pytest.raises(SupervisorError, match="未知模块"):
        engine.start_module("nope")


# --------------------------------------------------------------------- 停止


def test_stop_all_runs_in_reverse_and_clears_every_port(tmp_path: Path) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    engine.start_all()
    report = engine.stop_all()
    assert report["ok"] is True
    assert procs.listening_ports() == set()
    for adapter in adapters.values():
        for pid in adapter.chain_pids():
            assert pid not in procs.processes
        record = engine._ledger.record(adapter.name)
        assert record is not None
        assert record.state == "stopped"
        assert record.forced_kill is False


def test_stop_all_takes_down_whole_descendant_tree(tmp_path: Path) -> None:
    """cmd → python → worker 深链：停完一个不剩（判过证据 2 的无孤儿面）。"""
    engine, adapters, procs, _clock = build_engine(tmp_path, chain_depth=3)
    engine.start_all()
    all_chain = [pid for adapter in adapters.values() for pid in adapter.chain_pids()]
    assert len(all_chain) == 3 * len(MODULE_ORDER)
    engine.stop_all()
    assert procs.listening_ports() == set()
    for pid in all_chain:
        assert pid not in procs.processes


def test_graceful_timeout_escalates_to_tree_kill(tmp_path: Path) -> None:
    """优雅停只杀入口留孤儿：超时升级整树强杀，forced_kill 记账。"""
    engine, adapters, procs, _clock = build_engine(tmp_path)
    engine.start_all()
    adapters["wire"].stop_leaves_orphans = True
    report = engine.stop_all()
    assert report["ok"] is True
    record = engine._ledger.record("wire")
    assert record is not None
    assert record.state == "stopped"
    assert record.forced_kill is True
    assert record.stop_reason == "graceful_stop_timeout+forced_kill"
    for pid in adapters["wire"].chain_pids():
        assert pid not in procs.processes, "孤儿 worker 没被补刀"
    assert procs.listening_ports() == set()


def test_stop_exception_escalates_to_tree_kill(tmp_path: Path) -> None:
    """优雅停抛异常：立即升级整树强杀，不重试。"""
    engine, adapters, procs, _clock = build_engine(tmp_path)
    engine.start_all()
    adapters["wire"].stop_raises = RuntimeError("compose stop 失败")
    report = engine.stop_all()
    assert report["ok"] is True
    record = engine._ledger.record("wire")
    assert record is not None
    assert record.state == "stopped"
    assert record.forced_kill is True
    assert record.stop_reason == "优雅停异常：compose stop 失败+forced_kill"
    for pid in adapters["wire"].chain_pids():
        assert pid not in procs.processes


def test_port_still_taken_after_kill_fails_the_module(tmp_path: Path) -> None:
    """强杀后端口仍被占（真有赖着不走的外部监听者）：failed 交人工。"""
    engine, adapters, _procs, _clock = build_engine(tmp_path)
    engine.start_all()
    adapters["wire"].stop_leaves_orphans = True
    adapters["wire"].take_port_externally()
    report = engine.stop_all()
    assert report["ok"] is False
    assert report["modules"]["wire"]["ok"] is False
    record = engine._ledger.record("wire")
    assert record is not None
    assert record.state == "failed"
    assert record.stop_reason == "graceful_stop_timeout+forced_kill"


def test_stop_skips_stopped_records_without_touching_the_port(tmp_path: Path) -> None:
    """账本已判 stopped（含 PID 复用判定）：stop 不碰端口上的活进程。"""
    engine, adapters, procs, _clock = build_engine(tmp_path)
    engine.start_all()
    stranger = adapters["wire"].entry_pid()
    assert stranger is not None
    procs.processes[stranger].cmdline = ("C:/Windows/notepad.exe",)
    engine.reconcile()
    stop_calls_before = adapters["wire"].stop_calls
    report = engine.stop_all()
    assert report["ok"] is True
    assert adapters["wire"].stop_calls == stop_calls_before
    assert stranger not in procs.killed_trees
    assert stranger in procs.processes


# --------------------------------------------------------------------- 重启


def test_restart_module_leaves_other_pids_alone(tmp_path: Path) -> None:
    """判过证据 3：只重启 wire，其它模块 pid 前后不变。"""
    engine, adapters, _procs, _clock = build_engine(tmp_path)
    engine.start_all()
    before = {name: view["pid"] for name, view in engine.state()["modules"].items()}
    result = engine.restart_module("wire")
    assert result["ok"] is True
    assert result["others_unchanged"] is True
    after = {name: view["pid"] for name, view in engine.state()["modules"].items()}
    assert after["wire"] != before["wire"]
    for name in MODULE_ORDER:
        if name != "wire":
            assert after[name] == before[name], f"{name} 的 pid 被牵连"
    record = engine._ledger.record("wire")
    assert record is not None
    assert record.state == "running"
    assert record.origin == "spawned"


def test_restart_unknown_module_raises(tmp_path: Path) -> None:
    engine, _adapters, _procs, _clock = build_engine(tmp_path)
    with pytest.raises(SupervisorError, match="未知模块"):
        engine.restart_module("nope")


# --------------------------------------------------------------------- 归属可见


def test_state_reports_managed_on_healthy_startup(tmp_path: Path) -> None:
    engine, _adapters, _procs, _clock = build_engine(tmp_path)
    engine.start_all()
    report = engine.state()
    assert report["order"] == list(MODULE_ORDER)
    for name, view in report["modules"].items():
        assert view["ownership"] == "managed", name
        assert view["evidence"] == EVIDENCE_LEDGER, name
        assert view["listening"] is True, name


def test_state_reports_quiet_module_as_managed_not_running(tmp_path: Path) -> None:
    engine, _adapters, _procs, _clock = build_engine(tmp_path)
    report = engine.state()
    for name, view in report["modules"].items():
        assert view["ownership"] == "managed", name
        assert view["evidence"] == EVIDENCE_NOT_RUNNING, name
        assert view["listening"] is False, name


def test_state_reports_external_when_port_taken_by_stranger(tmp_path: Path) -> None:
    engine, adapters, _procs, _clock = build_engine(tmp_path)
    adapters["senate"].take_port_externally()
    report = engine.state()
    senate = report["modules"]["senate"]
    assert senate["ownership"] == "external"
    assert senate["evidence"] == EVIDENCE_PORT_UNKNOWN
    assert senate["listening"] is True
    for name in MODULE_ORDER:
        if name != "senate":
            assert report["modules"][name]["ownership"] == "managed", name


def test_state_reports_cmdline_mismatch_on_pid_reuse(tmp_path: Path) -> None:
    """PID 被复用（命令行对不上）：external + cmdline_mismatch，不认人。"""
    engine, adapters, procs, _clock = build_engine(tmp_path)
    engine.start_all()
    stranger = adapters["wire"].entry_pid()
    assert stranger is not None
    procs.processes[stranger].cmdline = ("C:/Windows/System32/notepad.exe",)
    report = engine.state()
    wire = report["modules"]["wire"]
    assert wire["ownership"] == "external"
    assert wire["evidence"] == EVIDENCE_CMDLINE_MISMATCH
    assert wire["listening"] is True
    record = engine._ledger.record("wire")
    assert record is not None
    assert record.state == "stopped"
    assert record.stop_reason == STOP_PID_REUSE
    for name in MODULE_ORDER:
        if name != "wire":
            view = report["modules"][name]
            assert view["ownership"] == "managed", name
            assert view["evidence"] == EVIDENCE_LEDGER, name


# --------------------------------------------------------------------- 崩溃恢复


def test_crash_recovery_marks_dead_processes_stopped(tmp_path: Path) -> None:
    """判过证据 4：supervisor 死后重启，已死进程标 stopped，不误判 running。"""
    engine, adapters, procs, clock = build_engine(tmp_path)
    engine.start_all()
    for adapter in adapters.values():
        for pid in adapter.chain_pids():
            procs.processes.pop(pid, None)

    engine2 = second_generation(tmp_path, adapters, procs, clock)
    report = engine2.state()
    for name, view in report["modules"].items():
        assert view["state"] == "stopped", name
        assert view["stop_reason"] == STOP_PROCESS_DEAD, name
        assert view["ownership"] == "managed", name
        assert view["evidence"] == EVIDENCE_NOT_RUNNING, name
        assert view["listening"] is False, name


def test_stop_after_crash_recovery_kills_nothing(tmp_path: Path) -> None:
    """崩溃恢复后 stop_all：不误杀、不报错、killed_trees 为空。"""
    engine, adapters, procs, clock = build_engine(tmp_path)
    engine.start_all()
    for adapter in adapters.values():
        for pid in adapter.chain_pids():
            procs.processes.pop(pid, None)
    engine2 = second_generation(tmp_path, adapters, procs, clock)
    report = engine2.stop_all()
    assert report["ok"] is True
    assert procs.killed_trees == []
    assert procs.listening_ports() == set()


# --------------------------------------------------------------------- 并发互斥


def test_stop_all_waits_for_engine_lock(tmp_path: Path) -> None:
    """stop 执行中（持锁），后续 state() 必须排队，不能交错。"""
    engine, adapters, _procs, _clock = build_engine(tmp_path)
    engine.start_all()
    gate = threading.Event()
    # reversed 顺序里 tickhub 最后停：前 5 个停完后卡在 gate 上，锁一直被持有
    adapters["tickhub"].stop_gate = gate
    stop_box: dict[str, Any] = {}
    stopper = threading.Thread(target=lambda: stop_box.setdefault("r", engine.stop_all()))
    stopper.start()
    stopper.join(0.3)
    assert stopper.is_alive(), "stop_all 应还在执行（卡在 tickhub 的优雅停）"

    state_box: dict[str, Any] = {}
    watcher = threading.Thread(target=lambda: state_box.setdefault("r", engine.state()))
    watcher.start()
    watcher.join(0.3)
    assert watcher.is_alive(), "state() 必须排队等锁"

    gate.set()
    stopper.join(10)
    watcher.join(10)
    assert stop_box["r"]["ok"] is True
    assert list(state_box["r"]["modules"]) == list(MODULE_ORDER)


# ------------------------------------------------------- 端口冲突安全处置（S4 / QUOT-557）


def test_spawned_pids_tracks_only_own_spawns(tmp_path: Path) -> None:
    """自有 spawn 账：本任亲手拉起的入口 pid + 指纹在册；外来进程不在册。"""
    engine, adapters, _procs, _clock = build_engine(tmp_path)
    engine.start_all()
    spawned = engine.spawned_pids
    for name in MODULE_ORDER:
        entry = adapters[name].entry_pid()
        assert entry in spawned, name
        assert spawned[entry] == cmdline_hash(adapters[name].recorded_cmdline())
    assert 424242 not in spawned


def test_restart_own_occupant_recovers_provably_own_residual(tmp_path: Path) -> None:
    """端口被可证明自有的残留进程占用（账本 pid 已漂移）：重绑 + 安全重启解冲突，
    旧自有进程退出、端口重新监听、其它模块 PID 一个不变。"""
    engine, adapters, procs, _clock = build_engine(tmp_path)
    engine.start_all()
    occupant = adapters["wire"].entry_pid()
    assert occupant is not None
    old_chain = adapters["wire"].chain_pids()
    # 账本漂移：wire 的 pid 指向一个已死 pid，残留自有进程仍占着端口
    engine._ledger.update("wire", pid=999999)
    before_others = {n: adapters[n].entry_pid() for n in MODULE_ORDER if n != "wire"}

    result = engine.restart_own_occupant("wire", occupant)

    assert result["ok"] is True
    assert result["others_unchanged"] is True
    for pid in old_chain:
        assert pid not in procs.processes, "残留自有进程未被安全停掉"
    new_record = engine._ledger.record("wire")
    assert new_record is not None and new_record.pid is not None
    assert new_record.pid != occupant, "own_child 重拉后 PID 必须换新"
    assert new_record.origin == "spawned" and new_record.adopted_peer is False
    assert adapters["wire"].probe() is True
    for name, pid in before_others.items():
        assert adapters[name].entry_pid() == pid, f"{name} 的 pid 被牵连"


def test_restart_own_occupant_refuses_stranger_and_never_kills(tmp_path: Path) -> None:
    """外来进程占口（非本任 spawn）：失败关闭，绝不终止（DEC-014 / QUOT-545 红线）。"""
    engine, adapters, procs, _clock = build_engine(tmp_path)
    engine.start_all()
    procs.processes[424242] = FakeProcess(
        pid=424242, cmdline=("C:/other/stranger.exe",), ppid=None, ports=adapters["wire"].ports
    )
    with pytest.raises(SupervisorError, match="无法证明"):
        engine.restart_own_occupant("wire", 424242)
    assert procs.alive(424242), "外来进程被终止了"
    assert 424242 not in procs.killed_trees
    assert adapters["wire"].entry_pid() in procs.processes, "自有 wire 进程被误动"


def test_restart_own_occupant_refuses_dead_own_pid(tmp_path: Path) -> None:
    """自有 spawn 但已退出：证明不了「此刻仍存活」，失败关闭不动。"""
    engine, adapters, procs, _clock = build_engine(tmp_path)
    engine.start_all()
    occupant = adapters["wire"].entry_pid()
    assert occupant is not None
    procs.processes.pop(occupant)
    with pytest.raises(SupervisorError, match="无法证明"):
        engine.restart_own_occupant("wire", occupant)
    assert occupant not in procs.killed_trees


def test_restart_own_occupant_refuses_pid_reuse_and_never_kills(tmp_path: Path) -> None:
    """PID 复用（实时命令行指纹与 spawn 时不符）：失败关闭，绝不终止那个活进程。"""
    engine, adapters, procs, _clock = build_engine(tmp_path)
    engine.start_all()
    occupant = adapters["wire"].entry_pid()
    assert occupant is not None
    procs.processes[occupant].cmdline = ("C:/Windows/System32/notepad.exe",)
    with pytest.raises(SupervisorError, match="无法证明"):
        engine.restart_own_occupant("wire", occupant)
    assert procs.alive(occupant), "PID 复用进程被误杀"
    assert occupant not in procs.killed_trees
