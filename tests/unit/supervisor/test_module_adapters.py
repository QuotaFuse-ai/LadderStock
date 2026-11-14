"""模块适配器测试（QUOT-482-B + QUOT-506）：全部走假宿主，不起真进程、不占真端口。

覆盖：
- 注册表口径（顺序模块 + 按需模块 proxypool + 端口白名单）；
- 识别规则：端口在听 + cmdline 命中才算 probe 真，外部进程不算；
- 多面模块 start 拉起两条命令、stop 连 adopted 的另一面一起停；
- financecoffer 的 WSL 形态（发行版探测 / compose 停 / 容器回读）；
- proxyfleet 只经计划任务启停（enable→run / disable→end），不下发 taskkill；
- proxypool 的按需语义：双向探活、端口冲突报错、adopted 归属、停止无残留。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from ladderstock.supervisor.adapters import (
    MODULE_ORDER,
    MODULE_PORTS,
    ON_DEMAND_MODULES,
)
from ladderstock.supervisor.engine import SupervisorError
from ladderstock.supervisor.module_adapters import (
    BaseAdapter,
    CommandResult,
    FinanceCofferAdapter,
    Identification,
    PortEvidence,
    PreflightReport,
    ProcessSnapshot,
    ProxyFleetAdapter,
    ProxyPoolAdapter,
    ProxyPoolConfig,
    ProxyPoolMapping,
    build_default_adapters,
    launcher_entry,
    load_env_file,
    load_proxypool_config,
    preflight,
    resolve_panel_port,
)

_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _isolate_host_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`install_root()` / `launcher_entry()` 会读真实环境：隔离掉本机变量与 cwd。

    否则开发机上恰好配好的 `LDST_LAUNCHER` / `LDST_INSTALL_ROOT` 会让断言时过时不过。
    """
    for key in (
        "LDST_INSTALL_ROOT",
        "LDST_PEER_ROOT",
        "LDST_LAUNCHER",
        "LDST_HOST_CONFIG",
        "LDST_TICKHUB_ENV_FILES",
        "LDST_PROXYFLEET_TASK",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(_ROOT)


# --------------------------------------------------------------------------- #
# 假宿主
# --------------------------------------------------------------------------- #


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeHost:
    """端口表 + 进程表 + 命令执行的假宿主。"""

    def __init__(self) -> None:
        self.port_owner: dict[int, int] = {}
        self.procs: dict[int, ProcessSnapshot] = {}
        self.spawns: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []
        self.terminated: list[tuple[int, bool]] = []
        self.runs: list[tuple[str, ...]] = []
        self.responses: list[tuple[str, CommandResult]] = []
        self.listen_hidden: set[int] = set()
        #: 端口在监听表里但真实建连失败（模拟「只 listen 不 accept」/ 半死服务）
        self.unconnectable: set[int] = set()
        self._next_pid = 7000

    # ---- 数据准备

    def add_process(
        self,
        cmdline: tuple[str, ...],
        *,
        name: str = "python.exe",
        parent: int | None = None,
        ports: tuple[int, ...] = (),
    ) -> int:
        self._next_pid += 1
        pid = self._next_pid
        self.procs[pid] = ProcessSnapshot(pid=pid, parent_pid=parent, name=name, cmdline=cmdline)
        for port in ports:
            self.port_owner[port] = pid
        return pid

    def respond(self, needle: str, result: CommandResult) -> None:
        self.responses.append((needle, result))

    # ---- HostPivot 协议

    def listening(self, port: int) -> bool:
        if port in self.listen_hidden:
            return False
        return port in self.port_owner

    def handshake(self, host: str, port: int) -> bool:
        del host
        if port in self.listen_hidden:
            return False
        return port in self.port_owner and port not in self.unconnectable

    def owner_pid(self, port: int) -> int | None:
        return self.port_owner.get(port)

    def snapshot(self, pid: int) -> ProcessSnapshot | None:
        return self.procs.get(pid)

    def find_processes(self, marker: str) -> tuple[ProcessSnapshot, ...]:
        needle = marker.replace("\\", "/").lower()
        return tuple(
            proc
            for proc in self.procs.values()
            if needle in " ".join(proc.cmdline).replace("\\", "/").lower()
        )

    def parent_chain(self, pid: int, depth: int = 6) -> tuple[ProcessSnapshot, ...]:
        chain: list[ProcessSnapshot] = []
        current: int | None = pid
        while current and len(chain) < depth:
            snap = self.procs.get(current)
            if snap is None:
                break
            chain.append(snap)
            current = snap.parent_pid
        return tuple(chain)

    def terminate_tree(self, pid: int, *, force: bool) -> None:
        self.terminated.append((pid, force))
        dead = {pid}
        changed = True
        while changed:
            changed = False
            for proc in self.procs.values():
                if proc.pid not in dead and proc.parent_pid in dead:
                    dead.add(proc.pid)
                    changed = True
        for item in dead:
            self.procs.pop(item, None)
        for port, owner in list(self.port_owner.items()):
            if owner in dead:
                self.port_owner.pop(port, None)

    def spawn(self, argv: Any, *, cwd: Path, env: Any, log_path: Path) -> int:
        argv_tuple = tuple(str(part) for part in argv)
        self.spawns.append((argv_tuple, cwd, dict(env)))
        port: int | None = None
        for flag in ("--port", "--listen-port"):
            if flag in argv_tuple:
                port = int(argv_tuple[argv_tuple.index(flag) + 1])
                break
        ports = (port,) if port else ()
        pid = self.add_process(argv_tuple, name=Path(argv_tuple[0]).name, ports=ports)
        return pid

    def run(self, argv: Any, *, timeout: float, cwd: Path | None = None) -> CommandResult:
        argv_tuple = tuple(str(part) for part in argv)
        self.runs.append(argv_tuple)
        joined = " ".join(argv_tuple)
        for needle, result in self.responses:
            if needle in joined:
                return result
        return CommandResult(returncode=0)


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #

PEER_ENV_KEYS = {
    "tickhub": "LDST_PEER_DIR_TICKHUB",
    "tickhub_us": "LDST_PEER_DIR_TICKHUB_US",
    "wire": "LDST_PEER_DIR_WIRE",
    "senate": "LDST_PEER_DIR_SENATE",
    "financecoffer": "LDST_PEER_DIR_FINANCECOFFER",
}

EXES = {
    "tickhub": "th-cn.exe",
    "tickhub_us": "th-us.exe",
    "wire": "qfw.exe",
    "senate": "ssenate.exe",
}


def _make_peer_dirs(tmp_path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for key, env_key in PEER_ENV_KEYS.items():
        peer = tmp_path / "peers" / key
        peer.mkdir(parents=True)
        if key in EXES:
            scripts = peer / ".venv" / "Scripts"
            scripts.mkdir(parents=True)
            (scripts / EXES[key]).write_bytes(b"")
        env[env_key] = str(peer)
    return env


def _adapters(tmp_path: Path, host: FakeHost | None = None) -> tuple[FakeHost, dict[str, Any]]:
    fake = host or FakeHost()
    env = _make_peer_dirs(tmp_path)
    built = build_default_adapters(
        host=fake,
        env=env,
        log_dir=tmp_path / "logs",
        evidence_cache_sec=0.0,
    )
    return fake, cast(dict[str, Any], built)


def _wire(adapters: dict[str, Any]) -> BaseAdapter:
    return cast(BaseAdapter, adapters["wire"])


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #


def test_registry_contains_registered_modules_with_registered_ports(tmp_path: Path) -> None:
    _host, adapters = _adapters(tmp_path)
    assert set(adapters) == set(MODULE_ORDER) | ON_DEMAND_MODULES
    for name in MODULE_ORDER:
        assert tuple(adapters[name].ports) == MODULE_PORTS[name]
    # 按需模块的端口由宿主配置决定：假宿主读不到配置 → 未启用、无映射
    assert adapters["proxypool"].ports == ()


# --------------------------------------------------------------------------- #
# 识别规则
# --------------------------------------------------------------------------- #


def test_probe_requires_listening_and_matching_cmdline(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path)
    wire = _wire(adapters)
    assert wire.probe() is False

    host.add_process(
        ("D:/workplace/Wire/quotafuse-wire/.venv/Scripts/qfw.exe", "serve", "--port", "8790"),
        ports=(8790,),
    )
    assert wire.probe() is True


def test_probe_fails_closed_when_port_is_held_by_a_foreign_process(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path)
    wire = _wire(adapters)
    host.add_process(("D:/other/tool.exe", "--port", "8790"), ports=(8790,))
    assert wire.probe() is False
    evidence = wire.identify(8790)
    assert evidence.listening is True
    assert evidence.matched is False
    assert evidence.pid is not None


def test_identify_reports_port_pid_and_parent_chain(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path)
    senate = cast(BaseAdapter, adapters["senate"])
    launcher = host.add_process(
        ("D:/workplace/StockSenate/.venv/Scripts/ssenate.exe", "mcp", "http", "--port", "8765"),
        name="ssenate.exe",
    )
    child = host.add_process(
        ("python.exe", "D:/workplace/StockSenate/.venv/Scripts/ssenate.exe", "mcp", "http"),
        parent=launcher,
        ports=(8765,),
    )
    identification = senate.identify()
    assert identification.matched is True
    item = next(i for i in identification.ports if i.port == 8765)
    assert item.pid == child
    assert [p.pid for p in item.parent_chain][:2] == [child, launcher]
    assert "8765" in identification.describe()


# --------------------------------------------------------------------------- #
# 启停
# --------------------------------------------------------------------------- #


def test_start_spawns_every_face_with_absolute_venv_entry(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path)
    wire = _wire(adapters)
    spawned = wire.start()
    assert len(host.spawns) == 2
    for argv, _cwd, _env in host.spawns:
        assert argv[0].endswith("qfw.exe")
        assert Path(argv[0]).is_absolute()
    assert spawned.parent_pid is not None
    assert spawned.cmdline[1] == "serve"


def test_stop_terminates_all_faces_even_when_adopted(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path)
    wire = _wire(adapters)
    web = host.add_process(
        ("D:/workplace/Wire/quotafuse-wire/.venv/Scripts/qfw.exe", "serve", "--port", "8790"),
        ports=(8790,),
    )
    mcp = host.add_process(
        ("D:/workplace/Wire/quotafuse-wire/.venv/Scripts/qfw.exe", "mcp", "http", "--port", "8791"),
        ports=(8791,),
    )
    wire.stop()
    assert {pid for pid, _force in host.terminated} >= {web, mcp}
    assert host.port_owner == {}


def test_stop_never_touches_a_foreign_port_owner(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path)
    wire = _wire(adapters)
    host.add_process(("D:/other/tool.exe", "--port", "8790"), ports=(8790,))
    wire.stop()
    assert host.terminated == []
    assert 8790 in host.port_owner


def test_start_refuses_when_port_is_held_by_a_foreign_process(tmp_path: Path) -> None:
    from ladderstock.supervisor.engine import SupervisorError

    host, adapters = _adapters(tmp_path)
    wire = _wire(adapters)
    host.add_process(("D:/other/tool.exe", "--port", "8790"), ports=(8790,))
    with pytest.raises(SupervisorError, match="占用"):
        wire.start()


def test_tickhub_starts_mcp_and_admin_as_two_faces(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path)
    tickhub = cast(BaseAdapter, adapters["tickhub"])
    tickhub.start()
    commands = [argv for argv, _cwd, _env in host.spawns]
    assert any("start-all" in argv for argv in commands)
    assert any("admin" in argv and "serve" in argv for argv in commands)
    assert len(commands) == 2


# --------------------------------------------------------------------------- #
# financecoffer
# --------------------------------------------------------------------------- #


def _fc_host() -> FakeHost:
    host = FakeHost()
    host.respond(
        "wsl.exe -l -v", CommandResult(returncode=0, stdout="* Ubuntu-24.04    Running    2")
    )
    host.respond(
        "com.docker.compose.service=api",
        CommandResult(returncode=0, stdout="financecoffer-api-1"),
    )
    host.respond("FC_STOPPED", CommandResult(returncode=0, stdout="FC_STOPPED=yes"))
    return host


def test_financecoffer_probe_needs_both_windows_port_and_wsl_api_container(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path, _fc_host())
    fc = cast(FinanceCofferAdapter, adapters["financecoffer"])
    host.add_process(("C:/windows/system32/lxss/wslhost.exe", "{guid}", "932"), ports=(8100,))
    assert fc.probe() is True

    # 端口还在转发（wslhost 残留），但 WSL 内没有 api 容器 → 失败关闭
    host.responses = [("com.docker.compose.service=api", CommandResult(returncode=0, stdout=""))]
    host.port_owner[8100] = host.add_process(("wslhost.exe",), name="wslhost.exe")
    fc._cache.clear()
    fc._fc_cache = (0.0, "")
    assert fc.probe() is False


def test_financecoffer_start_runs_compose_then_keeps_an_anchor(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path, _fc_host())
    fc = cast(FinanceCofferAdapter, adapters["financecoffer"])
    spawned = fc.start()

    joined_runs = [" ".join(argv) for argv in host.runs]
    assert any("docker start $ids" in line or "docker compose" in line for line in joined_runs)
    assert spawned.pid in host.procs
    anchor_argv = host.procs[spawned.pid].cmdline
    assert "sleep infinity" in " ".join(anchor_argv)
    assert "Ubuntu-24.04" in anchor_argv


def test_financecoffer_start_refuses_foreign_port_owner_without_side_effects(
    tmp_path: Path,
) -> None:
    """QUOT-516：Windows 侧占口 → 拒绝启动，绝不先把 WSL 容器拉起来。"""
    host, adapters = _adapters(tmp_path, _fc_host())
    # 模块未运行（WSL 内无 api 容器）而端口被外部占住 = 真冲突形态
    host.responses = [
        (needle, result)
        for needle, result in host.responses
        if "com.docker.compose.service=api" not in needle
    ]
    host.respond("com.docker.compose.service=api", CommandResult(returncode=0, stdout=""))
    fc = cast(FinanceCofferAdapter, adapters["financecoffer"])
    host.add_process(("D:/other/squatter.exe", "--port", "8100"), ports=(8100,))
    with pytest.raises(SupervisorError, match="8100"):
        fc.start()
    assert host.spawns == []
    # 只允许识别类查询（docker ps 探容器），绝不允许 compose up / docker start
    joined_runs = [" ".join(argv) for argv in host.runs]
    assert not any("docker compose" in line or "docker start" in line for line in joined_runs)


def test_financecoffer_start_proceeds_when_own_container_already_listening(
    tmp_path: Path,
) -> None:
    host, adapters = _adapters(tmp_path, _fc_host())
    fc = cast(FinanceCofferAdapter, adapters["financecoffer"])
    host.add_process(("C:/windows/system32/lxss/wslhost.exe", "{guid}"), ports=(8100,))
    spawned = fc.start()
    assert spawned.pid in host.procs


def test_financecoffer_distro_override_wins_over_wsl_list(tmp_path: Path) -> None:
    env = _make_peer_dirs(tmp_path)
    env["LDST_WSL_DISTRO"] = "Debian-12"
    fake = FakeHost()
    adapters = build_default_adapters(
        host=fake, env=env, log_dir=tmp_path / "logs", evidence_cache_sec=0.0
    )
    fc = cast(FinanceCofferAdapter, adapters["financecoffer"])
    assert fc.distro() == "Debian-12"
    assert all("-l" not in argv for argv in fake.runs)


def test_financecoffer_stop_reads_back_container_gone(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path, _fc_host())
    fc = cast(FinanceCofferAdapter, adapters["financecoffer"])
    fc.start()
    host.port_owner.clear()
    fc.stop()
    joined_runs = [" ".join(argv) for argv in host.runs]
    assert any("FC_STOPPED" in line for line in joined_runs)


# --------------------------------------------------------------------------- #
# proxyfleet
# --------------------------------------------------------------------------- #


def test_proxyfleet_start_and_stop_go_through_the_scheduled_task(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path)
    pf = cast(ProxyFleetAdapter, adapters["proxyfleet"])
    host.respond(
        "schtasks.exe /run",
        CommandResult(returncode=0, stdout="SUCCESS"),
    )
    # /run 之后任务拉起的 pfctl 进程（计划任务垫片 → python pfctl.py run）
    host.add_process(
        ("D:/workplace/ruanjian/python312/python.exe", "D:/workplace/ProxyFleet/pfctl.py", "run"),
        ports=(8788, 8789),
    )
    spawned = pf.start()
    assert spawned.pid in host.procs
    assert any("/enable" in argv for argv in host.runs)

    pf.stop()
    ended = [" ".join(argv) for argv in host.runs]
    assert any("/disable" in line for line in ended)
    assert any("/end" in line for line in ended)


def test_proxyfleet_start_refuses_foreign_port_owner_without_triggering_task(
    tmp_path: Path,
) -> None:
    """QUOT-516：占口 → 拒绝启动，绝不先 enable/run 计划任务再等超时装死。"""
    host, adapters = _adapters(tmp_path)
    pf = cast(ProxyFleetAdapter, adapters["proxyfleet"])
    host.add_process(("D:/other/gateway.exe", "--port", "8788"), ports=(8788,))
    with pytest.raises(SupervisorError, match="8788"):
        pf.start()
    # 识别查询之外零命令：绝不 enable/run 计划任务
    assert all(argv[0] != "schtasks.exe" for argv in host.runs)


def test_proxyfleet_start_proceeds_when_own_pfctl_already_listening(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path)
    pf = cast(ProxyFleetAdapter, adapters["proxyfleet"])
    host.respond("schtasks.exe /run", CommandResult(returncode=0, stdout="SUCCESS"))
    host.add_process(
        ("D:/workplace/ruanjian/python312/python.exe", "D:/workplace/ProxyFleet/pfctl.py", "run"),
        ports=(8788, 8789),
    )
    spawned = pf.start()
    assert spawned.pid in host.procs


def test_proxyfleet_probe_is_false_for_a_foreign_listener(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path)
    pf = cast(ProxyFleetAdapter, adapters["proxyfleet"])
    host.add_process(("D:/other/gateway.exe", "--port", "8788"), ports=(8788,))
    assert pf.probe() is False


# --------------------------------------------------------------------------- #
# identify(port=None) 契约（QUOT-546）：单端口 PortEvidence / 无参聚合 Identification
# --------------------------------------------------------------------------- #


def test_financecoffer_identify_port_returns_single_port_evidence(tmp_path: Path) -> None:
    """带 port 调用返回单端口 PortEvidence（供 page_boot._identify 认领），不再是聚合。"""
    host, adapters = _adapters(tmp_path, _fc_host())
    fc = cast(FinanceCofferAdapter, adapters["financecoffer"])
    pid = host.add_process(("C:/windows/system32/lxss/wslhost.exe", "{guid}"), ports=(8100,))

    evidence = fc.identify(8100)

    assert isinstance(evidence, PortEvidence)
    assert evidence.port == 8100
    assert evidence.listening is True
    assert evidence.matched is True
    assert evidence.pid == pid


def test_financecoffer_identify_no_arg_returns_aggregate(tmp_path: Path) -> None:
    """无参 identify() 仍返回聚合 Identification，.matched / .ports 口径不变。"""
    host, adapters = _adapters(tmp_path, _fc_host())
    fc = cast(FinanceCofferAdapter, adapters["financecoffer"])
    host.add_process(("C:/windows/system32/lxss/wslhost.exe", "{guid}"), ports=(8100,))

    identification = fc.identify()

    assert isinstance(identification, Identification)
    assert identification.module == "financecoffer"
    assert identification.matched is True
    assert tuple(item.port for item in identification.ports) == MODULE_PORTS["financecoffer"]


def test_financecoffer_identify_port_not_listening_fails_closed(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path, _fc_host())
    fc = cast(FinanceCofferAdapter, adapters["financecoffer"])

    evidence = fc.identify(8100)

    assert isinstance(evidence, PortEvidence)
    assert evidence.listening is False
    assert evidence.matched is False
    assert evidence.pid is None


def test_proxyfleet_identify_port_returns_single_port_evidence(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path)
    pf = cast(ProxyFleetAdapter, adapters["proxyfleet"])
    pid = host.add_process(
        ("D:/workplace/ruanjian/python312/python.exe", "D:/workplace/ProxyFleet/pfctl.py", "run"),
        ports=(8788, 8789),
    )

    evidence = pf.identify(8788)

    assert isinstance(evidence, PortEvidence)
    assert evidence.port == 8788
    assert evidence.listening is True
    assert evidence.matched is True
    assert evidence.pid == pid


def test_proxyfleet_identify_no_arg_returns_aggregate(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path)
    pf = cast(ProxyFleetAdapter, adapters["proxyfleet"])
    host.add_process(
        ("D:/workplace/ruanjian/python312/python.exe", "D:/workplace/ProxyFleet/pfctl.py", "run"),
        ports=(8788, 8789),
    )

    identification = pf.identify()

    assert isinstance(identification, Identification)
    assert identification.module == "proxyfleet"
    assert identification.matched is True
    assert tuple(item.port for item in identification.ports) == MODULE_PORTS["proxyfleet"]


def test_proxyfleet_identify_port_foreign_listener_not_matched(tmp_path: Path) -> None:
    """端口在听但命令行不命中：matched=False（认领失败关闭，绝不终止）。"""
    host, adapters = _adapters(tmp_path)
    pf = cast(ProxyFleetAdapter, adapters["proxyfleet"])
    host.add_process(("D:/other/gateway.exe", "--port", "8788"), ports=(8788,))

    evidence = pf.identify(8788)

    assert isinstance(evidence, PortEvidence)
    assert evidence.listening is True
    assert evidence.matched is False


# --------------------------------------------------------------------------- #
# proxypool（按需模块）
# --------------------------------------------------------------------------- #


def _pool_mapping(
    mapping_id: str = "multica-api", port: int = 18080, *, enabled: bool = True
) -> ProxyPoolMapping:
    return ProxyPoolMapping(
        mapping_id=mapping_id,
        listen_port=port,
        upstream_host="127.0.0.1",
        upstream_port=8080,
        enabled=enabled,
        owner="ai",
    )


def _pool(
    host: FakeHost,
    tmp_path: Path,
    *,
    mappings: tuple[ProxyPoolMapping, ...] | None = None,
    listen_host: str = "127.0.0.1",
) -> ProxyPoolAdapter:
    config = ProxyPoolConfig(
        enabled=False,
        auto_start=False,
        listen_host=listen_host,
        mappings=mappings or (_pool_mapping(),),
    )
    return ProxyPoolAdapter(
        config=config,
        host=host,
        log_dir=tmp_path / "logs",
        evidence_cache_sec=0.0,
        spawn_wait_sec=1.0,
        graceful_wait_sec=1.0,
        readback_wait_sec=1.0,
    )


def test_proxypool_is_registered_on_demand_and_build_never_spawns(tmp_path: Path) -> None:
    assert "proxypool" in MODULE_PORTS
    assert "proxypool" in ON_DEMAND_MODULES
    assert "proxypool" not in MODULE_ORDER

    host, adapters = _adapters(tmp_path)
    pool = cast(ProxyPoolAdapter, adapters["proxypool"])
    assert pool.ports == ()
    assert host.spawns == []
    # 装配期只读配置，不注册计划任务、不写任何自启入口
    assert all("schtasks" not in argv for argv in host.runs)


def test_proxypool_start_spawns_one_forwarder_per_enabled_mapping(tmp_path: Path) -> None:
    host = FakeHost()
    pool = _pool(
        host,
        tmp_path,
        mappings=(
            _pool_mapping("multica-api", 18080),
            _pool_mapping("local-web", 18081),
            _pool_mapping("disabled-one", 18082, enabled=False),
        ),
    )
    spawned = pool.start()
    assert len(host.spawns) == 2
    assert spawned.cmdline[0] == sys.executable
    assert "proxypool_forwarder" in " ".join(spawned.cmdline)
    assert "multica-api" in spawned.cmdline
    assert 18080 in host.port_owner
    assert 18081 in host.port_owner
    assert 18082 not in host.port_owner
    assert pool.probe() is True


def test_proxypool_probe_requires_a_real_tcp_handshake(tmp_path: Path) -> None:
    """端口在监听表里但真实建连失败（只 listen 不 accept / 半死）→ probe 必须 False。"""
    host = FakeHost()
    pool = _pool(host, tmp_path)
    pool.start()
    assert pool.probe() is True

    host.unconnectable.add(18080)
    assert pool.probe() is False
    evidence = cast(PortEvidence, pool.identify(18080))
    assert evidence.listening is True
    assert evidence.matched is True
    assert "TCP 建连失败" in evidence.detail


def test_proxypool_probe_fails_closed_for_a_foreign_listener(tmp_path: Path) -> None:
    host = FakeHost()
    host.add_process(("D:/other/relay.exe", "--port", "18080"), ports=(18080,))
    pool = _pool(host, tmp_path)
    assert pool.probe() is False
    evidence = cast(PortEvidence, pool.identify(18080))
    assert evidence.listening is True
    assert evidence.matched is False


def test_proxypool_start_refuses_a_port_that_collides_with_another_module(
    tmp_path: Path,
) -> None:
    host = FakeHost()
    pool = _pool(host, tmp_path, mappings=(_pool_mapping("clash", 8790),))
    with pytest.raises(SupervisorError, match="8790"):
        pool.start()
    assert host.spawns == []


def test_proxypool_start_refuses_a_foreign_occupant_and_names_the_port(
    tmp_path: Path,
) -> None:
    host = FakeHost()
    foreign = host.add_process(("D:/other/relay.exe", "--port", "18080"), ports=(18080,))
    pool = _pool(host, tmp_path)
    with pytest.raises(SupervisorError, match="18080"):
        pool.start()
    assert host.spawns == []
    assert host.terminated == []
    assert 18080 in host.port_owner
    assert foreign in host.procs


def test_proxypool_start_adopts_an_existing_forwarder_instead_of_spawning_another(
    tmp_path: Path,
) -> None:
    host = FakeHost()
    existing = host.add_process(
        (
            "C:/python/python.exe",
            "C:/repo/src/ladderstock/supervisor/proxypool_forwarder.py",
            "--id",
            "multica-api",
            "--listen-host",
            "127.0.0.1",
            "--listen-port",
            "18080",
        ),
        name="python.exe",
        ports=(18080,),
    )
    pool = _pool(host, tmp_path)
    spawned = pool.start()
    assert host.spawns == []
    assert spawned.pid == existing
    assert [item.ownership for item in pool.adopted] == ["adopted"]
    assert pool.adopted[0].mapping_id == "multica-api"
    assert pool.probe() is True


def test_proxypool_stop_closes_ports_and_leaves_no_residual_process(tmp_path: Path) -> None:
    host = FakeHost()
    pool = _pool(host, tmp_path)
    pool.start()
    pid = pool._roots[0]
    pool.stop()
    assert 18080 not in host.port_owner
    assert pid not in host.procs
    assert host.find_processes("proxypool_forwarder") == ()
    assert pool.probe() is False


def test_proxypool_stop_refuses_to_claim_success_while_a_forwarder_remains(
    tmp_path: Path,
) -> None:
    host = FakeHost()
    pool = _pool(host, tmp_path)
    pool.start()
    # 同映射 Id 的遗留进程（不在端口上、也不是本次根）→ 停止后残留扫描必须能发现
    host.add_process(
        (
            "C:/python/python.exe",
            "C:/repo/src/ladderstock/supervisor/proxypool_forwarder.py",
            "--id",
            "multica-api",
        ),
        name="python.exe",
    )
    with pytest.raises(SupervisorError, match="残留"):
        pool.stop()


def test_proxypool_stop_never_touches_a_foreign_listener(tmp_path: Path) -> None:
    host = FakeHost()
    foreign = host.add_process(("D:/other/relay.exe", "--port", "18080"), ports=(18080,))
    pool = _pool(host, tmp_path)
    pool.stop()
    assert host.terminated == []
    assert foreign in host.procs


def test_proxypool_config_is_loaded_from_host_config(tmp_path: Path) -> None:
    host = FakeHost()
    row = {
        "Name": "proxypool",
        "Enabled": False,
        "AutoStart": False,
        "ListenHost": "127.0.0.1",
        "Mappings": [
            {
                "Id": "multica-api",
                "Listen": 18080,
                "UpstreamHost": "127.0.0.1",
                "UpstreamPort": 8080,
                "Enabled": True,
                "Owner": "ai",
            }
        ],
        "SelfHeal": {"MaxRestarts": 2, "WindowMinutes": 10},
        "NeedsHumanOnLimit": True,
    }
    host.respond("Import-PowerShellDataFile", CommandResult(returncode=0, stdout=json.dumps(row)))
    config_path = tmp_path / "LdstHostConfig.psd1"
    config_path.write_text("", encoding="utf-8")
    env = _make_peer_dirs(tmp_path)
    env["LDST_HOST_CONFIG"] = str(config_path)

    built = build_default_adapters(
        host=host, env=env, log_dir=tmp_path / "logs", evidence_cache_sec=0.0
    )
    pool = cast(ProxyPoolAdapter, built["proxypool"])
    assert pool.config.enabled is False
    assert pool.config.auto_start is False
    assert pool.ports == (18080,)
    assert pool.config.mappings[0].mapping_id == "multica-api"
    assert pool.config.mappings[0].owner == "ai"


# --------------------------------------------------------------------------- #
# env 文件
# --------------------------------------------------------------------------- #


def test_load_env_file_ignores_comments_and_strips_quotes(tmp_path: Path) -> None:
    path = tmp_path / "peer.env"
    path.write_text("# comment\nA=1\nB='two words'\n\nC=\"three\"\n", encoding="utf-8")
    assert load_env_file(path) == {"A": "1", "B": "two words", "C": "three"}


# --------------------------------------------------------------------------- #
# watch 看门狗（QUOT-523）
# --------------------------------------------------------------------------- #


def _watch_launcher(tmp_path: Path) -> tuple[str, ...]:
    exe = tmp_path / "launcher" / "ldst.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    return (str(exe),)


def _build_watch(host: FakeHost, tmp_path: Path, env: dict[str, str]) -> BaseAdapter:
    built = build_default_adapters(
        host=host, env=env, log_dir=tmp_path / "logs", evidence_cache_sec=0.0
    )
    return cast(BaseAdapter, built["watch"])


def test_watch_is_a_portless_supervised_module(tmp_path: Path) -> None:
    assert "watch" in MODULE_ORDER
    assert MODULE_ORDER[-1] == "watch"
    # QUOT-589 重修：看门狗不再监听 5952/5953，全机只留 5950/5951
    assert MODULE_PORTS["watch"] == ()
    _host, adapters = _adapters(tmp_path)
    assert adapters["watch"].ports == ()


def test_watch_start_spawns_the_watchdog_that_spawns_the_panel(tmp_path: Path) -> None:
    host = FakeHost()
    env = _make_peer_dirs(tmp_path)
    launcher = _watch_launcher(tmp_path)
    env["LDST_LAUNCHER"] = launcher[0]

    spawned = _build_watch(host, tmp_path, env).start()
    assert len(host.spawns) == 1
    argv, cwd, child_env = host.spawns[0]
    # `ldst` 是入口点、不吃 `-m`：启动器后必须直接跟子命令（QUOT-523 实测回归）
    assert argv[:2] == (launcher[0], "watchdog")
    assert "--port" not in argv  # 无端口：不监听任何端口（QUOT-589 重修）
    assert "--spawn-cmd" in argv
    assert cwd == _ROOT
    assert spawned.cmdline == argv
    # 看板重启走同启动器的 `run --port <按环境>`，不再经 schtasks；
    # env 未设 LDST_ENV → prod → 5950
    assert json.loads(argv[-1]) == [launcher[0], "run", "--port", "5950"]
    # 看门狗探活目标同样按环境：prod 指向 5950
    assert "--api" in argv
    assert argv[argv.index("--api") + 1] == "http://127.0.0.1:5950"
    # 托管标记落到子进程环境（进程级开关，不依赖继承自启动它的外壳）
    assert child_env["LDST_WATCHDOG_SUPERVISED"] == "1"


def test_watch_is_test_env_scoped_to_5951(tmp_path: Path) -> None:
    """QUOT-589 第二轮：test 环境看门狗只探/只重启 5951，绝不触碰正式 5950。"""
    host = FakeHost()
    env = _make_peer_dirs(tmp_path)
    launcher = _watch_launcher(tmp_path)
    env["LDST_LAUNCHER"] = launcher[0]
    env["LDST_ENV"] = "test"

    _build_watch(host, tmp_path, env).start()
    argv, _cwd, _child_env = host.spawns[0]

    assert json.loads(argv[-1]) == [launcher[0], "run", "--port", "5951"]
    assert argv[argv.index("--api") + 1] == "http://127.0.0.1:5951"
    assert "5950" not in argv


def _write_ports_yaml(config: Path, board: int) -> None:
    import yaml

    config.mkdir(parents=True, exist_ok=True)
    (config / "ports.yaml").write_text(
        yaml.safe_dump({"env": "test", "board": board}), encoding="utf-8"
    )


def test_resolve_panel_port_test_env_defaults_to_5951(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整合面①：test 环境无避让记录 → 默认 5951，绝不回落 5950。"""
    monkeypatch.setenv("LDST_CONFIG_DIR", str(tmp_path / "config"))
    env: dict[str, str] = {"LDST_ENV": "test"}
    assert resolve_panel_port(env) == 5951


def test_resolve_panel_port_prefers_board_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整合面②：有避让记录时 spawn-cmd 与 --api 同用记录端口。"""
    config = tmp_path / "install" / "config"
    _write_ports_yaml(config, 5971)
    monkeypatch.setenv("LDST_CONFIG_DIR", str(config))
    env: dict[str, str] = {"LDST_ENV": "test", "LDST_LAUNCHER": "ldst.exe"}

    assert resolve_panel_port(env) == 5971
    cmd = json.loads(_panel_spawn_command_for_test(env))
    assert cmd == ["ldst.exe", "run", "--port", "5971"]
    argv = _watch_argv_for_test(env, tmp_path)
    assert argv[argv.index("--api") + 1] == "http://127.0.0.1:5971"


def test_resolve_panel_port_explicit_override_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整合面③：LDST_PANEL_PORT 优先于避让记录。"""
    config = tmp_path / "install" / "config"
    _write_ports_yaml(config, 5971)
    monkeypatch.setenv("LDST_CONFIG_DIR", str(config))
    env: dict[str, str] = {
        "LDST_ENV": "test",
        "LDST_PANEL_PORT": "5981",
        "LDST_LAUNCHER": "ldst.exe",
    }

    assert resolve_panel_port(env) == 5981
    assert json.loads(_panel_spawn_command_for_test(env))[-1] == "5981"
    argv = _watch_argv_for_test(env, tmp_path)
    assert argv[argv.index("--api") + 1] == "http://127.0.0.1:5981"


def test_resolve_panel_port_test_env_never_touches_5950(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整合面④（红线）：test 环境任何组合下端口都不出现 5950。"""
    # 组合 A：无记录
    monkeypatch.setenv("LDST_CONFIG_DIR", str(tmp_path / "cfg-a"))
    env_a: dict[str, str] = {"LDST_ENV": "test"}
    # 组合 B：有避让记录
    config_b = tmp_path / "cfg-b"
    _write_ports_yaml(config_b, 5971)
    monkeypatch.setenv("LDST_CONFIG_DIR", str(config_b))
    env_b: dict[str, str] = {"LDST_ENV": "test", "LDST_LAUNCHER": "ldst.exe"}
    # 组合 C：显式覆盖
    env_c: dict[str, str] = {"LDST_ENV": "test", "LDST_PANEL_PORT": "5981"}

    for env in (env_a, env_b, env_c):
        port = resolve_panel_port(env)
        assert port != 5950
        spawn_cmd = json.loads(_panel_spawn_command_for_test(env))
        assert spawn_cmd[-1] != "5950"
        argv = _watch_argv_for_test(env, tmp_path)
        assert "5950" not in argv
        assert "5950" not in argv[argv.index("--api") + 1]


def _panel_spawn_command_for_test(env: dict[str, str]) -> str:
    from ladderstock.supervisor.module_adapters import _panel_spawn_command

    return _panel_spawn_command(env)


def _watch_argv_for_test(env: dict[str, str], tmp_path: Path) -> tuple[str, ...]:
    host = FakeHost()
    launcher = tmp_path / "bin" / "ldst.exe"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_bytes(b"")
    full_env = dict(env)
    full_env["LDST_LAUNCHER"] = str(launcher)
    watch = cast(
        BaseAdapter,
        build_default_adapters(
            host=host,
            env=full_env,
            log_dir=tmp_path / "logs",
            evidence_cache_sec=0.0,
        )["watch"],
    )
    return cast(Any, watch)._argv


def test_watch_probe_fails_closed_for_a_foreign_watchdog(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path)
    host.add_process(("other-watchdog.exe", "--port", "5951"))
    watch = cast(BaseAdapter, adapters["watch"])
    assert watch.probe() is False


def test_watch_probe_requires_every_match_token(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path)
    watch = cast(BaseAdapter, adapters["watch"])
    # 别的 CLI 的 watchdog 子命令不算本模块
    host.add_process(("python.exe", "-m", "someother.cli.main", "watchdog"))
    assert watch.probe() is False
    host.procs.clear()
    # ldst.exe 形态：cmdline 没有 `-m ladderstock.cli.main`，走备用令牌组
    host.add_process(
        (
            r"D:\LadderStock\.venv\Scripts\python.exe",
            r"D:\LadderStock\.venv\Scripts\ldst.exe",
            "watchdog",
        )
    )
    assert watch.probe() is True
    host.procs.clear()
    host.add_process(("python.exe", "-m", "ladderstock.cli.main", "watchdog"))
    assert watch.probe() is True


def test_launcher_entry_prefers_env_then_venv_next_to_install_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit" / "ldst.exe"
    assert launcher_entry({"LDST_LAUNCHER": str(explicit)}) == (str(explicit),)

    root = tmp_path / "install"
    venv_exe = root / "LadderStock" / ".venv" / "Scripts" / "ldst.exe"
    venv_exe.parent.mkdir(parents=True)
    venv_exe.write_bytes(b"")
    monkeypatch.setenv("LDST_INSTALL_ROOT", str(root))
    assert launcher_entry({}) == (str(venv_exe),)

    # 找不到 venv 启动器就退回当前解释器（源码树形态：python -m ... 等价）
    monkeypatch.setenv("LDST_INSTALL_ROOT", str(tmp_path / "empty"))
    assert launcher_entry({}) == (sys.executable, "-m", "ladderstock.cli.main")

    # 显式 LDST_LAUNCHER 是 ldst 入口点：原样使用，不追加 -m
    assert launcher_entry({"LDST_LAUNCHER": str(explicit)}) == (str(explicit),)


# --------------------------------------------------------------------------- #
# 启动前检查：bundle 相对（QUOT-523）
# --------------------------------------------------------------------------- #


def _item(report: PreflightReport, name: str) -> Any:
    return next(item for item in report.items if item.module == name)


def test_preflight_reports_peers_that_miss_their_entry_files(tmp_path: Path) -> None:
    env = dict(_make_peer_dirs(tmp_path))
    empty = tmp_path / "no-such-peer"
    env["LDST_PEER_DIR_WIRE"] = str(empty)
    (tmp_path / "peers" / "financecoffer" / "deploy").mkdir(parents=True)

    report = preflight(env)
    assert _item(report, "tickhub").ok is True
    wire = _item(report, "wire")
    assert wire.ok is False
    assert str(empty / ".venv" / "Scripts" / "qfw.exe") in wire.detail
    assert report.ok is False
    assert {item.module for item in report.failures()} == {"wire"}
    assert "[FAIL] wire" in report.describe()


def test_preflight_financecoffer_needs_the_wsl_compose_dir(tmp_path: Path) -> None:
    env = dict(_make_peer_dirs(tmp_path))
    assert _item(preflight(env), "financecoffer").ok is False

    (tmp_path / "peers" / "financecoffer" / "deploy").mkdir(parents=True)
    assert _item(preflight(env), "financecoffer").ok is True


def test_preflight_watch_needs_a_launcher_and_reports_portless(tmp_path: Path) -> None:
    env = dict(_make_peer_dirs(tmp_path))
    env["LDST_LAUNCHER"] = _watch_launcher(tmp_path)[0]

    watch = _item(preflight(env), "watch")
    assert watch.ok is True
    assert "5952" not in watch.detail  # 无端口：preflight 不再报任何端口
    assert "LDST_LAUNCHER" in watch.detail

    env["LDST_LAUNCHER"] = str(tmp_path / "missing" / "ldst.exe")
    watch = _item(preflight(env), "watch")
    assert watch.ok is False
    assert "不存在" in watch.detail


def test_preflight_proxyfleet_reports_the_scheduled_task_only(tmp_path: Path) -> None:
    env = dict(_make_peer_dirs(tmp_path))
    env["LDST_PROXYFLEET_TASK"] = "ProxyFleet-Canary"
    item = _item(preflight(env), "proxyfleet")
    assert item.ok is True
    assert "ProxyFleet-Canary" in item.detail
    assert item.paths == ()


def test_preflight_leaves_proxypool_out_of_the_startup_path(tmp_path: Path) -> None:
    env = dict(_make_peer_dirs(tmp_path))
    assert all(item.module != "proxypool" for item in preflight(env).items)
    assert all(item.module != "proxypool" for item in preflight(env, include_on_demand=False).items)


def test_preflight_include_on_demand_reports_the_proxypool_config(tmp_path: Path) -> None:
    config_path = tmp_path / "LdstHostConfig.psd1"
    config_path.write_text("", encoding="utf-8")
    env = dict(_make_peer_dirs(tmp_path))
    env["LDST_HOST_CONFIG"] = str(config_path)

    report = preflight(env, include_on_demand=True)
    item = _item(report, "proxypool")
    # 隔离环境下宿主 pivot 读不到 psd1（真实 powershell）：报告「未启用或无映射」，不判定失败
    assert item.ok is True
    assert str(config_path) in item.detail
    assert item.paths == (config_path,)


def test_preflight_round_trips_the_psd1_through_the_proxypool_loader(tmp_path: Path) -> None:
    host = FakeHost()
    host.respond(
        "Import-PowerShellDataFile",
        CommandResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "Name": "proxypool",
                    "Enabled": False,
                    "AutoStart": False,
                    "ListenHost": "127.0.0.1",
                    "Mappings": [
                        {
                            "Id": "multica-api",
                            "Listen": 18080,
                            "UpstreamHost": "127.0.0.1",
                            "UpstreamPort": 8080,
                            "Enabled": True,
                            "Owner": "ai",
                        }
                    ],
                }
            ),
        ),
    )
    config_path = tmp_path / "LdstHostConfig.psd1"
    config_path.write_text("", encoding="utf-8")
    config = load_proxypool_config(host=host, path=config_path)
    assert [mapping.mapping_id for mapping in config.enabled_mappings()] == ["multica-api"]


def test_preflight_never_falls_back_to_hardcoded_workplace_paths(tmp_path: Path) -> None:
    root = tmp_path / "install"
    env = {"LDST_INSTALL_ROOT": str(root), "LDST_LAUNCHER": sys.executable}
    report = preflight(env)

    assert report.install_root == root
    assert report.ok is False
    for item in report.items:
        for path in item.paths:
            if path == Path(sys.executable):
                continue  # 退回当前解释器：那是解释器自己的位置，不是 peer 路径
            text = str(path)
            assert "workplace" not in text.lower()
            assert "QuotaFuseModules" not in text
            assert text.startswith(str(root))
    # 各 peer 缺的是安装根下的同级目录，而不是某个旧部署路径
    assert str(root / "TickHub-CN") in _item(report, "tickhub").detail
