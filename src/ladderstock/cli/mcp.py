"""`ldst mcp` 命令体：stdio 与 Streamable HTTP 两种传输。

约定（docs/CLI.md §F）：

- `mcp stdio` 的 **stdout 只输出 MCP 协议报文**：启动/退出/异常日志一律写 stderr，
  否则客户端解析协议会直接失败；
- `mcp http` 默认只监听回环，端口被占用直接报错退出码 3（DEC-004，不自动换端口）；
- 工具清单与实现见 `ladderstock/mcp/server.py`，`mcp tools list` 仍由 main.py 输出。
"""

from __future__ import annotations

import sys
from typing import Annotated, Any

import typer

from ladderstock.cli.legacy_ports import warn_legacy_port
from ladderstock.cli.output import OutputOption, error
from ladderstock.cli.run import is_addr_in_use, port_free
from ladderstock.core.listener import LOOPBACK_HOSTS, require_binding_policy
from ladderstock.errors import EXIT_CONFIG, EXIT_OK

# 与 cli/main.py 的 DEFAULT_MCP_PORT 一致（DEC-004：端口固定，不漂移）
DEFAULT_MCP_PORT = 5951
DEFAULT_HOST = "127.0.0.1"

#: 非回环绑定的显式开关与 Token（与 `LDST_WEB_*` 同一判据，见 core/listener.py）
REMOTE_ALLOW_ENV = "LDST_MCP_ALLOW_REMOTE"
REMOTE_TOKEN_ENV = "LDST_MCP_TOKEN"


def _log(message: str) -> None:
    sys.stderr.write(f"ldst: {message}\n")
    sys.stderr.flush()


def _log_config() -> dict[str, Any]:
    """uvicorn 日志全部写 stderr；stdout 留给协议。

    级别必须是 `logging` 认得的**大写**名（`dictConfig` 里小写 `info` 会抛
    `ValueError: Unable to configure logger 'uvicorn'`，服务直接起不来）。
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"default": {"format": "ldst(mcp): %(levelname)s %(message)s"}},
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stderr",
            }
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["default"], "level": "WARNING", "propagate": False},
        },
    }


def mcp_stdio(output: OutputOption = None) -> None:
    """MCP stdio 服务：阻塞到客户端关闭管道（Ctrl+C 亦可）。"""
    from ladderstock.mcp.server import serve_stdio

    _log("MCP stdio 已启动（stdout 仅协议报文，日志走 stderr）")
    try:
        serve_stdio()
    except KeyboardInterrupt:  # pragma: no cover - 交互终端
        pass
    finally:
        _log("MCP stdio 已退出")
    raise typer.Exit(EXIT_OK)


def mcp_http(
    port: Annotated[int, typer.Option(help="MCP 端口")] = DEFAULT_MCP_PORT,
    host: Annotated[str, typer.Option(help="监听地址")] = DEFAULT_HOST,
    output: OutputOption = None,
) -> None:
    """MCP Streamable HTTP（`POST /mcp`），默认回环监听。

    非回环绑定必须显式 `LDST_MCP_ALLOW_REMOTE=1` + `LDST_MCP_TOKEN`（≥16 字符），
    否则拒绝启动（退出码 3）；放行后 `/mcp` 的每个请求都要求
    `Authorization: Bearer <token>`（`hmac.compare_digest` 比较）。判据与 web
    共用 `core/listener.py`，不再各写一半（audit R-1）。
    """
    # 显式敲旧端口时补一句迁移说明，然后照旧执行（DEC-004：不接管、不回退）
    warn_legacy_port(port)
    try:
        token = require_binding_policy(
            host,
            allow_env=REMOTE_ALLOW_ENV,
            token_env=REMOTE_TOKEN_ENV,
            surface="MCP 服务",
        )
    except OSError as exc:
        # 非回环缺开关 / 缺 Token：按配置错误退出，绝不静默对外暴露
        error(str(exc))
        raise typer.Exit(EXIT_CONFIG) from None

    check_host = host if host in LOOPBACK_HOSTS else DEFAULT_HOST
    if not port_free(port, check_host):
        error(f"端口 {port} 已被占用；请用 --port 指定其他端口（DEC-004）")
        raise typer.Exit(EXIT_CONFIG)

    import uvicorn

    from ladderstock.mcp.server import create_http_app

    app = create_http_app(host=host, token=token)
    suffix = "；Bearer 鉴权已启用" if token else ""
    _log(f"MCP 已启动 http://{host}:{port}/mcp（只读工具{suffix}；Ctrl+C 退出）")
    try:
        uvicorn.run(app, host=host, port=port, log_config=_log_config(), access_log=False)
    except KeyboardInterrupt:  # pragma: no cover - 由 uvicorn 先处理
        pass
    except OSError as exc:
        # 策略已在上面单独判过，这里只会是真正的 bind 失败；占用与其它错误文案分开
        if is_addr_in_use(exc):
            error(
                f"端口 {port} 已被占用：{exc}；"
                "请用 --port 指定其他端口（不会自动换端口，见 DEC-004）"
            )
        else:
            error(f"端口 {port} 无法监听：{exc}；请用 --port 指定其他端口（DEC-004）")
        raise typer.Exit(EXIT_CONFIG) from None
    finally:
        _log("MCP 已停止，端口已释放")
    raise typer.Exit(EXIT_OK)


def register(mcp_app: typer.Typer) -> None:
    """把 `mcp stdio` / `mcp http` 挂到 main.py 已建好的 Typer 组上。"""
    mcp_app.command("stdio", help="MCP stdio 服务（stdout 只输出协议报文）")(mcp_stdio)
    mcp_app.command("http", help="MCP Streamable HTTP，默认回环监听")(mcp_http)


__all__ = [
    "DEFAULT_MCP_PORT",
    "REMOTE_ALLOW_ENV",
    "REMOTE_TOKEN_ENV",
    "mcp_http",
    "mcp_stdio",
    "register",
]
