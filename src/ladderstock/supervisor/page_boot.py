"""网页打开即自启自有子进程（QUOT-522）。

接线对象是**已有**的 QUOT-482 supervisor 账本（不新建一套 supervisor）：

- 看板应用启动（= 用户打开网页，`create_app` 的 startup 钩子）时调一次
  `ensure()`，幂等：
  - **watch 扫描器**：本机已有当日分钟样本就立刻跑一轮扫描（落盘口径与
    `ldst watch start` 一致：`watch/status.json` + `watch/events-<date>.jsonl`），
    并拉起自有守护子进程按 `LDST_DEBOUNCE_TICK` 周期重复扫描；样本还没落盘
    （TickHub 未取数）时按退避重试——**首屏那一刻没有样本也不会永远不跑**；
  - **取数器**：`ldst schedule run`（APScheduler 长驻）作为自有长驻子进程，
    只在没有同名实例时拉起。
- **peer（tickhub / tickhub_us / wire / senate / financecoffer / proxyfleet）
  一律 adopt-only**：只识别端口持有者、命中识别规则就记进账本
  （origin=`adopted`，pid + cmdline_hash），**绝不 spawn / restart / stop**。
  端口被「不属于该 peer 的进程」占着时如实记 `port_occupied`，绝不去杀。

宿主接触面收在 `HostPivot` 后面（listening / owner_pid / find_processes / spawn），
测试注入假实现。本模块只在 `stop_children()` 里终止**自己拉起**的子进程。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ladderstock.core import runtime
from ladderstock.supervisor.ledger import HEALTH_OK, Ledger, ModuleRecord, now_iso
from ladderstock.supervisor.module_adapters import (
    HostPivot,
    ProcessSnapshot,
    default_host_pivot,
)
from ladderstock.supervisor.proctree import cmdline_hash, split_windows_commandline
from ladderstock.watch import status as watch_status
from ladderstock.watch.signal import WatchEvent, detect_signals, load_bars

if TYPE_CHECKING:
    from ladderstock.supervisor.adapters import ModuleAdapter

_log = logging.getLogger(__name__)

#: 总开关：`0` / `false` / `off` 关闭页面自启（自有子进程不拉起）
PAGE_AUTOSTART_ENV = "LDST_PAGE_AUTOSTART"
#: 守护进程周期心跳间隔（秒）
TICK_ENV = "LDST_DEBOUNCE_TICK"
DEFAULT_TICK_SEC = 30.0
#: 守护进程内的扫描命令（JSON argv，由看板拉起时注入）
GUARD_SCAN_ARG_ENV = "LDST_WATCH_GUARD_SCAN"
#: 守护进程的退出标记文件：看板关闭时写它，守护在扫描空档自行退出
GUARD_STOP_ARG_ENV = "LDST_WATCH_GUARD_STOP"
#: 自带子进程的退出标记（看板关闭时写；`python -m ...` 在 Windows 上收不到优雅终止）
CHILD_STOP_ARG_ENV = "LDST_PAGE_BOOT_STOP"
#: 等自有子进程正常退出的轮询（秒）：先给子进程一个自退窗口，等不到再恢复它（/F）
STOP_GRACE_SEC = 5.0
STOP_POLL_SEC = 0.2
MODE_ONCE = "page-autostart-once"
MODE_GUARD = "page-autostart-guard"
#: 找当日分钟样本时回看的天数（节假日 / 空盘不空转）
BAR_SEARCH_LOOKBACK_DAYS = 7
#: 守护进程首轮遇「暂无样本」（退出码 4）的退避重试间隔（秒）
GUARD_RETRY_DELAYS: tuple[float, ...] = (5.0, 15.0, 45.0, 120.0, 300.0)

WATCH_GUARD_MODULE = "watch_guard"
SCHEDULER_MODULE = "scheduler"
SELF_MODULES: tuple[str, ...] = (WATCH_GUARD_MODULE, SCHEDULER_MODULE)

#: adopt-only 的 peer 模块（顺序同 MODULE_ORDER，排除自有模块）
ADOPT_ONLY_MODULES: tuple[str, ...] = (
    "tickhub",
    "tickhub_us",
    "wire",
    "senate",
    "financecoffer",
    "proxyfleet",
)

# ---- 报告状态 -------------------------------------------------------------- #

STATE_SPAWNED = "spawned"
STATE_ADOPTED = "adopted"
STATE_ALREADY_RUNNING = "already_running"
STATE_WAITING_SOURCE = "waiting_source"
STATE_PORT_OCCUPIED = "port_occupied"
STATE_NOT_LISTENING = "not_listening"
STATE_SKIPPED = "skipped"
STATE_FAILED = "failed"


def autostart_enabled(env: Mapping[str, str] | None = None) -> bool:
    raw = str((os.environ if env is None else env).get(PAGE_AUTOSTART_ENV, "")).strip().lower()
    return raw not in ("0", "false", "off", "no")


def _local_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# watch 扫描器（自有）
# --------------------------------------------------------------------------- #


class WatchScanner:
    """进程内跑盯盘扫描，落盘口径与 `ldst watch start` 完全一致。

    不联网、不取数（DEC-006）：样本来自 `<data_root>/minute_bars/`，或显式
    `sources=` 指定的离线 fixture。
    """

    def __init__(
        self,
        data_root: Path,
        *,
        threshold: float = 3.0,
        sources: Sequence[Path] = (),
        today: Callable[[], str] | None = None,
    ) -> None:
        self.data_root = Path(data_root)
        self.threshold = float(threshold)
        self.sources = tuple(Path(item) for item in sources)
        self._today = today or (lambda: date.today().isoformat())

    def _candidates(self, day: str) -> list[Path]:
        base = self.data_root / "minute_bars"
        found: list[Path] = [base / f"{day}.jsonl", base / f"{day}.json"]
        for source in self.sources:
            if source.is_dir():
                found.extend([source / f"{day}.jsonl", source / f"{day}.json"])
            else:
                found.append(source)
        return found

    def resolve_source(self, day: str | None = None) -> tuple[str, Path | None]:
        """当日样本优先；缺省时回看最近几天。返回 (交易日, 样本路径或 None)。"""
        if day is not None:
            day_text = date.fromisoformat(day).isoformat()
            for candidate in self._candidates(day_text):
                if candidate.is_file():
                    return day_text, candidate
            return day_text, None
        today = date.fromisoformat(self._today())
        for offset in range(BAR_SEARCH_LOOKBACK_DAYS + 1):
            day_text = date.fromordinal(today.toordinal() - offset).isoformat()
            for candidate in self._candidates(day_text):
                if candidate.is_file():
                    return day_text, candidate
        return today.isoformat(), None

    def events_path(self, day: str) -> Path:
        return watch_status.watch_dir(self.data_root) / f"events-{day}.jsonl"

    def status_path(self) -> Path:
        return watch_status.status_path(self.data_root)

    def read_events(self, day: str) -> tuple[list[WatchEvent], int]:
        """读当日已落盘事件；坏行只跳过并计数（与 CLI 同口径）。"""
        path = self.events_path(day)
        if not path.is_file():
            return [], 0
        events: list[WatchEvent] = []
        skipped = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(WatchEvent.from_dict(json.loads(line)))
            except (ValueError, TypeError):
                skipped += 1
        return events, skipped

    def _append_events(self, day: str, fresh: Sequence[WatchEvent]) -> None:
        path = self.events_path(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for event in fresh:
                handle.write(json.dumps(event.as_dict(), ensure_ascii=False) + "\n")

    def scan(
        self,
        *,
        mode: str = MODE_ONCE,
        trigger: str = "page-open",
        day: str | None = None,
    ) -> dict[str, Any]:
        """跑一轮扫描并落盘；任何失败折进报告，不抛异常（守护进程要活着）。"""
        try:
            day_text, source = self.resolve_source(day)
        except ValueError as exc:
            return {"ok": False, "trigger": trigger, "detail": f"日期不可解析：{exc}"}
        if source is None:
            return {
                "ok": False,
                "trigger": trigger,
                "waiting_source": True,
                "date": day_text,
                "detail": (
                    f"{day_text} 没有可用的分钟样本"
                    f"（找过 {self.data_root / 'minute_bars'}），等取数器落盘后自动重扫"
                ),
            }
        try:
            bars = load_bars(source)
            detected = detect_signals(bars, threshold=self.threshold)
        except (OSError, ValueError) as exc:
            return {"ok": False, "trigger": trigger, "detail": f"样本不可用：{exc}"}

        known, dirty_lines = self.read_events(day_text)
        known_keys = {event.key for event in known}
        fresh = [event for event in detected if event.key not in known_keys]
        if fresh:
            try:
                self._append_events(day_text, fresh)
            except OSError as exc:
                return {"ok": False, "trigger": trigger, "detail": f"事件落盘失败：{exc}"}
        total = known + fresh
        stamp = _local_iso()
        document: dict[str, Any] = {
            "running": True,
            "mode": mode,
            "date": day_text,
            "threshold": self.threshold,
            "started_at": stamp,
            "last_run_at": stamp,
            "bars": len(bars),
            "source": str(source),
            "source_detail": bars[0].source_detail if bars else "-",
            "event_count": len(total),
            "recent_events": [
                event.as_dict() for event in total[-watch_status.STATUS_RECENT_EVENTS :]
            ],
            "events_file": str(self.events_path(day_text)),
        }
        path = self.status_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(document, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            return {"ok": False, "trigger": trigger, "detail": f"状态落盘失败：{exc}"}
        report: dict[str, Any] = {
            "ok": True,
            "trigger": trigger,
            "date": day_text,
            "bars": len(bars),
            "new_events": len(fresh),
            "event_count": len(total),
            "source": str(source),
            "status_path": str(path),
        }
        if dirty_lines:
            report["warnings"] = [f"events 文件有 {dirty_lines} 行无法解析，已跳过"]
        return report


# --------------------------------------------------------------------------- #
# 自启总控
# --------------------------------------------------------------------------- #


@dataclass
class ChildReport:
    name: str
    state: str
    pid: int | None = None
    origin: str | None = None
    ports: list[int] = field(default_factory=list)
    detail: str = ""

    def view(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "pid": self.pid,
            "origin": self.origin,
            "ports": list(self.ports),
            "detail": self.detail,
        }


class PageBoot:
    """页面打开时的自启总控：幂等、单路失败只记账、绝不拖垮看板启动。"""

    def __init__(
        self,
        *,
        data_root: Path,
        ledger: Ledger,
        host: HostPivot | None = None,
        adapters: Mapping[str, ModuleAdapter] | None = None,
        scanner: WatchScanner | None = None,
        scheduler_argv: Sequence[str] | None = None,
        run_cwd: Path | None = None,
        log_dir: Path | None = None,
        env: Mapping[str, str] | None = None,
        tick_sec: float = DEFAULT_TICK_SEC,
        enabled: bool | None = None,
    ) -> None:
        self.data_root = Path(data_root)
        self.ledger = ledger
        self.host: HostPivot = host or default_host_pivot()
        self.adapters = dict(adapters or {})
        self.scanner = scanner or WatchScanner(self.data_root)
        self.scheduler_argv = list(scheduler_argv) if scheduler_argv else []
        self.run_cwd = Path(run_cwd) if run_cwd else self.data_root
        self.log_dir = Path(log_dir) if log_dir else _default_log_dir()
        self.env = dict(os.environ if env is None else env)
        self.tick_sec = float(tick_sec)
        self.enabled = autostart_enabled(self.env) if enabled is None else bool(enabled)
        self._lock = threading.RLock()
        self._last: dict[str, Any] | None = None

    # ------------------------------------------------------------------ 入口

    def ensure(self, *, trigger: str = "page-open") -> dict[str, Any]:
        """幂等自启：自有子进程按需拉起，peer 只认领记账。任何情况都返回报告。"""
        with self._lock:
            report: dict[str, Any] = {
                "ok": True,
                "trigger": trigger,
                "at": now_iso(),
                "enabled": self.enabled,
                "actions": {},
                "children": {},
                "peers": {},
            }
            scan: dict[str, Any] | None = None
            if self.enabled:
                scan = self.scanner.scan(mode=MODE_ONCE, trigger=trigger)
                report["watch_scan"] = scan
            for name, ensure_child in (
                (WATCH_GUARD_MODULE, self._ensure_watch_guard),
                (SCHEDULER_MODULE, self._ensure_scheduler),
            ):
                if not self.enabled:
                    report["children"][name] = ChildReport(
                        name,
                        STATE_SKIPPED,
                        detail=f"{PAGE_AUTOSTART_ENV}=0，页面自启已关闭",
                    ).view()
                    continue
                try:
                    outcome, child = ensure_child(scan)
                except Exception as exc:  # noqa: BLE001 - 单路失败不拖垮启动
                    _log.warning("页面自启 %s 失败（已吞）", name, exc_info=True)
                    outcome = STATE_FAILED
                    child = ChildReport(name, STATE_FAILED, detail=str(exc))
                report["actions"][name] = outcome
                report["children"][name] = child.view()
            for name in ADOPT_ONLY_MODULES:
                try:
                    peer = self._adopt_peer(name)
                except Exception as exc:  # noqa: BLE001
                    _log.warning("peer 认领 %s 失败（已吞）", name, exc_info=True)
                    peer = ChildReport(name, STATE_FAILED, detail=str(exc))
                report["peers"][name] = peer.view()
            self._last = report
            return report

    def ensure_module(self, module: str) -> ChildReport:
        """重拉通道入口（QUOT-607）：单模块幂等 ensure，自愈在自有子进程死后调用。

        只认 `SELF_MODULES` 两个名字，其余如实返回 skipped（自愈按失败记账，
        绝不会出现「未知模块」——这两个名字本来就该由页面自启负责）。
        """
        with self._lock:
            if module == WATCH_GUARD_MODULE:
                _, child = self._ensure_watch_guard(None)
            elif module == SCHEDULER_MODULE:
                _, child = self._ensure_scheduler(None)
            else:
                child = ChildReport(module, STATE_SKIPPED, detail=f"非页面自有子进程：{module}")
            return child

    def last_report(self) -> dict[str, Any] | None:
        with self._lock:
            return self._last

    # ------------------------------------------------------------------ 命令

    def _self_exe_command(self) -> tuple[str, ...]:
        """自有子进程的入口：冻结态用自身 exe，源码态优先 console-script。

        打包后的 exe 本身就是 CLI，再补 `-m ladderstock.cli.main` 会变成未知参数；
        子命令（`__watch-guard` / `schedule run`）直接接在 exe 后面（QUOT-525）。
        """
        if runtime.is_frozen():
            return (str(runtime.executable()),)
        exe = "ldst.exe" if os.name == "nt" else "ldst"
        console = Path(sys.executable).with_name(exe)
        if console.is_file():
            return (str(console),)
        return (sys.executable, "-m", "ladderstock.cli.main")

    def _scan_command(self) -> tuple[str, ...]:
        threshold = f"{self.scanner.threshold:g}"
        return (*self._self_exe_command(), "watch", "start", "--threshold", threshold)

    def _watch_guard_cmdline(self) -> tuple[str, ...]:
        return (*self._self_exe_command(), "__watch-guard", "--tick", f"{self.tick_sec:g}")

    def _scheduler_cmdline(self) -> tuple[str, ...]:
        if self.scheduler_argv:
            return tuple(self.scheduler_argv)
        return (*self._self_exe_command(), "schedule", "run")

    def _find_by_cmdline(self, cmdline: Sequence[str]) -> ProcessSnapshot | None:
        """按命令行指纹找活进程（同命令不同 PID = 已在跑，不重复拉起）。

        Windows 的 CIM 回读会把整个 argv 拼成一条 CommandLine，所以先拆回 argv
        再算指纹；单元素串在非 Windows 上也一样处理。
        """
        wanted = cmdline_hash(cmdline)
        needles = sorted({part for part in cmdline if len(part) >= 6}, key=len, reverse=True)
        for needle in needles[:3]:
            for snap in self.host.find_processes(needle):
                if cmdline_hash(_snapshot_argv(snap.cmdline)) == wanted:
                    return snap
        return None

    # ------------------------------------------------------- 自有：watch 守护

    def _ensure_watch_guard(self, scan: dict[str, Any] | None) -> tuple[str, ChildReport]:
        cmdline = self._watch_guard_cmdline()
        existing = self._find_by_cmdline(cmdline)
        if existing is not None:
            self._record_self(WATCH_GUARD_MODULE, existing.pid, cmdline, "已在运行")
            return STATE_ALREADY_RUNNING, ChildReport(
                WATCH_GUARD_MODULE,
                STATE_ALREADY_RUNNING,
                pid=existing.pid,
                origin="spawned",
                detail="自有 watch 守护已在运行（不重复拉起）",
            )
        waiting = bool(scan and scan.get("waiting_source"))
        env = self._child_env()
        env[GUARD_SCAN_ARG_ENV] = json.dumps(list(self._scan_command()), ensure_ascii=False)
        env[GUARD_STOP_ARG_ENV] = str(self._stop_marker_path(WATCH_GUARD_MODULE))
        self._clear_stop_marker(WATCH_GUARD_MODULE)
        if waiting and scan is not None:
            env["LDST_WATCH_GUARD_WAIT"] = ",".join(f"{delay:g}" for delay in GUARD_RETRY_DELAYS)
        try:
            pid = self.host.spawn(
                cmdline,
                cwd=self.run_cwd,
                env=env,
                log_path=self.log_dir / "watch-guard.log",
            )
        except Exception as exc:  # noqa: BLE001
            return STATE_FAILED, ChildReport(
                WATCH_GUARD_MODULE, STATE_FAILED, detail=f"拉起失败：{exc}"
            )
        self._record_self(WATCH_GUARD_MODULE, pid, cmdline, "守护启动")
        if waiting:
            return STATE_WAITING_SOURCE, ChildReport(
                WATCH_GUARD_MODULE,
                STATE_WAITING_SOURCE,
                pid=pid,
                origin="spawned",
                detail="守护已拉起；当前无分钟样本，按退避重试，样本落盘后自动扫描",
            )
        return STATE_SPAWNED, ChildReport(
            WATCH_GUARD_MODULE,
            STATE_SPAWNED,
            pid=pid,
            origin="spawned",
            detail="watch 守护已拉起，按周期扫描",
        )

    def _child_env(self) -> dict[str, str]:
        env = dict(self.env)
        env[TICK_ENV] = f"{self.tick_sec:g}"
        env["PYTHONUNBUFFERED"] = "1"
        # 入口退化成 `python -m` 时，子进程要能找到本仓库（src 布局 / 未安装）；
        # 冻结态没有源码树，子进程就是同一个 exe，不需要 PYTHONPATH。
        if not runtime.is_frozen():
            src = Path(__file__).resolve().parents[3] / "src"
            if src.is_dir():
                paths = [
                    str(src),
                    *[item for item in env.get("PYTHONPATH", "").split(os.pathsep)],
                ]
                env["PYTHONPATH"] = os.pathsep.join(item for item in paths if item)
        return env

    # ----------------------------------------------------------- 自有：调度器

    def _ensure_scheduler(self, scan: dict[str, Any] | None) -> tuple[str, ChildReport]:
        del scan
        cmdline = self._scheduler_cmdline()
        existing = self._find_by_cmdline(cmdline)
        if existing is not None:
            self._record_self(SCHEDULER_MODULE, existing.pid, cmdline, "已在运行")
            return STATE_ALREADY_RUNNING, ChildReport(
                SCHEDULER_MODULE,
                STATE_ALREADY_RUNNING,
                pid=existing.pid,
                origin="spawned",
                detail="取数调度器已在运行（不重复拉起）",
            )
        try:
            env = self._child_env()
            env[CHILD_STOP_ARG_ENV] = str(self._stop_marker_path(SCHEDULER_MODULE))
            self._clear_stop_marker(SCHEDULER_MODULE)
            pid = self.host.spawn(
                cmdline,
                cwd=self.run_cwd,
                env=env,
                log_path=self.log_dir / "scheduler.log",
            )
        except Exception as exc:  # noqa: BLE001
            return STATE_FAILED, ChildReport(
                SCHEDULER_MODULE, STATE_FAILED, detail=f"拉起失败：{exc}"
            )
        self._record_self(SCHEDULER_MODULE, pid, cmdline, "调度器启动")
        return STATE_SPAWNED, ChildReport(
            SCHEDULER_MODULE,
            STATE_SPAWNED,
            pid=pid,
            origin="spawned",
            detail="取数调度器已拉起（长驻）",
        )

    def _record_self(self, module: str, pid: int, cmdline: Sequence[str], note: str) -> None:
        stamp = now_iso()
        existing = self.ledger.record(module)
        record = ModuleRecord(
            module=module,
            ports=list(existing.ports) if existing else [],
            pid=int(pid),
            parent_pid=os.getpid(),
            started_at=stamp,
            origin="spawned",
            cmdline_hash=cmdline_hash(cmdline),
            state="running",
            health=HEALTH_OK,
            updated_at=stamp,
        )
        if existing is not None:
            # 就地改字段，不整条替换：重拉计数 / 退避窗口 / 停止意图是自愈的
            # 记账，每次页面自启重拉都换新记录会把它们清零（QUOT-607 回归发现）。
            for name in (
                "restart_count",
                "restart_window",
                "stopping_by_user",
                "forced_kill",
                "adopted_peer",
            ):
                setattr(record, name, getattr(existing, name))
        record.last_exit_reason = note
        self.ledger.update(record)

    # ------------------------------------------------------- peer adopt-only

    def _adopt_peer(self, name: str) -> ChildReport:
        adapter = self.adapters.get(name)
        ports = list(getattr(adapter, "ports", ()) or ())
        listening = [port for port in ports if self.host.listening(port)]
        matched = self._identify(adapter, listening)
        if matched is not None:
            self._record_peer(name, matched, ports)
            return ChildReport(
                name,
                STATE_ADOPTED,
                pid=matched.pid,
                origin="adopted",
                ports=ports,
                detail="已在运行：仅认领记账，不重启 / 不终止",
            )
        if not listening:
            return ChildReport(
                name, STATE_NOT_LISTENING, ports=ports, detail="peer 未在监听（不拉起）"
            )
        holder = next((self.host.owner_pid(port) for port in listening), None)
        record = self.ledger.record(name)
        return ChildReport(
            name,
            STATE_PORT_OCCUPIED,
            pid=record.pid if record else holder,
            origin=record.origin if record else None,
            ports=ports,
            detail=(
                f"端口 {listening} 在听，但持有者 pid={holder} 的命令行不匹配 {name}"
                " 的识别规则；维持现状，绝不终止"
            ),
        )

    def _identify(self, adapter: Any, listening: Sequence[int]) -> ProcessSnapshot | None:
        """端口监听者的命令行是否命中该 peer 的识别规则（读不到即不认）。"""
        identify = getattr(adapter, "identify", None)
        if not callable(identify):
            return None
        for port in listening:
            evidence = identify(port)
            if getattr(evidence, "matched", False) and evidence.pid is not None:
                parent = evidence.parent_chain[0].parent_pid if evidence.parent_chain else None
                return ProcessSnapshot(
                    pid=int(evidence.pid),
                    parent_pid=parent,
                    name=evidence.name,
                    cmdline=tuple(evidence.cmdline),
                )
        return None

    def _record_peer(self, name: str, snap: ProcessSnapshot, ports: Sequence[int]) -> None:
        record = self.ledger.record(name)
        digest = cmdline_hash(snap.cmdline)
        if (
            record is not None
            and record.pid == snap.pid
            and record.cmdline_hash == digest
            and record.state == "running"
        ):
            return
        stamp = now_iso()
        self.ledger.update(
            ModuleRecord(
                module=name,
                ports=list(record.ports) if record and record.ports else list(ports),
                pid=snap.pid,
                parent_pid=snap.parent_pid,
                started_at=record.started_at if record else stamp,
                origin="adopted",
                # 外部 peer 认领（QUOT-545）：与 engine.adopt_module 同一口径，
                # 停止路径一律跳过
                adopted_peer=True,
                cmdline_hash=digest,
                state="running",
                health=HEALTH_OK,
                updated_at=stamp,
            )
        )

    # ------------------------------------------------------------------ 关闭

    def _stop_marker_path(self, name: str = WATCH_GUARD_MODULE) -> Path:
        return self.log_dir / f"{name}.stop"

    def _clear_stop_marker(self, name: str) -> None:
        try:
            self._stop_marker_path(name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            _log.warning("退出标记不可清理：%s", self._stop_marker_path(name))

    def stop_children(self) -> dict[str, str]:
        """只停本页面自启**自己拉起**的子进程（账本 origin=spawned 的 watch_guard）。

        Windows 上无控制台的 python 进程拒绝优雅终止（taskkill 报「只能强制终止」），
        所以先写退出标记让守护下一拍自退，等不到再 `/F` 连根。
        """
        with self._lock:
            outcomes: dict[str, str] = {}
            deadline = time.monotonic() + STOP_GRACE_SEC
            for name in SELF_MODULES:
                record = self.ledger.record(name)
                if record is None or record.origin != "spawned" or record.pid is None:
                    outcomes[name] = "not_owned"
                    continue
                pid = int(record.pid)
                try:
                    if self._request_stop(name, pid, deadline):
                        outcomes[name] = "stopped"
                    else:
                        outcomes[name] = "failed:没退出"
                except Exception as exc:  # noqa: BLE001
                    outcomes[name] = f"failed:{exc}"
            for name in SELF_MODULES:
                self._clear_stop_marker(name)
            return outcomes

    def _request_stop(self, name: str, pid: int, deadline: float) -> bool:
        """写退出标记 → 等子进程自退 → 超时仍活着就 `/F` 连根。返回是否已退出。"""
        marker = self._stop_marker_path(name)
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(now_iso(), encoding="utf-8")
        except OSError:
            _log.warning("退出标记写不了，直接终止：%s", marker)
        if self._await_exit(pid, deadline):
            return True
        self.host.terminate_tree(pid, force=True)
        return self.host.snapshot(pid) is None

    def _await_exit(self, pid: int, deadline: float) -> bool:
        while time.monotonic() < deadline:
            if self.host.snapshot(pid) is None:
                return True
            time.sleep(STOP_POLL_SEC)
        return self.host.snapshot(pid) is None


def _snapshot_argv(cmdline: Sequence[str]) -> tuple[str, ...]:
    """CIM 回读的命令行是拼接串；单元素时先拆回 argv 再算指纹。"""
    if len(cmdline) == 1 and cmdline[0]:
        return split_windows_commandline(cmdline[0])
    return tuple(cmdline)


def _default_log_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return base / "LadderStock" / "logs" / "supervisor"


# --------------------------------------------------------------------------- #
# 守护进程入口（`ldst __watch-guard`）
# --------------------------------------------------------------------------- #


def _scan_argv_from_env(env: Mapping[str, str]) -> list[str]:
    raw = str(env.get(GUARD_SCAN_ARG_ENV, "")).strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    return [str(part) for part in parsed] if isinstance(parsed, list) else []


def watch_guard_main(env: Mapping[str, str] | None = None) -> int:
    """自有 watch 守护主循环：周期跑一轮离线扫描，暂无样本时按退避重试。

    扫描命令 = 本仓库 `ldst watch start`（与用户手敲的是同一条路径）。
    """
    values = dict(os.environ if env is None else env)
    try:
        tick = float(values.get(TICK_ENV) or DEFAULT_TICK_SEC)
    except ValueError:
        tick = DEFAULT_TICK_SEC
    retries = [
        float(item)
        for item in str(values.get("LDST_WATCH_GUARD_WAIT", "")).split(",")
        if item.strip()
    ]
    scan_argv = _scan_argv_from_env(values)
    if not scan_argv:
        return 2
    stop_path = Path(values[GUARD_STOP_ARG_ENV]) if values.get(GUARD_STOP_ARG_ENV) else None
    while not _stop_requested(stop_path):
        code = subprocess.call(scan_argv, cwd=os.getcwd())  # noqa: S603 — argv 由本进程给定
        if code == 4 and retries:
            _sleep_unless_stopping(retries.pop(0), stop_path)
            continue
        retries = []
        _sleep_unless_stopping(max(tick, 1.0), stop_path)
    return 0


def _stop_requested(path: Path | None) -> bool:
    return path is not None and path.exists()


def _sleep_unless_stopping(seconds: float, stop: Path | None) -> None:
    """睡到点或被要求退出；退出标记在退避等待里也要马上生效。"""
    deadline = time.monotonic() + seconds
    while True:
        if _stop_requested(stop):
            return
        left = deadline - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(left, STOP_POLL_SEC))


__all__ = [
    "ADOPT_ONLY_MODULES",
    "BAR_SEARCH_LOOKBACK_DAYS",
    "DEFAULT_TICK_SEC",
    "GUARD_RETRY_DELAYS",
    "PAGE_AUTOSTART_ENV",
    "PageBoot",
    "SCHEDULER_MODULE",
    "SELF_MODULES",
    "TICK_ENV",
    "WATCH_GUARD_MODULE",
    "WatchScanner",
    "autostart_enabled",
    "watch_guard_main",
]
