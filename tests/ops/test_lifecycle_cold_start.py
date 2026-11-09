"""冷启动生命周期验收（QUOT-514）：真实宿主、真实端口、真实进程。

与 tests/unit/supervisor/（全 fake、不占真端口）互补：本文件在部署宿主上真起
真停六个顺序模块，逐端口断言「TCP 可建连 且 端口归属 PID 的 cmdline 命中模块
关键字」，幂等起停 3 轮、每轮结束无残留。

平台门：仅 Windows 执行（CI 是 ubuntu，整模块 skip，沿用
tests/unit/web/test_control_acl.py 的 WINDOWS_ONLY 先例）。这是模块级平台门，
不是用例内兜底：Windows 上本文件没有任何 skip 分支，起不来 / 停不净一律 fail。
部署前提（Windows 上缺任一即真失败）：六个 peer 部署就位、WSL 默认发行版可用、
ProxyFleet 计划任务存在、当前进程有管理员权限（schtasks /change 需要）。

派单红线：
- 等待一律「轮询 + 超时（≤60s）」，无固定硬等；
- 失败消息固定口径：「期望端口 X 在听 / 实际：无监听 或 归属 PID=…（不属于本模块）」；
- 本单只做冷启动与幂等无残留；停止语义细节（515）与端口冲突（516）不在范围；
- 不改业务源码、不改 docs/supervisor/module-matrix.md。

既有进程保护：setup 时已命中关键字的在听端口记为 adopted（如生产 tickhub
8000），全程不终止；只拉缺失的面——半冷模块逐面起，不走 adapter.start() 整组
拉起，避免重复起已 adopted 的面；teardown 只停本测试拉起的进程，结束后
adopted 端口必须原样保留（PID 都不许变）。外部进程占口（在听但不命中）→
setup 直接失败，不动它。
"""

from __future__ import annotations

import os
import socket
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ladderstock.supervisor.adapters import MODULE_ORDER, MODULE_PORTS
from ladderstock.supervisor.module_adapters import (
    BaseAdapter,
    FinanceCofferAdapter,
    PortEvidence,
    ProxyFleetAdapter,
    WindowsHostPivot,
    build_default_adapters,
)

IS_WINDOWS = os.name == "nt"
WINDOWS_ONLY = pytest.mark.skipif(
    not IS_WINDOWS,
    reason="真实宿主验收只在 Windows 部署机执行（CI ubuntu 无 peer 部署 / WSL / 计划任务）",
)

pytestmark = WINDOWS_ONLY

POLL_TIMEOUT_SEC = 60.0
POLL_INTERVAL_SEC = 0.5
TCP_CONNECT_TIMEOUT_SEC = 1.0

LifecycleAdapter = BaseAdapter | FinanceCofferAdapter | ProxyFleetAdapter


@dataclass(frozen=True)
class PortPlan:
    module: str
    port: int
    #: setup 时已在听且命中关键字的既有进程；None = 冷端口，由本测试拉起
    adopt_pid: int | None


@dataclass
class World:
    host: WindowsHostPivot
    adapters: dict[str, LifecycleAdapter]
    plans: tuple[PortPlan, ...]
    log_dir: Path


@dataclass
class Started:
    """本测试拉起的一份责任：teardown 只碰这里登记的东西。"""

    module: str
    ports: tuple[int, ...]
    #: True = 形态专属停止走适配器（financecoffer 容器 / proxyfleet 计划任务）
    via_adapter: bool
    pids: list[int] = field(default_factory=list)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    host = WindowsHostPivot()
    log_dir = tmp_path_factory.mktemp("quot-514-logs")
    built = build_default_adapters(host=host, log_dir=log_dir)
    adapters: dict[str, LifecycleAdapter] = {}
    plans: list[PortPlan] = []
    for name in MODULE_ORDER:
        adapter = built[name]
        assert isinstance(adapter, (BaseAdapter, FinanceCofferAdapter, ProxyFleetAdapter))
        adapters[name] = adapter
        for ev in adapter.identify().ports:
            if ev.listening and not ev.matched:
                pytest.fail(
                    f"期望端口 {ev.port} 在听 / 实际：归属 PID={ev.pid}（不属于本模块）"
                    "——setup 拒绝在外部占口上继续"
                )
            adopt_pid = ev.pid if ev.listening and ev.matched else None
            plans.append(PortPlan(module=name, port=ev.port, adopt_pid=adopt_pid))
            action = f"adopted pid={adopt_pid}" if adopt_pid is not None else "cold will-start"
            print(f"[setup] {name} port={ev.port} {action}")
    expected = {port for name in MODULE_ORDER for port in MODULE_PORTS[name]}
    assert {plan.port for plan in plans} == expected, "识别端口与注册表白名单不一致"
    yield World(host=host, adapters=adapters, plans=tuple(plans), log_dir=log_dir)


