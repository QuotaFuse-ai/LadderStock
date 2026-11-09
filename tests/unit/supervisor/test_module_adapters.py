"""六模块适配器测试（QUOT-482-B）：全部走假宿主，不起真进程、不占真端口。

覆盖：
- 注册表口径（六模块 + 端口白名单）；
- 识别规则：端口在听 + cmdline 命中才算 probe 真，外部进程不算；
- 多面模块 start 拉起两条命令、stop 连 adopted 的另一面一起停；
- financecoffer 的 WSL 形态（发行版探测 / compose 停 / 容器回读）；
- proxyfleet 只经计划任务启停（enable→run / disable→end），不下发 taskkill。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from ladderstock.supervisor.adapters import MODULE_ORDER, MODULE_PORTS
from ladderstock.supervisor.module_adapters import (
    BaseAdapter,
    CommandResult,
    FinanceCofferAdapter,
    ProcessSnapshot,
    ProxyFleetAdapter,
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

    def owner_pid(self, port: int) -> int | None:
        return self.port_owner.get(port)

    def snapshot(self, pid: int) -> ProcessSnapshot | None:
        return self.procs.get(pid)

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
        if "--port" in argv_tuple:
            port = int(argv_tuple[argv_tuple.index("--port") + 1])
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


def test_registry_contains_exactly_six_modules_with_registered_ports(tmp_path: Path) -> None:
    _host, adapters = _adapters(tmp_path)
    assert set(adapters) == set(MODULE_ORDER)
    for name, adapter in adapters.items():
        assert tuple(adapter.ports) == MODULE_PORTS[name]


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


def test_proxyfleet_probe_is_false_for_a_foreign_listener(tmp_path: Path) -> None:
    host, adapters = _adapters(tmp_path)
    pf = cast(ProxyFleetAdapter, adapters["proxyfleet"])
    host.add_process(("D:/other/gateway.exe", "--port", "8788"), ports=(8788,))
    assert pf.probe() is False


# --------------------------------------------------------------------------- #
# env 文件
# --------------------------------------------------------------------------- #


def test_load_env_file_ignores_comments_and_strips_quotes(tmp_path: Path) -> None:
    path = tmp_path / "peer.env"
    path.write_text("# comment\nA=1\nB='two words'\n\nC=\"three\"\n", encoding="utf-8")
    assert load_env_file(path) == {"A": "1", "B": "two words", "C": "three"}
