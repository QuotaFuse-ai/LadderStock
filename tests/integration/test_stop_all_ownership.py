"""stop_all 归属边界回归（QUOT-545）+ 端口安全自愈（QUOT-547）：真实临时进程。

QUOT-545 两条停止语义同场验证：
- 前任 supervisor 自己 spawn 的进程（崩溃后新会话对账为 adopted、无标记）：
  下一次 stop_all 仍能正常停止——防止「凡 adopted 都跳过」的过度修复。
- 外部 peer 认领（adopted_peer 标记）与自有进程混跑：同一次 stop_all
  只停自有进程，peer 原样存活、PID 不变。

QUOT-547 安全自愈（控制面 heal_ports 接真引擎、探真临时进程）：
- adopted_peer：诊断认成 adopted_peer（非 foreign），heal 只认领 + 复检，
  绝不终止 / 重启；同场 stop_all 也跳过它，peer 原样存活、PID 不变。
- own_child：诊断认成 own_child，heal 停止并重拉，PID 换新、端口重新监听。

进程全部由测试拉起（`python -c …` 绑临时端口），端口与生产
5950 / 8000 / 9100 / 9000 / 8100 等不相交（fixture 内有红线守卫）。
不连接、不终止任何生产进程；CI（Linux）与部署机（Windows）跑同一套断言。
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from ladderstock.core.boundary import PeerControl
from ladderstock.supervisor.adapters import MODULE_ORDER
from ladderstock.supervisor.engine import SupervisorEngine
from ladderstock.supervisor.ledger import Ledger
from ladderstock.supervisor.module_adapters import (
    BaseAdapter,
    HostPivot,
    StartFace,
    default_host_pivot,
)
from ladderstock.supervisor.proctree import default_process_ops
from ladderstock.web.control import ControlPlane, PortOwner

#: 红线端口：生产 peer 与看板，测试的任何端口都不许落在其中。
PRODUCTION_PORTS = frozenset(
    {5950, 5952, 8000, 8787, 8002, 8765, 8786, 8100, 8788, 8789, 8790, 8791, 9100, 9000}
)

#: 绑固定临时端口的本机进程：命令行带识别 token，SIGTERM/SIGINT 正常退出。
BOUND_SCRIPT = r"""
import signal, socket, sys

token, port = sys.argv[1], int(sys.argv[2])
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", port))
server.listen(8)


def _stop(*_args):
    try:
        server.close()
    finally:
        raise SystemExit(0)


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)
while True:
    try:
        conn, _ = server.accept()
    except OSError:
        break
    conn.close()
"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _build_adapters(
    ports: dict[str, tuple[int, ...]], tokens: dict[str, str], tmp_path: Path, host: HostPivot
) -> dict[str, BaseAdapter]:
    adapters: dict[str, BaseAdapter] = {}
    for name in MODULE_ORDER:
        port = ports[name][0]
        face = StartFace(
            face="peer",
            argv=(sys.executable, "-c", BOUND_SCRIPT, tokens[name], str(port)),
            cwd=tmp_path,
            port=port,
            match=(tokens[name],),
            log_name=f"{name}.log",
        )
        adapters[name] = BaseAdapter(
            name,
            ports[name],
            (face,),
            host=host,
            log_dir=tmp_path / "logs",
            evidence_cache_sec=0.0,
            graceful_wait_sec=1.0,
            readback_wait_sec=2.0,
        )
    return adapters


def _build_engine(adapters: dict[str, BaseAdapter], ledger: Ledger) -> SupervisorEngine:
    return SupervisorEngine(
        adapters=adapters,
        ledger=ledger,
        procs=default_process_ops(),
        start_grace_sec=20.0,
        stop_grace_sec=10.0,
        kill_grace_sec=10.0,
        poll_interval_sec=0.1,
    )


