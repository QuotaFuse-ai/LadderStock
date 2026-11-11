"""`SupervisorEngine.stop_owned`（QUOT-527）：应用退出只停自有子进程。

托盘退出 / 服务停止走这里；adopted peer（账本是 alive 但 origin=adopted）
必须原样保留——不终止、不重启、账本状态不动。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fakes import FakeAdapter, build_engine

from ladderstock.supervisor import HEALTH_OK
from ladderstock.supervisor.ledger import ModuleRecord, now_iso
from ladderstock.supervisor.proctree import cmdline_hash


def _seed_adopted(engine: Any, adapter: FakeAdapter) -> list[int]:
    """直接在账本里种一条 adopted 记录（模拟上一任 supervisor 留下的活进程）。"""
    spawned = adapter.start()
    engine._ledger.update(
        ModuleRecord(
            module=adapter.name,
            ports=list(adapter.ports),
            pid=spawned.pid,
            parent_pid=spawned.parent_pid,
            started_at=now_iso(),
            origin="adopted",
            cmdline_hash=cmdline_hash(spawned.cmdline),
            state="running",
            health=HEALTH_OK,
        )
    )
    return adapter.chain_pids()


def test_stop_owned_stops_spawned_children_and_keeps_adopted_peer(
    tmp_path: Path,
) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    engine.start_module("wire")
    owned_pids = adapters["wire"].chain_pids()

    adopted_pids = _seed_adopted(engine, adapters["tickhub"])

    payload = engine.stop_owned()

    assert payload["ok"] is True
    assert payload["kept_adopted"] == ["tickhub"]
    assert engine._ledger.record("wire").state == "stopped"
    assert not any(procs.alive(pid) for pid in owned_pids)
    assert adapters["wire"].stop_calls == 1

    # adopted peer 从头到尾没被碰过
    assert adapters["tickhub"].stop_calls == 0
    assert all(procs.alive(pid) for pid in adopted_pids)
    kept = engine._ledger.record("tickhub")
    assert kept.state == "running"
    assert kept.pid == adopted_pids[0]


def test_stop_owned_is_safe_to_repeat(tmp_path: Path) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    engine.start_module("wire")
    adopted_pids = _seed_adopted(engine, adapters["tickhub"])

    first = engine.stop_owned()
    second = engine.stop_owned()

    assert first["ok"] is True and second["ok"] is True
    assert second["kept_adopted"] == ["tickhub"]
    assert adapters["wire"].stop_calls == 1  # 第二次不再重复停
    assert second["modules"]["wire"]["skipped"] == "stopped"
    assert all(procs.alive(pid) for pid in adopted_pids)


def test_stop_owned_skips_modules_never_started(tmp_path: Path) -> None:
    engine, adapters, _procs, _clock = build_engine(tmp_path)
    engine.start_module("tickhub")

    payload = engine.stop_owned()

    assert payload["ok"] is True
    assert payload["kept_adopted"] == []
    assert payload["modules"]["wire"]["skipped"] == "no_record"
    assert adapters["tickhub"].stop_calls == 1
    for name in ("wire", "senate", "financecoffer", "proxyfleet", "watch"):
        assert adapters[name].stop_calls == 0
        assert adapters[name].start_calls == []


def test_stop_owned_does_not_list_stopped_adopted_as_kept(tmp_path: Path) -> None:
    engine, adapters, _procs, _clock = build_engine(tmp_path)
    engine._ledger.update(
        ModuleRecord(
            module="tickhub",
            ports=list(adapters["tickhub"].ports),
            origin="adopted",
            state="stopped",
        )
    )

    payload = engine.stop_owned()

    assert payload["kept_adopted"] == []
    assert payload["modules"]["tickhub"]["skipped"] == "adopted"
    assert adapters["tickhub"].stop_calls == 0
