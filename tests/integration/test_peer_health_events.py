"""peer 健康事件通道端到端（QUOT-556）：真实临时进程 + 生产事件装配。

验收场景（派单正文）：kill 一个 adopted / owned peer → crash 事件发出 →
（确认后）重启 → recovered 事件；同场无关的外部 peer 全程 PID 不变、不被终止
（回归 QUOT-545 / 546 的 adopt-only 红线）。

复用 `test_stop_all_ownership` 的 World（临时端口 + 生产端口红线守卫 + 兜底清理）；
事件通道走生产装配（`HealthEventBus` + `build_port_inspector` 真 psutil 扫描），
自愈重拉走 `AdapterRestarter`：附加模式 spawn=False（只认领）、单入口 spawn=True。
HTTP 层 `confirm: true` 闸门钉在 `tests/unit/web/test_supervisor_events_endpoint.py`，
此处直接调引擎重启 = 「用户已确认」之后的路径。
"""

from __future__ import annotations

from test_stop_all_ownership import World

from ladderstock.supervisor.engine import SupervisorEngine
from ladderstock.supervisor.health_events import (
    EVENT_CRASH,
    EVENT_RECOVERED,
    HealthEventBus,
)
from ladderstock.supervisor.restart import AdapterRestarter
from ladderstock.supervisor.selfheal import SelfHealEngine
from ladderstock.web.supervisor_api import build_port_inspector


def _heal(
    world: World, engine: SupervisorEngine, *, spawn: bool
) -> tuple[SelfHealEngine, HealthEventBus]:
    bus = HealthEventBus()
    heal = SelfHealEngine(
        world.ledger_a,
        AdapterRestarter(engine, spawn=spawn),
        sleeper=lambda _seconds: None,  # 退避不真等；窗口记账仍走真时钟
        event_bus=bus,
        port_inspector=build_port_inspector(),
    )
    return heal, bus


def test_adopted_peer_crash_confirmed_restart_recovers(world: World) -> None:
    """外部 peer 崩溃 → crash 事件 → 确认重启（spawn 替代）→ recovered；
    同场另一个外部 peer 全程原样存活。附加模式自愈绝不自动拉起。"""
    peer = world.start_external_peer("tickhub")
    bystander = world.start_external_peer("wire")
    assert world.engine_a.adopt_module("tickhub") is not None
    assert world.engine_a.adopt_module("wire") is not None
    heal, bus = _heal(world, world.engine_a, spawn=False)

    heal.tick()
    assert bus.since(0) == [], "健康基线不该发事件"

    peer.terminate()
    peer.wait(timeout=10)
    world.await_gone("tickhub", peer.pid)

    heal.tick()
    events = bus.since(0)
    assert [item.event for item in events] == [EVENT_CRASH]
    assert events[0].module == "tickhub"
    assert events[0].adopted_peer is True
    assert events[0].reason
    assert world.ledger_a.record("tickhub").pid == peer.pid, "附加模式自愈换绑了 peer 的 pid"
    assert bystander.poll() is None, "无关外部 peer 被终止"

    report = world.engine_a.restart_module("tickhub")  # = 用户确认后的重启路径
    assert report["ok"] is True, report
    record = world.ledger_a.record("tickhub")
    assert record is not None and record.pid is not None
    assert record.pid != peer.pid
    assert record.origin == "spawned" and record.adopted_peer is False

    heal.tick()
    kinds = [item.event for item in bus.since(0)]
    assert kinds == [EVENT_CRASH, EVENT_RECOVERED]
    assert bus.since(0)[-1].pid == record.pid
    assert bystander.poll() is None, "重启 tickhub 牵连了无关外部 peer"
    assert world.ledger_a.record("wire").pid == bystander.pid


def test_owned_peer_crash_selfheal_recovers(world: World) -> None:
    """自有 peer 崩溃 → crash 事件 → 526 自愈自动重拉 → recovered 事件。"""
    world.engine_a.start_module("wire")
    record = world.ledger_a.record("wire")
    assert record is not None and record.pid is not None
    assert record.origin == "spawned" and record.adopted_peer is False
    old_pid = record.pid
    heal, bus = _heal(world, world.engine_a, spawn=True)

    heal.tick()
    assert bus.since(0) == []

    world.host.terminate_tree(old_pid, force=True)
    world.await_pid_gone(old_pid)

    heal.tick()  # 先观测（crash）再处置（重拉替代实例）
    heal.tick()  # 确认新进程活着 → recovered
    events = bus.since(0)
    assert [item.event for item in events] == [EVENT_CRASH, EVENT_RECOVERED], (
        f"events={[item.event for item in events]} record={world.ledger_a.record('wire')}"
    )
    assert events[0].module == "wire" and events[0].adopted_peer is False
    new_record = world.ledger_a.record("wire")
    assert new_record is not None and new_record.pid is not None
    assert new_record.pid != old_pid, "自有 peer 重拉后 PID 必须换新"
    assert events[-1].pid == new_record.pid
    assert world.adapters_a["wire"].probe() is True