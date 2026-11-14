"""端口冲突生命周期验收（QUOT-516）：占口后启动必须报错，禁止静默换端口。

与 514/515 互补：本文件不做冷启 / 停服断言，只验「外部进程占住模块端口时，
adapter.start() 必须抛出含端口号的 SupervisorError，且不抢口、不悄悄换到
端口表里的其他端口」。占口用本测试自己的 socket（SO_EXCLUSIVEADDRUSE），
启动失败后断言端口归属仍是测试进程、其余端口无新增监听，finally 释放占口，
参数化 3 轮验证无残留。

代表性端口（三种形态各一；全部取测试环境 18000 系——QUOT-589 端口隔离红线）：
- 18787 tickhub 管理端（BaseAdapter，命令行型；其 start 已有 _foreign_occupant 预检）；
- 18100 financecoffer（WSL / docker compose 形态）；
- 18788 proxyfleet（Windows 计划任务形态）。
生产 8000/8787/8100/8788 等一律不用作冲突靶口：它们是生产 adopted 面（在听且
命中关键字），红线禁止触碰；用同模块的测试环境端口代表各形态。

平台门：仅 Windows 执行（CI ubuntu 整模块 skip，沿用 514 的模块级平台门）。
部署前提与 514 相同：peer 部署就位、WSL 可用、ProxyFleet 计划任务存在、管理员权限。
三个靶口在 setup 时必须是冷端口（在听即失败）——本测试只占冷口，绝不与
adopted 生产面或外部占口纠缠。

派单红线：
- 等待一律「轮询 + 超时（≤60s）」，无固定硬等；
- 失败口径：「期望报错未报错 / 实际：…」与「期望端口 X 仍归测试占用 / 实际：…」；
- 缺陷修正仅限对应模块的启动逻辑（FinanceCofferAdapter / ProxyFleetAdapter
  补 Windows 侧冲突预检），不改识别 / 停止语义；
- 不改 frontend/src、不删断言、不提高 timeout 掩盖偶发。
"""

from __future__ import annotations

import os
import socket
import threading
import time

import pytest

from ladderstock.supervisor.adapters import MODULE_ORDER
from ladderstock.supervisor.engine import SupervisorError
from ladderstock.supervisor.modules import module_ports
from ops.test_lifecycle_cold_start import (
    POLL_INTERVAL_SEC,
    POLL_TIMEOUT_SEC,
    WINDOWS_ONLY,
    World,
    _tcp_ok,
)

pytestmark = WINDOWS_ONLY

#: 冲突靶口 → 模块（三种形态各一；全部取测试环境 18000 系端口——QUOT-589
#: 端口隔离红线：绝不占/不拉生产 8787/8100/8788）
CONFLICT_TARGETS: dict[int, str] = {
    18787: "tickhub",
    18100: "financecoffer",
    18788: "proxyfleet",
}

#: setup 已 select_env('test')（经 conftest world fixture），实时取 18000 系表
ALL_PORTS = tuple(port for name in MODULE_ORDER for port in module_ports()[name])


def _occupy(port: int) -> tuple[socket.socket, threading.Event, threading.Thread]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Windows 下排他占口：模块即使带 SO_REUSEADDR 也抢不走
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(16)
    except OSError:
        sock.close()
        raise
    stop = threading.Event()

    def _drain() -> None:
        # 适配器用 TCP 建连探活；只 listen 不 accept 会填满 backlog，
        # 后续探测超时被读成「未监听」，占口就形同虚设——必须持续排空。
        sock.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _addr = sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            conn.close()

    thread = threading.Thread(target=_drain, daemon=True, name=f"quot-516-occupy-{port}")
    thread.start()
    return sock, stop, thread


def _listening_ports(world: World) -> set[int]:
    return {port for port in ALL_PORTS if world.host.listening(port)}


def _assert_port_still_owned(world: World, port: int) -> None:
    pid = world.host.owner_pid(port)
    tcp = _tcp_ok(port)
    assert pid == os.getpid() and tcp, (
        f"期望端口 {port} 仍归测试占用（PID={os.getpid()}）/ "
        f"实际：归属 PID={pid} tcp={'ok' if tcp else 'refused'}（占口被抢走）"
    )


