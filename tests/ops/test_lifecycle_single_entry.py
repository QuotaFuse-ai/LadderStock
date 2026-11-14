"""单入口生命周期验收（QUOT-523）：一个入口全起、同一入口全停。

与 514/515 的区别是**被测对象**：514/515 直接调适配器起停，本文件走生产装配
（`build_supervisor_engines`：与看板 startup 钩子 / `ldst supervisor start|stop`
同一份引擎 + 同一本账），断言：

1. 起：`start_all()` 返回 ok；每个模块端口在听、归属判定 `managed`
   （账本 PID 存活 + cmdline 指纹命中，双证据）；
2. 常驻：验收期内**本轮拉起的** PID 保持存活（单入口「打开即全起」不是起完就散）；
3. 停：`stop_all()` 返回 ok；本轮拉起端口全部释放、拉起 PID 零残留；
4. 第二轮幂等：重复起停仍全绿，adopted 进程 PID 原样不变；
5. 同一本账：自愈快照与启停引擎看到同一组模块。

派单红线（QUOT-523）：**迁移不动生产 5950/8000/9100/9000 现有进程**。所以：

- 启动时已在听且命中模块关键字的端口按「账本 + cmdline 指纹」记账、判为 managed，
  但记为 adopted：本轮不 spawn、不重启，`stop_all` 之后 PID 必须原样
  （外部进程一律不碰，DEC-014）；
- 因此在生产机上跑本文件时，全部七模块大概率都是 adopted：它验的是
  「单入口能全起、全停、常驻」，**不验**生产进程能被它停掉——停掉生产要等
  用户批准的切换窗口；
- 外部占口（在听但不命中）→ setup 直接失败，不碰它；
冷端口（本机没跑该模块）真起真停：QUOT-589 端口隔离修正——financecoffer /
proxyfleet 的生产形态句柄（WSL compose / schtasks）端口写死生产值，测试环境
不可拉起且绝不允许 enable/run 生产计划任务，这两模块按 QUOT-550 的
fake_module_server 模式换成 fake 适配器（同端口表、同识别协议）；watch 启动器
固定源码态 CLI（安装根 ldst.exe 是主仓旧码，缺 watchdog 子命令）。

平台门：仅 Windows（CI 是 ubuntu，整模块 skip，沿用 514 先例）。
放行口：`LDST_SINGLE_ENTRY_ALLOW_EXTERNAL=1` 时外部占口降级为只观察（开发机常见
是别家工具占着 8788 之类），默认关闭——验收要的是「要么是我们的、要么没有」。

常驻时长 `LDST_SINGLE_ENTRY_DWELL_SEC`（默认 600 秒）。这棵树常驻期间就是
「单入口打开」的真实形态；它随本轮 stop_all 结束，不是生产替代品——生产切换
窗口需用户批准（见 docs/supervisor/single-entry.md）。
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ladderstock.supervisor.adapters import MODULE_ORDER, ModuleAdapter
from ladderstock.supervisor.engine import SupervisorEngine
from ladderstock.supervisor.ledger import Ledger, ModuleRecord
from ladderstock.supervisor.module_adapters import (
    BaseAdapter,
    HostPivot,
    PortEvidence,
    StartFace,
    build_default_adapters,
    default_host_pivot,
)
from ladderstock.supervisor.modules import module_ports, reset_env, select_env
from ladderstock.supervisor.selfheal import SelfHealEngine
from ladderstock.web.supervisor_api import SupervisorEngines, build_supervisor_engines
from ops.test_lifecycle_cold_start import (
    FAKE_SERVER,
    POLL_INTERVAL_SEC,
    POLL_TIMEOUT_SEC,
    REPO_ROOT,
    WINDOWS_ONLY,
    _tcp_ok,
)

pytestmark = WINDOWS_ONLY

#: 验收期间拉起 PID 必须存活的秒数（0 = 跳过常驻段，只验起停）
DWELL_SEC = float(os.environ.get("LDST_SINGLE_ENTRY_DWELL_SEC") or "600")

#: 开发机放行外部占口（降级为只观察）；验收口径下必须保持关闭
ALLOW_EXTERNAL = (os.environ.get("LDST_SINGLE_ENTRY_ALLOW_EXTERNAL") or "").strip() in {
    "1",
    "true",
    "yes",
}

#: 现/近期空闲时的探测缓存：adopted 端口上 PID 不能变，缓存会把「被替换」藏起来
EVIDENCE_CACHE_SEC = 0.0

#: 本机源码检出不是部署布局：多仓 workspace 的父目录、以及 LDST_PEER_ROOT 指到的
#: 本仓自身，都不含 peer 可执行文件。生产装配（`build_supervisor_engines`）读进程
#: 环境，所以这里逐模块补 `LDST_PEER_DIR_<MODULE>`，让真起真停段能找到入口。
PEER_SIBLINGS = {
    "tickhub": Path("TickHub-CN"),
    "tickhub_us": Path("TickHub-US"),
    "wire": Path("Wire") / "quotafuse-wire",
    "senate": Path("StockSenate"),
    "financecoffer": Path("FinanceCoffer"),
    "proxyfleet": Path("ProxyFleet"),
}
PEER_DIR_VARS = {module: f"LDST_PEER_DIR_{module.upper()}" for module in PEER_SIBLINGS}


@contextmanager
def _existing_peer_dirs(repo_root: Path) -> Iterator[None]:
    """把源码树里现成的 peer 检出补进进程环境（只在本模块生命周期内生效）。

    已显式设置（部署 / `.env`）的变量一律不动；找不到的模块也不猜，留给
    `start_all` 如实报「入口不存在」。
    """
    roots = [repo_root.parent]
    install = (os.environ.get("LDST_INSTALL_ROOT") or "").strip()
    if install:
        roots.insert(0, Path(install))
    peer_root = (os.environ.get("LDST_PEER_ROOT") or "").strip()
    if peer_root:
        roots = [Path(peer_root), *roots]
    added: list[str] = []
    for module, var in PEER_DIR_VARS.items():
        if (os.environ.get(var) or "").strip():
            continue
        for root in roots:
            candidate = root / PEER_SIBLINGS[module]
            if (candidate / ".venv").is_dir():
                os.environ[var] = str(candidate)
                added.append(var)
                break
    try:
        yield
    finally:
        for var in added:
            os.environ.pop(var, None)


@dataclass(frozen=True)
class EntryPlan:
    module: str
    port: int
    #: 启动时已在听且命中关键字：本轮不 spawn，停服后 PID 必须不变
    adopt_pid: int | None
    #: 外部占口（放行模式下）：不纳入任何起停断言
    observe_only: bool = False


@dataclass
class Entry:
    root: Path
    engines: SupervisorEngines
    engine: SupervisorEngine
    ledger: Ledger
    heal: SelfHealEngine
    host: HostPivot
    adapters: Mapping[str, ModuleAdapter]
    plans: tuple[EntryPlan, ...]
    notes: dict[str, str] = field(default_factory=dict)

    def plans_of(self, module: str) -> tuple[EntryPlan, ...]:
        return tuple(plan for plan in self.plans if plan.module == module)

    @property
    def skipped(self) -> set[str]:
        return {plan.module for plan in self.plans if plan.observe_only}

    @property
    def adopted(self) -> dict[str, int]:
        return {
            plan.module: plan.adopt_pid
            for plan in self.plans
            if plan.adopt_pid is not None and not plan.observe_only
        }

    def summary(self) -> str:
        lines = []
        for name in MODULE_ORDER:
            ports = ",".join(str(p.port) for p in self.plans_of(name))
            lines.append(f"  {name:<14} ports={ports:<14} {self.notes.get(name, '?')}")
        return "\n".join(lines)


#: 形态专属模块（生产句柄写死生产端口，测试环境不可拉起）→ fake 模块替身
FAKE_MODULES = ("financecoffer", "proxyfleet")


def _source_launcher(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """源码态 CLI 启动器：watch 的 watchdog 子命令只在当前工作树代码里存在。"""
    del env
    return (sys.executable, "-m", "ladderstock.cli.main")


def _patched_build(**kwargs: object) -> dict[str, ModuleAdapter]:
    from ladderstock.supervisor.module_adapters import build_default_adapters as real_build

    built = real_build(**kwargs)  # type: ignore[arg-type]
    log_dir = kwargs.get("log_dir")
    for name in FAKE_MODULES:
        built[name] = _fake_adapter(name, log_dir if isinstance(log_dir, Path) else Path("logs"))
    return built


def _fake_adapter(name: str, log_dir: Path) -> BaseAdapter:
    ports = tuple(module_ports()[name])
    faces = tuple(
        StartFace(
            face="server",
            argv=(sys.executable, str(FAKE_SERVER), "--module", name, "--port", str(port)),
            cwd=REPO_ROOT,
            port=port,
            match=("fake_module_server", "--module", name),
            log_name=f"fake-{name}-{port}.log",
        )
        for port in ports
    )
    return BaseAdapter(name, ports, faces, log_dir=log_dir, evidence_cache_sec=0.0)


@pytest.fixture(scope="module")
def monkeypatch_module() -> Iterator[pytest.MonkeyPatch]:
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def entry(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch_module: pytest.MonkeyPatch
) -> Iterator[Entry]:
    # QUOT-589 端口隔离红线：单入口验收只跑测试环境（18000 系端口表），
    # 绝不拉起/触碰生产 8000/8787/8002/8790/8791/8765/5950。
    select_env("test")
    # 形态专属模块（WSL compose / schtasks）写死生产端口，测试环境绝不触碰：
    # 全部装配路径（engine / 自愈 / 单入口 web 装配）统一换成 fake 模块适配器。
    monkeypatch_module.setattr(
        "ladderstock.web.supervisor_api.build_default_adapters", _patched_build
    )
    monkeypatch_module.setattr(
        "ops.test_lifecycle_single_entry.build_default_adapters", _patched_build
    )
    # watch 启动器固定源码态 CLI：安装根 ldst.exe 是主仓旧码，缺 watchdog 子命令。
    monkeypatch_module.setattr(
        "ladderstock.supervisor.module_adapters.launcher_entry", _source_launcher
    )
    root = tmp_path_factory.mktemp("quot-523-single-entry")
    repo_root = Path(__file__).resolve().parents[2]
    try:
        with _existing_peer_dirs(repo_root):
            yield from _build_entry(root)
    finally:
        reset_env()


def _build_entry(root: Path) -> Iterator[Entry]:
    engines = build_supervisor_engines(root, panel_url="http://127.0.0.1:15951/")
    adapters = build_default_adapters(log_dir=root / "logs", evidence_cache_sec=EVIDENCE_CACHE_SEC)
    host = default_host_pivot()
    plans: list[EntryPlan] = []
    notes: dict[str, str] = {}
    for name in MODULE_ORDER:
        ident = adapters[name].identify()
        for ev in ident.ports:
            assert ev.port >= 18000 and ev.port < 19000, (
                f"端口隔离红线：{name} 解析到非测试端口 {ev.port}（须 18000 系）"
            )
            if ev.listening and not ev.matched and not ALLOW_EXTERNAL:
                pytest.fail(
                    f"期望端口 {ev.port} 在听 / 实际：归属 PID={ev.pid}（不属于本模块）"
                    "——setup 拒绝在外部占口上继续"
                )
            observe = ev.listening and not ev.matched
            adopt_pid = ev.pid if ev.listening and ev.matched else None
            plans.append(
                EntryPlan(module=name, port=ev.port, adopt_pid=adopt_pid, observe_only=observe)
            )
            if adopt_pid is not None:
                _record_adoption(engines.ledger, name, ev)
        for face in getattr(adapters[name], "faces", ()):
            launcher = next((part for part in face.argv if part.endswith(("ldst.exe", "ldst"))), "")
            if launcher and face.argv[-1].startswith("["):
                print(
                    f"[setup] {name} 启动面 {face.log_name}: "
                    f"spawn-cmd={_spawn_cmd_evidence(launcher, face.argv[-1])}"
                )
        observed = sorted(p.port for p in plans if p.module == name and p.observe_only)
        adopted = sorted(p.port for p in plans if p.module == name and p.adopt_pid is not None)
        cold = [
            p.port for p in plans if p.module == name and p.adopt_pid is None and not p.observe_only
        ]
        notes[name] = (
            f"adopted={adopted} cold={cold} observe-only={observed}"
            if observed or adopted
            else "all-cold（已全起时会真起真停）"
        )
        print(f"[setup] {name}: {notes[name]}")
    expected = {port for module in MODULE_ORDER for port in module_ports()[module]}
    assert {plan.port for plan in plans} == expected, "识别端口与注册表白名单不一致"
    assert all(p.port >= 18000 and p.port < 19000 for p in plans)
    single = Entry(
        root=root,
        engines=engines,
        engine=engines.processes,
        ledger=engines.ledger,
        heal=engines.heal,
        host=host,
        adapters=adapters,
        plans=tuple(plans),
        notes=notes,
    )
    print(f"[setup] 单入口装配完成（账本 {root / 'supervisor-ledger.json'}）:\n{single.summary()}")
    try:
        yield single
    finally:
        # 安全网：无论断言怎么失败，最后都由同一条停止路径把本轮拉起的面收干净
        try:
            single.engine.stop_all()
        except Exception:
            pass


def _record_adoption(ledger: Ledger, module: str, ev: PortEvidence) -> None:
    """adopted 面按账本 + 真实 cmdline 指纹记账（引擎归属判定的两个证据）。"""
    record = ledger.record(module) or ModuleRecord(
        module=module, ports=list(module_ports()[module])
    )
    record.pid = ev.pid
    record.cmdline_hash = _cmdline_hash_of(ev)
    record.state = "running"
    record.origin = "adopted"
    ledger.update(record)


def _cmdline_hash_of(ev: PortEvidence) -> str:
    from ladderstock.supervisor.proctree import cmdline_hash

    return cmdline_hash(ev.cmdline)


def _spawn_cmd_evidence(launcher: str, spawn_cmd: str) -> str:
    """面板重启命令的启动面诊断：启动器在不在、命令里会不会混进 `-m`。"""
    present = Path(launcher).is_file()
    argv = json.loads(spawn_cmd)
    mixed = "-m" in argv if present else False
    return (
        f"launcher={'ok' if present else 'missing'}({launcher})"
        f" argv0={argv[0] if argv else '?'}"
        f" {'-m 混入启动器（必秒退）' if mixed else '无 -m'}"
    )


def _await_listening(plan: EntryPlan) -> None:
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    while True:
        if _tcp_ok(plan.port):
            return
        if time.monotonic() >= deadline:
            pytest.fail(f"期望端口 {plan.port}（{plan.module}）在听 / 实际：无监听")
        time.sleep(POLL_INTERVAL_SEC)


def _release_ports(entry: Entry) -> list[int]:
    """只有监听者确属本模块的端口才纳入「必须释放」集合。"""
    released: list[int] = []
    for plan in entry.plans:
        if plan.observe_only:
            continue
        # 全量识别再挑端口：financecoffer 的 identify() 不吃端口参数（只认整模块）
        items = entry.adapters[plan.module].identify().ports
        evidence = next((item for item in items if item.port == plan.port), None)
        if evidence is not None and (evidence.listening or evidence.matched):
            released.append(plan.port)
    return released


def _await_released(entry: Entry, ports: list[int], owned: set[int]) -> None:
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    while True:
        still = [f"端口 {port} 仍在监听" for port in ports if _tcp_ok(port)]
        still += [
            f"残留子进程 PID={pid}" for pid in sorted(owned) if entry.host.snapshot(pid) is not None
        ]
        if not still:
            return
        if time.monotonic() >= deadline:
            pytest.fail("单入口关闭后未全停：" + "；".join(still))
        time.sleep(POLL_INTERVAL_SEC)


def _assert_adopted_untouched(entry: Entry) -> None:
    for module, pid in entry.adopted.items():
        adapter = entry.adapters[module]
        for plan in entry.plans_of(module):
            ev = next(item for item in adapter.identify().ports if item.port == plan.port)
            assert ev.listening and ev.matched and ev.pid == pid, (
                f"adopted 进程被改动：{module} 端口 {plan.port} 期望 PID={pid} / "
                f"实际 PID={ev.pid} listening={ev.listening} matched={ev.matched}"
            )


def _assert_managed(entry: Entry) -> None:
    state = entry.engine.state()
    for module in MODULE_ORDER:
        if module in entry.skipped:
            continue
        item = state["modules"][module]
        assert item["ownership"] == "managed", (
            f"{module} 归属={item['ownership']} evidence={item['evidence']}"
            "（账本 + cmdline 双证据未命中）"
        )


def _spawned_pids(entry: Entry) -> set[int]:
    """本轮 start_all 真正拉起的 PID（账本 origin=started 且存活）。"""
    started = {plan.module for plan in entry.plans if plan.adopt_pid is None}
    state = entry.engine.state()
    return {
        item["pid"]
        for name, item in state["modules"].items()
        if name in started and item.get("pid")
    }


def _one_cycle(entry: Entry, cycle: int) -> None:
    # 探针是失败关闭的：拉起前必须确认账本没有把冷启动模块误记成 running
    for module in MODULE_ORDER:
        plans = entry.plans_of(module)
        # QUOT-589 重修：watch 无端口（MODULE_PORTS[watch]==()），无端口计划可依
        if module in entry.skipped or not plans or plans[0].adopt_pid is not None:
            continue
        record = entry.ledger.record(module)
        if record is not None and record.state == "running":
            # 部分面在听、部分面没起（如 financecoffer 只在 WSL 侧活着）：端口为空的
            # 模块探针必然 False，单入口会拿「无监听」当没起——只做诊断不阻断
            print(f"[cycle {cycle}] {module} 账本仍记 running 但 Windows 侧无监听：{record.pid}")
    started = entry.engine.start_all()
    assert started["ok"] is True, f"[cycle {cycle}] start_all 未全起：{started}"
    for plan in entry.plans:
        if plan.observe_only:
            continue
        _await_listening(plan)
    _assert_managed(entry)
    owned = _spawned_pids(entry)
    held = _release_ports(entry)
    print(f"[cycle {cycle}] all-up, spawned_pids={sorted(owned)}, holding={held}")

    dwell = DWELL_SEC if cycle == 1 else 0.0
    if dwell > 0:
        deadline = time.monotonic() + dwell
        while time.monotonic() < deadline:
            dead = [pid for pid in sorted(owned) if entry.host.snapshot(pid) is None]
            assert not dead, f"常驻期间拉起进程退出：{dead}（单入口没有守住自己的子进程）"
            time.sleep(min(5.0, max(0.1, deadline - time.monotonic())))

    stopped = entry.engine.stop_all()
    assert stopped["ok"] is True, f"[cycle {cycle}] stop_all 未全停：{stopped}"
    _await_released(entry, held, owned)
    _assert_adopted_untouched(entry)
    print(f"[cycle {cycle}] all-down: ports released, spawned residual=0, adopted untouched")


def test_single_entry_open_all_up_close_all_down(entry: Entry) -> None:
    """单入口打开 = 全起（含常驻）；关闭 = 全停；第二轮幂等。"""
    _one_cycle(entry, 1)
    _one_cycle(entry, 2)


def test_selfheal_and_process_engine_share_one_ledger(entry: Entry) -> None:
    """同一本账：自愈快照里的模块 = 启停引擎的模块；自愈看的就是这本账。"""
    assert entry.heal.ledger.path == entry.engines.ledger.path
    entry.engine.start_all()
    try:
        snapshot = entry.heal.snapshot()
        by_module = {item["module"]: item for item in snapshot["modules"]}
        assert set(MODULE_ORDER) <= set(by_module)
        for module in MODULE_ORDER:
            assert by_module[module]["health"] in {"ok", "restarting", "needs_human"}
            assert isinstance(by_module[module]["restarts_5min"], int)
    finally:
        entry.engine.stop_all()