def _evidence(world: World, plan: PortPlan) -> PortEvidence:
    ident = world.adapters[plan.module].identify()
    return next(item for item in ident.ports if item.port == plan.port)


def _tcp_ok(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=TCP_CONNECT_TIMEOUT_SEC):
            return True
    except OSError:
        return False


def _actual_desc(ev: PortEvidence) -> str:
    if not ev.listening:
        return "无监听"
    if ev.matched:
        return f"归属 PID={ev.pid}（属于本模块但 TCP 建连失败）"
    return f"归属 PID={ev.pid}（不属于本模块）"


def _await_listening(world: World, plan: PortPlan) -> PortEvidence:
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    while True:
        ev = _evidence(world, plan)
        if ev.listening and ev.matched and _tcp_ok(plan.port):
            return ev
        if time.monotonic() >= deadline:
            pytest.fail(f"期望端口 {plan.port} 在听 / 实际：{_actual_desc(ev)}")
        time.sleep(POLL_INTERVAL_SEC)


def _start_missing(world: World) -> list[Started]:
    started: list[Started] = []
    for name in MODULE_ORDER:
        mine = [plan for plan in world.plans if plan.module == name and plan.adopt_pid is None]
        if not mine:
            continue
        adapter = world.adapters[name]
        ports = tuple(plan.port for plan in mine)
        if isinstance(adapter, BaseAdapter):
            faces = [
                next(item for item in adapter.faces if item.port == plan.port) for plan in mine
            ]
            for face in faces:
                exe = Path(face.argv[0])
                if not exe.is_file():
                    pytest.fail(f"{name} 的入口不存在：{exe}")
            for face in faces:
                pid = world.host.spawn(
                    face.argv,
                    cwd=face.cwd,
                    # 复用适配器的环境装配（tickhub env 文件等），不在测试里另复制一份
                    env=adapter._environment(),
                    log_path=world.log_dir / face.log_name,
                )
                started.append(
                    Started(module=name, ports=(face.port,), via_adapter=False, pids=[pid])
                )
        else:
            item = Started(module=name, ports=ports, via_adapter=True)
            started.append(item)
            spawned = adapter.start()
            if isinstance(adapter, FinanceCofferAdapter):
                item.pids.append(spawned.pid)
    return started


def _teardown(world: World, started: list[Started]) -> None:
    for item in reversed(started):
        if item.via_adapter:
            world.adapters[item.module].stop()
        else:
            for pid in item.pids:
                world.host.terminate_tree(pid, force=True)
    pending = [port for item in started for port in item.ports]
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    while pending:
        pending = [port for port in pending if _tcp_ok(port)]
        if not pending:
            return
        if time.monotonic() >= deadline:
            pytest.fail(f"teardown 后端口仍在听：{pending}（本测试拉起的进程未停净）")
        time.sleep(POLL_INTERVAL_SEC)


def _assert_no_residual(world: World, started: list[Started]) -> None:
    for item in started:
        for pid in item.pids:
            assert world.host.snapshot(pid) is None, f"{item.module} 残留进程 PID={pid}"
        for port in item.ports:
            assert not _tcp_ok(port), f"{item.module} 残留监听端口 {port}"
    for plan in world.plans:
        if plan.adopt_pid is None:
            continue
        ev = _evidence(world, plan)
        assert ev.listening and ev.matched and ev.pid == plan.adopt_pid, (
            f"adopted 进程被改动：端口 {plan.port} 期望 PID={plan.adopt_pid} / "
            f"实际 PID={ev.pid} listening={ev.listening} matched={ev.matched}"
        )


@pytest.mark.parametrize("cycle", [1, 2, 3])
def test_cold_start_lifecycle(world: World, cycle: int) -> None:
    """一轮 = 起缺失面 → 10 端口双证据 → 只停本次拉起 → 无残留且 adopted 原样。"""
    started: list[Started] = []
    try:
        started = _start_missing(world)
        for plan in world.plans:
            ev = _await_listening(world, plan)
            origin = "adopted" if plan.adopt_pid is not None else "started"
            print(
                f"[cycle {cycle}] {plan.module} port={plan.port} {origin} "
                f"pid={ev.pid} tcp=ok cmdline-match=True"
            )
    finally:
        _teardown(world, started)
    _assert_no_residual(world, started)
    print(f"[cycle {cycle}] teardown clean: no residual, adopted untouched")
