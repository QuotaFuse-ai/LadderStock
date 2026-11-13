"""QUOT-594 端口自动避让：allocator 纯逻辑 + engine 接入 + ports.yaml 复用。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ladderstock.supervisor.engine import SupervisorError
from ladderstock.supervisor.port_allocator import (
    MAX_SHIFT,
    AllocationFailed,
    PortOverrideStore,
    allocate_ports,
    load_board_override,
    load_port_overrides,
    save_port_overrides,
)

# ---------------------------------------------------------------- allocator 纯逻辑


def test_foreign_occupied_shifts_to_next_free() -> None:
    """foreign 占用 → 避让到 base+1。"""
    # 8000 被占用（不是 own/adopted），8001 空闲
    allocation = allocate_ports(
        (8000,),
        owner_classifier=lambda port: "foreign",
        free_probe=lambda port: port != 8000,
    )
    assert allocation.actual == (8001,)
    assert allocation.shifted


def test_own_and_adopted_do_not_shift() -> None:
    """own_child / adopted_peer 占用 → 保留原端口（正常 adopt，绝不避让）。"""
    for owner in ("own_child", "adopted_peer"):
        allocation = allocate_ports(
            (8000,),
            owner_classifier=lambda port, owner=owner: owner,
            free_probe=lambda port: False,
        )
        assert allocation.actual == (8000,)
        assert not allocation.shifted


def test_consecutive_occupation_keeps_scanning() -> None:
    """连续占用（+1、+2 也被占）→ 继续扫描到首个空闲。"""
    occupied = {8000, 8001, 8002}
    allocation = allocate_ports(
        (8000,),
        owner_classifier=lambda port: "foreign",
        free_probe=lambda port: port not in occupied,
    )
    assert allocation.actual == (8003,)


def test_limit_exhausted_raises_allocation_failed() -> None:
    """上限 +100 仍无空闲 → AllocationFailed（needs_human），绝不无限扫描。"""
    with pytest.raises(AllocationFailed):
        allocate_ports(
            (8000,),
            owner_classifier=lambda port: "foreign",
            free_probe=lambda port: False,
            max_shift=MAX_SHIFT,
        )


def test_multi_port_each_shifts_independently() -> None:
    """多面模块（如 tickhub 8000/8787）：逐端口独立避让。"""
    occupied = {8000, 8787, 8001}
    allocation = allocate_ports(
        (8000, 8787),
        owner_classifier=lambda port: "foreign",
        free_probe=lambda port: port not in occupied,
    )
    assert allocation.actual == (8002, 8788)


def test_free_port_kept_untouched() -> None:
    """空闲端口原样保留，不扫描不换号。"""
    allocation = allocate_ports(
        (8000,),
        owner_classifier=lambda port: "free",
        free_probe=lambda port: True,
    )
    assert allocation.actual == (8000,)
    assert not allocation.shifted


# ---------------------------------------------------------------- ports.yaml 持久化


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    save_port_overrides(tmp_path, 5953, {"tickhub": (8003, 8788), "wire": (8792, 8793)})
    assert load_board_override(tmp_path) == 5953
    assert load_port_overrides(tmp_path) == {
        "tickhub": (8003, 8788),
        "wire": (8792, 8793),
    }


def test_load_missing_or_corrupt_returns_empty(tmp_path: Path) -> None:
    assert load_port_overrides(tmp_path) == {}
    assert load_board_override(tmp_path) is None
    (tmp_path / "ports.yaml").write_text("::: not yaml [", encoding="utf-8")
    assert load_port_overrides(tmp_path) == {}


def test_store_module_ports_falls_back_on_env_switch(tmp_path: Path, monkeypatch: Any) -> None:
    """记录面数与基准表相符但环境切换（8000→18000 系）→ 回落基准表。

    ports.yaml 记 env 键：load 时 env 不一致整段失效，防止 prod 避让结果
    泄漏进 test 环境（QUOT-593 两套并行）。
    """
    import ladderstock.supervisor.port_allocator as allocator_mod

    save_port_overrides(tmp_path, 5950, {"tickhub": (8003, 8788)})
    assert load_port_overrides(tmp_path) == {"tickhub": (8003, 8788)}
    monkeypatch.setattr(allocator_mod, "current_env", lambda: "test")
    assert load_port_overrides(tmp_path) == {}

    store = PortOverrideStore(tmp_path, default_board=5950)
    store.refresh()
    assert store.module_ports("tickhub", (18000, 18787)) == (18000, 18787)


def test_store_module_ports_falls_back_on_length_mismatch(tmp_path: Path) -> None:
    """记录面数与基准表不符（面数变了）→ 回落基准表。"""
    save_port_overrides(tmp_path, 5950, {"tickhub": (8003,)})
    store = PortOverrideStore(tmp_path, default_board=5950)
    store.refresh()
    assert store.module_ports("tickhub", (8000, 8787)) == (8000, 8787)
    assert store.module_ports("tickhub", (8000,)) == (8003,)


def test_store_set_module_persists_for_next_run(tmp_path: Path) -> None:
    store = PortOverrideStore(tmp_path, default_board=5950)
    store.set_module("tickhub_us", (8003,))
    again = PortOverrideStore(tmp_path, default_board=5950)
    again.refresh()
    assert again.module_ports("tickhub_us", (8002,)) == (8003,)
    assert again.board_port() == 5950


def test_store_board_port_override(tmp_path: Path) -> None:
    store = PortOverrideStore(tmp_path, default_board=5950)
    store.set_board(5953)
    again = PortOverrideStore(tmp_path, default_board=5950)
    again.refresh()
    assert again.board_port() == 5953


# ---------------------------------------------------------------- engine 接入


class _StaticAdapter:
    """不走 FakeAdapter 进程链的最小适配器：probe 可控、端口可重写。"""

    def __init__(self, name: str, ports: tuple[int, ...], *, procs: Any = None) -> None:
        self.name = name
        self.ports = ports
        self.rebased_to: tuple[int, ...] | None = None
        self.starts = 0
        self._listening = False
        self._owner: str = "foreign"

    def set_foreign_listening(self, listening: bool) -> None:
        self._listening = listening

    def set_owner(self, owner: str) -> None:
        self._owner = owner

    def start(self) -> Any:
        self.starts += 1
        self._listening = True
        from ladderstock.supervisor.adapters import SpawnedProcess

        return SpawnedProcess(pid=40000 + self.starts, parent_pid=1, cmdline=("fake",))

    def stop(self) -> None:
        self._listening = False

    def kill(self) -> None:
        self._listening = False

    def probe(self) -> bool:
        return self._listening


def _make_engine(tmp_path: Path, adapters: dict[str, Any]) -> Any:
    import ladderstock.supervisor.engine as engine_mod
    from ladderstock.supervisor import Ledger, SupervisorEngine

    procs = _StaticProcs()
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    ledger.load()
    # 测试只注册局部适配器且端口可避让重写，registry 校验不适用：临时替换
    # engine 命名空间里的引用（engine 是 from-import，patch adapters 无效）
    original = engine_mod.module_registry
    engine_mod.module_registry = lambda mapping: dict(mapping)  # type: ignore[assignment]
    try:
        engine = SupervisorEngine(
            adapters=adapters,
            ledger=ledger,
            procs=procs,
            poll_interval_sec=0.25,
        )
    finally:
        engine_mod.module_registry = original
    return engine


class _StaticProcs:
    def self_pid(self) -> int:
        return 1

    def alive(self, pid: int) -> bool:
        return pid >= 40000

    def cmdline(self, pid: int) -> tuple[str, ...] | None:
        return ("fake",) if pid >= 40000 else None

    def tree_pids(self, root_pid: int) -> list[int]:
        return [root_pid]

    def kill_tree(self, root_pid: int) -> None:
        pass


def _avoider(owners: dict[int, str], occupied: set[int], store: PortOverrideStore | None = None):
    """可注入避让器：occupied 里的端口按 owners 分类，其余空闲。"""
    from ladderstock.supervisor.port_allocator import allocate_ports

    def avoid(module: str, base: tuple[int, ...], record: Any) -> tuple[int, ...]:
        allocation = allocate_ports(
            base,
            owner_classifier=lambda port: owners.get(port, "free"),
            free_probe=lambda port: port not in occupied,
        )
        if allocation.shifted and store is not None:
            store.set_module(module, allocation.actual)
        return allocation.actual

    return avoid


def _rebaser(adapter: Any, ports: tuple[int, ...]) -> Any:
    rebased = _StaticAdapter(adapter.name, ports)
    rebased.rebased_to = ports
    return rebased


def _full_adapters(names: dict[str, Any]) -> dict[str, Any]:
    """state() 会遍历 MODULE_ORDER probe 全部模块：补齐缺失模块的哑适配器。"""
    from ladderstock.supervisor.adapters import MODULE_ORDER

    return {name: names.get(name) or _StaticAdapter(name, (0,)) for name in MODULE_ORDER}


def test_engine_foreign_occupied_shifts_and_persists(tmp_path: Path) -> None:
    """foreign 占用 8002 → 引擎避让到 8003 → 账本记实际端口 → ports.yaml 复用。"""
    store = PortOverrideStore(tmp_path / "config", default_board=5950)
    adapter = _StaticAdapter("tickhub_us", (8002,))
    adapter.set_foreign_listening(True)
    adapters = _full_adapters(
        {"tickhub_us": adapter, "tickhub": _StaticAdapter("tickhub", (8000,))}
    )
    engine = _make_engine(tmp_path, adapters)
    engine._port_avoider = _avoider({8002: "foreign"}, {8002}, store)
    engine._adapter_rebaser = _rebaser

    engine.start_module("tickhub_us")

    record = engine._ledger.record("tickhub_us")
    assert record.ports == [8003]
    assert engine._avoided_from["tickhub_us"] == (8002,)
    assert store.module_ports("tickhub_us", (8002,)) == (8003,)


def test_engine_own_child_occupied_refuses(tmp_path: Path) -> None:
    """own_child 占用 → 避让器回原端口 → 维持拒绝拉起（不造双实例）。"""
    adapter = _StaticAdapter("tickhub_us", (8002,))
    adapter.set_foreign_listening(True)
    engine = _make_engine(tmp_path, {"tickhub_us": adapter})
    engine._port_avoider = _avoider({8002: "own_child"}, {8002})
    engine._adapter_rebaser = _rebaser

    with pytest.raises(SupervisorError, match="本方相关进程"):
        engine.start_module("tickhub_us")
    assert adapter.starts == 0


def test_engine_no_avoider_keeps_dec004(tmp_path: Path) -> None:
    """未注入避让器 → DEC-004 原语义：占用即拒绝。"""
    adapter = _StaticAdapter("tickhub_us", (8002,))
    adapter.set_foreign_listening(True)
    engine = _make_engine(tmp_path, {"tickhub_us": adapter})

    with pytest.raises(SupervisorError, match="DEC-004"):
        engine.start_module("tickhub_us")


def test_engine_allocation_failed_surfaces(tmp_path: Path) -> None:
    """上限内无空闲 → SupervisorError（needs_human 由自愈层按 health 判定）。"""
    adapter = _StaticAdapter("tickhub_us", (8002,))
    adapter.set_foreign_listening(True)
    engine = _make_engine(tmp_path, {"tickhub_us": adapter})
    engine._port_avoider = _avoider({8002: "foreign"}, set(range(8002, 8500)))
    engine._adapter_rebaser = _rebaser

    with pytest.raises(SupervisorError, match="避让失败"):
        engine.start_module("tickhub_us")


def test_engine_state_reports_avoided_from(tmp_path: Path) -> None:
    """state 带 avoided_from，前端据此显示「已自动避让」。"""
    store = PortOverrideStore(tmp_path / "config", default_board=5950)
    adapter = _StaticAdapter("tickhub_us", (8002,))
    adapter.set_foreign_listening(True)
    adapters = _full_adapters(
        {"tickhub_us": adapter, "tickhub": _StaticAdapter("tickhub", (8000,))}
    )
    engine = _make_engine(tmp_path, adapters)
    engine._port_avoider = _avoider({8002: "foreign"}, {8002}, store)
    engine._adapter_rebaser = _rebaser

    engine.start_module("tickhub_us")

    state = engine.state()
    modules = {
        name: view
        for name, view in state["modules"].items()
        if name in ("tickhub_us", "tickhub")
    }
    assert modules["tickhub_us"]["avoided_from"] == [8002]
    assert "avoided_from" not in modules["tickhub"]
