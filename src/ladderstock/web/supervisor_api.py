"""supervisor 面（QUOT-491 / 482-A / 482-C / 523）。

三类路由：

- `GET /api/supervisor/state`：每模块 `health: ok|restarting|needs_human` 与
  `restarts_5min`（任务书对外契约）；另附 `self_heal: {attempts, max, status}`、
  `state`（484 前端面板契约）与 482-A 归属视角（`ownership` / `evidence` /
  `listening`）。
- `POST /api/supervisor/state/{module}/reset`：人工解除 `needs_human`。
- `POST /api/supervisor/start|restart|stop`（QUOT-523 单入口）：按注册表顺序
  全起 / 单模块重起 / 反向全停；**只动账本能证明是我们拉起的进程**（归属判定
  见 `SupervisorEngine`，外部进程一律不碰）。写面闸门**复用雷达面同一份实现**
  （`privacy_write_guard`：回环 + Host 白名单 + 写方法来源证据），不新增鉴权。

引擎组装（`build_supervisor_engines`）：一张账本同时供自愈巡检（482-C）与
顺序启停（482-A）使用——两边看到的是同一份 pid / state，不会各记各的。
`own_processes()`（`LDST_SUPERVISOR_OWN_PROCESSES=1`）决定看板启动 / 退出时
是否连子进程一起起停；默认关闭（附加模式）：看板只是观测 + 按需操作，
绝不因为开关看板而碰生产上的 5950/8000/9100/9000 进程。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ladderstock.supervisor.adapters import MODULE_ORDER
from ladderstock.supervisor.engine import SupervisorEngine, SupervisorError
from ladderstock.supervisor.ledger import Ledger
from ladderstock.supervisor.module_adapters import build_default_adapters
from ladderstock.supervisor.notify import build_default_notifier
from ladderstock.supervisor.page_boot import PageBoot
from ladderstock.supervisor.proctree import default_process_ops
from ladderstock.supervisor.restart import AdapterRestarter
from ladderstock.supervisor.selfheal import (
    DEFAULT_LOG_MAX_LINES,
    WINDOW_SECONDS,
    SelfHealEngine,
)
from ladderstock.web.radar import privacy_write_guard

if TYPE_CHECKING:
    from fastapi import FastAPI

SUPERVISOR_PREFIX = "/api/supervisor"

SUPERVISOR_DENIED = "自愈状态面只对本机回环开放（127.0.0.1 / localhost / ::1）"
SUPERVISOR_WRITE_DENIED = "自愈写面只接受同源来源（Origin 或 X-LDST-Client: 1）"

#: 看板启动 / 退出时是否连子进程一起起停（QUOT-523 单入口）。默认 0 = 附加模式：
#: 只观测、只响应用户显式操作，看板开关本身不碰任何进程。
OWN_PROCESSES_ENV = "LDST_SUPERVISOR_OWN_PROCESSES"

#: 全停是不可逆批量动作，请求体必须带 `confirm: true`（或给出 modules 只停单个）
STOP_CONFIRMATION_FIELD = "confirm"


def own_processes(env: Mapping[str, str] | None = None) -> bool:
    """单入口模式开关：`LDST_SUPERVISOR_OWN_PROCESSES` 非空且不为 0。"""
    values = os.environ if env is None else env
    return (values.get(OWN_PROCESSES_ENV) or "").strip() not in ("", "0")


async def _supervisor_write_guard(request: Request) -> None:
    await privacy_write_guard(
        request,
        denied=SUPERVISOR_DENIED,
        subject="自愈状态面",
        write_denied=SUPERVISOR_WRITE_DENIED,
    )


@dataclass
class SupervisorEngines:
    """看板侧的 supervisor 装配：自愈巡检 + 顺序启停共用一张账本。"""

    heal: SelfHealEngine
    processes: SupervisorEngine
    ledger: Ledger


def build_supervisor_engines(
    root: str | Path,
    *,
    panel_url: str,
    env: Mapping[str, str] | None = None,
) -> SupervisorEngines:
    """按 `<data_root>/supervisor/` 组装生产引擎：一张账本 + 自愈 + 顺序启停。

    重拉通道 = `AdapterRestarter`（bundle 自带命令表，QUOT-526），不再读
    `restart-commands.json`。单入口模式（`LDST_SUPERVISOR_OWN_PROCESSES=1`）下
    自愈还负责**冷启动**：托管 `MODULE_ORDER` 全表，账本无 pid 的模块按命令表
    立刻拉起。默认附加模式只认领既有实例、绝不自动拉起（peer adopt-only）。
    """
    supervisor_dir = Path(root) / "supervisor"
    ledger = Ledger(supervisor_dir / "ledger.json", window_seconds=WINDOW_SECONDS)
    notifier = build_default_notifier(supervisor_dir / "notifications.log", panel_url=panel_url)
    processes = SupervisorEngine(
        adapters=build_default_adapters(env=env, log_dir=supervisor_dir / "logs"),
        ledger=ledger,
        procs=default_process_ops(),
    )
    cold_start = own_processes(env)
    heal = SelfHealEngine(
        ledger,
        AdapterRestarter(processes, spawn=cold_start),
        notifier,
        log_dir=supervisor_dir / "logs",
        log_max_lines=DEFAULT_LOG_MAX_LINES,
        modules=MODULE_ORDER if cold_start else None,
        cold_start=cold_start,
    )
    return SupervisorEngines(heal=heal, processes=processes, ledger=ledger)


def build_page_boot(root: str | Path, *, ledger: Ledger | None = None) -> PageBoot:
    """按 `<data_root>` 组装页面自启（QUOT-522）：同一份账本 + 真适配器识别 peer。

    适配器构造不碰宿主（只在 ensure()/identify() 时才查询），Windows 之外的开发机
    也能安全构建；任何一路失败都在 `ensure()` 内部折叠进报告。
    """
    from ladderstock.cli.paths import project_root
    from ladderstock.supervisor.module_adapters import build_default_adapters

    data_root = Path(root)
    supervisor_dir = data_root / "supervisor"
    resolved = ledger or Ledger(supervisor_dir / "ledger.json", window_seconds=WINDOW_SECONDS)
    try:
        adapters: dict[str, Any] = dict(build_default_adapters(log_dir=supervisor_dir / "logs"))
    except Exception:  # noqa: BLE001 - 识别器组装失败时 peer 一律记 not_listening
        adapters = {}
    return PageBoot(
        data_root=data_root,
        ledger=resolved,
        adapters=adapters,
        run_cwd=project_root(),
        log_dir=supervisor_dir / "logs",
    )


def build_process_engine(
    root: str | Path,
    *,
    heal: SelfHealEngine,
    env: Mapping[str, str] | None = None,
) -> SupervisorEngine:
    """只组装顺序启停引擎，复用既有自愈账本（`supervisor_engine=` 注入时用）。"""
    supervisor_dir = Path(root) / "supervisor"
    return SupervisorEngine(
        adapters=build_default_adapters(env=env, log_dir=supervisor_dir / "logs"),
        ledger=heal.ledger,
        procs=default_process_ops(),
    )


def build_selfheal_engine(root: str | Path, *, panel_url: str) -> SelfHealEngine:
    """只组装自愈引擎（482-C 旧入口；测试与只读观测仍可用）。"""
    return build_supervisor_engines(root, panel_url=panel_url).heal


def _stop_result(processes: Any, modules: list[str]) -> dict[str, Any]:
    """按前端契约 `{confirm: true, modules: [...]}` 停模块；空列表 = 反向全停。"""
    stopped: list[str] = []
    skipped: list[str] = []
    if not modules:
        outcome = processes.stop_all()
        for name, item in (outcome.get("modules") or {}).items():
            (stopped if item.get("ok") else skipped).append(name)
        return {"ok": bool(outcome.get("ok")), "stopped": stopped, "skipped": skipped}
    for name in modules:
        try:
            processes.stop_module(name)
        except SupervisorError:
            skipped.append(name)
        else:
            stopped.append(name)
    return {"ok": not skipped, "stopped": stopped, "skipped": skipped}


def build_stop_router(processes: Any) -> APIRouter:
    """`/api/supervisor/{start,restart,stop}`（QUOT-523）：全部走写面闸门。"""
    router = APIRouter(prefix=SUPERVISOR_PREFIX, tags=["supervisor"])

    @router.post("/start", dependencies=[Depends(_supervisor_write_guard)])
    def start(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        modules = [str(name) for name in (payload or {}).get("modules") or []]
        if not modules:
            outcome = processes.start_all()
            return {"ok": bool(outcome.get("ok")), "modules": outcome.get("modules") or {}}
        results: dict[str, Any] = {}
        for name in modules:
            try:
                processes.start_module(name)
            except SupervisorError as exc:
                results[name] = {"ok": False, "error": str(exc)}
            else:
                results[name] = {"ok": True, "error": None}
        return {"ok": all(item["ok"] for item in results.values()), "modules": results}

    @router.post("/restart", dependencies=[Depends(_supervisor_write_guard)])
    def restart(payload: dict[str, Any]) -> dict[str, Any]:
        modules = [str(name) for name in payload.get("modules") or []]
        if not modules:
            raise HTTPException(status_code=422, detail="restart 需要 modules: [模块名]")
        results: dict[str, Any] = {}
        for name in modules:
            try:
                processes.restart_module(name)
            except SupervisorError as exc:
                results[name] = {"ok": False, "error": str(exc)}
            else:
                results[name] = {"ok": True, "error": None}
        return {"ok": all(item["ok"] for item in results.values()), "modules": results}

    @router.post("/stop", dependencies=[Depends(_supervisor_write_guard)])
    def stop(payload: dict[str, Any]) -> dict[str, Any]:
        # 全停是不可逆动作（七个模块一起下），必须显式确认；单模块停不要求
        modules = [str(name) for name in payload.get("modules") or []]
        if not modules and payload.get(STOP_CONFIRMATION_FIELD) is not True:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"全停需要 {STOP_CONFIRMATION_FIELD}: true"
                    "（或给出 modules: [模块名] 只停指定模块）"
                ),
            )
        return _stop_result(processes, modules)

    return router


def build_supervisor_router(heal: Any) -> APIRouter:
    router = APIRouter(prefix=SUPERVISOR_PREFIX, tags=["supervisor"])

    @router.get("/state")
    def state() -> dict[str, Any]:
        return heal.snapshot()

    @router.post("/state/{module}/reset", dependencies=[Depends(_supervisor_write_guard)])
    def reset(module: str) -> dict[str, Any]:
        result = heal.reset(module)
        if result is None:
            raise HTTPException(status_code=404, detail=f"账本中无模块：{module}")
        return {"ok": True, **result}

    return router


def build_page_boot_router(boot: PageBoot | None) -> APIRouter:
    """页面自启面（QUOT-522）：只读报告 + 回环写方法幂等再跑一次。"""
    router = APIRouter(prefix=SUPERVISOR_PREFIX, tags=["supervisor"])

    @router.get("/page-boot")
    def page_boot_state() -> dict[str, Any]:
        last = boot.last_report() if boot is not None else None
        return {
            "ok": True,
            "configured": boot is not None,
            "enabled": bool(boot.enabled) if boot is not None else False,
            "last_report": last,
        }

    @router.post("/page-boot", dependencies=[Depends(_supervisor_write_guard)])
    def page_boot_ensure() -> dict[str, Any]:
        if boot is None:
            raise HTTPException(status_code=503, detail="页面自启未接线")
        return boot.ensure(trigger="api-request")

    return router


def mount_supervisor(
    app: FastAPI,
    heal: Any,
    *,
    processes: Any | None = None,
    own_processes_on_lifecycle: bool = False,
) -> None:
    """挂路由 + 引擎随应用启停。

    `processes` 给出时注册启停路由；`own_processes_on_lifecycle` 为真时启动
    全起、退出只停自有子进程（单入口模式，QUOT-527：adopted peer 保留）；
    否则退出只停自愈巡检线程，不碰任何子进程（附加模式——绝不因为关看板而
    停生产进程）。
    """
    app.include_router(build_supervisor_router(heal))
    if processes is not None:
        app.include_router(build_stop_router(processes))

    async def startup() -> None:
        if processes is not None and own_processes_on_lifecycle:
            await _in_thread(processes.start_all)
        await _in_thread(heal.start)

    async def shutdown() -> None:
        if processes is not None and own_processes_on_lifecycle:
            # QUOT-527：应用退出 = 只停本控制面 spawn 的子进程；adopted peer 保留
            await _in_thread(processes.stop_owned)
        await _in_thread(lambda: heal.stop(timeout=2.0))

    app.router.on_startup.append(startup)
    app.router.on_shutdown.append(shutdown)


async def _in_thread(fn: Any) -> None:
    """起停是阻塞调用（等端口 / 等进程），挪出事件循环，别卡住启动/关闭握手。"""
    await asyncio.to_thread(fn)


def mount_page_boot(app: FastAPI, boot: PageBoot) -> None:
    """QUOT-522：路由 + startup 钩子（打开网页 = 拉起自有子进程，peer 只认领）。

    引擎自身的启停另有 `mount_supervisor`；这里只管页面自启与 own children 的停。
    """
    app.include_router(build_page_boot_router(boot))
    app.router.on_startup.append(boot.ensure)
    app.router.on_shutdown.append(boot.stop_children)


__all__ = [
    "OWN_PROCESSES_ENV",
    "STOP_CONFIRMATION_FIELD",
    "SUPERVISOR_DENIED",
    "SUPERVISOR_PREFIX",
    "SUPERVISOR_WRITE_DENIED",
    "SupervisorEngines",
    "build_page_boot",
    "build_page_boot_router",
    "build_selfheal_engine",
    "build_process_engine",
    "build_stop_router",
    "build_supervisor_engines",
    "build_supervisor_router",
    "mount_page_boot",
    "mount_supervisor",
    "own_processes",
]
