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
from collections.abc import Callable
from typing import Any

from ladderstock.supervisor.adapters import (
    MODULE_ORDER,
    MODULE_PORTS,
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
from ladderstock.supervisor.proctree import ProcessOps, cmdline_hash

#: 拉起后等端口监听的窗口（QUOT-482 派单正文：每条路径必须带超时）
DEFAULT_START_GRACE_SEC = 30.0
#: 优雅停的窗口；超时升级整树强杀
DEFAULT_STOP_GRACE_SEC = 15.0
#: 强杀后等端口释放的窗口；再不释放只能 failed 交人工
DEFAULT_KILL_GRACE_SEC = 10.0
#: probe 轮询间隔
POLL_INTERVAL_SEC = 0.25

#: 单模块归属判定结果：managed = 账本证明是本 supervisor 拉的；external = 不是
Ownership = str

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
        #: 本任 supervisor 亲自 spawn 过的模块（reconcile 据此区分 spawned/adopted）
        self._spawned_here: set[str] = set()

    # ------------------------------------------------------------------ 对账

    def reconcile(self) -> dict[str, str]:
        """崩溃恢复对账：进程已死 → stopped，绝不误判 running（判过证据 4）。"""
        with self._lock:
            return self._ledger.reconcile(self._verify_pid, spawned_this_session=self._spawned_here)

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
        """按注册表顺序全起；单模块失败不挡后面的，最后汇总。"""
        with self._lock:
            self.reconcile()
            results: dict[str, Any] = {}
            for name in MODULE_ORDER:
                try:
                    self._start_module(name)
                except SupervisorError as exc:
                    results[name] = {"ok": False, "error": str(exc)}
                else:
                    results[name] = {"ok": True, "error": None}
            return {
                "ok": all(item["ok"] for item in results.values()),
                "modules": results,
            }

    def start_module(self, name: str) -> dict[str, Any]:
        """单模块启动（对外单模块入口，restart 也走它）。"""
        with self._lock:
            self._start_module(name)
            record = self._ledger.record(name)
            return {"module": name, "ok": True, "record": _view(record)}

    def _start_module(self, name: str) -> ModuleRecord:
        adapter = self._adapter(name)
        record = self._ledger.record(name) or ModuleRecord(
            module=name, ports=list(MODULE_PORTS[name])
        )
        record.ports = list(MODULE_PORTS[name])
        # 显式启动撤销之前的主动停意图：否则自愈会把这次合法运行当成「用户停过」。
        record.stopping_by_user = False

        if adapter.probe():
            if self._verify_pid(record) == "alive":
                # 幂等：已经在跑且账本对得上，直接续 running
                record.state = "running"
                record.health = HEALTH_OK
                self._ledger.update(record)
                return record
            raise SupervisorError(
                f"{name} 的端口 {record.ports} 已被其它进程占用"
                f"（账本 PID {record.pid} 不是监听者），按 DEC-004 不自动换端口"
            )

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
        self._spawned_here.add(name)
        self._ledger.update(record)

        if self._await_probe(adapter, self._start_grace):
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

    def stop_module(self, name: str) -> dict[str, Any]:
        """单模块停止（对外单模块入口，restart 也走它）。

        外部 peer 认领（`adopted_peer`）跳过不动，记录原样返回（QUOT-545）。
        """
        with self._lock:
            self._stop_module(name)
            record = self._ledger.record(name)
            return {"module": name, "ok": True, "record": _view(record)}

    def _stop_module(self, name: str) -> ModuleRecord:
        adapter = self._adapter(name)
        record = self._ledger.record(name) or ModuleRecord(
            module=name, ports=list(MODULE_PORTS[name])
        )
        record.ports = list(MODULE_PORTS[name])

        if record.adopted_peer:
            # 外部 peer 认领（QUOT-545）：只认领、不终止、不重启，退出后原样保留
            # （QUOT-519/523/527 红线）。不写停止意图、不改状态——账本对它是
            # 观测记录，不是处置依据。前任 supervisor 遗留的自有进程不带此标记，
            # 崩溃恢复语义不变。
            return record

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
        """
        with self._lock:
            before = {other: self._ledger.record(other) for other in MODULE_ORDER if other != name}
            self._stop_module(name)
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
            if (
                evidence is None
                or evidence.pid is None
                or not getattr(evidence, "cmdline", ())
            ):
                return None
            parent_chain = getattr(evidence, "parent_chain", ())
            parent = parent_chain[0].parent_pid if parent_chain else None
            record.pid = int(evidence.pid)
            record.parent_pid = parent
            record.started_at = record.started_at or now_iso()
            record.origin = "adopted"
            # 账本证明不了是本控制面拉的（否则走上面的续健康分支）：这是外部
            # peer 认领，不是前任 supervisor 遗留的自有进程——任何停止路径
            # 一律跳过（QUOT-545）。
            record.adopted_peer = True
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
            self.reconcile()
            modules: dict[str, Any] = {}
            for name in MODULE_ORDER:
                record = self._ledger.record(name) or ModuleRecord(
                    module=name, ports=list(MODULE_PORTS[name])
                )
                listening = self._adapter(name).probe()
                ownership, evidence = self._ownership(record, listening)
                view = _view(record)
                view.update(
                    {
                        "ports": record.ports,
                        "ownership": ownership,
                        "evidence": evidence,
                        "listening": listening,
                    }
                )
                modules[name] = view
            return {
                "supervisor_pid": self._procs.self_pid(),
                "order": list(MODULE_ORDER),
                "modules": modules,
            }

    def _ownership(self, record: ModuleRecord, listening: bool) -> tuple[Ownership, str]:
        if record.state == "stopped" and record.stop_reason == STOP_PID_REUSE:
            # 对账已证实疑似 PID 复用：端口上的活进程不是我们拉的（展示层翻译账本结论）
            return "external", EVIDENCE_CMDLINE_MISMATCH
        if record.state in ("starting", "running") and record.pid is not None:
            verdict = self._verify_pid(record)
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
