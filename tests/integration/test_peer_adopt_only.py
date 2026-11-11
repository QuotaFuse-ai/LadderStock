"""peer adopt-only 边界验收（QUOT-528）：真实临时 peer + 四条生命周期路径。

被测承诺：supervisor 的**冷启 / 自愈 / 网页自启 / 退出**四条路径只能「认领」
已在监听的 peer（`origin=adopted`），不得 spawn / restart / terminate。
断言口径是**真实进程 PID 不变**，不是假对象字段。

| 路径 | 被测入口 | 期望 |
|---|---|---|
| 冷启 | `SelfHealEngine(cold_start=True)` → `adopt_module()` | 认领，pid 不变，不 spawn |
| 自愈 | `SelfHealEngine.tick()`（含账本 pid 漂移） | 不重拉、不换 pid；重新认领同一实例 |
| 网页自启 | `PageBoot.ensure()` | `adopted`，pid 不变，不 spawn |
| 退出 | `stop_children()` / `stop_all()` | 只停自有子进程，adopted peer 原样保留 |

peer 全部由测试自己拉起（`python -c …`，绑定临时端口 0），端口集合与生产
5950 / 8000 / 9100 / 9000 / 8100 不相交（守卫见最后一个用例）。本文件不连接、
不终止、不重启任何生产 peer，也没有平台 skip：CI（Linux）与部署机（Windows）
跑同一套断言。

已知缺陷：`stop_all()` 对 adopted 记录照样调适配器 `stop()`，会把 peer 一起
终止（QUOT-545；本文件用 `xfail(strict=True)` 钉住，修复后自动 XPASS）。
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from ladderstock.supervisor.adapters import MODULE_ORDER
from ladderstock.supervisor.adapters import MODULE_PORTS as REGISTRY_PORTS
from ladderstock.supervisor.engine import SupervisorEngine, SupervisorError
from ladderstock.supervisor.ledger import HEALTH_OK, Ledger
from ladderstock.supervisor.module_adapters import (
    BaseAdapter,
    HostPivot,
    StartFace,
    default_host_pivot,
)
from ladderstock.supervisor.page_boot import (
    ADOPT_ONLY_MODULES,
    SELF_MODULES,
    STATE_ADOPTED,
    PageBoot,
)
from ladderstock.supervisor.proctree import default_process_ops
from ladderstock.supervisor.restart import AdapterRestarter
from ladderstock.supervisor.selfheal import SelfHealEngine

#: 四条路径里必须原样保留的 peer（代表 8000 / 8100 两个生产面）
PEER_MODULES: tuple[str, ...] = ("tickhub", "financecoffer")

#: 红线端口：生产 peer 与看板。测试的任何端口都不许落在其中。
PRODUCTION_PORTS = frozenset(
    {5950, 5952, 8000, 8787, 8002, 8765, 8786, 8100, 8788, 8789, 8790, 8791, 9100, 9000}
)

#: 本机 peer 进程：绑临时端口、把实际端口写盘、收到 SIGTERM 正常退出。
PEER_SCRIPT = r"""
import signal, socket, sys

token, port_file = sys.argv[1], sys.argv[2]
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", 0))
server.listen(8)
with open(port_file, "w", encoding="utf-8") as handle:
    handle.write(str(server.getsockname()[1]))
    handle.flush()


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


@dataclass
class FakePeer:
    module: str
    token: str
    port: int
    process: subprocess.Popen[str]

    @property
    def pid(self) -> int:
        return int(self.process.pid)

    def alive(self) -> bool:
        return self.process.poll() is None

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)


