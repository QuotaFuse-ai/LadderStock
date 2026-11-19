"""自有实例停止语义：命令行已确认属于本模块的实例一律可停（不再「认领即永不停止」）。

历史语义（QUOT-545）是「外部 peer 认领后任何停止路径跳过」，它让上一代没退干净
的残留永远关不掉，形成「停不住 → 端口冲突 → 起不来」的死循环。现在改为按客观
证据处置：适配器命令行证据命中本模块 → 视为自有，可停、可重启；命令行不匹配的
外部程序仍然绝不终止（由 `_reap_stale` 的 matched 判据与 foreign 分类保证）。

全部走 fake（fakes.py），不起真进程。
"""

from __future__ import annotations

from pathlib import Path

from fakes import build_engine

from ladderstock.supervisor.engine import SupervisorEngine
from ladderstock.supervisor.ledger import HEALTH_OK, Ledger, ModuleRecord
from ladderstock.supervisor.module_adapters import (
    Identification,
    PortEvidence,
    ProcessSnapshot,
)


def _evidence(module: str, pid: int) -> Identification:
    cmdline = (f"C:/venv/{module}.exe", "run")
    snap = ProcessSnapshot(pid=pid, parent_pid=1, name=f"{module}.exe", cmdline=cmdline)
    return Identification(
        module=module,
        matched=True,
        ports=(
            PortEvidence(
                port=0,
                listening=True,
                matched=True,
                pid=pid,
                name=snap.name,
                cmdline=cmdline,
                parent_chain=(snap,),
            ),
        ),
    )


def _adopt_instance(engine, adapters, procs, name: str) -> int:
    """模拟「端口上已有本模块程序」：命令行命中识别规则，按证据认领。"""
    chain = procs.spawn_chain(
        (f"C:/venv/{name}.exe", "run"), ppid=1, ports=adapters[name].ports, depth=1
    )
    adapters[name].identify = lambda: _evidence(name, chain[0])  # type: ignore[attr-defined]
    record = engine.adopt_module(name)
    assert record is not None and record.pid == chain[0]
    assert record.origin == "adopted"
    assert record.adopted_peer is False, "认领不再打「永不停止」标记"
    return chain[0]


def _second_session_engine(tmp_path: Path, adapters, procs, clock) -> SupervisorEngine:
    """同一本账 + 同一批适配器，新引擎实例 = 上一任 supervisor 崩溃后的新会话。"""
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
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


# ------------------------------------------------------- 认领实例：可停可重启


def test_stop_all_stops_adopted_instance(tmp_path: Path) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    pid = _adopt_instance(engine, adapters, procs, "wire")

    report = engine.stop_all()

    assert report["ok"] is True
    assert "skipped" not in report["modules"]["wire"]
    assert adapters["wire"].stop_calls >= 1, "停止路径没有走到适配器"
    assert pid not in procs.processes, "认领的实例没有被停止"
    assert engine._ledger.record("wire").state == "stopped"


def test_stop_module_stops_adopted_instance(tmp_path: Path) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    pid = _adopt_instance(engine, adapters, procs, "wire")

    view = engine.stop_module("wire")

    assert view["ok"] is True
    assert view["record"]["adopted_peer"] is False
    assert pid not in procs.processes


def test_restart_module_replaces_adopted_instance(tmp_path: Path) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    pid = _adopt_instance(engine, adapters, procs, "wire")

    report = engine.restart_module("wire")

    assert report["ok"] is True
    new_pid = report["record"]["pid"]
    assert new_pid != pid, "重启后仍指着旧实例"
    assert adapters["wire"].start_calls, "重启没有拉起新实例"
    assert pid not in procs.processes, "旧实例没有被清掉"


def test_stop_all_stops_owned_and_adopted_together(tmp_path: Path) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    engine.start_module("senate")
    senate_pids = adapters["senate"].chain_pids()
    adopted_pid = _adopt_instance(engine, adapters, procs, "wire")

    report = engine.stop_all()

    assert report["ok"] is True
    for pid in senate_pids:
        assert pid not in procs.processes
    assert adopted_pid not in procs.processes
    assert engine._ledger.record("senate").state == "stopped"
    assert engine._ledger.record("wire").state == "stopped"


