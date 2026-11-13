"""同源 `/platform/*` 转发（QUOT-589）。

QUOT-589 之前前端按 `import.meta.env.DEV` 猜看门狗端口：built dist 落在 5951
这类非 5950 端口时跨源直打 prod 看门狗 5952——CORS 挡下后误报「服务离线」，
「重启平台」还会错杀正式服 5950。修法是**同源化**：前端只打本 origin 的
`/platform/*`，由 serving backend 转发到**本实例自己的**看门狗。

- 看门狗端口由 `LDST_WATCHDOG_PORT` 注入（缺省 5952 = prod 5950 的 DEC-063
  裁决，语义不变）；dev 实例（5951）注入 5953，与 prod 隔离。
- 转发是服务端到服务端：`Origin` 统一置成看门狗自己的回环 origin——它必然
  在看门狗白名单里，浏览器侧不再需要任何 CORS 放行；白名单语义不变。
- 只转发看门狗声明的两条面（`health` / `restart`），其余一律 404——转发面
  不做放大器。看门狗不可达回 503（不是 500），前端按探测失败照常判红。
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

#: 环境变量名：本实例看门狗端口（backend 代理据此转发，前端不猜端口）
WATCHDOG_PORT_ENV = "LDST_WATCHDOG_PORT"

#: 缺省 = prod 看门狗 5952（DEC-063 裁决）；dev 实例应注入 5953
DEFAULT_PROXY_WATCHDOG_PORT = 5952

FORWARD_TIMEOUT_SEC = 5.0
#: 看门狗 `/platform/` 下存在的路径；白名单外一律 404
FORWARDABLE_PATHS = frozenset({"health", "restart"})


def watchdog_port(env: dict[str, str] | None = None) -> int:
    """本实例看门狗端口；环境变量缺失 / 非法时回缺省 5952。"""
    values = os.environ if env is None else env
    raw = (values.get(WATCHDOG_PORT_ENV) or "").strip()
    if not raw:
        return DEFAULT_PROXY_WATCHDOG_PORT
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_PROXY_WATCHDOG_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PROXY_WATCHDOG_PORT


def forward_platform(
    method: str,
    path: str,
    *,
    port: int,
    body: bytes | None = None,
    request_fn: Any = None,
) -> Response:
    """把 `/platform/{path}` 转发到本实例看门狗；不可达回 503。

    `request_fn` 供测试注入假 httpx；生产用 `httpx.request`。
    """
    if path not in FORWARDABLE_PATHS:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    url = f"http://127.0.0.1:{port}/platform/{path}"
    headers: dict[str, str] = {}
    if method in {"POST", "OPTIONS"}:
        # 看门狗只认白名单 Origin；服务端转发统一冒充它自己的回环 origin
        headers["Origin"] = f"http://127.0.0.1:{port}"
    if method == "POST":
        headers["Content-Type"] = "application/json"
    request = request_fn if request_fn is not None else httpx.request
    try:
        upstream = request(method, url, headers=headers, content=body, timeout=FORWARD_TIMEOUT_SEC)
    except (httpx.HTTPError, OSError):
        return JSONResponse(
            {"detail": f"看门狗不可达（127.0.0.1:{port}）"}, status_code=503
        )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


def build_platform_router(port: int | None = None) -> APIRouter:
    """同源转发路由：`/platform/{path}` → 本实例看门狗。"""
    resolved = watchdog_port() if port is None else port
    router = APIRouter()

    @router.api_route(
        "/platform/{path:path}",
        methods=["GET", "POST", "OPTIONS", "HEAD"],
        include_in_schema=False,
    )
    async def platform_forward(request: Request, path: str) -> Response:
        body = await request.body() if request.method == "POST" else None
        return forward_platform(request.method, path, port=resolved, body=body)

    return router


__all__ = [
    "DEFAULT_PROXY_WATCHDOG_PORT",
    "FORWARDABLE_PATHS",
    "WATCHDOG_PORT_ENV",
    "build_platform_router",
    "forward_platform",
    "watchdog_port",
]