def _start_peer(tmp_path: Path, module: str) -> FakePeer:
    token = f"quot528-{module}-{uuid.uuid4().hex[:10]}"
    port_file = tmp_path / f"peer-{module}.port"
    process = subprocess.Popen(
        [sys.executable, "-c", PEER_SCRIPT, token, str(port_file)],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if port_file.is_file():
            text = port_file.read_text(encoding="utf-8").strip()
            if text.isdigit():
                return FakePeer(module=module, token=token, port=int(text), process=process)
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            pytest.fail(f"fake peer {module} 启动失败（rc={process.returncode}）：{stderr[-800:]}")
        time.sleep(0.05)
    process.kill()
    pytest.fail(f"fake peer {module} 30s 内没有写出监听端口")


class PeerAdapter(BaseAdapter):
    """生产 `BaseAdapter` 的真实识别 / 探活 / 停止实现，只把端口换成临时端口。

    `start()` 直接记一笔并拒绝：adopt-only peer 在任何一条路径上被真正拉起，
    都应当是断言失败，而不是悄悄多出一个实例。
    """

    def __init__(
        self,
        name: str,
        ports: tuple[int, ...],
        *,
        token: str,
        tmp_path: Path,
        host: HostPivot,
    ) -> None:
        face = StartFace(
            face="peer",
            argv=(
                sys.executable,
                "-c",
                PEER_SCRIPT,
                token,
                str(tmp_path / f"spawn-{name}.port"),
            ),
            cwd=tmp_path,
            port=ports[0],
            match=(token,),
            log_name=f"{name}.log",
        )
        super().__init__(
            name,
            ports,
            (face,),
            host=host,
            log_dir=tmp_path / "logs",
            evidence_cache_sec=0.0,
        )
        self.start_calls: list[str] = []

    def start(self):  # type: ignore[override] - 故意只记录并拒绝
        self.start_calls.append(self.name)
        raise SupervisorError(f"{self.name}: adopt-only peer，测试拒绝实际拉起")


@dataclass
class World:
    tmp_path: Path
    host: HostPivot
    peers: dict[str, FakePeer]
    adapters: dict[str, PeerAdapter]
    ports: dict[str, tuple[int, ...]]
    ledger: Ledger
    engine: SupervisorEngine

    def baseline(self) -> dict[str, int]:
        return {name: peer.pid for name, peer in self.peers.items()}

    def page_boot(self) -> PageBoot:
        return PageBoot(
            data_root=self.tmp_path / "data",
            ledger=self.ledger,
            host=self.host,
            adapters=dict(self.adapters),
            run_cwd=self.tmp_path,
            log_dir=self.tmp_path / "logs",
            enabled=False,  # 本文件只验 peer；自有子进程由 test_lifecycle_page_boot 负责
        )

    def heal(self) -> SelfHealEngine:
        clock = _Clock()
        sleeper = _Sleeper(clock)
        return SelfHealEngine(
            self.ledger,
            AdapterRestarter(self.engine),
            clock=clock,
            sleeper=sleeper,
            modules=PEER_MODULES,
            cold_start=True,
            module_ports=self.ports,
        )


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Sleeper:
    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.naps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.naps.append(seconds)
        self.clock.advance(seconds)


def _await_identified(
    adapters: dict[str, PeerAdapter],
    peers: dict[str, FakePeer],
    ports: dict[str, tuple[int, ...]],
) -> None:
    deadline = time.monotonic() + 30.0
    for name, peer in peers.items():
        while True:
            evidence = adapters[name].identify(ports[name][0])
            if evidence.listening and evidence.matched and evidence.pid == peer.pid:
                break
            if time.monotonic() >= deadline:
                pytest.fail(
                    f"30s 内无法把 peer {name} 识别为端口 {ports[name][0]} 的持有者："
                    f"listening={evidence.listening} matched={evidence.matched} "
                    f"pid={evidence.pid} 期望={peer.pid}"
                )
            time.sleep(0.1)


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    host = default_host_pivot()
    peers = {name: _start_peer(tmp_path, name) for name in PEER_MODULES}
    ports: dict[str, tuple[int, ...]] = {name: (_free_port(),) for name in MODULE_ORDER}
    for name, peer in peers.items():
        ports[name] = (peer.port,)

    # 注册表校验读的是模块常量；测试把它换成临时端口，避免任何生产端口被探。
    import ladderstock.supervisor.adapters as adapters_module
    import ladderstock.supervisor.engine as engine_module

    monkeypatch.setattr(adapters_module, "MODULE_PORTS", ports)
    monkeypatch.setattr(engine_module, "MODULE_PORTS", ports)

    adapters: dict[str, PeerAdapter] = {}
    for name in MODULE_ORDER:
        token = (
            peers[name].token
            if name in peers
            else f"quot528-idle-{name}-{uuid.uuid4().hex[:8]}"
        )
        adapters[name] = PeerAdapter(
            name, ports[name], token=token, tmp_path=tmp_path, host=host
        )

    ledger = Ledger(tmp_path / "supervisor" / "ledger.json", window_seconds=300.0)
    engine = SupervisorEngine(
        adapters=adapters,
        ledger=ledger,
        procs=default_process_ops(),
        start_grace_sec=10.0,
        stop_grace_sec=3.0,
        kill_grace_sec=3.0,
        poll_interval_sec=0.05,
    )
    _await_identified(adapters, peers, ports)
    world = World(
        tmp_path=tmp_path,
        host=host,
        peers=peers,
        adapters=adapters,
        ports=ports,
        ledger=ledger,
        engine=engine,
    )
    try:
        yield world
    finally:
        for peer in peers.values():
            peer.stop()


def _assert_peer_untouched(world: World, module: str, expected_pid: int) -> None:
    peer = world.peers[module]
    assert peer.pid == expected_pid, f"peer {module} PID 从 {expected_pid} 变成 {peer.pid}"
    assert peer.alive(), f"peer {module} PID={expected_pid} 已被终止"
    evidence = world.adapters[module].identify(world.ports[module][0])
    assert evidence.listening, f"peer {module} 端口 {world.ports[module][0]} 不再监听"
    assert evidence.matched and evidence.pid == expected_pid, (
        f"peer {module} 端口持有者变了：期望 PID={expected_pid} / "
        f"实际 PID={evidence.pid} matched={evidence.matched}"
    )


def _dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=30)
    return int(process.pid)


