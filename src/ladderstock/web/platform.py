"""主服务自带的同源 `/platform/*` 面（QUOT-589 重修）。

用户裁决（硬约束）：全机只留 5950 正式 / 5951 测试两个端口——独立看门狗不再
监听 5952/5953，健康与重启改由**主服务自己**提供，前端 `platformBaseUrl()`
恒同源打本 origin。

- `GET /platform/health`：实时探测本实例 `/api/health`（回环 2s），载荷形状与
  旧独立看门狗逐字段兼容（`status` / `uptime_sec` / `version` / `db_ok` /
  `checked_at` / `api` / `watchdog`），前端 `usePlatformHealth` 契约不变。
- `POST /platform/restart`：分离式拉起本实例的重启脚本（部署侧 kill + 重新
  launch，如 `restart-dev.ps1` / `restart-api.ps1`），先回 `{ok:true}` 再让
  脚本干活。

安全边界（沿用 QUOT-443 硬约束，一条不减）：

1. 只服务回环客户端；写方法校验来源（QUOT-178 双证据口径：`Origin` 出现即只
   认同源，无 `Origin` 则须 `X-LDST-Client: 1`），绝不回 `*`。
2. 30s 冷却：重复重启回 429。
3. 分离式 spawn：Windows `DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP`，输出落
   临时文件不落管道——重启脚本会拉起常驻子进程，管道永远等不到 EOF
   （实测 91s 教训），本请求不被重启过程拖死。
4. 脚本路径只来自启动参数或环境变量，必须是绝对路径的现存文件；找不到如实回
   409，不静默成功。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ladderstock import __version__
from ladderstock.cli.watchdog import RESTART_THROTTLE_SEC
from ladderstock.web.control import is_loopback_host

NO_STORE = "no-store"

#: 平台面写请求的非浏览器证据头（QUOT-178 双证据，与控制面同口径）
CLIENT_HEADER = "X-LDST-Client"
CLIENT_HEADER_VALUE = "1"

#: 重启脚本来源（优先级从高到低）：显式参数 > `LDST_RESTART_SCRIPT` > 按实例
#: 端口找部署侧脚本（5950 → restart-api.ps1，其余 → restart-dev.ps1）。
RESTART_SCRIPT_ENV = "LDST_RESTART_SCRIPT"


def default_restart_script(api_port: int) -> str:
    """按实例端口取部署侧重启脚本路径（5950 → restart-api.ps1，其余 → restart-dev.ps1）。

    只做端口→脚本命名映射，不做存在性检查：文件缺失由重启时的
    `_script_issue` 如实回 409，映射本身在任何机器上都成立。
    """
    name = "restart-api.ps1" if api_port == 5950 else "restart-dev.ps1"
    return str(Path.home() / "AppData" / "Local" / "LadderStock" / name)


def resolve_restart_script(
    api_port: int,
    env: dict[str, str] | None = None,
    *,
    default_script: Callable[[int], str] = default_restart_script,
) -> str:
    values = os.environ if env is None else env
    explicit = (values.get(RESTART_SCRIPT_ENV) or "").strip()
    if explicit:
        return explicit
    return default_script(api_port)


def detached_spawn(
    argv: list[str],
    *,
    popen: Callable[..., Any] = subprocess.Popen,
    log_dir: Path | None = None,
) -> int:
    """分离式拉起：本调用不等它退出，输出落文件不落管道。

    Windows 用 `DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP`；POSIX 用
    `start_new_session`。日志写到临时文件（`log_dir` 给定则落该目录）。
    """
    handle, path = tempfile.mkstemp(prefix="ldst-restart-", suffix=".log", dir=log_dir)
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    try:
        with os.fdopen(handle, "wb") as sink:
            process = popen(argv, stdout=sink, stderr=subprocess.STDOUT, shell=False, **kwargs)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return int(process.pid)


def platform_health_payload(api_base: str) -> dict[str, Any]:
    """健康载荷：顶层描述主服务；`watchdog` 是兼容占位（无端口，无 uptime）。

    探测失败时 `uptime_sec` / `db_ok` 为 `null`——不能拿主服务自报的时长冒充
    平台时长（与旧看门狗同口径）。
    """
    url = f"{api_base.rstrip('/')}/api/health"
    try:
        response = httpx.get(url, timeout=2.0)
        api = response.json() if response.status_code == 200 else None
        if not isinstance(api, dict):
            api = None
    except (httpx.HTTPError, ValueError, OSError):
        api = None
    if api is None:
        status = "offline"
    elif api.get("db_ok") is False or api.get("status") not in (None, "ok"):
        status = "degraded"
    else:
        status = "ok"
    return {
        "status": status,
        "uptime_sec": api.get("uptime_sec") if api is not None else None,
        "version": (api.get("version") if api is not None else None) or __version__,
        "db_ok": api.get("db_ok") if api is not None else None,
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "api": api,
        "watchdog": {"version": __version__, "portless": True},
    }


def _write_guard(request: Request) -> None:
    """回环客户端 + 写来源证据（QUOT-178 双证据口径）。"""
    client = request.client.host if request.client else ""
    if not is_loopback_host(client):
        raise HTTPException(status_code=403, detail="Forbidden")
    origin = request.headers.get("origin")
    if origin is not None:
        if not _same_origin(request, origin):
            raise HTTPException(status_code=403, detail="Forbidden")
        return
    if (request.headers.get(CLIENT_HEADER) or "").strip() != CLIENT_HEADER_VALUE:
        raise HTTPException(status_code=403, detail="Forbidden")


def _same_origin(request: Request, origin: str) -> bool:
    """`Origin` 必须是本机页面（http(s) + 回环主机 + 本实例端口）。"""
    from urllib.parse import urlsplit

    text = origin.strip()
    if not text or text.lower() == "null":
        return False
    try:
        parsed = urlsplit(text)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not is_loopback_host(host):
        return False
    server = request.scope.get("server") or ()
    padded = tuple(server) + (None, None)
    server_port_raw = padded[1]
    server_port = server_port_raw if isinstance(server_port_raw, int) else None
    if port is not None and server_port:
        return port == server_port
    return True


def request_bound_port(request: Request) -> int:
    """uvicorn 真实绑定端口（QUOT-589 第二轮）：本实例探活/重启一律指向自己。

    `request.scope["server"]` 是 ASGI 服务器填的 `(host, port)`——dev 5951 上
    就是 5951，prod 5950 上就是 5950，任意 `--port` 天然正确。异常时回落
    `WEB_PORT`（仅发生在非 HTTP 上下文，正常服务路径不触达）。
    """
    from ladderstock.web.server import WEB_PORT

    server = request.scope.get("server") or ()
    raw = tuple(server)[1] if len(tuple(server)) > 1 else None
    return raw if isinstance(raw, int) else WEB_PORT


def build_platform_router(
    api_port: int | Callable[[Request], int],
    *,
    restart_script: str = "",
    default_script: Callable[[int], str] = default_restart_script,
    probe_health: Callable[[str], dict[str, Any]] = platform_health_payload,
    spawn: Callable[[list[str]], int] = detached_spawn,
) -> APIRouter:
    """同源 `/platform/*` 路由；`probe_health` / `spawn` 供测试注入。

    `api_port` 是静态端口号或按请求推导端口的 callable（生产传
    `request_bound_port`：从 `request.scope["server"][1]` 取真实绑定端口，
    测试实例绝不错指 prod）。
    """
    router = APIRouter(prefix="/platform")

    script_override = restart_script
    throttle = {"last": 0.0}
    lock = threading.Lock()

    def _port_for(request: Request | None = None) -> int:
        if callable(api_port):
            if request is None:
                raise RuntimeError("端口按请求推导时必须有 Request")
            return api_port(request)
        return api_port

    @router.get("/health")
    def platform_health(request: Request) -> JSONResponse:
        payload = probe_health(f"http://127.0.0.1:{_port_for(request)}")
        if not isinstance(payload, dict):
            # 契约兜底：探活实现失败也不得把响应变成 null——离线也要有完整形状
            payload = {
                "status": "offline",
                "uptime_sec": None,
                "version": __version__,
                "db_ok": None,
                "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "api": None,
                "watchdog": {"version": __version__, "portless": True},
            }
        return JSONResponse(payload, headers=_no_store())

    @router.post("/restart")
    def platform_restart(request: Request) -> JSONResponse:
        _write_guard(request)
        now = time.monotonic()
        with lock:
            if throttle["last"] and now - throttle["last"] < RESTART_THROTTLE_SEC:
                remaining = int(RESTART_THROTTLE_SEC - (now - throttle["last"])) + 1
                return JSONResponse(
                    {"ok": False, "reason": f"重启过于频繁，请 {remaining}s 后再试"},
                    status_code=429,
                    headers=_no_store(),
                )
            throttle["last"] = now
        script = script_override or resolve_restart_script(
            _port_for(request), default_script=default_script
        )
        issue = _script_issue(script)
        if issue is not None:
            return JSONResponse(
                {"ok": False, "reason": f"重启脚本不可用（{issue}）"},
                status_code=409,
                headers=_no_store(),
            )
        argv = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script]
        try:
            detached_pid = spawn(argv)
        except OSError as exc:
            return JSONResponse(
                {"ok": False, "reason": f"重启脚本拉起失败：{exc}"},
                status_code=503,
                headers=_no_store(),
            )
        return JSONResponse(
            {"ok": True, "via": "script", "script": script, "launcher_pid": detached_pid},
            headers=_no_store(),
        )

    return router


def _no_store() -> dict[str, str]:
    return {"Cache-Control": NO_STORE}


def _script_issue(path: str) -> str | None:
    if not path:
        return "未配置重启脚本（LDST_RESTART_SCRIPT / 部署脚本缺失）"
    candidate = Path(path)
    if not candidate.is_absolute():
        return "必须是绝对路径"
    if not candidate.is_file():
        return "文件不存在"
    return None
