"""监听暴露面的唯一判据：默认只绑回环，非回环必须显式开关 + Bearer Token。

web（`ldst run`，5950）与 MCP（`ldst mcp http`，5951）是两条独立的监听面，
但策略必须只有一份——QUOT-99 修好了 web，MCP 侧却还在「只告警」（audit R-1），
正是两处各写一半的后果（DEC-008 的教训）。本模块提供：

1. `require_binding_policy()` —— 判据本体：回环放行；非回环必须
   `<前缀>_ALLOW_REMOTE=1` 且 `<前缀>_TOKEN`（≥16 字符），否则抛 `OSError`
   （CLI 按配置错误退出码 3），**绝不静默对外暴露**；
2. `TokenGuardMiddleware` —— 鉴权实现：纯 ASGI 中间件，校验
   `Authorization: Bearer <token>`（可选额外 header），用
   `hmac.compare_digest` 常量时间比较，不相等返回 401。

两条监听面只传各自的 env 前缀与路径前缀，判据与比较逻辑共用这一份。
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

#: 默认只绑回环（DEC-004）；非回环必须显式声明 + 提供鉴权 Token
LOOPBACK_HOSTS: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")

#: 对外监听 Token 的最短长度（低于这个长度等于没有鉴权）
MIN_REMOTE_TOKEN_LEN = 16

#: 401 响应体：不回显任何服务端细节，也不提示 token 长度/前缀
UNAUTHORIZED_BODY = b'{"detail":"unauthorized"}'

#: 纯 ASGI 三元组（scope 放宽为 `Any`，以便同时容纳 Starlette / 裸 ASGI 应用）
ASGIApp = Callable[
    [Any, Callable[[], Awaitable[Any]], Callable[[Any], Awaitable[None]]],
    Awaitable[None],
]


def env_flag(name: str) -> bool:
    """环境变量开关：只有 `1/true/yes/on` 算显式打开（大小写不敏感）。"""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def require_binding_policy(
    host: str, *, allow_env: str, token_env: str, surface: str
) -> str | None:
    """判定监听地址能否放行；返回需要强制的 Token（回环返回 None）。

    - 回环（127.0.0.1 / localhost / ::1）：放行，无鉴权；
    - 非回环：**必须**同时满足
      1. `allow_env` 为真（等价于「我确实要对外监听」）；
      2. `token_env` 至少有 `MIN_REMOTE_TOKEN_LEN` 个字符。

    不满足抛 `OSError`，由调用方按配置错误处理（退出码 3）。
    报错只提变量名与长度要求，**不回显凭据原文**。
    """
    if host in LOOPBACK_HOSTS:
        return None
    if not env_flag(allow_env):
        raise OSError(
            f"拒绝监听非回环地址 {host}：{surface} 默认只绑回环（DEC-004）。"
            f"确需对外暴露请显式设置 {allow_env}=1 并配置 {token_env}"
        )
    token = os.environ.get(token_env, "").strip()
    if len(token) < MIN_REMOTE_TOKEN_LEN:
        raise OSError(
            f"监听非回环地址 {host} 必须配置鉴权：{token_env} 至少 "
            f"{MIN_REMOTE_TOKEN_LEN} 个字符（不回显凭据原文）"
        )
    return token


def bearer_token(headers: Mapping[str, str], *, extra_header: str | None = None) -> str:
    """从请求头取客户端提供的 Token：优先 `Authorization: Bearer`，其次额外 header。

    `headers` 的键必须已小写（ASGI scope 里的 header 名由中间件统一小写后再传进来）。
    """
    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    if extra_header:
        return headers.get(extra_header.lower(), "").strip()
    return ""


def token_matches(supplied: str, expected: str) -> bool:
    """常量时间比较；任一侧为空一律视为不匹配（空 Token 永远不等于空值）。"""
    if not supplied or not expected:
        return False
    return hmac.compare_digest(supplied, expected)


def _headers(scope: Mapping[str, Any]) -> dict[str, str]:
    pairs = scope.get("headers") or []
    return {
        key.decode("latin-1").lower(): value.decode("latin-1") for key, value in pairs
    }


async def _send_json(
    send: Callable[[Mapping[str, Any]], Awaitable[None]], status: int, body: bytes
) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class TokenGuardMiddleware:
    """纯 ASGI 的 Bearer 鉴权中间件（web 与 MCP 共用）。

    - `path_prefix`：只保护该前缀下的路径（web 传 `/api/`，MCP 传 `/mcp`）；
      空字符串表示保护全部 HTTP 请求。
    - `extra_header`：可选兼容头（web 的历史客户端用 `X-Web-Token`）。
    - 非 HTTP 请求（`lifespan` 等）直接透传，不影响启动/关闭握手。
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        path_prefix: str = "",
        extra_header: str | None = None,
    ) -> None:
        self.app = app
        self.token = token
        self.path_prefix = path_prefix
        self.extra_header = extra_header

    async def __call__(
        self,
        scope: Mapping[str, Any],
        receive: Callable[[], Awaitable[Mapping[str, Any]]],
        send: Callable[[Mapping[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http" or not self._guarded(str(scope.get("path") or "")):
            await self.app(scope, receive, send)
            return
        supplied = bearer_token(_headers(scope), extra_header=self.extra_header)
        if not token_matches(supplied, self.token):
            await _send_json(send, 401, UNAUTHORIZED_BODY)
            return
        await self.app(scope, receive, send)

    def _guarded(self, path: str) -> bool:
        return not self.path_prefix or path.startswith(self.path_prefix)


__all__ = [
    "LOOPBACK_HOSTS",
    "MIN_REMOTE_TOKEN_LEN",
    "TokenGuardMiddleware",
    "bearer_token",
    "env_flag",
    "require_binding_policy",
    "token_matches",
]
