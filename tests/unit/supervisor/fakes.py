"""supervisor 内核测试（QUOT-482-A）：全部走 fake，不起真进程、不占真端口。

Fake 世界模型：
- `FakeProcs` 是一个进程表（pid → cmdline / 父子链 / 绑定端口），
  `kill_tree` 按树删除（模拟 taskkill /T 的整树语义）；
- `FakeAdapter.start()` 往进程表里 spawn 入口进程（可选带 worker 子进程），
  `probe()` = 模块进程树里还有进程绑着自己的端口；
- `FakeClock` 推进引擎的轮询等待（monotonic / sleep 可注入）。
"""

from __future__ import annotations

from typing import Any

from ladderstock.supervisor.adapters import SpawnedProcess
from ladderstock.supervisor.proctree import cmdline_hash


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeProcess:
    def __init__(
        self,
        pid: int,
        cmdline: tuple[str, ...],
        ppid: int | None,
        ports: tuple[int, ...] = (),
    ) -> None:
        self.pid = pid
        self.cmdline = cmdline
        self.ppid = ppid
        self.ports = tuple(ports)


class FakeProcs:
    """进程表 + 整树删除；`unreachable_cmdline` 模拟查询通道挂掉。"""

    def __init__(self, self_pid: int = 99999) -> None:
        self.self_pid_value = self_pid
        self.processes: dict[int, FakeProcess] = {}
        self.killed_trees: list[int] = []
        self.unreachable_cmdline: set[int] = set()
        self._next_pid = 50000

    def spawn_chain(
        self,
        cmdline: tuple[str, ...],
        *,
        ppid: int | None = None,
        ports: tuple[int, ...] = (),
        depth: int = 1,
    ) -> list[int]:
        """spawn 一串进程 `cmd → python → worker`（最后一个绑端口），返回整链。"""
        parent = ppid
        chain: list[int] = []
        for index in range(depth):
            self._next_pid += 1
            pid = self._next_pid
            # 入口进程的命令行必须与 spawn 时记录的一致（账本哈希可比）；
            # 后续 worker 才带自己的参数后缀
            actual = cmdline if index == 0 else cmdline + (f"#w{index}",)
            bound = ports if index == depth - 1 else ()
            self.processes[pid] = FakeProcess(pid=pid, cmdline=actual, ppid=parent, ports=bound)
            chain.append(pid)
            parent = pid
        return chain

    # ---- ProcessOps 协议

    def self_pid(self) -> int:
        return self.self_pid_value

    def alive(self, pid: int) -> bool:
        return pid in self.processes

    def cmdline(self, pid: int) -> tuple[str, ...] | None:
        if pid in self.unreachable_cmdline:
            return None
        proc = self.processes.get(pid)
        return proc.cmdline if proc else None

    def tree_pids(self, root_pid: int) -> list[int]:
        """ParentProcessId 递归语义：从 root 向下吸收 ppid 指向树内成员的活进程。

        根可以已经不在进程表里（入口死了、worker 成孤儿）——真实 Windows 上
        孤儿的 ParentProcessId 仍指向原父 PID，全表扫描照样能把树找齐。
        """
        tree = {root_pid}
        changed = True
        while changed:
            changed = False
            for proc in self.processes.values():
                if proc.pid not in tree and proc.ppid in tree:
                    tree.add(proc.pid)
                    changed = True
        return sorted(tree)

    def kill_tree(self, root_pid: int) -> None:
        self.killed_trees.append(root_pid)
        for pid in self.tree_pids(root_pid):
            self.processes.pop(pid, None)

    def listening_ports(self) -> set[int]:
        bound: set[int] = set()
        for proc in self.processes.values():
            bound.update(proc.ports)
        return bound


