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
    PortEvidence,
    ProcessSnapshot,
    ProxyFleetAdapter,
    ProxyPoolAdapter,
    ProxyPoolConfig,
    ProxyPoolMapping,
    build_default_adapters,
    load_env_file,
)

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
        self.spawns: list[tuple[tuple[str, ...], Path]] = []
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
        self.spawns.append((argv_tuple, cwd))
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
    for argv, _cwd in host.spawns:
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
    commands = [argv for argv, _cwd in host.spawns]
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
    host.responses = [
        ("com.docker.compose.service=api", CommandResult(returncode=0, stdout=""))
    ]
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
