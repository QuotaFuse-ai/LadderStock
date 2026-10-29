"""监听暴露面判据（audit R-1）：web / MCP 共用一份策略 + 常量时间比较 + 401 中间件。"""

from __future__ import annotations

from typing import Any

import anyio
import pytest

from ladderstock.cli import mcp as mcp_cli
from ladderstock.core.listener import (
    MIN_REMOTE_TOKEN_LEN,
    TokenGuardMiddleware,
    bearer_token,
    require_binding_policy,
    token_matches,
)
from ladderstock.web import server as web

SURFACES = (
    ("web", web.REMOTE_ALLOW_ENV, web.REMOTE_TOKEN_ENV),
    ("mcp", mcp_cli.REMOTE_ALLOW_ENV, mcp_cli.REMOTE_TOKEN_ENV),
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for _, allow_env, token_env in SURFACES:
        monkeypatch.delenv(allow_env, raising=False)
        monkeypatch.delenv(token_env, raising=False)


def _policy(label: str, allow_env: str, token_env: str) -> Any:
    if label == "web":
        return web.require_binding_policy
    return lambda host: require_binding_policy(
        host, allow_env=allow_env, token_env=token_env, surface="MCP 服务"
    )


@pytest.mark.parametrize("label,allow_env,token_env", SURFACES)
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_is_allowed_without_token(label, allow_env, token_env, host) -> None:
    assert _policy(label, allow_env, token_env)(host) is None


@pytest.mark.parametrize("label,allow_env,token_env", SURFACES)
def test_non_loopback_without_flag_is_refused(label, allow_env, token_env) -> None:
    with pytest.raises(OSError) as excinfo:
        _policy(label, allow_env, token_env)("0.0.0.0")
    assert allow_env in str(excinfo.value)


@pytest.mark.parametrize("label,allow_env,token_env", SURFACES)
def test_non_loopback_with_flag_but_no_token_is_refused(
    monkeypatch: pytest.MonkeyPatch, label, allow_env, token_env
) -> None:
    monkeypatch.setenv(allow_env, "1")
    with pytest.raises(OSError) as excinfo:
        _policy(label, allow_env, token_env)("0.0.0.0")
    assert token_env in str(excinfo.value)


@pytest.mark.parametrize("label,allow_env,token_env", SURFACES)
def test_non_loopback_with_short_token_is_refused(
    monkeypatch: pytest.MonkeyPatch, label, allow_env, token_env
) -> None:
    monkeypatch.setenv(allow_env, "1")
    monkeypatch.setenv(token_env, "x" * (MIN_REMOTE_TOKEN_LEN - 1))
    with pytest.raises(OSError):
        _policy(label, allow_env, token_env)("0.0.0.0")


@pytest.mark.parametrize("label,allow_env,token_env", SURFACES)
def test_non_loopback_with_flag_and_token_is_allowed(
    monkeypatch: pytest.MonkeyPatch, label, allow_env, token_env
) -> None:
    token = "t" * MIN_REMOTE_TOKEN_LEN
    monkeypatch.setenv(allow_env, "1")
    monkeypatch.setenv(token_env, token)
    assert _policy(label, allow_env, token_env)("0.0.0.0") == token


def test_error_messages_never_echo_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "s" * (MIN_REMOTE_TOKEN_LEN - 1)
    monkeypatch.setenv(web.REMOTE_ALLOW_ENV, "1")
    monkeypatch.setenv(web.REMOTE_TOKEN_ENV, secret)
    with pytest.raises(OSError) as excinfo:
        web.require_binding_policy("0.0.0.0")
    assert secret not in str(excinfo.value)


def test_token_matches_is_constant_time_and_rejects_empty() -> None:
    assert token_matches("abc", "abc") is True
    assert token_matches("abc", "abd") is False
    assert token_matches("", "") is False
    assert token_matches("abc", "") is False
    assert token_matches("", "abc") is False


def test_bearer_token_parsing() -> None:
    assert bearer_token({"authorization": "Bearer abc"}) == "abc"
    assert bearer_token({"authorization": "bearer  abc "}) == "abc"
    assert bearer_token({}, extra_header="x-web-token") == ""
    assert bearer_token({"x-web-token": "abc"}, extra_header="X-Web-Token") == "abc"


def _run_guard(token: str, headers: list[tuple[bytes, bytes]], path: str = "/mcp") -> list[Any]:
    """跑一次中间件：返回下游是否被调用 + 响应帧。"""
    called: list[bool] = []
    sent: list[Any] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        called.append(True)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def receive() -> Any:  # pragma: no cover - 中间件不读 body
        return {"type": "http.request", "body": b""}

    async def send(message: Any) -> None:
        sent.append(message)

    guard = TokenGuardMiddleware(app, token=token, path_prefix="/mcp")
    scope = {"type": "http", "path": path, "headers": headers}
    anyio.run(guard, scope, receive, send)
    return called, sent


def test_guard_rejects_missing_or_wrong_token() -> None:
    called, sent = _run_guard("good-token", [])
    assert called == []
    assert sent[0]["status"] == 401

    called, sent = _run_guard("good-token", [(b"authorization", b"Bearer wrong")])
    assert called == []
    assert sent[0]["status"] == 401


def test_guard_allows_correct_token_and_ignores_other_paths() -> None:
    called, sent = _run_guard("good-token", [(b"authorization", b"Bearer good-token")])
    assert called == [True]
    assert sent[0]["status"] == 200

    called, _ = _run_guard("good-token", [], path="/healthz")
    assert called == [True]


def test_mcp_log_config_levels_are_valid_for_dictconfig() -> None:
    """回归：小写 `info` 会让 uvicorn 启动直接抛 ValueError（服务起不来）。"""
    import logging

    config = mcp_cli._log_config()
    for name, entry in config["loggers"].items():
        logging._checkLevel(entry["level"])  # 非法级别会抛 ValueError
        assert name.startswith("uvicorn")
    assert config["handlers"]["default"]["stream"] == "ext://sys.stderr"


def test_mcp_http_refuses_non_loopback_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """验收反例①：非回环 + 无 token → 拒绝启动，退出码 3（不会真正 bind）。"""
    from typer.testing import CliRunner

    from ladderstock.cli.main import app

    runner = CliRunner()
    refused = runner.invoke(app, ["mcp", "http", "--host", "0.0.0.0", "--port", "5998"])
    assert refused.exit_code == 3
    assert mcp_cli.REMOTE_ALLOW_ENV in refused.output

    # 验收反例②：有开关、缺 token → 仍然拒绝
    monkeypatch.setenv(mcp_cli.REMOTE_ALLOW_ENV, "1")
    missing = runner.invoke(app, ["mcp", "http", "--host", "0.0.0.0", "--port", "5998"])
    assert missing.exit_code == 3
    assert mcp_cli.REMOTE_TOKEN_ENV in missing.output