class FakeAdapter:
    """单模块适配器假实现：start spawn 进程链，probe 看进程表。"""

    def __init__(
        self,
        name: str,
        ports: tuple[int, ...],
        procs: FakeProcs,
        *,
        supervisor_pid: int | None = None,
        chain_depth: int = 2,
        refuse_start: Exception | None = None,
    ) -> None:
        self.name = name
        self.ports = ports
        self._procs = procs
        self._supervisor_pid = supervisor_pid if supervisor_pid is not None else procs.self_pid()
        self._chain_depth = chain_depth
        self._refuse_start = refuse_start
        self.start_calls: list[list[int]] = []
        self.stop_calls: int = 0
        #: 优雅停时是否留孤儿（只杀入口，worker 继续监听）——模拟真实世界的半停
        self.stop_leaves_orphans = False
        #: 优雅停抛异常（模拟 compose stop 报错）
        self.stop_raises: Exception | None = None
        #: probe 永远 False（模拟端口起不来，start 超时路径）
        self.probe_always_false = False
        #: stop() 阻塞门（并发互斥测试用；None = 不阻塞）
        self.stop_gate: Any = None
        self._chain: list[int] = []
        self._external_ports: set[int] = set()

    def start(self) -> SpawnedProcess:
        if self._refuse_start:
            raise self._refuse_start
        cmdline = (f"C:/venv/{self.name}.exe", "run")
        self._chain = self._procs.spawn_chain(
            cmdline, ppid=self._supervisor_pid, ports=self.ports, depth=self._chain_depth
        )
        self.start_calls.append(list(self._chain))
        return SpawnedProcess(pid=self._chain[0], parent_pid=self._supervisor_pid, cmdline=cmdline)

    def stop(self) -> None:
        self.stop_calls += 1
        if self.stop_gate is not None:
            self.stop_gate.wait(timeout=5)
        if self.stop_raises:
            raise self.stop_raises
        if not self._chain:
            return
        if self.stop_leaves_orphans:
            # 只杀入口进程：worker 成孤儿继续监听 → 引擎必须超时升级整树杀
            self._procs.processes.pop(self._chain[0], None)
            return
        self._procs.kill_tree(self._chain[0])

    def kill(self) -> None:
        if self._chain:
            self._procs.kill_tree(self._chain[0])

    def probe(self) -> bool:
        if self.probe_always_false:
            return False
        if self._external_ports:
            return True
        if not self.ports:
            # 无端口模块（QUOT-589 看门狗）：进程链里还有活进程即健康，
            # 不按端口回读（kill_tree 后链上 pid 全死 → quiet）
            return any(pid in self._procs.processes for pid in self._chain)
        for port in self.ports:
            for pid in self._chain:
                proc = self._procs.processes.get(pid)
                if proc and port in proc.ports:
                    return True
        return False

    def take_port_externally(self) -> None:
        """模拟「别人家的进程」占住本模块端口（不在本模块进程链里）。"""
        self._external_ports = set(self.ports)

    def release_external(self) -> None:
        self._external_ports.clear()

    def entry_pid(self) -> int | None:
        return self._chain[0] if self._chain else None

    def chain_pids(self) -> list[int]:
        return list(self._chain)

    def recorded_cmdline(self) -> tuple[str, ...]:
        return (f"C:/venv/{self.name}.exe", "run")


def build_engine(
    tmp_path: Any,
    *,
    clock: FakeClock | None = None,
    chain_depth: int = 2,
    start_grace: float = 1.0,
    stop_grace: float = 1.0,
    kill_grace: float = 1.0,
) -> tuple[Any, dict[str, FakeAdapter], FakeProcs, FakeClock]:
    """搭一套「注册表全 6 模块 + fake 适配器」的引擎（含落盘账本）。"""
    from ladderstock.supervisor import Ledger, SupervisorEngine
    from ladderstock.supervisor.adapters import MODULE_ORDER, MODULE_PORTS

    procs = FakeProcs()
    clock = clock or FakeClock()
    adapters = {
        name: FakeAdapter(name, MODULE_PORTS[name], procs, chain_depth=chain_depth)
        for name in MODULE_ORDER
    }
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    ledger.load()
    engine = SupervisorEngine(
        adapters=adapters,
        ledger=ledger,
        procs=procs,
        start_grace_sec=start_grace,
        stop_grace_sec=stop_grace,
        kill_grace_sec=kill_grace,
        poll_interval_sec=0.25,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    return engine, adapters, procs, clock


def ledger_entry_hash(adapter: FakeAdapter) -> str:
    return cmdline_hash(adapter.recorded_cmdline())
