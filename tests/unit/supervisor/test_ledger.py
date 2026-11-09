"""进程账本测试（QUOT-482-A）：原子落盘、损坏自愈、恢复对账三态。"""

from __future__ import annotations

import json
from pathlib import Path

from ladderstock.supervisor.ledger import (
    MODULE_STATES,
    STOP_BY_REQUEST,
    STOP_PID_REUSE,
    STOP_PROCESS_DEAD,
    Ledger,
    ModuleRecord,
)


def make_record(module: str = "wire", **overrides: object) -> ModuleRecord:
    fields: dict[str, object] = {
        "module": module,
        "ports": [8790, 8791],
        "pid": 4242,
        "parent_pid": 99999,
        "started_at": "2026-11-09T00:00:00+00:00",
        "origin": "spawned",
        "cmdline_hash": "ab" * 32,
        "state": "running",
    }
    fields.update(overrides)
    return ModuleRecord(**fields)  # type: ignore[arg-type]


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    ledger.update(make_record())
    ledger.update(make_record("tickhub", pid=111, ports=[8000, 8787]))

    restored = Ledger(tmp_path / "supervisor" / "ledger.json")
    restored.load()
    record = restored.record("wire")
    assert record is not None
    assert record.module == "wire"
    assert record.ports == [8790, 8791]
    assert record.pid == 4242
    assert record.parent_pid == 99999
    assert record.origin == "spawned"
    assert record.cmdline_hash == "ab" * 32
    assert record.state == "running"
    assert restored.record("tickhub") is not None


def test_save_leaves_no_tmp_file_behind(tmp_path: Path) -> None:
    path = tmp_path / "supervisor" / "ledger.json"
    ledger = Ledger(path)
    ledger.update(make_record())
    ledger.save()
    assert path.is_file()
    assert list(tmp_path.glob("*.tmp")) == []
    assert list((tmp_path / "supervisor").glob("*.tmp")) == []


def test_load_missing_file_starts_empty(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    ledger.load()
    assert ledger.records() == {}


def test_load_corrupt_json_quarantines_and_starts_empty(tmp_path: Path) -> None:
    path = tmp_path / "supervisor" / "ledger.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not valid json", encoding="utf-8")
    ledger = Ledger(path)
    ledger.load()
    assert ledger.records() == {}
    assert path.with_suffix(".json.corrupt").is_file()
    assert not path.is_file()


def test_load_non_dict_json_quarantines(tmp_path: Path) -> None:
    path = tmp_path / "supervisor" / "ledger.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(["wire", "tickhub"]), encoding="utf-8")
    ledger = Ledger(path)
    ledger.load()
    assert ledger.records() == {}
    assert path.with_suffix(".json.corrupt").is_file()


def test_load_skips_entries_without_module_name(tmp_path: Path) -> None:
    path = tmp_path / "supervisor" / "ledger.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"": {"state": "running"}, "wire": {"module": "wire"}}), encoding="utf-8"
    )
    ledger = Ledger(path)
    ledger.load()
    assert set(ledger.records()) == {"wire"}


def test_from_dict_falls_back_on_bad_state_and_origin(tmp_path: Path) -> None:
    record = ModuleRecord.from_dict({"module": "wire", "state": "exploded", "origin": "aliens"})
    assert record.state == "failed"
    assert record.origin == "spawned"
    assert set(MODULE_STATES) == {"starting", "running", "stopping", "stopped", "failed"}


def test_reconcile_dead_process_marks_stopped(tmp_path: Path) -> None:
    """判过证据 4 的账本面：账本说 running、进程已死 → stopped，绝不误判。"""
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    ledger.update(make_record())
    summary = ledger.reconcile(lambda record: "dead")
    assert summary == {"wire": "stopped:process_dead"}
    record = ledger.record("wire")
    assert record is not None
    assert record.state == "stopped"
    assert record.stop_reason == STOP_PROCESS_DEAD
    assert record.stopped_at is not None


def test_reconcile_alive_process_gets_adopted(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    ledger.update(make_record())
    summary = ledger.reconcile(lambda record: "alive")
    assert summary == {"wire": "adopted:still_running"}
    record = ledger.record("wire")
    assert record is not None
    assert record.state == "running"
    assert record.origin == "adopted"


def test_reconcile_keeps_spawned_origin_for_this_session(tmp_path: Path) -> None:
    """本任 supervisor 自己拉起的进程：对账后 origin 保持 spawned，不冒充 adopted。"""
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    ledger.update(make_record())
    ledger.reconcile(lambda record: "alive", spawned_this_session={"wire"})
    record = ledger.record("wire")
    assert record is not None
    assert record.state == "running"
    assert record.origin == "spawned"


def test_reconcile_pid_reuse_marks_stopped_without_ownership(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    ledger.update(make_record())
    summary = ledger.reconcile(lambda record: "reuse")
    assert summary == {"wire": "stopped:suspected_pid_reuse"}
    record = ledger.record("wire")
    assert record is not None
    assert record.state == "stopped"
    assert record.stop_reason == STOP_PID_REUSE


def test_reconcile_leaves_terminal_states_alone(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    ledger.update(make_record("wire", state="stopped", stop_reason=STOP_BY_REQUEST))
    ledger.update(make_record("tickhub", state="failed", stop_reason="start_timeout"))
    summary = ledger.reconcile(lambda record: "alive")
    assert summary == {"wire": "kept:stopped", "tickhub": "kept:failed"}
    wire = ledger.record("wire")
    assert wire is not None and wire.state == "stopped"
    tickhub = ledger.record("tickhub")
    assert tickhub is not None and tickhub.state == "failed"


def test_reconcile_persists_results(tmp_path: Path) -> None:
    path = tmp_path / "supervisor" / "ledger.json"
    ledger = Ledger(path)
    ledger.update(make_record())
    ledger.reconcile(lambda record: "dead", at="2026-11-09T01:02:03+00:00")
    restored = Ledger(path)
    restored.load()
    record = restored.record("wire")
    assert record is not None
    assert record.state == "stopped"
    assert record.stopped_at == "2026-11-09T01:02:03+00:00"
