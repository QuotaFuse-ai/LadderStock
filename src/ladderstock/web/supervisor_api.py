"""supervisor 自愈状态面（QUOT-491 / 482-C）。

两个路由：

- `GET /api/supervisor/state`：每模块 `health: ok|restarting|needs_human` 与
  `restarts_5min`（任务书对外契约）；另附 `self_heal: {attempts, max, status}`
  与 `state`（484 前端面板契约，stopped 的静息态在 health 里折叠为 ok）。
- `POST /api/supervisor/state/{module}/reset`：人工解除 `needs_human`
  （清重拉窗口、健康回 ok）。写面闸门**复用雷达面同一份实现**
  （`privacy_write_guard`：回环 + Host 白名单 + 写方法来源证据），不新增鉴权。

引擎组装（`build_selfheal_engine`）：最小账本（482-A 未落地，见
`supervisor/__init__.py` 的边界说明）+ 命令表重拉（482-B 未落地）+ 事件日志 /
Windows Toast 通知。落盘全部收在 `<data_root>/supervisor/` 下。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ladderstock.supervisor.ledger import Ledger
from ladderstock.supervisor.notify import build_default_notifier
from ladderstock.supervisor.page_boot import PageBoot
from ladderstock.supervisor.restart import CommandTableRestarter
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


async def _supervisor_write_guard(request: Request) -> None:
    await privacy_write_guard(
        request,
        denied=SUPERVISOR_DENIED,
        subject="自愈状态面",
        write_denied=SUPERVISOR_WRITE_DENIED,
    )


def build_selfheal_engine(root: str | Path, *, panel_url: str) -> SelfHealEngine:
    """按 `<data_root>/supervisor/` 组装生产引擎：账本 + 命令表 + 通知。"""
    supervisor_dir = Path(root) / "supervisor"
    ledger = Ledger(supervisor_dir / "ledger.json", window_seconds=WINDOW_SECONDS)
    restarter = CommandTableRestarter.from_file(supervisor_dir / "restart-commands.json")
    notifier = build_default_notifier(supervisor_dir / "notifications.log", panel_url=panel_url)
    return SelfHealEngine(
        ledger,
        restarter,
        notifier,
        log_dir=supervisor_dir / "logs",
        log_max_lines=DEFAULT_LOG_MAX_LINES,
    )


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


def build_supervisor_router(engine: SelfHealEngine) -> APIRouter:
    router = APIRouter(prefix=SUPERVISOR_PREFIX, tags=["supervisor"])

    @router.get("/state")
    def state() -> dict[str, Any]:
        return engine.snapshot()

    @router.post("/state/{module}/reset", dependencies=[Depends(_supervisor_write_guard)])
    def reset(module: str) -> dict[str, Any]:
        result = engine.reset(module)
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


def mount_supervisor(app: FastAPI, engine: SelfHealEngine) -> None:
    """挂路由 + 引擎随应用启停（startup 起 / shutdown 停，等待打断巡检等待）。"""
    app.include_router(build_supervisor_router(engine))
    app.router.on_startup.append(engine.start)
    app.router.on_shutdown.append(lambda: engine.stop(timeout=2.0))


def mount_page_boot(app: FastAPI, boot: PageBoot) -> None:
    """QUOT-522：路由 + startup 钩子（打开网页 = 拉起自有子进程，peer 只认领）。

    引擎自身的启停另有 `mount_supervisor`；这里只管页面自启与 own children 的停。
    """
    app.include_router(build_page_boot_router(boot))
    app.router.on_startup.append(boot.ensure)
    app.router.on_shutdown.append(boot.stop_children)


__all__ = [
    "SUPERVISOR_PREFIX",
    "build_page_boot",
    "build_page_boot_router",
    "build_selfheal_engine",
    "build_supervisor_router",
    "mount_page_boot",
    "mount_supervisor",
]
