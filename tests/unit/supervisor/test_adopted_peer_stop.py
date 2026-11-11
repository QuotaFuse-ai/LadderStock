"""adopted peer 停止边界（QUOT-545）：外部 peer 认领绝不终止，前任自有进程仍可停。

账本以 `adopted_peer` 标记区分两种 adopted：
- 外部 peer 认领（`adopt_module` 证据路径 / PageBoot 记账）→ 停止路径一律跳过；
- 前任 supervisor 遗留自有进程（对账续托管）→ 无标记，stop_all 正常停止。

全部走 fake（fakes.py），不起真进程；真实进程的回归见
`tests/integration/test_stop_all_ownership.py`。
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


def _adopt_external_peer(engine, adapters, procs, name: str) -> int:
    """模拟外部 peer：进程不在适配器链里，按识别证据认领。返回 peer pid。"""
    chain = procs.spawn_chain(
        (f"C:/venv/{name}.exe", "run"), ppid=1, ports=adapters[name].ports, depth=1
    )
    adapters[name].identify = lambda: _evidence(name, chain[0])  # type: ignore[attr-defined]
    record = engine.adopt_module(name)
    assert record is not None and record.pid == chain[0]
    assert record.origin == "adopted" and record.adopted_peer is True
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


# ------------------------------------------------------- 外部 peer：一律跳过


def test_stop_all_skips_adopted_peer_and_keeps_ledger_intact(tmp_path: Path) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    pid = _adopt_external_peer(engine, adapters, procs, "wire")

    report = engine.stop_all()

    assert report["ok"] is True
    assert report["modules"]["wire"]["skipped"] == "adopted_peer"
    assert adapters["wire"].stop_calls == 0, "对外部 peer 调了适配器 stop()"
    assert procs.killed_trees == [], "对外部 peer 动了强杀"
    assert pid in procs.processes, "外部 peer 被终止"
    record = engine._ledger.record("wire")
    assert record is not None
    assert record.pid == pid
    assert record.state == "running", "外部 peer 的账本状态被停止流程改动"
    assert record.stopping_by_user is False, "外部 peer 被误写主动停意图"
    assert record.health == HEALTH_OK


def test_stop_module_skips_adopted_peer(tmp_path: Path) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    pid = _adopt_external_peer(engine, adapters, procs, "wire")

    view = engine.stop_module("wire")

    assert view["ok"] is True
    assert view["record"]["pid"] == pid
    assert view["record"]["adopted_peer"] is True
    assert adapters["wire"].stop_calls == 0
    assert pid in procs.processes


def test_restart_module_keeps_adopted_peer_pid(tmp_path: Path) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    pid = _adopt_external_peer(engine, adapters, procs, "wire")
    adapters["wire"].take_port_externally()  # 端口在听（真实适配器按证据 probe）

    report = engine.restart_module("wire")

    assert report["ok"] is True
    assert report["record"]["pid"] == pid, "重启把外部 peer 换成了新进程"
    assert adapters["wire"].start_calls == [], "重启 spawn 了外部 peer"
    assert adapters["wire"].stop_calls == 0
    assert pid in procs.processes


def test_stop_all_still_stops_owned_modules_alongside_skipped_peer(tmp_path: Path) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    engine.start_module("senate")
    senate_pids = adapters["senate"].chain_pids()
    peer_pid = _adopt_external_peer(engine, adapters, procs, "wire")

    report = engine.stop_all()

    assert report["ok"] is True
    for pid in senate_pids:
        assert pid not in procs.processes, "自有模块没有被停止"
    assert peer_pid in procs.processes, "外部 peer 被连带终止"
    assert engine._ledger.record("senate").state == "stopped"
    assert engine._ledger.record("wire").state == "running"


# --------------------------------------------- 前任自有进程：崩溃恢复仍可停


def test_previous_supervisor_spawned_process_is_still_stopped(tmp_path: Path) -> None:
    """回归护栏：不能「凡 adopted 都跳过」——前任自己拉的进程必须仍可停。"""
    engine_a, adapters, procs, clock = build_engine(tmp_path)
    engine_a.start_module("wire")
    pids = adapters["wire"].chain_pids()
    assert engine_a._ledger.record("wire").origin == "spawned"

    engine_b = _second_session_engine(tmp_path, adapters, procs, clock)
    summary = engine_b.reconcile()
    record = engine_b._ledger.record("wire")
    assert summary["wire"] == "adopted:still_running"
    assert record.origin == "adopted" and record.adopted_peer is False

    report = engine_b.stop_all()

    assert report["ok"] is True
    assert "skipped" not in report["modules"]["wire"]
    for pid in pids:
        assert pid not in procs.processes, "前任 supervisor 的进程没有被停止"
    assert engine_b._ledger.record("wire").state == "stopped"


def test_adopted_peer_flag_survives_crash_recovery(tmp_path: Path) -> None:
    """外部 peer 认领跨会话仍是外部 peer：对账续托管不得洗掉标记。"""
    engine_a, adapters, procs, clock = build_engine(tmp_path)
    pid = _adopt_external_peer(engine_a, adapters, procs, "wire")

    engine_b = _second_session_engine(tmp_path, adapters, procs, clock)
    summary = engine_b.reconcile()

    assert summary["wire"] == "adopted:still_running"
    record = engine_b._ledger.record("wire")
    assert record.adopted_peer is True and record.pid == pid
    engine_b.stop_all()
    assert pid in procs.processes
    assert adapters["wire"].stop_calls == 0


def test_explicit_spawn_clears_peer_flag_and_restores_stoppability(tmp_path: Path) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    pid = _adopt_external_peer(engine, adapters, procs, "wire")
    procs.kill_tree(pid)  # 外部 peer 自己死了
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


def test_reconcile_dead_peer_marks_stopped_without_touching_flag(tmp_path: Path) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    pid = _adopt_external_peer(engine, adapters, procs, "wire")
    procs.kill_tree(pid)

    engine.reconcile()

    record = engine._ledger.record("wire")
    assert record.state == "stopped"
    assert record.adopted_peer is True
    assert procs.killed_trees == [pid]  # 只有测试自己杀的那一次