# --------------------------------------------- 前任自有进程：崩溃恢复仍可停


def test_previous_supervisor_spawned_process_is_still_stopped(tmp_path: Path) -> None:
    """回归护栏：前任自己拉的进程必须仍可停。"""
    engine_a, adapters, procs, clock = build_engine(tmp_path)
    engine_a.start_module("wire")
    pids = adapters["wire"].chain_pids()
    assert engine_a._ledger.record("wire").origin == "spawned"

    engine_b = _second_session_engine(tmp_path, adapters, procs, clock)
    summary = engine_b.reconcile()
    record = engine_b._ledger.record("wire")
    assert summary["wire"] == "alive:spawned_prev_session"
    assert record.origin == "spawned" and record.adopted_peer is False

    report = engine_b.stop_all()

    assert report["ok"] is True
    assert "skipped" not in report["modules"]["wire"]
    for pid in pids:
        assert pid not in procs.processes, "前任 supervisor 的进程没有被停止"
    assert engine_b._ledger.record("wire").state == "stopped"


def test_adopted_instance_survives_crash_recovery_as_stoppable(tmp_path: Path) -> None:
    """跨会话仍是可停实例：对账续托管不得把「可停」变回「永不停止」。"""
    engine_a, adapters, procs, clock = build_engine(tmp_path)
    pid = _adopt_instance(engine_a, adapters, procs, "wire")

    engine_b = _second_session_engine(tmp_path, adapters, procs, clock)
    summary = engine_b.reconcile()

    assert summary["wire"] in ("adopted:still_running", "alive:adopted_prev_session")
    record = engine_b._ledger.record("wire")
    assert record is not None and record.adopted_peer is False and record.pid == pid
    engine_b.stop_all()
    assert pid not in procs.processes


def test_explicit_spawn_clears_adopt_origin(tmp_path: Path) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    pid = _adopt_instance(engine, adapters, procs, "wire")
    procs.kill_tree(pid)  # 实例自己死了
    adapters["wire"].release_external()

    engine.start_module("wire")  # 显式启动 = 真正 spawn，归属回到本控制面
    record = engine._ledger.record("wire")
    assert record.origin == "spawned" and record.adopted_peer is False
    new_pids = adapters["wire"].chain_pids()

    engine.stop_all()

    for new_pid in new_pids:
        assert new_pid not in procs.processes


# ------------------------------------------------------------- 账本层口径


def test_legacy_record_without_flag_stays_stoppable(tmp_path: Path) -> None:
    """旧账本（无 adopted_peer 字段）读入默认 False：崩溃恢复语义不变。"""
    record = ModuleRecord.from_dict(
        {"module": "wire", "origin": "adopted", "state": "running", "pid": 42}
    )
    assert record.adopted_peer is False


def test_flag_roundtrips_through_disk(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    ledger = Ledger(path)
    ledger.update(
        ModuleRecord(module="wire", pid=42, origin="adopted", adopted_peer=True, state="running")
    )
    reloaded = Ledger(path)
    record = reloaded.record("wire")
    assert record is not None and record.adopted_peer is True


def test_record_restart_success_clears_peer_flag(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.update(
        ModuleRecord(module="wire", pid=42, origin="adopted", adopted_peer=True, state="running")
    )
    entry = ledger.record_restart("wire", ok=True, pid=43, now=1000.0)
    assert entry.adopted_peer is False and entry.pid == 43


def test_reconcile_dead_instance_marks_stopped(tmp_path: Path) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    pid = _adopt_instance(engine, adapters, procs, "wire")
    procs.kill_tree(pid)

    engine.reconcile()

    record = engine._ledger.record("wire")
    assert record.state == "stopped"
    assert record.adopted_peer is False
    assert procs.killed_trees == [pid]  # 只有测试自己杀的那一次
