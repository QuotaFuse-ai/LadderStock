"""冷启动前的「认领」与 bundle 命令表重拉通道（QUOT-526）。

- `SupervisorEngine.adopt_module()`：端口监听者命中识别规则 → 记账为 adopted，
  pid 不变、不 spawn、不杀；外部占口 / 读不到证据一律不认（失败关闭）。
- `AdapterRestarter`：先认领、再拉起；附加模式只认领；引擎报错折叠成失败结论。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fakes import build_engine

from ladderstock.supervisor.engine import SupervisorError
from ladderstock.supervisor.ledger import ModuleRecord
from ladderstock.supervisor.module_adapters import (
    Identification,
    PortEvidence,
    ProcessSnapshot,
)
from ladderstock.supervisor.restart import AdapterRestarter


def _evidence(module: str, pid: int, *, matched: bool = True) -> Identification:
    cmdline = (f"C:/venv/{module}.exe", "run")
    snap = ProcessSnapshot(pid=pid, parent_pid=1, name=f"{module}.exe", cmdline=cmdline)
    return Identification(
        module=module,
        matched=matched,
        ports=(
            PortEvidence(
                port=0,
                listening=True,
                matched=matched,
                pid=pid,
                name=snap.name,
                cmdline=cmdline,
                parent_chain=(snap,),
            ),
        ),
    )


# ------------------------------------------------------------------ adopt_module


def test_adopt_module_records_matching_listener_without_spawn(tmp_path: Path) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    chain = procs.spawn_chain(
        ("C:/venv/wire.exe", "run"), ppid=1, ports=adapters["wire"].ports, depth=1
    )
    adapters["wire"].identify = lambda: _evidence("wire", chain[0])  # type: ignore[attr-defined]

    record = engine.adopt_module("wire")

    assert record is not None
    assert record.pid == chain[0]
    assert record.origin == "adopted"
    assert record.state == "running"
    assert record.cmdline_hash
    assert adapters["wire"].start_calls == []  # 认领不 spawn
    assert procs.killed_trees == []

    again = engine.adopt_module("wire")
    assert again is not None and again.pid == chain[0]  # 幂等，pid 不变


def test_adopt_module_refuses_foreign_occupant(tmp_path: Path) -> None:
    engine, adapters, procs, _clock = build_engine(tmp_path)
    outside = procs.spawn_chain(("C:/Windows/notepad.exe",), ppid=1, ports=(8790,), depth=1)[0]
    adapters["wire"].identify = lambda: _evidence("wire", outside, matched=False)  # type: ignore[attr-defined]
    before = dict(procs.processes)

    assert engine.adopt_module("wire") is None

    assert dict(procs.processes) == before  # 不杀、不动
    assert engine._ledger.record("wire") is None
    assert adapters["wire"].start_calls == []


def test_adopt_module_fails_closed_without_identify(tmp_path: Path) -> None:
    engine, _adapters, _procs, _clock = build_engine(tmp_path)

    assert engine.adopt_module("wire") is None


def test_adopt_module_unknown_module_raises(tmp_path: Path) -> None:
    engine, _adapters, _procs, _clock = build_engine(tmp_path)

    with pytest.raises(SupervisorError, match="未知模块"):
        engine.adopt_module("nope")


# --------------------------------------------------------------- AdapterRestarter


class StubEngine:
    """只实现 restarter 用到的两个入口，记录调用。"""

    def __init__(
        self,
        *,
        adopted: ModuleRecord | None = None,
        start_error: Exception | None = None,
    ) -> None:
        self.adopted = adopted
        self.start_error = start_error
        self.start_calls: list[str] = []

    def adopt_module(self, module: str) -> ModuleRecord | None:
        return self.adopted

    def start_module(self, module: str) -> dict[str, Any]:
        if self.start_error is not None:
            raise self.start_error
        self.start_calls.append(module)
        return {"module": module, "ok": True, "record": {"pid": 777}}


def test_restarter_adopts_running_instance_first() -> None:
    engine = StubEngine(adopted=ModuleRecord(module="wire", pid=321))
    restarter = AdapterRestarter(engine)  # type: ignore[arg-type]

    outcome = restarter.restart("wire")

    assert outcome.ok is True
    assert outcome.pid == 321
    assert outcome.adopted is True
    assert engine.start_calls == []


def test_restarter_starts_via_bundle_command_table() -> None:
    engine = StubEngine()
    restarter = AdapterRestarter(engine)  # type: ignore[arg-type]

    outcome = restarter.restart("wire")

    assert outcome.ok is True
    assert outcome.pid == 777
    assert outcome.adopted is False
    assert engine.start_calls == ["wire"]


def test_restarter_in_attach_mode_never_spawns() -> None:
    engine = StubEngine()
    restarter = AdapterRestarter(engine, spawn=False)  # type: ignore[arg-type]

    outcome = restarter.restart("wire")

    assert outcome.ok is False
    assert "附加模式" in outcome.detail
    assert engine.start_calls == []


def test_restarter_folds_engine_errors_into_failure() -> None:
    engine = StubEngine(start_error=SupervisorError("端口已被其它进程占用"))
    restarter = AdapterRestarter(engine)  # type: ignore[arg-type]

    outcome = restarter.restart("wire")

    assert outcome.ok is False
    assert "端口已被其它进程占用" in outcome.detail


def test_restarter_guards_unexpected_exceptions() -> None:
    engine = StubEngine(start_error=RuntimeError("宿主查询炸了"))
    restarter = AdapterRestarter(engine)  # type: ignore[arg-type]

    outcome = restarter.restart("wire")

    assert outcome.ok is False
    assert "重拉调用异常" in outcome.detail


def test_restarter_known_modules_cover_module_order() -> None:
    from ladderstock.supervisor.adapters import MODULE_ORDER

    assert AdapterRestarter(StubEngine()).known_modules == MODULE_ORDER  # type: ignore[arg-type]
