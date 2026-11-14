"""单入口保活验收（QUOT-550）：kill → 自愈拉回 → 端口恢复，全程 ephemeral。

被测对象是**生产装配路径**（`SupervisorEngine` + `AdapterRestarter` +
`SelfHealEngine(cold_start)`），但进程与端口全部 ephemeral：

- 被管模块 = 3 个假服务面（`fake_module_server.py`，cmdline 带识别关键字），
  使用真实模块名（tickhub / tickhub_us / watch——watch 即 5952 看门狗在单入口
  里的身份），端口由 OS 分配（`bind(("127.0.0.1", 0))` 后取实际值），绝不碰
  5950/5951/5952/8000/9000/9100 等生产端口；
- 真实进程、真实 spawn、真实强杀、真实端口监听——只有模块「实现」是假的；
- kill 断言是真的：直接 `terminate_tree` 强杀本轮 spawn 的进程（不写
  stopping_by_user），轮询端口确实不再监听，再驱动自愈 `tick()`，断言
  适配器重新拉起、端口恢复监听、账本 pid 换新且 ownership=managed；
- 外部占口不碰：一个「外来」假进程（cmdline 不含本模块识别关键字）占住
  gamma 槽位的端口，start / 自愈对它一律拒绝拉起、绝不终止（DEC-014）；
- teardown 只停本测试经 supervisor 拉起的进程与测试自己启的外部进程，
  生产进程（`D:\\QuotaFuseModules\\LadderStock` 等）零接触。

本文件没有整文件 skip；平台门只针对「宿主 ProcessOps 形态」（CI ubuntu 无
WindowsProcessOps，见 fixture 内 skip 理由）。禁止 expect(true) / --retry 换绿。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ladderstock.supervisor.adapters import module_registry
from ladderstock.supervisor.engine import SupervisorEngine
from ladderstock.supervisor.ledger import Ledger
from ladderstock.supervisor.module_adapters import BaseAdapter, PsutilHostPivot, StartFace
from ladderstock.supervisor.modules import MODULE_PORTS
from ladderstock.supervisor.proctree import WindowsProcessOps
from ladderstock.supervisor.restart import AdapterRestarter
from ladderstock.supervisor.selfheal import SelfHealEngine

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_SERVER = Path(__file__).with_name("fake_module_server.py")

POLL_TIMEOUT_SEC = 30.0
POLL_INTERVAL_SEC = 0.25

# 全部顺序模块都给 ephemeral 假实现（module_registry 要求 MODULE_ORDER 一个不缺）；
# 重点断言集中在 tickhub / tickhub_us / watch——watch 即 5952 看门狗在单入口里的
# 身份，本单主角就是「看门狗死了没人拉」。
TEST_MODULES = ("tickhub", "tickhub_us", "wire", "senate", "financecoffer", "proxyfleet", "watch")
HEAL_MODULES = ("tickhub", "tickhub_us", "watch")


def _free_ephemeral_port() -> int:
    """向 OS 要一个空闲端口并立刻释放：分配给假模块当监听口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _make_adapter(name: str, port: int, host: PsutilHostPivot, log_dir: Path) -> BaseAdapter:
    argv = (sys.executable, str(FAKE_SERVER), "--module", name, "--port", str(port))
    return BaseAdapter(
        name,
        (port,),
        (
            StartFace(
                face="server",
                argv=argv,
                cwd=REPO_ROOT,
                port=port,
                match=("fake_module_server", "--module", name),
                log_name=f"fake-{name}.log",
            ),
        ),
        host=host,
        log_dir=log_dir,
        evidence_cache_sec=0.0,
    )


