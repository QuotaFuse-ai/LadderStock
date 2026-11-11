"""端口冲突安全处置端到端（QUOT-557 / S4）：真实临时进程 + 生产事件装配。

验收场景（派单正文）：
- 外来进程占口 → 诊断 foreign → 发 port_conflict 事件（带占用者分类）供 S5 弹窗，
  绝不终止外来进程、绝不自动换端口（DEC-004 / DEC-014 / QUOT-545 红线回归）。
- 自有残留进程占口 → 诊断 own_child → 安全重启解冲突：PID 换新、端口重新监听、
  旧自有进程退出；随后 recovered 事件。

复用 `test_stop_all_ownership` 的 World（临时端口 + 生产端口红线守卫 + 兜底清理）。
事件通道走生产装配：`build_port_inspector(spawned_pids=…)` 真 psutil 扫描 +
`_own_conflict_restarter(engine)` 真重绑重启；自愈 tick 先观测再处置（QUOT-556 口径）。
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest
from test_stop_all_ownership import BOUND_SCRIPT, World

from ladderstock.supervisor.engine import SupervisorEngine
from ladderstock.supervisor.health_events import (
    EVENT_PORT_CONFLICT,
    EVENT_RECOVERED,
    OWNER_FOREIGN,
    OWNER_OWN_CHILD,
    HealthEventBus,
)
from ladderstock.supervisor.restart import AdapterRestarter, pid_alive
from ladderstock.supervisor.selfheal import RestartOutcome, SelfHealEngine
from ladderstock.web.supervisor_api import _own_conflict_restarter, build_port_inspector


def _heal(
    world: World, engine: SupervisorEngine, *, spawn: bool
) -> tuple[SelfHealEngine, HealthEventBus, list[tuple[str, int]]]:
    """生产装配 + 处置通道探针：记录每一次安全重启调用（module, 占用者 pid）。"""
    bus = HealthEventBus()
    calls: list[tuple[str, int]] = []
    real = _own_conflict_restarter(engine)

    def spy(module: str, pid: int) -> RestartOutcome:
        calls.append((module, pid))
        return real(module, pid)

    heal = SelfHealEngine(
        world.ledger_a,
        AdapterRestarter(engine, spawn=spawn),
        sleeper=lambda _seconds: None,  # 退避不真等；窗口记账仍走真时钟
        backoff=(0.0, 0.0, 0.0),
        event_bus=bus,
        port_inspector=build_port_inspector(spawned_pids=lambda: engine.spawned_pids),
        port_probe_interval=0.0,  # 每 tick 都真扫端口：恢复判定不受节流延迟（生产默认 5s）
        port_conflict_restarter=spy,
        is_pid_alive=pid_alive,
    )
    return heal, bus, calls


def _start_stranger(world: World, name: str) -> subprocess.Popen[str]:
    """起一个真正外来的进程占住 name 的端口：命令行 token 与 name 不同，既不在
    supervisor spawn 账里、也不被 name 的适配器识别（双重保证绝不致被误杀）。"""
    port = world.ports[name][0]
    process = subprocess.Popen(
        [sys.executable, "-c", BOUND_SCRIPT, f"quot557-stranger-{name}", str(port)],
        cwd=world.tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    world.external.append(process)
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if world.host.listening(port) and world.host.owner_pid(port) == process.pid:
            return process
        time.sleep(0.1)
    pytest.fail(f"30s 内外来进程 pid={process.pid} 没有占住端口 {port}")


def _dead_pid() -> int:
    """一个确定已退出的 pid（账本漂移用）：即便被复用，命令行指纹也对不上自有账。"""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=10)
    return process.pid


def test_foreign_occupation_reports_and_never_kills(world: World) -> None:
    """外来进程占口：port_conflict 事件（foreign + 占用者 pid）供弹窗；外来进程
    全程存活、绝不被自动处置、账本 pid 不被换绑（DEC-014 / QUOT-545 红线回归）。"""
    world.engine_a.start_module("wire")
    record = world.ledger_a.record("wire")
    assert record is not None and record.pid is not None
    own_pid = record.pid
    # 自有进程崩溃退出，端口被一个真正外来的进程抢占
    world.host.terminate_tree(own_pid, force=True)
    world.await_pid_gone(own_pid)
    stranger = _start_stranger(world, "wire")

    heal, bus, calls = _heal(world, world.engine_a, spawn=False)
    heal.tick()
    heal.tick()

    events = bus.since(0)
    assert [e.event for e in events] == [EVENT_PORT_CONFLICT], (
        f"events={[e.event for e in events]}"
    )
    conflict = events[0]
    assert conflict.module == "wire"
    assert conflict.conflicts[0]["owner"] == OWNER_FOREIGN
    assert conflict.conflicts[0]["pid"] == stranger.pid
    assert "不会终止" in conflict.reason
    assert calls == [], "外来冲突被自动处置了（红线）"
    assert stranger.poll() is None, f"外来进程 pid={stranger.pid} 被终止了（红线）"
    assert world.ledger_a.record("wire").pid == own_pid, "外来冲突换绑了账本 pid"


def test_own_residual_occupation_safe_restart_recovers(world: World) -> None:
    """自有残留进程占口（账本 pid 漂移到已死 pid）：own_child 冲突 → 安全重启解冲突，
    PID 换新、端口重新监听、旧自有进程退出；随后 recovered 事件。"""
    world.engine_a.start_module("wire")
    record = world.ledger_a.record("wire")
    assert record is not None and record.pid is not None
    own_pid = record.pid
    # 账本漂移：wire 的 pid 指向已死 pid，残留自有进程 own_pid 仍占着端口
    world.ledger_a.update("wire", pid=_dead_pid())

    heal, bus, calls = _heal(world, world.engine_a, spawn=True)
    heal.tick()  # 观测 own_child 冲突 → 处置：重绑 + 安全重启（先于崩溃巡检）

    assert calls == [("wire", own_pid)], f"处置通道调用异常：{calls}"
    new_record = world.ledger_a.record("wire")
    assert new_record is not None and new_record.pid is not None
    assert new_record.pid != own_pid, "own_child 重拉后 PID 必须换新"
    assert new_record.origin == "spawned" and new_record.adopted_peer is False
    assert world.adapters_a["wire"].probe() is True
    world.await_pid_gone(own_pid)

    events = bus.since(0)
    assert events[0].event == EVENT_PORT_CONFLICT
    assert events[0].conflicts[0]["owner"] == OWNER_OWN_CHILD
    assert events[0].conflicts[0]["pid"] == own_pid

    heal.tick()  # 确认新进程健康 → recovered
    assert [e.event for e in bus.since(0)] == [EVENT_PORT_CONFLICT, EVENT_RECOVERED]