"""停服生命周期验收（QUOT-515）：真实宿主、真实端口、真实进程。

QUOT-492 拆出的第 2 段，依赖 QUOT-514 把「10 个端口怎么起、怎么判在听」写好。
本文件**直接复用 514 的 world fixture 与启动/判听助手**（不另写一套启动逻辑），
在此之上只加「停服 → 断言释放」这一段：调用停止流程后，对本测试拉起的每个端口
断言三件事——TCP 建连被拒（或明确超时无响应）、端口归属 PID 查不到（无进程占用）、
启动时记录的 PID 集合停服后全部不在（零残留子进程）。幂等起停 3 轮全绿。

平台门：仅 Windows 执行（CI 是 ubuntu，整模块 skip，沿用 514 / test_control_acl.py
的 WINDOWS_ONLY 先例）。这是模块级平台门，不是用例内兜底：Windows 上本文件没有
任何 skip 分支，停不净一律 fail。部署前提与 514 相同（六 peer 就位、管理员权限）。

派单红线：
- 等待一律「轮询 + 超时（≤60s）」，无固定硬等；
- 停服失败消息固定口径：「端口 X 仍在监听，PID=…」；
- 本单只做停服断言；冷启（514）与端口冲突（516）不在范围；
- 不改业务源码。

既有进程保护（与 514 一致）：setup 时已命中关键字的在听端口记为 adopted（QUOT-589
起本套件固定 select_env("test")，端口表即测试环境 18000 系；若仍出现非测试端口
在听命中，setup 直接 fail 拒绝继续），全程不终止；停服只停本测试拉起的面，
结束后 adopted 端口必须原样
保留（PID 都不许变）。因此「10 端口全释放」的口径是：本测试拉起的端口全部释放，
adopted 生产端口原样保留——与 514 的无残留断言同一责任模型。QUOT-589 端口隔离
修正：financecoffer/proxyfleet 换 fake 模块适配器，全部拉起面都是直接 spawn 的
子进程，零残留 PID 集收录所有拉起面。
"""

from __future__ import annotations

import time

import pytest

from ops.test_lifecycle_cold_start import (
    POLL_INTERVAL_SEC,
    POLL_TIMEOUT_SEC,
    WINDOWS_ONLY,
    PortPlan,
    Started,
    World,
    _await_listening,
    _evidence,
    _start_missing,
    _tcp_ok,
)

pytestmark = WINDOWS_ONLY


def _stop_started(world: World, started: list[Started]) -> None:
    """被测动作：按 514 的责任模型停止本次拉起（全部直接 spawn，整树杀）。"""
    for item in reversed(started):
        for pid in item.pids:
            world.host.terminate_tree(pid, force=True)


def _port_plan(world: World, port: int) -> PortPlan:
    return next(plan for plan in world.plans if plan.port == port)


def _await_released(world: World, started: list[Started], owned_pids: set[int]) -> None:
    """轮询直到：拉起端口 TCP 拒绝且归属 PID 查不到、记录 PID 全不在。超时按口径 fail。"""
    started_ports = [port for item in started for port in item.ports]
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    while True:
        still_listening: list[tuple[int, int | None]] = []
        for port in started_ports:
            ev = _evidence(world, _port_plan(world, port))
            if _tcp_ok(port) or ev.listening:
                still_listening.append((port, ev.pid))
        residual = [pid for pid in sorted(owned_pids) if world.host.snapshot(pid) is not None]
        if not still_listening and not residual:
            return
        if time.monotonic() >= deadline:
            msgs = [f"端口 {port} 仍在监听，PID={pid}" for port, pid in still_listening]
            msgs += [f"残留子进程 PID={pid}" for pid in residual]
            pytest.fail("停服后未释放：" + "；".join(msgs))
        time.sleep(POLL_INTERVAL_SEC)


def _assert_adopted_untouched(world: World) -> None:
    for plan in world.plans:
        if plan.adopt_pid is None:
            continue
        ev = _evidence(world, plan)
        assert ev.listening and ev.matched and ev.pid == plan.adopt_pid, (
            f"adopted 进程被改动：端口 {plan.port} 期望 PID={plan.adopt_pid} / "
            f"实际 PID={ev.pid} listening={ev.listening} matched={ev.matched}"
        )


def _finalize(world: World, started: list[Started], owned_pids: set[int]) -> None:
    """finally 安全网：尽力清掉仍活着的拉起进程（幂等），失败路径也不留残留。"""
    for item in reversed(started):
        try:
            for pid in item.pids:
                world.host.terminate_tree(pid, force=True)
        except Exception:
            pass
    for pid in owned_pids:
        try:
            if world.host.snapshot(pid) is not None:
                world.host.terminate_tree(pid, force=True)
        except Exception:
            pass


@pytest.mark.parametrize("cycle", [1, 2, 3])
def test_stop_lifecycle(world: World, cycle: int) -> None:
    """一轮 = 起缺失面 → 10 端口在听 → 停服 → 拉起端口全释放且零残留、adopted 原样。"""
    started: list[Started] = []
    owned_pids: set[int] = set()
    try:
        started = _start_missing(world)
        for item in started:
            owned_pids.update(item.pids)
        started_ports: list[int] = []
        for plan in world.plans:
            ev = _await_listening(world, plan)
            if plan.adopt_pid is None:
                started_ports.append(plan.port)
                # 全部拉起面都是直接 spawn 的子进程（QUOT-589 隔离修正后不再有
                # 形态专属适配器面），监听归属 PID 全部纳入零残留集合。
                if ev.pid is not None:
                    owned_pids.add(ev.pid)
        print(f"[cycle {cycle}] precondition: 10 ports listening, owned_pids={sorted(owned_pids)}")
        _stop_started(world, started)
        _await_released(world, started, owned_pids)
        for port in started_ports:
            ev = _evidence(world, _port_plan(world, port))
            print(
                f"[cycle {cycle}] port={port} released tcp=refused "
                f"pid={ev.pid} listening={ev.listening}"
            )
        print(f"[cycle {cycle}] residual=0 (owned {len(owned_pids)} pids all gone)")
        _assert_adopted_untouched(world)
        for plan in world.plans:
            if plan.adopt_pid is not None:
                ev = _evidence(world, plan)
                print(
                    f"[cycle {cycle}] port={plan.port} adopted-untouched "
                    f"pid={ev.pid} listening={ev.listening} matched={ev.matched}"
                )
    finally:
        _finalize(world, started, owned_pids)