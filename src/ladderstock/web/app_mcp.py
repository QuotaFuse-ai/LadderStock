"""应用内 MCP 面（DEC-092）：`__desktop` / `__service` 启动时随主进程拉起。

此前 MCP 是一条独立命令（`ldst mcp http`），装机形态下没人帮用户跑它——
桌面应用里它是应用的一个面：主进程起一条 daemon 线程跑 Streamable HTTP，
生命周期与看板一致，不再需要额外进程或手工启动。

三条纪律：

1. **只回环**：与 `ldst mcp http` 同一判据（`core/listener.py`），默认
   127.0.0.1；不对外暴露。
2. **端口占用不挡启动**：端口被占（含用户手动起的旧 `ldst mcp http`）时
   记警告、返回 `port_busy`，看板照常可用；外部工具继续用既有实例。
3. **环境隔离**：test 环境的看板占 5951，MCP 面让位到 15951（与模块端口
   +10000 平移同一套口径），避免自撞。
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from typing import Any

_LOG = logging.getLogger(__name__)

#: prod 默认端口：与 `cli/mcp.py` 的 DEFAULT_MCP_PORT 一致（DEC-004 不漂移）
MCP_DEFAULT_PORT = 5951
#: test 环境：看板面板本身就占 5951（QUOT-589 双端口裁决），MCP 面平移 +10000
MCP_TEST_PORT = 15951


def app_mcp_enabled(env: dict[str, str] | None = None) -> bool:
    """`LDST_APP_MCP=1` 才随应用起（`ldst run` 开发态默认不带上）。

    布尔口径与 `modules/monitor.enabled()` 一致：1/true/yes 非空即开。
    """
    source = os.environ if env is None else env
    raw = (source.get("LDST_APP_MCP") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def choose_mcp_port(env_name: str | None = None) -> int:
    """按环境选 MCP 面端口：prod=5951 / test=15951。

    与 supervisor/modules 的两套端口表同口径：test 环境任何一条监听面
    都不得落在 5950/5951（那是看板 prod/test 的面板口）。
    """
    from ladderstock.supervisor.modules import current_env

    name = (env_name or current_env()).strip().lower()
    return MCP_TEST_PORT if name == "test" else MCP_DEFAULT_PORT


def _port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) != 0


_FACE: dict[str, Any] = {"started": False, "port": None, "thread": None}
_LOCK = threading.Lock()


def start_mcp_face(
    host: str = "127.0.0.1",
    port: int | None = None,
    *,
    app: Any | None = None,
    server_factory: Any | None = None,
) -> dict[str, Any]:
    """在 daemon 线程里起 MCP HTTP 面；幂等（重复调用返回既有结论）。

    `app` / `server_factory` 可注入测试假件；缺省构建真实
    `ladderstock.mcp.server` 应用与 uvicorn server。返回
    `{"started", "port", "detail"}`：`started=False` 时 detail 给人话原因
    （端口被占等），调用方决定怎么提示。
    """
    with _LOCK:
        if _FACE["started"]:
            return {"started": True, "port": _FACE["port"], "detail": "already_running"}

        from ladderstock.core.listener import LOOPBACK_HOSTS, require_binding_policy

        try:
            # 回环绑定不需要开关/Token；非回环沿用 web 同一把闸（带 MCP 自己的 env 名）
            require_binding_policy(
                host,
                allow_env="LDST_MCP_ALLOW_REMOTE",
                token_env="LDST_MCP_TOKEN",
                surface="MCP 面",
            )
        except OSError as exc:
            return {"started": False, "port": None, "detail": f"绑定策略拒绝：{exc}"}

        chosen = int(port) if port else choose_mcp_port()

        def _port_free_probe(candidate: int) -> bool:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe_host = host if host in LOOPBACK_HOSTS else "127.0.0.1"
                return sock.connect_ex((probe_host, candidate)) != 0

        if not _port_free_probe(chosen):
            return {
                "started": False,
                "port": chosen,
                "detail": (
                    f"端口 {chosen} 已被占用（可能是手动起的 ldst mcp http）；"
                    "沿用既有实例，应用内 MCP 面未启动"
                ),
            }

        if app is None:
            from ladderstock.mcp.server import create_http_app

            app = create_http_app(host=host, token=None)

        def _default_server() -> Any:
            import uvicorn

            config = uvicorn.Config(
                app, host=host, port=chosen, log_level="warning", access_log=False
            )
            return uvicorn.Server(config)

        server = server_factory() if server_factory is not None else _default_server()

        def _serve() -> None:
            try:
                server.run()
            except Exception:  # noqa: BLE001 - 面线程死了不能带走主进程
                _LOG.exception("mcp_face_thread_crashed")

        thread = threading.Thread(target=_serve, name="ldst-mcp-face", daemon=True)
        thread.start()
        _FACE.update({"started": True, "port": chosen, "thread": thread})
        return {"started": True, "port": chosen, "detail": None}


__all__ = [
    "MCP_DEFAULT_PORT",
    "MCP_TEST_PORT",
    "app_mcp_enabled",
    "choose_mcp_port",
    "start_mcp_face",
]