# ---------------------------------------------------------------- 路径一：冷启


def test_cold_start_adopts_running_peer_without_spawn(world: World) -> None:
    baseline = world.baseline()
    heal = world.heal()

    heal.tick()

    for name, pid in baseline.items():
        record = world.ledger.record(name)
        assert record is not None, f"冷启后 peer {name} 没有入账"
        assert record.origin == "adopted", f"peer {name} origin={record.origin}"
        assert record.state == "running"
        assert record.pid == pid, f"冷启把 peer {name} 的 PID 从 {pid} 改成 {record.pid}"
        assert record.health == HEALTH_OK
        assert record.restart_count == 0, "认领被误记成一次崩溃恢复"
        assert record.restart_window == []
        assert world.adapters[name].start_calls == [], f"冷启改动了 peer {name} 的启动面"
        _assert_peer_untouched(world, name, pid)

    heal.tick()  # 幂等：第二轮不得换绑或重拉

    for name, pid in baseline.items():
        assert world.ledger.record(name).pid == pid
        assert world.adapters[name].start_calls == []
        _assert_peer_untouched(world, name, pid)


# ---------------------------------------------------------------- 路径二：自愈


def test_selfheal_ticks_never_restart_or_respawn_live_peer(world: World) -> None:
    heal = world.heal()
    heal.tick()  # 冷启认领，建立账本基线
    baseline = world.baseline()

    for _ in range(3):
        heal.tick()

    for name, pid in baseline.items():
        record = world.ledger.record(name)
        assert record is not None and record.pid == pid
        assert record.restart_count == 0
        assert world.adapters[name].start_calls == []
        _assert_peer_untouched(world, name, pid)

    # 账本 PID 漂移到已死进程：自愈必须重新认领**同一个**实例，而不是拉起新进程。
    world.ledger.update(
        "tickhub", pid=_dead_pid(), state="running", origin="spawned", health=HEALTH_OK
    )
    heal.tick()

    record = world.ledger.record("tickhub")
    assert record is not None
    assert record.pid == baseline["tickhub"], "自愈没有把账本换绑回原 peer 实例"
    assert record.origin == "adopted"
    assert record.restart_count == 0, "认领不应占用 5 分钟重拉窗口"
    assert world.adapters["tickhub"].start_calls == []
    _assert_peer_untouched(world, "tickhub", baseline["tickhub"])


