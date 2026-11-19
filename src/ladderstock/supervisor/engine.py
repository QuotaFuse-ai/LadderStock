"""顺序启停引擎（QUOT-482-A）：全起 / 全停 / 单模块重启 / 归属可见。

启动顺序 `adapters.MODULE_ORDER`（tickhub → tickhub_us → wire → senate →
financecoffer → proxyfleet），停止严格反向。每条停止路径带超时：优雅停
（adapter.stop）超时或异常 → 升级整树强杀（`kill_tree`）并记 `forced_kill`；
停完逐模块回读 `probe()` 确认端口真的没了。

归属判定只认**账本 + cmdline 匹配**：账本里 pid 存活且命令行指纹匹配才是
managed；端口在听但账本无法证明是我们拉的一律 external——**不停外部进程**
（QUOT-482-A 派单正文；D2 接管策略不在本单）。外部 peer 认领（`adopted_peer`
标记，QUOT-545）同样绝不终止：stop_all / stop_module / restart 一律跳过，
只有前任 supervisor 遗留的自有进程（对账续托管、无标记）才可被停止。

并发互斥：`start_all / stop_all / restart_module / state` 全程持同一把
`RLock`——并发启停直接排队，不交错（设计文档 §并发互斥）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ladderstock.supervisor.adapters import (
    MODULE_ORDER,
    ModuleAdapter,
    module_registry,
)
from ladderstock.supervisor.ledger import (
    HEALTH_OK,
    HEALTH_STOPPED,
    STOP_BY_REQUEST,
    STOP_PID_REUSE,
    Ledger,
    ModuleRecord,
    now_iso,
)
from ladderstock.supervisor.modules import MODULE_PORTS
from ladderstock.supervisor.proctree import ProcessOps, cmdline_hash

#: 拉起后等端口监听的窗口（QUOT-482 派单正文：每条路径必须带超时）
DEFAULT_START_GRACE_SEC = 30.0
#: 优雅停的窗口；超时升级整树强杀
DEFAULT_STOP_GRACE_SEC = 15.0
#: 强杀后等端口释放的窗口；再不释放只能 failed 交人工
DEFAULT_KILL_GRACE_SEC = 10.0
#: probe 轮询间隔
POLL_INTERVAL_SEC = 0.25

#: 启动阶段：同阶段内并行拉起，阶段之间串行。后一阶段依赖前一阶段就位——
#: senate / financecoffer 的知识库与记忆要 qdrant 先起来，watch 再看前面全部。
#: 原来是全串行：任何一个模块等满 30s grace，后面的模块都干等着。
START_STAGES: tuple[tuple[str, ...], ...] = (
    ("tickhub", "tickhub_us", "wire", "qdrant", "proxyfleet"),
    ("senate", "financecoffer", "watch"),
)
#: 阶段内并发度：起进程是 IO 等待，但也不宜一次把整批进程全拉起来抢 CPU
START_WORKERS = 4
#: 本方进程集合缓存时长：`descendants()` 在 Windows 上是一次 PowerShell 父子表
#: 快照（约 0.7s），8 个模块与自愈 tick 共享同一份，TTL 内不重算。
OWN_TREE_TTL_SEC = 5.0

#: 单模块归属判定结果：managed = 账本证明是本 supervisor 拉的；external = 不是
Ownership = str

#: 端口避让器（QUOT-594）：给「模块名 / 基准端口 / 账本记录」，回实际可用端口。
#: own/adopted 占用回原端口（正常 adopt / 续跑）；foreign 占用回避让端口；
#: 上限内无空闲抛 AllocationFailed（引擎翻成 needs_human 语义的 SupervisorError）。
PortAvoider = Callable[[str, tuple[int, ...], "ModuleRecord"], tuple[int, ...]]

#: 端口重建器（QUOT-594）：按实际端口重建适配器（argv 里的 --port 字面值）。
#: 不支持避让的适配器（WSL compose / schtasks 等无端口参数形态）返回 None。
AdapterRebaser = Callable[["ModuleAdapter", tuple[int, ...]], "ModuleAdapter | None"]

EVIDENCE_LEDGER = "ledger"
EVIDENCE_CMDLINE_MISMATCH = "cmdline_mismatch"
EVIDENCE_PORT_UNKNOWN = "port_taken_by_unknown"
EVIDENCE_NOT_RUNNING = "not_running"


class SupervisorError(RuntimeError):
    """引擎操作失败（启动端口被占 / 停止后端口仍被占等），消息给人看。"""


class SupervisorEngine:
    """顺序启停引擎。全部依赖可注入，测试不起真进程。"""

    def __init__(
        self,
        *,
        adapters: dict[str, ModuleAdapter],
        ledger: Ledger,
        procs: ProcessOps,
        start_grace_sec: float = DEFAULT_START_GRACE_SEC,
        stop_grace_sec: float = DEFAULT_STOP_GRACE_SEC,
        kill_grace_sec: float = DEFAULT_KILL_GRACE_SEC,
        poll_interval_sec: float = POLL_INTERVAL_SEC,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        port_avoider: PortAvoider | None = None,
        adapter_rebaser: AdapterRebaser | None = None,
    ) -> None:
        self._adapters = module_registry(adapters)
        self._ledger = ledger
        self._procs = procs
        self._start_grace = float(start_grace_sec)
        self._stop_grace = float(stop_grace_sec)
        self._kill_grace = float(kill_grace_sec)
        self._poll = float(poll_interval_sec)
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = threading.RLock()
        #: 每模块一把锁：模块生命周期（启 / 停 / 重启）只串行同一模块，跨模块可并行。
        #: 全局 `_lock` 仍用于整体操作（reconcile / stop_all），两者不交叉获取：
        #: 整体 → 模块 单向，避免「整体 ⇄ 模块」死锁。
        self._module_locks: dict[str, threading.RLock] = {
            name: threading.RLock() for name in self._adapters
        }
        #: `_spawned_*` 的专用短锁（改造成模块锁后，起模块时不再需要全局锁）。
        self._state_lock = threading.RLock()
        #: 本任 supervisor 亲自 spawn 过的模块（reconcile 据此区分 spawned/adopted）
        self._spawned_here: set[str] = set()
        #: 本任 supervisor 亲自 spawn 过的进程 pid → cmdline 指纹（QUOT-557 / S4）。
        #: 端口冲突安全处置据此把「残留自有进程」认成 own_child；用前必须再验
        #: 存活 + 指纹（防 PID 复用把外来进程误认成自有，见 build_port_inspector）。
        self._spawned_pids: dict[int, str] = {}
        #: 本方进程集合缓存（`descendants` 一次快照服务所有模块，见 OWN_TREE_TTL_SEC）。
        self._own_tree_at: float = 0.0
        self._own_tree_pids: frozenset[int] = frozenset()
        #: 快照并行判活时缓存过期的同一刻只拍一次父子表
        self._tree_lock = threading.Lock()
        #: 外部认领 / 上一任入口的子树缓存（按 pid，同样 TTL）。
        self._peer_tree_at: dict[int, float] = {}
        self._peer_tree_pids: dict[int, set[int]] = {}
        #: 端口避让（QUOT-594）：未注入 = 维持 DEC-004 原语义（冲突即拒绝拉起）。
        self._port_avoider = port_avoider
        self._adapter_rebaser = adapter_rebaser
        #: 本任 supervisor 检测到的避让记录：模块名 → 基准端口表（state 可见）。
        self._avoided_from: dict[str, tuple[int, ...]] = {}

    @property
    def ledger(self) -> Ledger:
        """只读暴露账本（QUOT-547）：控制面诊断 API 据此统一归属口径。

        只给读入口（`record` / `records`），写仍走引擎方法——诊断面绝不改账本。
        """
        return self._ledger

    @property
    def spawned_pids(self) -> dict[int, str]:
        """本任 supervisor spawn 过的 pid → cmdline 指纹（只读副本，QUOT-557）。

        含已退出进程的历史 pid：调用方必须自行验存活 + 指纹后才可据此认归属，
        绝不把死 pid / 指纹不符（PID 复用）当成自有进程。
        """
        with self._state_lock:
            return dict(self._spawned_pids)

    # ------------------------------------------------------------------ 对账

    def reconcile(self) -> dict[str, str]:
        """崩溃恢复对账：进程已死 → stopped，绝不误判 running（判过证据 4）。"""
        with self._lock:
            return self._ledger.reconcile(self._verify_pid, spawned_this_session=self._spawned_here)

    def _claimed_pid(self, adapter: ModuleAdapter) -> int:
        """端口证据里「确实属于本模块」的监听 pid（拿不到返回 0）。

        `identify` 是适配器的扩展能力（Protocol 只保证 `probe()`），用运行时
        探测取；没有该能力的适配器一律返回 0，行为与改动前一致。
        """
        identify = getattr(adapter, "identify", None)
        if identify is None:
            return 0
        try:
            ident = identify()
        except Exception:
            return 0
        for item in getattr(ident, "ports", ()) or ():
            pid = getattr(item, "pid", None)
            if getattr(item, "matched", False) and pid:
                return int(pid)
        return 0

    def _listener_in_tree(self, adapter: ModuleAdapter, record: ModuleRecord) -> bool:
        """适配器认定属于本模块的监听 pid 是否在本方进程集合里。

        用 `_own_trees`（本任自己的整棵树 + 账本入口树，带 TTL 缓存）而不是
        `tree_pids(record.pid)`：多面模块另一面的 pid 是账本 pid 的**兄弟**，
        单棵树判不出来；逐节点 `tree_pids` 在 Windows 上还要按节点数×0.7s。
        """
        if not record.pid:
            return False
        claimed = self._claimed_pid(adapter)
        if not claimed:
            return False
        return claimed in self._own_trees(record)

    def _repoint_to_live_listener(
        self, name: str, adapter: ModuleAdapter, record: ModuleRecord
    ) -> None:
        """wrapper 起完就退时，把账本 pid 指到真正在监听的进程。

        `run_stack.py` 这类入口只是拉起脚本：脚本退出后服务由它起的子进程继续
        监听。账本若死抱着 wrapper 的 pid，reconcile 会把「账本 pid 已死 + 端口
        在听」判成 suspected_pid_reuse，页面就显示「假 down」——服务明明在听，
        状态却是 stopped，停止 / 启动按钮也跟着失效成长辈。
        这里只认适配器的端口证据：`identify()` 判 matched 的进程才认领。
        """
        pid = int(record.pid or 0)
        if pid and self._procs.alive(pid):
            return
        live = self._claimed_pid(adapter)
        if not live or live == pid:
            return
        cmd = self._procs.cmdline(live) or ()
        record.pid = live
        if cmd:
            record.cmdline_hash = cmdline_hash(cmd)
            self._mark_spawned(name, live, record.cmdline_hash)

    def _reap_stale(self, adapter: ModuleAdapter, record: ModuleRecord) -> list[int]:
        """清掉本模块在端口上的残留 / 重复实例（账本不认的那些）。

        判据三合一，缺一不动：端口在监听、适配器证据确认「占用者属于本模块」
        （cmdline 特征匹配）、pid 不是账本当前登记的活实例。外部程序的命令行
        不匹配本模块特征，永远进不来——DEC-014「绝不终止非本控制面的进程」
        不变。

        这是「控制面没停干净 → 子进程还在 → 端口冲突 → 再也起不来」的死循环
        的兜底：上一代遗留的同款进程是我方产物，可以按整树清理后重起。
        """
        identify = getattr(adapter, "identify", None)
        if identify is None:
            return []
        ledger_pid = (
            record.pid if record.pid is not None and self._procs.alive(record.pid) else None
        )
        # 账本 root 活着，只豁免**它自己的进程树**（wrapper → child 属于正常实例）。
        # 不能像以前那样「root 活着就整体 return」：多面模块每一代各起一棵树，
        # 上一代没退干净的那一面仍占着旧端口，它的根不在账本 root 之下——那是残留，
        # 必须清，否则「停不干净 → 端口冲突 → 只能加一 → 端口漂移」永远循环。
        # 豁免范围 = 本任控制面 spawn 过的**所有进程树** + 账本入口树。
        # 多面模块（senate 的 mcp + webui）每个面各起一棵**平级**的树，只按账本
        # 那一个 pid 的树算，会把另一面当成残留误杀。
        own_tree = self._own_trees(record)
        victims: set[int] = set()
        for port in adapter.ports:
            try:
                item = identify(port)
            except Exception:
                continue
            pid = getattr(item, "pid", None)
            if not pid or not getattr(item, "matched", False):
                continue
            if ledger_pid is not None and int(pid) == int(ledger_pid):
                continue
            if int(pid) in own_tree:
                continue
            victims.add(int(pid))
        for pid in sorted(victims):
            self._procs.kill_tree(pid)
        if victims:
            deadline = self._monotonic() + self._kill_grace
            while self._monotonic() < deadline:
                if not any(self._procs.alive(pid) for pid in victims):
                    break
                self._sleep(self._poll)
        return sorted(victims)

    def _verify_pid(self, record: ModuleRecord) -> str:
        """对账三态：alive / reuse / dead。无证据一律不认（失败关闭）。"""
        pid = record.pid
        if pid is None or not self._procs.alive(pid):
            return "dead"
        cmdline = self._procs.cmdline(pid)
        if not cmdline:
            # 活着但查不到命令行：无法证明还是我们拉的那个，按疑似复用处理，
            # 不托管、不强杀（设计文档 §对账：无证据不认人）
            return "reuse"
        if record.cmdline_hash and cmdline_hash(cmdline) != record.cmdline_hash:
            return "reuse"
        return "alive"

    # ------------------------------------------------------------------ 启动

    def start_all(self) -> dict[str, Any]:
        """分阶段全起：同阶段内并行、阶段之间串行，单模块失败不挡后面的。

        原来是一个模块接一个模块地串行等 grace：起得最慢的那个（qdrant /
        financecoffer 冷启）会把后面所有模块一起拖住。并行只发生在模块之间，
        每个模块内部的生命周期仍由自己的模块锁串行保护。
        """
        self.reconcile()
        results: dict[str, Any] = {}
        for stage in START_STAGES:
            pending = [name for name in stage if name in self._adapters]
            if not pending:
                continue
            with ThreadPoolExecutor(
                max_workers=max(1, min(START_WORKERS, len(pending))),
                thread_name_prefix="ldst-start",
            ) as pool:
                futures = {pool.submit(self._start_one, name): name for name in pending}
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        future.result()
                    except SupervisorError as exc:
                        results[name] = {"ok": False, "error": str(exc)}
                    except Exception as exc:  # noqa: BLE001 - 单模块异常不挡别人
                        results[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    else:
                        results[name] = {"ok": True, "error": None}
        ordered = {name: results[name] for name in MODULE_ORDER if name in results}
        return {
            "ok": all(item["ok"] for item in ordered.values()),
            "modules": ordered,
        }

    def _start_one(self, name: str) -> None:
        """阶段内的单个模块启动（并行工作单元）。"""
        self._start_module(name)

    def start_module(self, name: str) -> dict[str, Any]:
        """单模块启动（对外单模块入口，restart 也走它）。"""
        with self._module_lock(name):
            self._start_module(name)
            record = self._ledger.record(name)
            return {"module": name, "ok": True, "record": _view(record)}

    def _module_lock(self, name: str) -> threading.RLock:
        """取某一模块的锁（模块不存在时退化为全局锁，保证不会 KeyError）。"""
        lock = self._module_locks.get(name)
        return lock if lock is not None else self._lock

    def _mark_spawned(
        self, name: str, pid: int | None = None, fingerprint: str | None = None
    ) -> None:
        """登记「本任 supervisor 亲手 spawn」的模块 / 进程（`_state_lock` 保护）。"""
        with self._state_lock:
            self._spawned_here.add(name)
            if pid is not None:
                self._spawned_pids[int(pid)] = fingerprint or ""

    def _start_module(self, name: str) -> ModuleRecord:
        """单模块启动（模块锁外壳：同一模块串行，跨模块可并行）。"""
        with self._module_lock(name):
            return self._start_module_locked(name)

    def _start_module_locked(self, name: str) -> ModuleRecord:
        adapter = self._adapter(name)
        record = self._ledger.record(name) or ModuleRecord(
            module=name, ports=list(MODULE_PORTS[name])
        )
        record.ports = list(MODULE_PORTS[name])
        # 显式启动撤销之前的主动停意图：否则自愈会把这次合法运行当成「用户停过」。
        record.stopping_by_user = False

        if adapter.probe():
            # 端口上有本模块形态、但账本不认的进程 → 上一代没停干净的残留 /
            # 重复实例，先按整树清掉再走正常启动。外部程序的命令行不匹配本模块
            # 特征，永远不会进入清理集合（DEC-014 红线不变）。账本里活着且就是
            # 监听者的实例同样排除，不会被误杀。
            self._reap_stale(adapter, record)
            verdict = self._verify_pid(record)
            if verdict == "reuse" and self._listener_in_tree(adapter, record):
                # 账本 pid 活着、指纹是旧的（换了启动命令），但端口上被认出是本模块
                # 的监听者就是它的子孙：同一个实例，重记指纹即可，不是 PID 复用。
                live = tuple(self._procs.cmdline(int(record.pid or 0)) or ())
                record.cmdline_hash = cmdline_hash(live) if live else ""
                verdict = "alive"
            if verdict == "alive":
                # 幂等：已经在跑且账本对得上，直接续 running。
                # 但「用户显式点启动」本身就是要托管：把上一任留下的外部认领
                # 升级为本程序托管，否则页面永远停在「外部接管 / 端口由控制面
                # 之外的进程监听」，而账本 pid 还指着另一个进程（QUOT-XXX）。
                record.state = "running"
                record.health = HEALTH_OK
                if record.origin == "adopted":
                    record.origin = "spawned"
                    record.adopted_peer = False
                    self._mark_spawned(name)
                self._ledger.update(record)
                return record
            claimed = (
                self._claimed_pid(adapter) if verdict == "dead" and not record.adopted_peer else 0
            )
            if claimed:
                # 端口在听、evidence 已确认是本模块进程，但账本记的 pid 已经死了
                # （上一任留下的陈旧记录，例如强杀后由另一个本模块实例接手）。
                # 按端口证据认领实际 pid，不能落到避让：避让器只认账本 pid，
                # 会把「自己人占着端口」误判成外来冲突并拒绝拉起。
                record.pid = claimed
                record.state = "running"
                record.health = HEALTH_OK
                record.origin = "spawned"
                record.adopted_peer = False
                self._mark_spawned(name)
                self._ledger.update(record)
                return record
            # 基准端口被 foreign 占用：优先避让（QUOT-594），未配避让器时
            # 维持 DEC-004 原语义。own/adopted 占用避让器内部回原端口并按
            # 对账口径处置，绝不到达这里。
            adapter = self._avoid_or_refuse(adapter, record)
            record.ports = list(adapter.ports)

        record.state = "starting"
        record.forced_kill = False
        record.stopped_at = None
        record.stop_reason = None
        self._ledger.update(record)

        spawned = adapter.start()
        record.pid = spawned.pid
        record.parent_pid = spawned.parent_pid
        record.started_at = now_iso()
        record.origin = "spawned"
        record.adopted_peer = False
        record.cmdline_hash = cmdline_hash(spawned.cmdline)
        self._mark_spawned(name, int(spawned.pid), record.cmdline_hash)
        self._ledger.update(record)

        if self._await_probe(adapter, self._start_grace):
            self._repoint_to_live_listener(name, adapter, record)
            record.state = "running"
            record.health = HEALTH_OK
            self._ledger.update(record)
            return record

        # 起不来不留半死进程占端口：整树强杀（超时升级的对称面）
        record.state = "failed"
        record.forced_kill = True
        record.stop_reason = "start_timeout"
        self._force_kill(record, reason="start_timeout")
        self._ledger.update(record)
        raise SupervisorError(
            f"{name} 拉起后 {self._start_grace:.0f}s 内端口无监听，已整树终止（failed）"
        )

    def mark_spawned(
        self,
        name: str,
        *,
        pid: int,
        cmdline: Sequence[str] = (),
        parent_pid: int | None = None,
    ) -> bool:
        """控制面（venv 形态）自己拉起子进程后回写账本。

        QUOT-XXX：控制面的 `POST /api/control/peers/{id}/start` 走自己的 spawn
        路径，不经过本引擎，账本因此停留在上一任的 `adopted` 记录上——页面把
        新进程读成「外部接管 / 端口由控制面之外进程监听」，而 pid 指向的却是
        另一个已不在本端口上的旧进程（8000 正式服实例）。
        由控制面在端口监听成功后回调本方法，把归属改写为本程序托管。
        """
        if not pid:
            return False
        with self._lock:
            record = self._ledger.record(name) or ModuleRecord(
                module=name, ports=list(MODULE_PORTS.get(name, ()))
            )
            record.ports = list(MODULE_PORTS.get(name, record.ports or ()))
            record.pid = int(pid)
            if parent_pid is not None:
                record.parent_pid = int(parent_pid)
            record.started_at = now_iso()
            record.origin = "spawned"
            record.adopted_peer = False
            record.state = "running"
            record.health = HEALTH_OK
            record.stop_reason = None
            record.stopped_at = None
            record.stopping_by_user = False
            # 指纹必须取自这个 pid 的实际命令行：控制面按自己的启动命令拉起
            # （如 `python run_stack.py`），与适配器 argv 不同。沿用账本里上一代
            # 的旧指纹，下一轮 reconcile 就把活着的新进程判成 PID 复用 → stopped。
            live = tuple(cmdline) or tuple(self._procs.cmdline(int(pid)) or ())
            record.cmdline_hash = cmdline_hash(live) if live else ""
            self._mark_spawned(name, int(pid), record.cmdline_hash or None)
            # 控制面 spawn 的是 wrapper（run_stack.py 这类），它可能起完就退：
            # 端口在听但账本 pid 已死 → 不在这里改指就会显示「假 down」。
            self._repoint_to_live_listener(name, self._adapter(name), record)
            self._ledger.update(record)
            return True

    # ------------------------------------------------------------------ 停止

    def stop_all(self) -> dict[str, Any]:
        """按注册表反向全停；单模块失败不挡后面的，最后汇总。

        外部 peer 认领（`adopted_peer`）一律跳过并在汇总里记 `skipped`，
        进程与账本原样保留（QUOT-545）。
        """
        with self._lock:
            self.reconcile()
            results: dict[str, Any] = {}
            for name in reversed(MODULE_ORDER):
                try:
                    record = self._stop_module(name)
                except SupervisorError as exc:
                    results[name] = {"ok": False, "error": str(exc)}
                else:
                    item: dict[str, Any] = {"ok": True, "error": None}
                    if record.adopted_peer:
                        item["skipped"] = "adopted_peer"
                    results[name] = item
            return {
                "ok": all(item["ok"] for item in results.values()),
                "modules": results,
            }

    def stop_owned(self) -> dict[str, Any]:
        """退出全停（QUOT-527 托盘/服务口径）：只停本控制面 spawn 的模块。

        与 `stop_all()` 的区别是 origin 过滤：`adopted`（外部既有实例，页面自启
        只认领记账）与无账本的外部进程一律不碰，原样保留在 `kept_adopted`。
        托盘退出、服务停止等「应用退出」路径必须走这里；只有用户显式
        `supervisor stop --force` 才允许停掉账本里的全部模块。
        """
        with self._lock:
            self.reconcile()
            results: dict[str, Any] = {}
            kept: set[str] = set()
            for name in reversed(MODULE_ORDER):
                record = self._ledger.record(name)
                if record is None:
                    results[name] = {"ok": True, "skipped": "no_record"}
                    continue
                if record.origin != "spawned":
                    if record.state in ("starting", "running"):
                        kept.add(name)
                    results[name] = {"ok": True, "skipped": record.origin}
                    continue
                if record.state == "stopped":
                    results[name] = {"ok": True, "skipped": "stopped"}
                    continue
                try:
                    self._stop_module(name)
                except SupervisorError as exc:
                    results[name] = {"ok": False, "error": str(exc)}
                else:
                    results[name] = {"ok": True}
            return {
                "ok": all(item["ok"] for item in results.values()),
                "modules": results,
                "kept_adopted": [name for name in MODULE_ORDER if name in kept],
            }

    def stop_module(self, name: str) -> dict[str, Any]:
        """单模块停止（对外单模块入口，restart 也走它）。

        命令行已确认属于本模块的实例（含 `origin="adopted"`）一律可停；
        命令行不匹配的外部程序不会进到这条路径。
        """
        with self._module_lock(name):
            self._stop_module(name)
            record = self._ledger.record(name)
            return {"module": name, "ok": True, "record": _view(record)}

    # ------------------------------------------------------------------ 停止

    def _avoid_or_refuse(self, adapter: ModuleAdapter, record: ModuleRecord) -> ModuleAdapter:
        """基准端口被占时的避让决策（QUOT-594）。

        配了避让器：foreign 占用 → 避让到空闲端口并重建适配器（argv 换端口
        字面值）；own/adopted 占用 → 避让器回原端口，此时保持 DEC-004 拒绝
        拉起（本方进程由 adopt / S4 处置通道负责，start 不强拉双实例）。
        未配避让器 / 重建不支持（WSL compose 等无端口参数形态）→ 维持原语义。
        AllocationFailed（上限内无空闲）翻成 SupervisorError 交人工（needs_human）。
        """
        name = adapter.name
        base = tuple(adapter.ports)
        if self._port_avoider is None:
            raise SupervisorError(
                f"{name} 的端口 {record.ports} 已被其它进程占用"
                f"（账本 PID {record.pid} 不是监听者），按 DEC-004 不自动换端口"
            )
        from ladderstock.supervisor.port_allocator import AllocationFailed

        try:
            actual = self._port_avoider(name, base, record)
        except AllocationFailed as exc:
            raise SupervisorError(f"{name} 端口避让失败：{exc}") from None
        if tuple(actual) == base:
            # 本方（own_child / adopted_peer）占用：不避让，维持原拒绝语义
            raise SupervisorError(
                f"{name} 的端口 {record.ports} 已被本方相关进程占用"
                f"（账本 PID {record.pid} 不是监听者），请先处理该进程再启动"
            )
        if self._adapter_rebaser is None:
            raise SupervisorError(
                f"{name} 避让到端口 {actual} 但当前适配器不支持端口重建，按 DEC-004 不自动换端口"
            )
        rebased = self._adapter_rebaser(adapter, tuple(actual))
        if rebased is None:
            raise SupervisorError(
                f"{name} 避让到端口 {actual} 但当前适配器形态不支持端口重建，"
                "按 DEC-004 不自动换端口"
            )
        self._avoided_from[name] = base
        return rebased

    def _stop_module(self, name: str) -> ModuleRecord:
        """单模块停止（模块锁外壳：同一模块串行，跨模块可并行）。"""
        with self._module_lock(name):
            return self._stop_module_locked(name)

    def _stop_module_locked(self, name: str) -> ModuleRecord:
        adapter = self._adapter(name)
        record = self._ledger.record(name) or ModuleRecord(
            module=name, ports=list(MODULE_PORTS[name])
        )
        record.ports = list(MODULE_PORTS[name])

        if record.adopted_peer:
            # 命令行匹配本模块的实例一律按自有处置（允许停止）：认领不再等于
            # 「永不停止」。打上永不停止标记会让上一代残留永远关不掉，形成
            # 「停不住 → 端口冲突 → 起不来」死循环。
            record.adopted_peer = False

        if record.state == "stopped":
            # 账本已判定停了：要么真停了，要么对账判定端口上的进程不是我们的
            # （PID 复用 / 被外部占用）——一律不动那个活进程（DEC-014：绝不终止
            # 非本控制面的进程）。stopping/failed 则继续走停（补刀半死进程）。
            return record

        # 主动停意图（D4）：自愈看到标记绝不重拉；显式 start 会清掉它。
        record.stopping_by_user = True
        record.health = HEALTH_STOPPED
        record.state = "stopping"
        self._ledger.update(record)

        # 1) 优雅停：adapter 自己的形态（venv 信号 / compose stop / wsl pkill）。
        #    stop() 抛异常 = 优雅停失败，直接升级强杀，不重试（失败即停原则）。
        graceful_error: str | None = None
        try:
            adapter.stop()
        except Exception as exc:  # noqa: BLE001 - 任何优雅停失败都升级强杀
            graceful_error = f"优雅停异常：{exc}"

        # 2) 窗口内等真的停了（回读端口，不是只看 adapter 返回）
        if self._await_quiet(adapter, self._stop_grace):
            if record.pid is not None and self._procs.alive(record.pid):
                # 端口静了但入口进程还在（卡死 / 掉线 / 只绑了一半）：这是半停，
                # 不是停成功。整树收掉——否则它还活着，下次启动又撞残留。
                self._force_kill(record, reason="graceful_stop_left_process")
                self._await_quiet(adapter, self._kill_grace)
            record.state = "stopped"
            record.stopped_at = now_iso()
            record.stop_reason = graceful_error or STOP_BY_REQUEST
            self._ledger.update(record)
            return record

        # 3) 超时升级：整树强杀（cmd → python → worker 一起走）
        record.forced_kill = True
        record.stop_reason = graceful_error or "graceful_stop_timeout"
        self._force_kill(record, reason=record.stop_reason)

        if self._await_quiet(adapter, self._kill_grace):
            record.state = "stopped"
            record.stopped_at = now_iso()
            self._ledger.update(record)
            return record

        record.state = "failed"
        self._ledger.update(record)
        raise SupervisorError(
            f"{name} 强杀后 {self._kill_grace:.0f}s 内端口 {record.ports} 仍被占用，"
            "需人工处理（failed）"
        )

    def _force_kill(self, record: ModuleRecord, *, reason: str) -> None:
        """整树强杀：从账本入口 PID 起 `taskkill /T` 级语义，子孙一个不留。"""
        pid = record.pid
        if pid is None:
            return
        self._procs.kill_tree(pid)
        record.stop_reason = f"{reason}+forced_kill"

    # ------------------------------------------------------------------ 重启

    def restart_module(self, name: str) -> dict[str, Any]:
        """单模块重启：只动该模块，其它模块的 pid 必须不变（判过证据 3）。

        全程持同一把引擎锁：重启期间 start_all/stop_all/state 一律排队。
        命令行已确认属于本模块的实例（含 `origin="adopted"`）按自有处置：卡死 /
        掉线照常停掉再拉起；端口被命令行不匹配的外部程序占着时报冲突、不动它。
        """
        with self._module_lock(name):
            # 不再因为「认领」拒绝重启：命令行已确认是本模块程序的实例按自有处置，
            # 卡死 / 掉线时照常停掉再拉起，避免残留长期占端口。
            adapter = self._adapter(name)
            before = {other: self._ledger.record(other) for other in MODULE_ORDER if other != name}
            try:
                self._stop_module(name)
            except SupervisorError as exc:
                if adapter.probe():
                    # 停止流程清不掉端口 → 端口上不是本模块能停的东西：如实报「被别人占着」，
                    # 而不是含糊的「强杀失败」（DEC-004：不终止外部进程、不自动换端口）。
                    raise SupervisorError(
                        f"{name} 的端口已被其它进程占用（停止流程清不掉），"
                        "按 DEC-004 不终止外部进程、不自动换端口"
                    ) from None
                raise SupervisorError(str(exc)) from None
            self._start_module(name)
            after = {other: self._ledger.record(other) for other in MODULE_ORDER if other != name}
            drifted = [
                other for other, rec in after.items() if _pid_of(before.get(other)) != _pid_of(rec)
            ]
            if drifted:
                raise SupervisorError(f"重启 {name} 牵连了其它模块的 PID：{', '.join(drifted)}")
            record = self._ledger.record(name)
            return {
                "module": name,
                "ok": True,
                "record": _view(record),
                "others_unchanged": True,
            }

    # ------------------------------------------------------- 端口冲突安全处置（S4）

    def _is_provably_own_spawn(self, pid: int) -> bool:
        """占用者是否「可证明自有」（QUOT-557 / S4，失败关闭）。

        三重证据缺一不可：本任 supervisor 亲手 spawn 过该 pid + 此刻仍存活 +
        实时命令行指纹与 spawn 时一致。任何一条不满足（含 PID 复用）都返回 False——
        宁可少修，绝不把外来 / 认领进程误认成自有而终止（QUOT-545 / DEC-014 红线）。
        """
        expected = self.spawned_pids.get(int(pid))
        if expected is None:
            return False
        if not self._procs.alive(int(pid)):
            return False
        cmdline = self._procs.cmdline(int(pid))
        if not cmdline:
            return False
        return cmdline_hash(cmdline) == expected

    def restart_own_occupant(self, name: str, occupant_pid: int) -> dict[str, Any]:
        """S4 安全处置：端口被「可证明自有」的残留进程占用 → 重绑账本并安全重启。

        只对 own_child 生效：先把账本 pid 重绑到可证明自有的占用者（让停止路径对准
        它，绝不指向外来进程），再走 `restart_module` 的既有闸门停 + 重拉。证明不了
        自有（非本任 spawn / 已死 / 指纹不符 = PID 复用）一律抛错不动（失败关闭）。
        绝不改端口配置（DEC-004）；绝不终止外来 / adopted 进程（DEC-014 / QUOT-545）。
        """
        with self._lock:
            if not self._is_provably_own_spawn(int(occupant_pid)):
                raise SupervisorError(
                    f"{name} 端口占用者 pid={occupant_pid} 无法证明是本控制面自有进程"
                    "（非本任 spawn / 已退出 / 命令行指纹不符），按失败关闭不处置、不终止"
                )
            record = self._ledger.record(name) or ModuleRecord(
                module=name, ports=list(MODULE_PORTS[name])
            )
            record.pid = int(occupant_pid)
            record.origin = "spawned"
            record.adopted_peer = False
            record.cmdline_hash = self.spawned_pids[int(occupant_pid)]
            self._ledger.update(record)
            return self.restart_module(name)

    # ------------------------------------------------------------------ 认领

    def adopt_module(self, name: str) -> ModuleRecord | None:
        """冷启动前先认领：端口监听者命中本模块识别规则就记账，不 spawn、不杀。

        返回被认领的账本记录；没有可认领的实例（端口空闲 / 持有者不是本模块 /
        读不到证据）返回 None，调用方据此决定是否真正拉起。判定口径与 PageBoot
        的 adopt-only 一致：只认「端口在听 + 命令行命中 + 有 pid」，读不到不认。

        按识别证据认领的实例记 `adopted_peer=True`（外部 peer，QUOT-545）：
        停止路径一律跳过。账本已证明存活的前任自有进程走续健康分支，不打标记，
        崩溃恢复后仍可被 stop_all 正常停止。
        """
        with self._lock:
            adapter = self._adapter(name)
            record = self._ledger.record(name) or ModuleRecord(
                module=name, ports=list(MODULE_PORTS[name])
            )
            record.ports = list(MODULE_PORTS[name])
            if self._verify_pid(record) == "alive":
                # 账本已经认得它（pid 存活 + cmdline 指纹匹配），只续健康态。
                record.state = "running"
                record.health = HEALTH_OK
                record.stopping_by_user = False
                self._ledger.update(record)
                return record
            evidence = self._matched_evidence(adapter)
            if evidence is None or evidence.pid is None or not getattr(evidence, "cmdline", ()):
                return None
            parent_chain = getattr(evidence, "parent_chain", ())
            parent = parent_chain[0].parent_pid if parent_chain else None
            prior_pid = record.pid  # 上一代账本里的 pid（走到这里必然已死或指纹不符）
            if record.origin == "spawned" and prior_pid is not None:
                # 账本记着「这个模块上一代是本控制面自己拉起、而那个 pid 已经死了」
                # ——端口上这条同款命令行就是上一代没停干净的残留 / 重复实例。
                # 这里**不认领**：认领等于把半死或重复的实例继续留在端口上，用户
                # 下一次点重启还是撞冲突。直接返回 None，让调用方走正常启动路径，
                # 由 `_start_module` 按证据清理残留后重新拉起（关得掉、起得来）。
                return None
            record.pid = int(evidence.pid)
            record.parent_pid = parent
            record.started_at = record.started_at or now_iso()
            record.origin = "adopted"
            # 命令行已确认是本模块自己的程序（适配器证据命中）：不再打
            # 「永不停止」标记。打上标记会让上一代没退干净的残留永远关不掉，
            # 形成「停不住 → 端口冲突 → 起不来」的死循环。停止路径改按
            # 「命令行属于本模块」这一条客观证据处置，不碰命令行不匹配的外部程序。
            record.adopted_peer = False
            record.cmdline_hash = cmdline_hash(evidence.cmdline)
            record.state = "running"
            record.forced_kill = False
            record.stopped_at = None
            record.stop_reason = None
            record.stopping_by_user = False
            record.health = HEALTH_OK
            self._ledger.update(record)
            return record

    def _matched_evidence(self, adapter: ModuleAdapter) -> Any | None:
        """适配器的识别证据里挑第一条命中的端口（读不到 / 抛错一律不认）。"""
        identify = getattr(adapter, "identify", None)
        if not callable(identify):
            return None
        try:
            identification = identify()
        except Exception:  # noqa: BLE001 - 识别失败按「无可认领实例」处理，失败关闭
            return None
        items = getattr(identification, "ports", None)
        if items is None:
            items = (identification,)
        for item in items:
            if getattr(item, "matched", False) and getattr(item, "pid", None) is not None:
                return item
        return None

    # ------------------------------------------------------------------ 归属可见

    def state(self) -> dict[str, Any]:
        """每个模块 managed / external + 判定依据（`GET /api/supervisor/state`）。

        判定依据只来自账本与 cmdline 匹配，不扫端口猜归属：
        - `ledger` —— 账本命中（pid 存活 + 命令行指纹匹配），managed；
        - `cmdline_mismatch` —— PID 活着但命令行对不上（疑似复用），external；
        - `port_taken_by_unknown` —— 端口在听但账本证明不了是谁拉的，external；
        - `not_running` —— 没在监听，托管模块的静止态，managed。
        """
        with self._lock:
            # 判活（tasklist + CIM 命令行）与端口探测都是逐模块独立的子进程查询，
            # 串行时 /state 是各项之和；并行后对账与归属共用同一份判定，不重复查。
            live = [
                record
                for record in self._ledger.records().values()
                if record.state in ("starting", "running")
            ]
            with ThreadPoolExecutor(
                max_workers=START_WORKERS * 2, thread_name_prefix="ldst-state"
            ) as pool:
                verdict_jobs = {
                    record.module: pool.submit(self._verify_pid, record) for record in live
                }
                listen_jobs = {
                    name: pool.submit(self._adapter(name).probe) for name in MODULE_ORDER
                }
                verdicts = {name: job.result() for name, job in verdict_jobs.items()}
                listening_of = {name: job.result() for name, job in listen_jobs.items()}
            self._ledger.reconcile(
                lambda record: verdicts.get(record.module) or self._verify_pid(record),
                spawned_this_session=self._spawned_here,
            )
            modules: dict[str, Any] = {}
            for name in MODULE_ORDER:
                record = self._ledger.record(name) or ModuleRecord(
                    module=name, ports=list(MODULE_PORTS[name])
                )
                listening = listening_of[name]
                ownership, evidence = self._ownership(record, listening, verdicts.get(name))
                view = _view(record)
                view.update(
                    {
                        "ports": record.ports,
                        "ownership": ownership,
                        "evidence": evidence,
                        "listening": listening,
                    }
                )
                if name in self._avoided_from:
                    # QUOT-594：避让过的模块带基准端口，前端据此显示「已自动避让」
                    view["avoided_from"] = list(self._avoided_from[name])
                modules[name] = view
            return {
                "supervisor_pid": self._procs.self_pid(),
                "order": list(MODULE_ORDER),
                "modules": modules,
            }

    def _ownership(
        self, record: ModuleRecord, listening: bool, verdict: str | None = None
    ) -> tuple[Ownership, str]:
        if record.state == "stopped" and record.stop_reason == STOP_PID_REUSE:
            # 对账已证实疑似 PID 复用：端口上的活进程不是我们拉的（展示层翻译账本结论）
            return "external", EVIDENCE_CMDLINE_MISMATCH
        if record.state in ("starting", "running") and record.pid is not None:
            verdict = verdict or self._verify_pid(record)
            if verdict == "alive":
                return "managed", EVIDENCE_LEDGER
            # 账本进程死了：端口还开着就是别人占的，没开就是早就退了
            return (
                ("external", EVIDENCE_PORT_UNKNOWN)
                if listening
                else (
                    "managed",
                    EVIDENCE_NOT_RUNNING,
                )
            )
        if listening:
            return "external", EVIDENCE_PORT_UNKNOWN
        return "managed", EVIDENCE_NOT_RUNNING

    def _own_trees(self, record: ModuleRecord) -> set[int]:
        """本任控制面自己的进程集合 + 账本入口树。

        所有模块进程都是 supervisor 的子进程，所以「本方」= 自己这棵树的全部后代；
        多面模块的每个面是平级的直接子进程，仍在同一棵树内——只按账本单个 pid 的
        树判，会把另一面当成残留误杀。外部认领（adopted peer）不在自己树下，按
        账本 pid 单独补进来。
        一次父子表快照在 Windows 上约 0.7s，所以结果按 OWN_TREE_TTL_SEC 缓存，
        8 个模块与自愈 tick 共享同一份。
        """
        with self._tree_lock:
            now = time.monotonic()
            if self._own_tree_at > now:
                own = set(self._own_tree_pids)
            else:
                own = self._snapshot_own_tree()
                self._own_tree_pids = frozenset(own)
                self._own_tree_at = time.monotonic() + OWN_TREE_TTL_SEC
            if record.pid is None:
                return own
            entry = int(record.pid)
            if entry not in own:
                own |= self._peer_tree(entry)
            return own

    def _snapshot_own_tree(self) -> set[int]:
        roots = {int(self._procs.self_pid())} | {int(item) for item in self.spawned_pids}
        return self._descendants(roots)

    def _descendants(self, roots: set[int]) -> set[int]:
        """多根子树并集；进程操作面没这个能力时退回逐根 `tree_pids`（测试规模小）。"""
        descend = getattr(self._procs, "descendants", None)
        if callable(descend):
            try:
                return {int(item) for item in descend(roots)}
            except Exception:  # noqa: BLE001 - 快照失败就当没有本方集合
                return set()
        tree: set[int] = set()
        for root in roots:
            try:
                tree.add(root)
                tree |= {int(item) for item in self._procs.tree_pids(root) or ()}
            except Exception:  # noqa: BLE001 - 单个根查不到不影响其它根
                continue
        return tree

    def _peer_tree(self, pid: int) -> set[int]:
        """账本入口（可能不在本任树下）的子树，按 pid 缓存 OWN_TREE_TTL_SEC。"""
        now = time.monotonic()
        if self._peer_tree_at.get(pid, 0.0) > now:
            return set(self._peer_tree_pids.get(pid, set()))
        tree = self._descendants({pid})
        tree.add(pid)
        self._peer_tree_at[pid] = now + OWN_TREE_TTL_SEC
        self._peer_tree_pids[pid] = tree
        return set(tree)

    def identifies_port(self, name: str, port: int) -> bool:
        """端口上的进程链是否命中该模块的识别规则（只读；未知模块 / 无证据为 False）。"""
        identify = getattr(self._adapters.get(name), "identify", None)
        if not callable(identify):
            return False
        return bool(getattr(identify(port), "matched", False))

    def has_stray_listener(self, name: str) -> bool:
        """端口上被识别成本模块、却不在账本 pid 进程树内的实例（上一代残留）。

        多面模块（senate 的 mcp + webui）每一代各起一棵树：账本 pid 活着只说明
        这一面在跑，另一面若还是上一代的树，就会一直占着旧端口。残留不清，重启
        只能加一，端口越漂越远。自愈据此把「活着但有树外残留」判成没恢复，走一遍
        「先清残留再起」。
        识别不到 / 查询失败一律 False —— 判不准就不误触发重启。
        """
        record = self._ledger.record(name)
        if record is None or record.pid is None:
            return False
        adapter = self._adapters.get(name)
        if adapter is None:
            return False
        ledger_pid = int(record.pid)
        if not self._procs.alive(ledger_pid):
            return False
        identify = getattr(adapter, "identify", None)
        if not callable(identify):
            return False
        candidates: list[int] = []
        for port in adapter.ports:
            try:
                item = identify(port)
            except Exception:  # noqa: BLE001 - 单个面查不到证据不影响其它面
                continue
            claimant = getattr(item, "pid", None)
            if not claimant or not getattr(item, "matched", False):
                continue
            if int(claimant) != ledger_pid:
                candidates.append(int(claimant))
        if not candidates:
            return False
        tree = self._own_trees(record)
        if not tree:
            return False
        return any(claimant not in tree for claimant in candidates)

    # ------------------------------------------------------------------ 内部

    def _adapter(self, name: str) -> ModuleAdapter:
        try:
            return self._adapters[name]
        except KeyError:
            raise SupervisorError(f"未知模块：{name}（可选：{', '.join(MODULE_ORDER)}）") from None

    def _await_probe(self, adapter: ModuleAdapter, grace: float) -> bool:
        deadline = self._monotonic() + grace
        while True:
            if adapter.probe():
                return True
            if self._monotonic() >= deadline:
                return False
            self._sleep(self._poll)

    def _await_quiet(self, adapter: ModuleAdapter, grace: float) -> bool:
        deadline = self._monotonic() + grace
        while True:
            if not adapter.probe():
                return True
            if self._monotonic() >= deadline:
                return False
            self._sleep(self._poll)


def _pid_of(record: ModuleRecord | None) -> int | None:
    return record.pid if record else None


def _view(record: ModuleRecord | None) -> dict[str, Any]:
    if record is None:
        return {}
    return record.to_dict()