@dataclass
class EphemeralWorld:
    """一份隔离验收世界：ephemeral 端口计划 + 各模块适配器 + 测试自启的外部进程。"""

    ports: dict[str, int]
    adapters: dict[str, BaseAdapter]
    host: PsutilHostPivot
    procs: WindowsProcessOps
    log_dir: Path
    ledger: Ledger
    engine: SupervisorEngine
    external: list[subprocess.Popen] = field(default_factory=list)

    def make_heal(self) -> SelfHealEngine:
        """按生产装配形态（spawn=True + cold_start）组装自愈，退避等待归零。"""
        return SelfHealEngine(
            self.ledger,
            AdapterRestarter(self.engine),
            sleeper=lambda _seconds: None,
            modules=HEAL_MODULES,
            cold_start=True,
            module_ports={name: (self.ports[name],) for name in TEST_MODULES},
        )


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[EphemeralWorld]:
    if os.name != "nt":
        pytest.skip("Windows 宿主验收（WindowsProcessOps 形态）；CI ubuntu 由单测覆盖引擎语义")
    host = PsutilHostPivot()
    procs = WindowsProcessOps()
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ports = {name: _free_ephemeral_port() for name in TEST_MODULES}
    adapters = {name: _make_adapter(name, ports[name], host, log_dir) for name in TEST_MODULES}

    # 生产代码读 MODULE_PORTS 取端口白名单（引擎 _start/_stop/adopt 都用它）；
    # 本验收把白名单换成 ephemeral 计划——只改测试进程内的模块级常量，
    # src/ 生产代码一字不动。
    ephemeral_plan = {**{name: (ports[name],) for name in TEST_MODULES}, "proxypool": ()}
    assert set(ephemeral_plan) == set(MODULE_PORTS)
    # registry 校验与引擎取白名单分别在 adapters / engine 两个命名空间，两处同换。
    monkeypatch.setattr("ladderstock.supervisor.adapters.MODULE_PORTS", ephemeral_plan)
    monkeypatch.setattr("ladderstock.supervisor.engine.MODULE_PORTS", ephemeral_plan)

    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    engine = SupervisorEngine(
        adapters=module_registry(adapters),
        ledger=ledger,
        procs=procs,
        start_grace_sec=20.0,
        stop_grace_sec=10.0,
        kill_grace_sec=10.0,
    )
    world = EphemeralWorld(
        ports=ports,
        adapters=adapters,
        host=host,
        procs=procs,
        log_dir=log_dir,
        ledger=ledger,
        engine=engine,
    )
    yield world
    _teardown(world)


def _teardown(world: EphemeralWorld) -> None:
    # 只停自己拉起的：supervisor 通道按 stopping_by_user 停干净，外部假进程单独收。
    try:
        world.engine.stop_all()
    except Exception:
        pass
    for proc in world.external:
        try:
            world.host.terminate_tree(proc.pid, force=True)
        except OSError:
            pass


def _wait_port(world: EphemeralWorld, name: str, *, listening: bool) -> None:
    port = world.ports[name]
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    while True:
        if world.host.listening(port) == listening:
            return
        if time.monotonic() >= deadline:
            pytest.fail(
                f"期望模块 {name} 端口 {port} {'在听' if listening else '已释放'} / "
                f"实际 listening={world.host.listening(port)}"
            )
        time.sleep(POLL_INTERVAL_SEC)


def test_kill_module_then_selfheal_pulls_back(world: EphemeralWorld) -> None:
    """kill 自有模块进程 → 自愈拉回 → 端口恢复监听、账本换绑新 pid。"""
    started = world.engine.start_all()
    assert started["ok"] is True, f"start_all 未全起：{started}"

    for name in TEST_MODULES:
        record = world.ledger.record(name)
        assert record is not None and record.pid, f"{name} 账本没有 pid"
        assert record.state == "running", name
    _wait_port(world, "watch", listening=True)
    original_pid = int(world.ledger.record("watch").pid)  # type: ignore[union-attr]

    # 整机断电式强杀（模拟看门狗 exit 15）：不走 stop 通道、不写停止意图，
    # 自愈必须把它当崩溃而不是主动关。
    world.host.terminate_tree(original_pid, force=True)
    _wait_port(world, "watch", listening=False)
    world.engine.reconcile()
    record = world.ledger.record("watch")
    assert record is not None and record.state == "stopped", (
        f"强杀后对账应判 stopped，实际 {record.state if record else '无记录'}"
    )

    heal = world.make_heal()
    heal.tick()
    _wait_port(world, "watch", listening=True)

    record = world.ledger.record("watch")
    assert record is not None and record.pid, "自愈后账本没有 pid"
    assert record.pid != original_pid, "自愈应拉起新进程（pid 必须换新）"
    assert record.state == "running", record.state
    assert world.engine.state()["modules"]["watch"]["ownership"] == "managed"
    # 其它两个模块全程未被牵连
    for other in ("tickhub", "tickhub_us"):
        other_record = world.ledger.record(other)
        assert other_record is not None and other_record.pid, f"{other} 被牵连"
        _wait_port(world, other, listening=True)

    # 再 tick 一轮确认稳定运行：进程活着、账本判活
    heal.tick()
    record = world.ledger.record("watch")
    assert record is not None and record.state == "running"


def test_selfheal_cold_start_from_empty_ledger(world: EphemeralWorld) -> None:
    """账本全空（模拟 supervisor 宿主刚冷启动）→ 自愈按 bundle 命令表全起。"""
    heal = world.make_heal()
    heal.tick()
    for name in HEAL_MODULES:
        _wait_port(world, name, listening=True)
        record = world.ledger.record(name)
        assert record is not None and record.pid, f"{name} 冷启动后账本没有 pid"
        assert record.state == "running", name