# ------------------------------------------------------------ 路径三：网页自启


def test_page_open_adopts_peers_without_touching_them(world: World) -> None:
    boot = world.page_boot()
    baseline = world.baseline()

    report = boot.ensure()

    for name, pid in baseline.items():
        item = report["peers"][name]
        assert item["state"] == STATE_ADOPTED, f"peer {name} 自启状态={item['state']}"
        assert item["pid"] == pid
        assert item["origin"] == "adopted"
        record = world.ledger.record(name)
        assert record is not None and record.pid == pid and record.origin == "adopted"
        assert world.adapters[name].start_calls == []
        _assert_peer_untouched(world, name, pid)

    second = boot.ensure(trigger="page-refresh")
    for name, pid in baseline.items():
        assert second["peers"][name]["pid"] == pid
        assert second["peers"][name]["state"] == STATE_ADOPTED
        _assert_peer_untouched(world, name, pid)


# ---------------------------------------------------------------- 路径四：退出


def test_exit_stop_children_leaves_adopted_peers_untouched(world: World) -> None:
    boot = world.page_boot()
    boot.ensure()
    baseline = world.baseline()

    outcomes = boot.stop_children()

    assert set(outcomes) == set(SELF_MODULES), f"关闭流程多停了模块：{outcomes}"
    assert set(outcomes).isdisjoint(PEER_MODULES), "关闭流程把 peer 当成自有子进程处理"
    for name, pid in baseline.items():
        record = world.ledger.record(name)
        assert record is not None and record.pid == pid and record.state == "running"
        _assert_peer_untouched(world, name, pid)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "已知缺陷 QUOT-545：SupervisorEngine.stop_all() 不区分 adopted 来源，"
        "对 adopted peer 照样调用适配器 stop() 并终止进程；"
        "后端修复后本用例应 XPASS，届时删掉 xfail"
    ),
)
def test_exit_single_entry_stop_all_leaves_adopted_peers_untouched(world: World) -> None:
    for name in PEER_MODULES:
        assert world.engine.adopt_module(name) is not None
    baseline = world.baseline()

    outcome = world.engine.stop_all()

    assert outcome["ok"] is True, outcome
    for name, pid in baseline.items():
        _assert_peer_untouched(world, name, pid)
        record = world.ledger.record(name)
        assert record is not None and record.pid == pid


# ------------------------------------------------------------------ 端口红线


def test_lifecycle_port_tables_never_include_production_ports(world: World) -> None:
    """生命周期接触面不含 5950（看板）/ 9100 / 9000（mem0）等生产端口。"""

    registered = {port for ports in REGISTRY_PORTS.values() for port in ports}
    assert registered.isdisjoint({5950, 9100, 9000}), (
        f"托管注册表出现了生产 peer 端口：{sorted(registered & {5950, 9100, 9000})}"
    )
    for name, ports in world.ports.items():
        assert set(ports).isdisjoint(PRODUCTION_PORTS), (
            f"测试给 {name} 分配了生产端口 {sorted(set(ports) & PRODUCTION_PORTS)}"
        )
    assert set(PEER_MODULES) <= set(ADOPT_ONLY_MODULES)
    assert set(ADOPT_ONLY_MODULES) <= set(MODULE_ORDER)