def _assert_no_new_listeners(world: World, port: int, baseline: set[int]) -> None:
    now = _listening_ports(world)
    added = (now - baseline) - {port}
    assert not added, (
        f"期望报错未报错 / 实际：模块未拒绝占口，改去监听了 {sorted(added)}"
        f"（静默换端口；靶口 {port} 基线外的新增监听）"
    )


def _cleanup_unexpected_start(world: World, module: str, port: int) -> None:
    """start() 意外成功（缺陷未修 / 回归）时的兜底：停掉并确认靶口释放。"""
    try:
        world.adapters[module].stop()
    finally:
        deadline = time.monotonic() + POLL_TIMEOUT_SEC
        while world.host.listening(port) or _tcp_ok(port):
            if time.monotonic() >= deadline:
                pytest.fail(f"兜底清理后端口 {port} 仍在听（意外拉起的模块未停净）")
            time.sleep(POLL_INTERVAL_SEC)


def _release_and_verify(
    world: World,
    sock: socket.socket,
    stop: threading.Event,
    thread: threading.Thread,
    port: int,
) -> None:
    stop.set()
    sock.close()
    thread.join(timeout=POLL_INTERVAL_SEC * 4)
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    while world.host.listening(port) or _tcp_ok(port):
        if time.monotonic() >= deadline:
            pytest.fail(f"期望端口 {port} 释放 / 实际：占口释放后仍在听（残留）")
        time.sleep(POLL_INTERVAL_SEC)
    print(f"[conflict] port={port} released tcp=refused listening=False")


def _assert_adopted_untouched(world: World) -> None:
    for plan in world.plans:
        if plan.adopt_pid is None:
            continue
        ident = world.adapters[plan.module].identify()
        ev = next(item for item in ident.ports if item.port == plan.port)
        assert ev.listening and ev.matched and ev.pid == plan.adopt_pid, (
            f"adopted 进程被改动：端口 {plan.port} 期望 PID={plan.adopt_pid} / "
            f"实际 PID={ev.pid} listening={ev.listening} matched={ev.matched}"
        )


@pytest.mark.parametrize("cycle", [1, 2, 3])
@pytest.mark.parametrize("port", sorted(CONFLICT_TARGETS))
def test_port_conflict(world: World, port: int, cycle: int) -> None:
    """占口 → start() 必须抛含端口号的错 → 不抢口不换口 → 释放无残留。"""
    module = CONFLICT_TARGETS[port]
    plan = next(item for item in world.plans if item.port == port)
    assert plan.adopt_pid is None, (
        f"靶口 {port} 在 setup 时已被 adopted（PID={plan.adopt_pid}），冲突测试只占冷口"
    )
    occupied: tuple[socket.socket, threading.Event, threading.Thread] | None = None
    unexpected = False
    try:
        occupied = _occupy(port)
        _assert_port_still_owned(world, port)
        baseline = _listening_ports(world) - {port}
        adapter = world.adapters[module]
        try:
            adapter.start()
        except SupervisorError as exc:
            message = str(exc)
            assert str(port) in message, f"期望报错包含端口号 {port} / 实际错误文案：{message}"
            print(f"[cycle {cycle}] port={port} module={module} refused: {message}")
        else:
            unexpected = True
            pytest.fail(
                f"期望报错未报错 / 实际：端口 {port} 被测试占用时 {module}.start() "
                "静默成功（未抛 SupervisorError）"
            )
        _assert_port_still_owned(world, port)
        _assert_no_new_listeners(world, port, baseline)
        _assert_adopted_untouched(world)
    finally:
        if unexpected:
            _cleanup_unexpected_start(world, module, port)
        if occupied is not None:
            _release_and_verify(world, *occupied, port)
    print(f"[cycle {cycle}] port={port} conflict cycle clean: no residual, adopted untouched")