@dataclass
class World:
    tmp_path: Path
    host: HostPivot
    ports: dict[str, tuple[int, ...]]
    tokens: dict[str, str]
    ledger_a: Ledger
    adapters_a: dict[str, BaseAdapter]
    engine_a: SupervisorEngine
    external: list[subprocess.Popen[str]] = field(default_factory=list)
    sessions: list[tuple[Ledger, dict[str, BaseAdapter]]] = field(default_factory=list)

    def new_session(self) -> tuple[SupervisorEngine, Ledger, dict[str, BaseAdapter]]:
        """上一任崩溃后的新会话：新账本实例（同盘）+ 新适配器 + 新引擎。"""
        ledger = Ledger(self.tmp_path / "supervisor" / "ledger.json")
        adapters = _build_adapters(self.ports, self.tokens, self.tmp_path, self.host)
        engine = _build_engine(adapters, ledger)
        self.sessions.append((ledger, adapters))
        return engine, ledger, adapters

    def start_external_peer(self, name: str) -> subprocess.Popen[str]:
        port = self.ports[name][0]
        process = subprocess.Popen(
            [sys.executable, "-c", BOUND_SCRIPT, self.tokens[name], str(port)],
            cwd=self.tmp_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.external.append(process)
        self.await_listening(name, process.pid)
        return process

    def await_listening(self, name: str, pid: int) -> None:
        port = self.ports[name][0]
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            evidence = self.adapters_a[name].identify(port)
            if evidence.listening and evidence.matched and evidence.pid == pid:
                return
            time.sleep(0.1)
        pytest.fail(f"30s 内 {name} 的临时进程 pid={pid} 没有被识别为端口 {port} 的持有者")

    def await_gone(self, name: str, pid: int) -> None:
        port = self.ports[name][0]
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if self.host.snapshot(pid) is None and not self.host.listening(port):
                return
            time.sleep(0.1)
        pytest.fail(f"30s 内 {name} 的进程 pid={pid} 没有退出或端口 {port} 没有释放")

    def await_pid_gone(self, pid: int) -> None:
        """只等进程退出，不看端口：重拉后同一端口已被新进程占用，端口判据不适用。"""
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if self.host.snapshot(pid) is None:
                return
            time.sleep(0.1)
        pytest.fail(f"30s 内旧进程 pid={pid} 没有退出")


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    host = default_host_pivot()
    ports: dict[str, tuple[int, ...]] = {name: (_free_port(),) for name in MODULE_ORDER}
    for name, module_ports in ports.items():
        assert set(module_ports).isdisjoint(PRODUCTION_PORTS), (
            f"测试给 {name} 分配了生产端口 {sorted(set(module_ports) & PRODUCTION_PORTS)}"
        )
    tokens = {name: f"quot545-{name}-{uuid.uuid4().hex[:10]}" for name in MODULE_ORDER}

    # 注册表校验读的是模块常量；换成临时端口，避免任何生产端口被探。
    import ladderstock.supervisor.adapters as adapters_module
    import ladderstock.supervisor.engine as engine_module

    monkeypatch.setattr(adapters_module, "MODULE_PORTS", ports)
    monkeypatch.setattr(engine_module, "MODULE_PORTS", ports)

    ledger_a = Ledger(tmp_path / "supervisor" / "ledger.json")
    adapters_a = _build_adapters(ports, tokens, tmp_path, host)
    engine_a = _build_engine(adapters_a, ledger_a)
    world = World(
        tmp_path=tmp_path,
        host=host,
        ports=ports,
        tokens=tokens,
        ledger_a=ledger_a,
        adapters_a=adapters_a,
        engine_a=engine_a,
    )
    try:
        yield world
    finally:
        for process in world.external:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        pids: set[int] = set()
        for ledger in [world.ledger_a, *(ledger for ledger, _ in world.sessions)]:
            for record in ledger.entries():
                if record.pid is not None:
                    pids.add(record.pid)
        for pid in sorted(pids):
            try:
                world.host.terminate_tree(pid, force=True)
            except Exception:  # noqa: BLE001 - 清理尽力而为
                pass


def test_previous_supervisor_spawned_process_is_still_stopped_by_stop_all(world: World) -> None:
    """回归护栏（QUOT-545 派单）：前任自己拉的进程，下一次 stop_all 必须仍能停。"""
    world.engine_a.start_module("wire")
    record_a = world.ledger_a.record("wire")
    assert record_a is not None and record_a.pid is not None
    assert record_a.origin == "spawned" and record_a.adopted_peer is False
    spawned_pid = record_a.pid

    engine_b, ledger_b, _adapters_b = world.new_session()
    summary = engine_b.reconcile()
    assert summary["wire"] == "adopted:still_running"
    record_b = ledger_b.record("wire")
    assert record_b is not None
    assert record_b.origin == "adopted" and record_b.adopted_peer is False

    outcome = engine_b.stop_all()

    assert outcome["ok"] is True, outcome
    assert "skipped" not in outcome["modules"]["wire"]
    world.await_gone("wire", spawned_pid)
    stopped = ledger_b.record("wire")
    assert stopped is not None and stopped.state == "stopped"
    assert stopped.stop_reason == "stopped_by_request"


def test_stop_all_stops_owned_process_and_spares_adopted_peer(world: World) -> None:
    """同一次 stop_all：自有进程停掉，外部 peer 认领原样保留（PID 不变）。"""
    peer = world.start_external_peer("tickhub")
    assert world.engine_a.adopt_module("tickhub") is not None
    world.engine_a.start_module("wire")
    record_a = world.ledger_a.record("wire")
    assert record_a is not None and record_a.pid is not None
    wire_pid = record_a.pid

    engine_b, ledger_b, adapters_b = world.new_session()
    outcome = engine_b.stop_all()

    assert outcome["ok"] is True, outcome
    assert outcome["modules"]["tickhub"]["skipped"] == "adopted_peer"
    assert "skipped" not in outcome["modules"]["wire"]
    world.await_gone("wire", wire_pid)

    assert peer.poll() is None, f"adopted peer pid={peer.pid} 被 stop_all 终止"
    evidence = adapters_b["tickhub"].identify(world.ports["tickhub"][0])
    assert evidence.listening and evidence.matched and evidence.pid == peer.pid, (
        f"peer 端口持有者变了：期望 PID={peer.pid} / 实际 PID={evidence.pid}"
    )
    record_b = ledger_b.record("tickhub")
    assert record_b is not None
    assert record_b.pid == peer.pid
    assert record_b.state == "running"
    assert record_b.adopted_peer is True
    assert record_b.stopping_by_user is False


# --------------------------------------------------------------------------- #
# QUOT-547：控制面端口诊断 + 安全自愈（真引擎、真临时进程）
# --------------------------------------------------------------------------- #


def _heal_plane(world: World, engine: SupervisorEngine) -> ControlPlane:
    """把顺序启停引擎接进控制面：端口 / 命令行探真实临时进程，HTTP/MCP 给假件。

    临时进程只是绑端口的 socket 服务，不是真 HTTP/MCP 服务——健康探针一律失败，
    自愈判定只依赖端口归属分类（账本认领态 + 实时命令行），不依赖健康结果。

    端口与命令行探针都读同一宿主 pivot（psutil，三平台一致）：
    - 控制面自带的 `probe_port` 在 POSIX 上拿不到 PID（只回 None），会把 adopted
      peer 误判 foreign；这里用 `host.owner_pid` 取真实持有者，CI/部署机同断言。
    - 命令行与适配器证据同源，指纹比对一致（spawned 记原始 argv、adopted 记
      宿主回读，二者对 BOUND_SCRIPT 实测同指纹），不会误触 PID 复用降级。
    """
    peers: dict[str, PeerControl] = {}
    registry: dict[str, Any] = {}
    for name in ("tickhub", "wire"):
        directory = world.tmp_path / "peers" / name
        directory.mkdir(parents=True, exist_ok=True)
        port = world.ports[name][0]
        peers[name] = PeerControl(
            key=name,
            directory=str(directory),
            import_package=name,
            mcp_port=port,
            web_port=None,
            web_port_label="",
            health_path="/healthz",
            start_commands=(("placeholder",),),
            config_keys=(),
        )
        registry[name] = {
            "product": name,
            "mcp_name": name,
            "default_url": f"http://127.0.0.1:{port}/mcp",
        }

    def _port_probe(port: int | None) -> list[PortOwner]:
        if not port or not world.host.listening(port):
            return []
        pid = world.host.owner_pid(port)
        snap = world.host.snapshot(pid) if pid is not None else None
        return [PortOwner(pid=pid, process=snap.name if snap is not None else None)]

    def _live_cmdline(pid: int) -> tuple[str, ...] | None:
        snap = world.host.snapshot(pid)
        return snap.cmdline if snap is not None else None

    return ControlPlane(
        data_root=world.tmp_path,
        peers=peers,
        components={},
        sources_loader=lambda: registry,
        port_probe=_port_probe,
        health_probe=lambda url, timeout: (False, "无 HTTP（临时进程）"),
        mcp_probe=lambda url, timeout: (False, "无 MCP（临时进程）"),
        cmdline_probe=_live_cmdline,
        supervisor=engine,
    )


def test_heal_spares_adopted_peer(world: World) -> None:
    """安全自愈：外部 peer 认领后诊断成 adopted_peer（非 foreign），heal 只认领 +
    复检，绝不终止 / 重启；同场 stop_all 也跳过它，peer 原样存活、PID 不变。"""
    peer = world.start_external_peer("tickhub")
    assert world.engine_a.adopt_module("tickhub") is not None
    plane = _heal_plane(world, world.engine_a)

    diag = plane.diagnose_ports("tickhub")["peers"][0]
    entry = diag["ports"][0]
    assert entry["class"] == "adopted_peer", entry
    assert entry["pid"] == peer.pid
    assert entry["listening"] is True

    result = plane.heal_ports("tickhub")
    assert result["action"] == "adopt", result
    assert result["ok"] is True
    assert peer.poll() is None, f"adopted peer pid={peer.pid} 被 heal 终止"

    outcome = world.engine_a.stop_all()
    assert outcome["ok"] is True, outcome
    assert outcome["modules"]["tickhub"]["skipped"] == "adopted_peer"
    assert peer.poll() is None, f"adopted peer pid={peer.pid} 被 stop_all 终止"
    evidence = world.adapters_a["tickhub"].identify(world.ports["tickhub"][0])
    assert evidence.listening and evidence.matched and evidence.pid == peer.pid, (
        f"peer 端口持有者变了：期望 PID={peer.pid} / 实际 PID={evidence.pid}"
    )


def test_heal_restarts_own_child(world: World) -> None:
    """安全自愈：本方拉起的子进程诊断成 own_child，heal 停止并重拉——PID 换新、
    端口重新监听、旧进程退出（与 adopted_peer 的「绝不终止」形成对照）。"""
    world.engine_a.start_module("wire")
    record = world.ledger_a.record("wire")
    assert record is not None and record.pid is not None
    assert record.origin == "spawned" and record.adopted_peer is False
    old_pid = record.pid
    plane = _heal_plane(world, world.engine_a)

    diag = plane.diagnose_ports("wire")["peers"][0]
    entry = diag["ports"][0]
    assert entry["class"] == "own_child", entry
    assert entry["pid"] == old_pid

    result = plane.heal_ports("wire")
    assert result["action"] == "restart", result
    assert result["ok"] is True
    new_record = world.ledger_a.record("wire")
    assert new_record is not None and new_record.pid is not None
    assert new_record.pid != old_pid, "own_child 重拉后 PID 必须换新"
    assert world.adapters_a["wire"].probe() is True
    world.await_pid_gone(old_pid)