def test_selfheal_respects_stop_intent(world: EphemeralWorld) -> None:
    """主动 stop（stopping_by_user）后自愈绝不重拉（D4 intentional 语义）。"""
    started = world.engine.start_all()
    assert started["ok"] is True, started
    stopped = world.engine.stop_module("watch")
    assert stopped["ok"] is True, stopped
    _wait_port(world, "watch", listening=False)

    heal = world.make_heal()
    heal.tick()
    assert not world.host.listening(world.ports["watch"]), (
        "主动停后自愈把它拉回来了：D4 语义被破坏（重启按钮静默失效的根因之一）"
    )
    record = world.ledger.record("watch")
    assert record is not None and record.stopping_by_user


def test_external_occupant_never_touched(world: EphemeralWorld) -> None:
    """外部进程（非本控制面）占住 watch 端口：start/自愈绝不终止它、绝不抢口。"""
    port = world.ports["watch"]
    external = subprocess.Popen(
        [sys.executable, str(FAKE_SERVER), "--module", "stranger", "--port", str(port)],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    world.external.append(external)
    _wait_port(world, "watch", listening=True)

    started = world.engine.start_all()
    # 失败关闭：watch 被外部占口，start_all 必须报错而不是假装全起
    assert started["modules"]["watch"]["ok"] is False, started
    assert "占用" in str(started["modules"]["watch"]["error"]), started["modules"]["watch"]
    for name in ("tickhub", "tickhub_us"):
        assert started["modules"][name]["ok"] is True, started["modules"][name]

    heal = world.make_heal()
    heal.tick()
    # 外部进程原样存活：cmdline 关键字不同 → 永远不被识别为自有，绝不终止（DEC-014）
    assert external.poll() is None, "外部占口进程被终止：DEC-014 红线被破坏"
    assert world.host.listening(port), "外部占口进程的端口被动过"


def test_restart_backend_never_silent(world: EphemeralWorld) -> None:
    """「重启按钮后端」等价路径：模块死了之后 restart_module 必须把它拉回来，
    而不是静默返回成功。旧模型里 watchdog 死了没人拉，按钮就静默失效——
    这里断言单入口 backend 通道对死模块的 restart 是真实恢复。"""
    started = world.engine.start_all()
    assert started["ok"] is True, started
    victim = int(world.ledger.record("watch").pid)  # type: ignore[union-attr]
    world.host.terminate_tree(victim, force=True)
    _wait_port(world, "watch", listening=False)

    result = world.engine.restart_module("watch")
    assert result["ok"] is True, result
    _wait_port(world, "watch", listening=True)
    record = world.ledger.record("watch")
    assert record is not None and record.pid and record.pid != victim
    assert record.state == "running", record.state


def test_backoff_then_needs_human(world: EphemeralWorld) -> None:
    """连续崩溃达上限：自愈停止重拉、置 needs_human——不无限重试也不静默放弃。"""
    started = world.engine.start_all()
    assert started["ok"] is True, started

    kills = 0

    def kill_and_mark_dead() -> None:
        nonlocal kills
        record = world.ledger.record("tickhub_us")
        assert record is not None and record.pid
        world.host.terminate_tree(int(record.pid), force=True)
        kills += 1

    # 自愈带真实退避（5s/15s/45s）与 5min 窗口；把窗口时间推过去，逐轮 kill。
    now = {"t": 1000.0}

    def fake_clock() -> float:
        return now["t"]

    class FastSleeper:
        def __call__(self, seconds: float) -> None:
            now["t"] += max(seconds, 1.0)

    heal = SelfHealEngine(
        world.ledger,
        AdapterRestarter(world.engine),
        sleeper=FastSleeper(),
        clock=fake_clock,
        poll_interval=1.0,
        modules=HEAL_MODULES,
        cold_start=True,
        module_ports={name: (world.ports[name],) for name in TEST_MODULES},
    )
    for _ in range(3):
        kill_and_mark_dead()
        heal.tick()
    kill_and_mark_dead()
    heal.tick()  # 第 4 次死亡：窗口已满 → needs_human
    record = world.ledger.record("tickhub_us")
    assert record is not None and record.health == "needs_human", record
    assert kills == 4

    # needs_human 后自愈不再动作；人工 reset 解除
    pid_before = record.pid
    heal.tick()
    record = world.ledger.record("tickhub_us")
    assert record is not None and record.health == "needs_human" and record.pid == pid_before
    reset = heal.reset("tickhub_us")
    assert reset is not None
    heal.tick()
    _wait_port(world, "tickhub_us", listening=True)
