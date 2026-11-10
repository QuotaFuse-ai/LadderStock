"""页面打开即自启自有子进程（QUOT-522）：契约 + 生命周期，全部假宿主。

验收口径落到断言上：

- 打开网页（`ensure()`）后 watch 扫描**当场在跑**：`watch/status.json` 落盘、
  `watch/events-<date>.jsonl` 去重追加，与 `ldst watch start` 同一份格式；
- 自有子进程（watch 守护 / 取数调度器）只在没有同名实例时拉起，二次打开
  `already_running`，不重复拉起；
- peer（tickhub / wire / …）**adopt-only**：命中识别规则只记账（origin=adopted，
  pid 不变），不 spawn / 不 terminate；外部进程占口如实记 `port_occupied`，绝不杀；
- `LDST_PAGE_AUTOSTART=0` 一个自有子进程都不拉；
- 关闭时只停自己拉起的（账本 origin=spawned），adopted 的 peer 一概不碰。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from ladderstock.supervisor.ledger import Ledger
from ladderstock.supervisor.module_adapters import (
    CommandResult,
    PortEvidence,
    ProcessSnapshot,
)
from ladderstock.supervisor.page_boot import (
    GUARD_RETRY_DELAYS,
    GUARD_SCAN_ARG_ENV,
    GUARD_STOP_ARG_ENV,
    SCHEDULER_MODULE,
    STATE_ADOPTED,
    STATE_ALREADY_RUNNING,
    STATE_NOT_LISTENING,
    STATE_PORT_OCCUPIED,
    STATE_SKIPPED,
    STATE_SPAWNED,
    STATE_WAITING_SOURCE,
    TICK_ENV,
    WATCH_GUARD_MODULE,
    PageBoot,
    WatchScanner,
    autostart_enabled,
    watch_guard_main,
)
from ladderstock.watch import status as watch_status

GENERATED_AT = "2026-11-10"

SCHED_CMD = ("python.exe", "-m", "ladderstock.cli.main", "schedule", "run")


class FakeHost:
    """端口表 + 进程表 + spawn 记录的假宿主（HostPivot 协议子集）。"""

    def __init__(self) -> None:
        self.port_owner: dict[int, int] = {}
        self.procs: dict[int, ProcessSnapshot] = {}
        self.spawns: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []
        self.terminated: list[tuple[int, bool]] = []
        self.runs: list[tuple[str, ...]] = []
        self.responses: list[tuple[str, CommandResult]] = []
        self._next_pid = 9000

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

    # ---- HostPivot 协议

    def listening(self, port: int) -> bool:
        return port in self.port_owner

    def handshake(self, host: str, port: int) -> bool:
        del host
        return port in self.port_owner

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

    def spawn(self, argv: object, *, cwd: Path, env: object, log_path: Path) -> int:
        argv_tuple = tuple(str(part) for part in argv)  # type: ignore[union-attr]
        env_map = {str(k): str(v) for k, v in dict(env or {}).items()}  # type: ignore[arg-type]
        self.spawns.append((argv_tuple, cwd, env_map))
        return self.add_process(argv_tuple, name=Path(argv_tuple[0]).name)

    def run(self, argv: object, *, timeout: float, cwd: Path | None = None) -> CommandResult:
        del timeout, cwd
        argv_tuple = tuple(str(part) for part in argv)  # type: ignore[union-attr]
        self.runs.append(argv_tuple)
        joined = " ".join(argv_tuple)
        for needle, result in self.responses:
            if needle in joined:
                return result
        return CommandResult(returncode=0)


class FakePeerAdapter:
    """peer 假适配器：`ports` + `identify(port)`，证据由宿主端口表推出。"""

    def __init__(
        self,
        name: str,
        ports: tuple[int, ...],
        host: FakeHost,
        *,
        match_tokens: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self.ports = ports
        self.host = host
        self.match_tokens = tuple(token.lower() for token in match_tokens)

    def identify(self, port: int) -> PortEvidence:
        if not self.host.listening(port):
            return PortEvidence(port=port, listening=False)
        pid = self.host.owner_pid(port)
        chain = self.host.parent_chain(pid) if pid is not None else ()
        snap = chain[0] if chain else None
        if snap is None:
            return PortEvidence(port=port, listening=True, pid=pid)
        joined = " ".join(snap.cmdline).replace("\\", "/").lower()
        matched = all(token in joined for token in self.match_tokens)
        return PortEvidence(
            port=port,
            listening=True,
            matched=matched,
            pid=snap.pid,
            name=snap.name,
            cmdline=tuple(snap.cmdline),
            parent_chain=chain,
            detail="fake",
        )


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #


def _write_samples(data_root: Path, *, day: str) -> Path:
    """两根隔 3 分钟、涨幅 ≥ 3% 的分钟样本 → 一轮扫描至少一条 threshold_up。"""
    target = data_root / "minute_bars"
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{day}.jsonl"
    rows = [
        {"ts": f"{day}T09:31:00", "code": "600000", "price": 10.00},
        {"ts": f"{day}T09:34:00", "code": "600000", "price": 10.50},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def _fresh_day() -> str:
    return date.today().isoformat()


def _boot(
    tmp_path: Path,
    host: FakeHost,
    *,
    adapters: dict[str, FakePeerAdapter] | None = None,
    env: dict[str, str] | None = None,
    threshold: float = 3.0,
) -> PageBoot:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    scanner = WatchScanner(tmp_path, threshold=threshold)
    return PageBoot(
        data_root=tmp_path,
        ledger=ledger,
        host=host,
        adapters=adapters or {},
        scanner=scanner,
        scheduler_argv=list(SCHED_CMD),
        run_cwd=tmp_path,
        log_dir=tmp_path / "logs",
        env={"LDST_PAGE_AUTOSTART": "1"} if env is None else env,
        tick_sec=30.0,
    )


# --------------------------------------------------------------------------- #
# 开关
# --------------------------------------------------------------------------- #


def test_autostart_switch_defaults_on_and_honours_off_values() -> None:
    assert autostart_enabled({}) is True
    assert autostart_enabled({"LDST_PAGE_AUTOSTART": "1"}) is True
    for raw in ("0", "false", "off", "no", "OFF", " False "):
        assert autostart_enabled({"LDST_PAGE_AUTOSTART": raw}) is False


# --------------------------------------------------------------------------- #
# 自有子进程：打开即拉起
# --------------------------------------------------------------------------- #


def test_page_open_scans_bars_and_spawns_owned_children(tmp_path: Path) -> None:
    """打开网页 → 当日样本当场扫一轮并落盘；watch 守护 + 取数器各拉一次。"""
    day = _fresh_day()
    _write_samples(tmp_path, day=day)
    host = FakeHost()
    boot = _boot(tmp_path, host)

    report = boot.ensure()

    assert report["ok"] is True
    assert report["enabled"] is True
    scan = report["watch_scan"]
    assert scan["ok"] is True and scan["date"] == day
    assert scan["bars"] == 2 and scan["new_events"] >= 1

    status = watch_status.read_status_document(watch_status.status_path(tmp_path))
    assert status.ok, status.error
    assert status.document is not None
    assert status.document["running"] is True
    assert status.document["mode"] == "page-autostart-once"
    assert status.document["date"] == day
    assert status.document["bars"] == 2
    events_file = tmp_path / "watch" / f"events-{day}.jsonl"
    assert events_file.is_file()
    assert len(events_file.read_text(encoding="utf-8").splitlines()) == scan["new_events"]

    assert report["actions"][WATCH_GUARD_MODULE] == STATE_SPAWNED
    assert report["actions"][SCHEDULER_MODULE] == STATE_SPAWNED
    spawned = [item[0] for item in host.spawns]
    assert any("__watch-guard" in argv for argv in spawned)
    assert SCHED_CMD in spawned
    guard_env = next(item[2] for item in host.spawns if "__watch-guard" in item[0])
    assert guard_env[TICK_ENV] == "30"
    assert json.loads(guard_env[GUARD_SCAN_ARG_ENV])[-2:] == [
        "--threshold",
        "3",
    ]

    ledger = boot.ledger
    for name in (WATCH_GUARD_MODULE, SCHEDULER_MODULE):
        record = ledger.record(name)
        assert record is not None
        assert record.origin == "spawned"
        assert record.state == "running"

    guard_pid = report["children"][WATCH_GUARD_MODULE]["pid"]
    scheduler_pid = report["children"][SCHEDULER_MODULE]["pid"]
    assert guard_pid in host.procs and scheduler_pid in host.procs
    assert host.terminated == []


def test_second_open_is_idempotent(tmp_path: Path) -> None:
    """二次打开（刷新 / 多标签页）：识别到同命令实例 → already_running，不再拉起。"""
    day = _fresh_day()
    _write_samples(tmp_path, day=day)
    host = FakeHost()
    boot = _boot(tmp_path, host)

    first = boot.ensure()
    spawns_after_first = len(host.spawns)
    again = boot.ensure(trigger="page-refresh")

    assert again["actions"][WATCH_GUARD_MODULE] == STATE_ALREADY_RUNNING
    assert again["actions"][SCHEDULER_MODULE] == STATE_ALREADY_RUNNING
    assert len(host.spawns) == spawns_after_first
    assert (
        again["children"][WATCH_GUARD_MODULE]["pid"] == first["children"][WATCH_GUARD_MODULE]["pid"]
    )
    # 重扫去重：同一批事件不重复追加
    assert again["watch_scan"]["new_events"] == 0
    events_file = tmp_path / "watch" / f"events-{day}.jsonl"
    assert (
        len(events_file.read_text(encoding="utf-8").splitlines())
        == first["watch_scan"]["event_count"]
    )


def test_existing_guard_launched_by_type_earlier_is_reused(tmp_path: Path) -> None:
    """已有同名守护（例如另一次打开先跑起来）→ 认领账本，不再拉起。"""
    host = FakeHost()
    boot = _boot(tmp_path, host)
    existing = host.add_process(boot._watch_guard_cmdline())

    report = boot.ensure()

    assert report["children"][WATCH_GUARD_MODULE]["pid"] == existing
    assert all("__watch-guard" not in argv for argv, _, _ in host.spawns)
    record = boot.ledger.record(WATCH_GUARD_MODULE)
    assert record is not None and record.pid == existing and record.origin == "spawned"


def test_no_samples_spawns_guard_in_waiting_mode(tmp_path: Path) -> None:
    """首屏没有分钟样本：不伪造信号，守护带退避在等样本，样本落盘后自动扫。"""
    host = FakeHost()
    boot = _boot(tmp_path, host)

    report = boot.ensure()

    assert report["watch_scan"]["ok"] is False
    assert report["watch_scan"]["waiting_source"] is True
    assert not watch_status.status_path(tmp_path).exists()
    assert report["actions"][WATCH_GUARD_MODULE] == STATE_WAITING_SOURCE
    guard = report["children"][WATCH_GUARD_MODULE]
    assert guard["pid"] in host.procs
    guard_env = next(item[2] for item in host.spawns if "__watch-guard" in item[0])
    delays = [float(part) for part in guard_env["LDST_WATCH_GUARD_WAIT"].split(",")]
    assert delays == list(GUARD_RETRY_DELAYS)
    assert guard_env[GUARD_STOP_ARG_ENV] == str(boot._stop_marker_path())


def test_scanner_falls_back_to_recent_day(tmp_path: Path) -> None:
    """今日无样本、最近几天有：按回看窗口取最近一份，不空转。"""
    previous = (date.today() - timedelta(days=1)).isoformat()
    _write_samples(tmp_path, day=previous)
    scanner = WatchScanner(tmp_path)

    day, source = scanner.resolve_source()
    assert day == previous and source is not None

    report = scanner.scan(trigger="page-open")
    assert report["ok"] is True and report["date"] == previous


def test_disabled_switch_spawns_nothing(tmp_path: Path) -> None:
    """LDST_PAGE_AUTOSTART=0：一个自有子进程都不拉，peer 也不认领。"""
    _write_samples(tmp_path, day=_fresh_day())
    host = FakeHost()
    boot = _boot(tmp_path, host, env={"LDST_PAGE_AUTOSTART": "0"}, adapters={})

    report = boot.ensure()

    assert report["enabled"] is False
    assert "watch_scan" not in report
    assert report["children"][WATCH_GUARD_MODULE]["state"] == STATE_SKIPPED
    assert report["children"][SCHEDULER_MODULE]["state"] == STATE_SKIPPED
    assert host.spawns == []
    assert not watch_status.status_path(tmp_path).exists()


# --------------------------------------------------------------------------- #
# peer：adopt-only
# --------------------------------------------------------------------------- #


def test_peer_is_adopted_without_touching_process(tmp_path: Path) -> None:
    """peer 在听且命令行命中：只记账 origin=adopted，pid 原样、不 spawn 不杀。"""
    host = FakeHost()
    peer_pid = host.add_process(
        ("tickhub.exe", "--port", "8000"),
        name="tickhub.exe",
        ports=(8000,),
    )
    adapters = {"tickhub": FakePeerAdapter("tickhub", (8000,), host, match_tokens=("tickhub",))}
    boot = _boot(tmp_path, host, adapters=adapters)

    report = boot.ensure()

    peer = report["peers"]["tickhub"]
    assert peer["state"] == STATE_ADOPTED
    assert peer["pid"] == peer_pid
    assert peer["origin"] == "adopted"
    record = boot.ledger.record("tickhub")
    assert record is not None
    assert record.pid == peer_pid and record.origin == "adopted"
    assert "tickhub" not in report["actions"]
    assert all("tickhub" not in " ".join(argv) for argv, _, _ in host.spawns)
    assert host.terminated == []
    assert peer_pid in host.procs


def test_adopted_peer_not_restarted_or_stopped_on_second_open(tmp_path: Path) -> None:
    host = FakeHost()
    peer_pid = host.add_process(("wire", "--port", "8790"), ports=(8790,))
    adapters = {"wire": FakePeerAdapter("wire", (8790,), host, match_tokens=("wire",))}
    boot = _boot(tmp_path, host, adapters=adapters)

    boot.ensure()
    boot.ensure()

    assert boot.ledger.record("wire").pid == peer_pid  # type: ignore[union-attr]
    assert host.terminated == []


def test_peer_not_listening_is_left_alone(tmp_path: Path) -> None:
    host = FakeHost()
    adapters = {"senate": FakePeerAdapter("senate", (8765, 8786), host, match_tokens=("senate",))}
    boot = _boot(tmp_path, host, adapters=adapters)

    report = boot.ensure()

    peer = report["peers"]["senate"]
    assert peer["state"] == STATE_NOT_LISTENING
    assert peer["pid"] is None
    assert boot.ledger.record("senate") is None
    assert all("senate" not in " ".join(argv) for argv, _, _ in host.spawns)


def test_foreign_occupant_reported_and_never_killed(tmp_path: Path) -> None:
    """端口在听但持有者不是该 peer：如实记 port_occupied，绝不终止。"""
    host = FakeHost()
    squatter = host.add_process(("squatter.exe",), name="squatter.exe", ports=(8100,))
    adapters = {
        "financecoffer": FakePeerAdapter(
            "financecoffer", (8100,), host, match_tokens=("financecoffer",)
        )
    }
    boot = _boot(tmp_path, host, adapters=adapters)

    report = boot.ensure()

    peer = report["peers"]["financecoffer"]
    assert peer["state"] == STATE_PORT_OCCUPIED
    assert squatter in host.procs
    assert host.terminated == []
    assert all("financecoffer" not in " ".join(argv) for argv, _, _ in host.spawns)


def test_child_failure_is_contained_in_report(tmp_path: Path) -> None:
    """自有子进程拉起失败（exe 不存在等）：记 failed，不拖垮看板启动、peer 照常认领。"""
    _write_samples(tmp_path, day=_fresh_day())

    class ExplodingHost(FakeHost):
        def spawn(self, argv: object, *, cwd: Path, env: object, log_path: Path) -> int:
            raise OSError("spawn failed")

    host = ExplodingHost()
    peer_pid = host.add_process(("tickhub", "--port", "8000"), ports=(8000,))
    adapters = {"tickhub": FakePeerAdapter("tickhub", (8000,), host, match_tokens=("tickhub",))}
    boot = _boot(tmp_path, host, adapters=adapters)

    report = boot.ensure()

    assert report["ok"] is True
    assert report["actions"][WATCH_GUARD_MODULE] == "failed"
    assert report["actions"][SCHEDULER_MODULE] == "failed"
    assert report["peers"]["tickhub"]["pid"] == peer_pid
    assert boot.last_report() is report


# --------------------------------------------------------------------------- #
# 关闭：只停自己拉起的
# --------------------------------------------------------------------------- #


def test_stop_children_only_stops_owned_children(tmp_path: Path) -> None:
    """先写退出标记等守护自退；假宿主不会自退 → 宽限期后强制终止（只这一个 pid）。"""
    host = FakeHost()
    peer_pid = host.add_process(("tickhub", "--port", "8000"), ports=(8000,))
    adapters = {"tickhub": FakePeerAdapter("tickhub", (8000,), host, match_tokens=("tickhub",))}
    boot = _boot(tmp_path, host, adapters=adapters)
    report = boot.ensure()
    guard_pid = report["children"][WATCH_GUARD_MODULE]["pid"]
    scheduler_pid = report["children"][SCHEDULER_MODULE]["pid"]

    outcomes = boot.stop_children()

    assert outcomes[WATCH_GUARD_MODULE] == "stopped"
    assert outcomes[SCHEDULER_MODULE] == "stopped"
    # 两个子进程都先等自退窗口（假宿主不会自退）再 /F 连根
    assert (guard_pid, True) in host.terminated
    assert (scheduler_pid, True) in host.terminated
    assert peer_pid in host.procs
    assert all(pid != peer_pid for pid, _ in host.terminated)
    assert not boot._stop_marker_path().exists()


def test_stop_children_prefers_guard_self_exit(tmp_path: Path) -> None:
    """守护看到退出标记自退（不劳 /F）：假宿主在 terminate_tree 前「自己」消失。"""
    host = FakeHost()
    boot = _boot(tmp_path, host)
    report = boot.ensure()
    guard_pid = report["children"][WATCH_GUARD_MODULE]["pid"]

    def fake_snapshot(pid: int) -> ProcessSnapshot | None:
        snap = host.procs.get(pid)
        if pid == guard_pid:
            return None
        return snap

    host.snapshot = fake_snapshot  # type: ignore[method-assign]
    outcomes = boot.stop_children()

    assert outcomes[WATCH_GUARD_MODULE] == "stopped"
    assert (guard_pid, True) not in host.terminated


def test_stop_children_skips_adopted_records(tmp_path: Path) -> None:
    host = FakeHost()
    boot = _boot(tmp_path, host)
    ledger = boot.ledger
    ledger.update(WATCH_GUARD_MODULE, pid=4242, origin="adopted", state="running")

    outcomes = boot.stop_children()

    assert outcomes[WATCH_GUARD_MODULE] == "not_owned"
    assert host.terminated == []


# --------------------------------------------------------------------------- #
# 守护入口 `ldst __watch-guard`
# --------------------------------------------------------------------------- #


def test_watch_guard_requires_scan_argv() -> None:
    assert watch_guard_main({}) == 2
    assert watch_guard_main({GUARD_SCAN_ARG_ENV: "not-json"}) == 2


class _StopLoop(Exception):
    """打断守护主循环（否则 while True 永远不返回）。"""


@pytest.fixture()
def guard_loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[list[object], Path]:
    """用虚拟时钟替换 subprocess.call / time.sleep / time.monotonic。"""
    import ladderstock.supervisor.page_boot as module

    record: list[object] = []
    clock = [0.0]
    scans = [0]

    def fake_call(argv: list[str], *, cwd: str) -> int:
        scans[0] += 1
        record.append(("call", list(argv), clock[0]))
        return 4 if scans[0] == 1 else 0

    def fake_sleep(seconds: float) -> None:
        record.append(("sleep", seconds, clock[0]))
        clock[0] += seconds
        if clock[0] > 200:
            raise _StopLoop

    monkeypatch.setattr(module.subprocess, "call", fake_call)
    monkeypatch.setattr(module.time, "sleep", fake_sleep)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    return record, tmp_path / "guard.stop"


def _guard_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        GUARD_SCAN_ARG_ENV: json.dumps(["python", "-m", "ldst", "watch", "start"]),
        TICK_ENV: "12",
        "LDST_WATCH_GUARD_WAIT": "5,15",
    }
    env.update(extra or {})
    return env


def _waits(record: list[object]) -> list[float]:
    """相邻两次扫描之间的虚拟时间差（退避 / 周期）。"""
    calls = [item[2] for item in record if item[0] == "call"]  # type: ignore[misc]
    return [round(b - a, 3) for a, b in zip(calls, calls[1:], strict=False)]


def test_watch_guard_retries_then_uses_tick(guard_loop: tuple[list[object], Path]) -> None:
    """首轮暂无样本（exit 4）→ 按退避重试；成功一轮后按 tick 周期重复。"""
    record, stop = guard_loop
    with pytest.raises(_StopLoop):
        watch_guard_main(_guard_env({GUARD_STOP_ARG_ENV: str(stop)}))

    calls = [item for item in record if item[0] == "call"]
    assert calls[0][1] == ["python", "-m", "ldst", "watch", "start"]
    waits = _waits(record)
    assert waits[0] == 5.0
    assert set(waits[1:]) == {12.0}


def test_watch_guard_exits_when_marker_exists(
    guard_loop: tuple[list[object], Path],
) -> None:
    """退出标记已存在：一次都不扫，直接退 0（看板关闭时的正常退出口）。"""
    record, stop = guard_loop
    stop.write_text("stop", encoding="utf-8")

    assert watch_guard_main(_guard_env({GUARD_STOP_ARG_ENV: str(stop)})) == 0
    assert record == []


def test_watch_guard_exits_mid_wait_when_marker_appears(
    guard_loop: tuple[list[object], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """退避等待中看到退出标记：立刻返回，不再扫下一轮。"""
    import ladderstock.supervisor.page_boot as module

    record, stop = guard_loop
    sleeps = [0]

    def sleep_then_stop(seconds: float) -> None:
        record.append(("sleep", seconds, 0.0))
        sleeps[0] += 1
        if sleeps[0] == 2:
            stop.write_text("stop", encoding="utf-8")
        if sleeps[0] > 100:
            raise _StopLoop

    monkeypatch.setattr(module.time, "sleep", sleep_then_stop)

    assert watch_guard_main(_guard_env({GUARD_STOP_ARG_ENV: str(stop)})) == 0
    assert len([item for item in record if item[0] == "call"]) == 1
