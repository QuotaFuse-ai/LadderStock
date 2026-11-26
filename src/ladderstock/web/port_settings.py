"""看板端口设置 API（DEC-092）：关于页读 / 改 / 还原看板端口。

三个端点（`/api/settings/port`）：

- `GET`：默认端口、用户偏好、当前访问端口、保留端口清单（模块 + MCP 面）——
  前端用同一份数据渲染输入框与校验提示，不自己猜规则；
- `PUT {"board": N}`：先校验再落盘。校验四关：端口范围（1024–65535）、不与
  本环境模块端口冲突、不与 MCP 面冲突、**当前空闲才允许保存**（被占用直接
  409，带占用者 PID / 进程名）。保存只写 `config/ports.yaml` 的 `board_user`，
  重启应用后生效；
- `DELETE`：清除偏好，回到默认端口 + 自动避让。

红线：只写配置、不碰运行中的服务（换端口必须重启，本面绝不热替换监听）；
只回环；写入幂等。
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ladderstock.supervisor.port_allocator import (
    clear_board_user_pref,
    load_board_user_pref,
    reserved_app_ports,
    save_board_user_pref,
)

#: 端口合法下限（低于 1024 是系统保留段，普通用户应用不该占）
MIN_PORT = 1024
MAX_PORT = 65535


class BoardPortBody(BaseModel):
    board: int


def _default_reserved_ports() -> set[int]:
    """当前环境的保留端口：模块端口 + MCP 面端口 + 双环境面板口。

    与看板避让器共用同一份计算（`port_allocator.reserved_app_ports`），
    避免「保存时挡、避让时放行」两套口径（DEC-092 对撞事故就是这么来的）。
    """
    return reserved_app_ports()


def _port_busy_owner(port: int) -> dict[str, Any]:
    """端口被谁占着（尽力而为）：psutil 可用给 PID + 进程名，否则只报占用。"""
    try:
        import psutil

        for conn in psutil.net_connections(kind="tcp"):
            if (
                conn.status == psutil.CONN_LISTEN
                and conn.laddr
                and int(conn.laddr.port) == port
            ):
                pid = int(conn.pid) if conn.pid else None
                name = None
                if pid:
                    try:
                        name = psutil.Process(pid).name()
                    except psutil.Error:
                        name = None
                return {"busy": True, "pid": pid, "process": name}
    except Exception:  # noqa: BLE001 - psutil 缺席只退化描述，不挡保存
        pass
    return {"busy": True, "pid": None, "process": None}


def request_bound_port_local(request: Request) -> int | None:
    """从 ASGI scope 取真实监听口（与 platform.request_bound_port 同口径）。

    差异只在取不到时：这里回 None（设置页显示「未知」）而不是回落 WEB_PORT。
    """
    server = request.scope.get("server") or ()
    raw = tuple(server)[1] if len(tuple(server)) > 1 else None
    return raw if isinstance(raw, int) else None


def build_port_settings_router(
    config_dir: Path,
    *,
    default_board: int = 5950,
    reserved_ports: set[int] | None = None,
    port_free: Callable[[int], bool] | None = None,
    request_port: Callable[[], int | None] | None = None,
) -> APIRouter:
    """构建端口设置路由。

    `port_free` / `request_port` 可注入（测试用）；生产前者是回环连接探测，
    后者从请求 scope 取当前实际监听端口（取不到就 null，前端自己知道在哪个口）。
    """
    router = APIRouter(prefix="/api/settings", tags=["settings"])

    def _probe(port: int) -> bool:
        if port_free is not None:
            return port_free(port)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex(("127.0.0.1", port)) != 0

    reserved = reserved_ports if reserved_ports is not None else _default_reserved_ports()

    @router.get("/port")
    def get_port_settings(request: Request) -> dict[str, Any]:
        user = load_board_user_pref(config_dir)
        current = (
            request_bound_port_local(request) if request_port is None else request_port()
        )
        return {
            "default_board": default_board,
            "user_board": user,
            "current_board": int(current) if current else None,
            "reserved_ports": sorted(reserved),
            "min_port": MIN_PORT,
            "max_port": MAX_PORT,
            # 用户语义："改完要重启应用才生效"
            "takes_effect": "restart",
        }

    @router.put("/port")
    def put_port_settings(body: BoardPortBody) -> dict[str, Any]:
        port = body.board
        if not MIN_PORT <= port <= MAX_PORT:
            raise HTTPException(
                status_code=422,
                detail=f"端口必须在 {MIN_PORT}–{MAX_PORT} 之间（收到 {port}）",
            )
        if port == default_board:
            # 设回默认 = 撤销偏好：语义与 DELETE 一致，避免「偏好==默认」的怪状态
            clear_board_user_pref(config_dir)
            return {
                "ok": True,
                "board": None,
                "action": "cleared",
                "detail": f"已回到默认端口 {default_board}（含自动避让）；重启应用后生效",
            }
        if port in reserved:
            listing = "、".join(str(p) for p in sorted(reserved))
            raise HTTPException(
                status_code=422,
                detail=f"端口 {port} 已保留给内部模块使用，请换一个（保留清单：{listing}）",
            )
        if not _probe(port):
            owner = _port_busy_owner(port)
            who = f"PID {owner['pid']}"
            if owner.get("process"):
                who = f"PID {owner['pid']} · {owner['process']}"
            raise HTTPException(
                status_code=409,
                detail=f"端口 {port} 正被其它程序占用（{who}），换一个或先关掉它再保存",
            )
        if port == default_board:
            # 设回默认 = 撤销偏好：语义与 DELETE 一致，避免「偏好==默认」的怪状态
            clear_board_user_pref(config_dir)
            return {
                "ok": True,
                "board": None,
                "action": "cleared",
                "detail": f"已回到默认端口 {default_board}（含自动避让）；重启应用后生效",
            }
        save_board_user_pref(config_dir, port)
        return {
            "ok": True,
            "board": port,
            "action": "saved",
            "detail": f"已保存，重启应用后从 http://127.0.0.1:{port}/ 打开",
        }

    @router.delete("/port")
    def delete_port_settings() -> dict[str, Any]:
        clear_board_user_pref(config_dir)
        return {
            "ok": True,
            "board": None,
            "action": "cleared",
            "detail": f"已清除偏好，重启应用后回到默认端口 {default_board}（含自动避让）",
        }

    return router


__all__ = ["build_port_settings_router"]
