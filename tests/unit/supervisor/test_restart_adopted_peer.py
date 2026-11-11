"""外部 peer 确认后一键重启的引擎边界（QUOT-556）。

`restart_module` 对 `adopted_peer` 的三态处置：

| peer 状态 | 期望 |
|---|---|
| 进程已死 + 端口空闲 | 拉起替代实例，归属回本控制面（spawned、清标记） |
| 进程活着 + 端口在听 | 幂等续 running：不 spawn、不停止、PID 不变 |
| 进程活着 + 端口未监听（卡死） | **失败关闭**：绝不为了重启终止外部进程 |
| 进程已死 + 端口被外来进程占着 | DEC-004 报错：不换端口、不终止占用者 |

HTTP 层的 `confirm: true` 闸门在 `tests/unit/web/test_supervisor_events_endpoint.py`；
本文件钉引擎层——确认只授权「拉起替代」，任何分支都不产生对外部进程的终止。
全部走 fake（fakes.py），不起真进程。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes import build_engine

from ladderstock.supervisor.engine import SupervisorEngine, SupervisorError
from ladderstock.supervisor.ledger import Ledger, ModuleRecord
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


def test_dead_peer_with_free_port_restarts_into_owned_process(tmp_path: Path) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    old_pid = _adopt_external_peer(engine, adapters, procs, "wire")
    procs.kill_tree(old_pid)  # 外部 peer 自己崩溃了

    report = engine.restart_module("wire")

    assert report["ok"] is True
    record = engine._ledger.record("wire")
    assert record.origin == "spawned", "确认重启后归属必须回到本控制面"
    assert record.adopted_peer is False
    assert record.pid is not None and record.pid != old_pid
    assert record.state == "running"
    assert adapters["wire"].start_calls, "没有真正拉起替代实例"
    for pid in adapters["wire"].chain_pids():
        assert pid in procs.processes


def test_alive_listening_peer_restart_is_idempotent_noop(tmp_path: Path) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    pid = _adopt_external_peer(engine, adapters, procs, "wire")
    adapters["wire"].take_port_externally()  # 端口在听（真实适配器按证据 probe）

    report = engine.restart_module("wire")

    assert report["ok"] is True
    assert report["record"]["pid"] == pid
    assert adapters["wire"].start_calls == []
    assert adapters["wire"].stop_calls == 0
    assert pid in procs.processes
    assert engine._ledger.record("wire").adopted_peer is True


def test_alive_hung_peer_restart_fails_closed_without_kill(tmp_path: Path) -> None:
    """进程活着但端口未监听：绝不为了重启而终止外部进程，也不造双实例。"""
    engine, adapters, procs, _clock = build_engine(tmp_path)
    pid = _adopt_external_peer(engine, adapters, procs, "wire")  # probe=False（链不在适配器里）

    with pytest.raises(SupervisorError, match="不终止外部进程"):
        engine.restart_module("wire")

    assert adapters["wire"].start_calls == [], "卡死 peer 被 spawn 了双实例"
    assert adapters["wire"].stop_calls == 0
    assert procs.killed_trees == [], "卡死 peer 被强杀"
    assert pid in procs.processes, "外部进程被终止"
    record = engine._ledger.record("wire")
    assert record.pid == pid and record.adopted_peer is True
    assert record.state == "running" and record.stopping_by_user is False


def test_dead_peer_with_foreign_port_holder_reports_conflict_without_kill(
    tmp_path: Path,
) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    old_pid = _adopt_external_peer(engine, adapters, procs, "wire")
    procs.kill_tree(old_pid)
    foreign = procs.spawn_chain(
        ("C:/other/app.exe", "serve"), ppid=1, ports=adapters["wire"].ports, depth=1
    )
    adapters["wire"].take_port_externally()

    with pytest.raises(SupervisorError, match="已被其它进程占用"):
        engine.restart_module("wire")

    assert foreign[0] in procs.processes, "外来端口占用者被终止"
    assert procs.killed_trees == [old_pid], "除测试自己杀的那次外发生了其它终止"
    assert adapters["wire"].start_calls == []


def test_owned_module_restart_still_replaces_process(tmp_path: Path) -> None:
    """回归护栏：自有模块的重启语义不受 peer 闸门影响。"""
    engine, adapters, procs, _clock = build_engine(tmp_path)
    engine.start_module("wire")
    old_pids = adapters["wire"].chain_pids()

    report = engine.restart_module("wire")

    assert report["ok"] is True
    for pid in old_pids:
        assert pid not in procs.processes
    for pid in adapters["wire"].chain_pids():
        assert pid in procs.processes
    assert engine._ledger.record("wire").origin == "spawned"


def test_legacy_adopted_without_peer_flag_restarts_without_engine_gate(
    tmp_path: Path,
) -> None:
    """旧账本（origin=adopted、无 adopted_peer 标记）：引擎层不设闸。

    HTTP 层仍按 DEC-079 失败关闭要 confirm；引擎层只认 adopted_peer 标记，
    前任自有进程的崩溃恢复语义不变。
    """
    engine, adapters, procs, _clock = build_engine(tmp_path)
    engine._ledger.update(
        ModuleRecord(
            module="wire",
            ports=list(adapters["wire"].ports),
            pid=424242,
            origin="adopted",
            adopted_peer=False,
            state="stopped",
        )
    )

    report = engine.restart_module("wire")

    assert report["ok"] is True
    record = engine._ledger.record("wire")
    assert record.origin == "spawned" and record.adopted_peer is False
    assert adapters["wire"].start_calls


def test_second_session_dead_peer_restart_keeps_other_modules_intact(tmp_path: Path) -> None:
    """崩溃恢复后的新会话：确认重启死掉的外部 peer，不牵连其它模块。"""
    engine_a, adapters, procs, clock = build_engine(tmp_path)
    engine_a.start_module("senate")
    senate_pids = adapters["senate"].chain_pids()
    old_pid = _adopt_external_peer(engine_a, adapters, procs, "wire")
    procs.kill_tree(old_pid)

    engine_b = SupervisorEngine(
        adapters=adapters,
        ledger=Ledger(tmp_path / "supervisor" / "ledger.json"),
        procs=procs,
        start_grace_sec=1.0,
        stop_grace_sec=1.0,
        kill_grace_sec=1.0,
        poll_interval_sec=0.25,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    engine_b.reconcile()

    report = engine_b.restart_module("wire")

    assert report["ok"] is True and report["others_unchanged"] is True
    for pid in senate_pids:
        assert pid in procs.processes, "重启 peer 牵连了其它模块"
    assert engine_b._ledger.record("wire").origin == "spawned"