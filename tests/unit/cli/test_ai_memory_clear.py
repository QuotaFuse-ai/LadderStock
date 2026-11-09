"""`ldst ai memory-clear`（QUOT-487）：只清本命名空间，三条退出码路径。

不起真 mem0：把 `MemoryClient` 换成包了 MockTransport 的同款实例。
退出码口径来自 docs/CLI.md §K：成功 0、通道未启用 3、mem0 不可达 9。
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from typer.testing import CliRunner

from ladderstock.ai import memory
from ladderstock.cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _channel(monkeypatch):
    """通道配置钉在测试名下（conftest 的隔离 fixture 会先置空，这里覆盖回测试值）。"""
    monkeypatch.setenv("LDST_MEMORY_BASE_URL", "http://mem0.test")
    monkeypatch.setenv("LDST_MEMORY_USER_ID", "cli-clear-user")


def _fake_client(monkeypatch, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    original = memory.MemoryClient

    def factory(
        base_url: str, timeout_sec: float = memory.DEFAULT_TIMEOUT_SEC
    ) -> memory.MemoryClient:
        return original(
            base_url, timeout_sec, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(memory, "MemoryClient", factory)


def test_clear_requires_enabled_channel(monkeypatch) -> None:
    """`LDST_MEMORY_BASE_URL` 显式置空 → 3（无事可做，不假装清了）。"""
    monkeypatch.setenv("LDST_MEMORY_BASE_URL", "")
    result = runner.invoke(app, ["ai", "memory-clear", "--yes"])
    assert result.exit_code == 3, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "记忆通道未启用" in result.stderr


def test_clear_success_payload(monkeypatch) -> None:
    """DELETE 命中 → 0 + 结构化载荷；命名空间是本 user_id + ladderstock-assistant。"""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"deleted_ids": ["m1", "m2"]})

    _fake_client(monkeypatch, handler)
    result = runner.invoke(app, ["ai", "memory-clear", "--yes", "--output", "json"])
    assert result.exit_code == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    payload = json.loads(result.stdout)
    assert payload["cleared"] is True
    assert payload["removed"] == 2
    assert payload["agent_id"] == memory.AGENT_ID
    assert payload["user_id"] == "cli-clear-user"
    assert seen["method"] == "DELETE"
    assert seen["params"]["user_id"] == "cli-clear-user"
    assert seen["params"]["agent_id"] == memory.AGENT_ID


def test_clear_mem0_unreachable(monkeypatch) -> None:
    """mem0 连不上 → 9，且明确说「未执行清空」。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _fake_client(monkeypatch, handler)
    result = runner.invoke(app, ["ai", "memory-clear", "--yes", "--output", "json"])
    assert result.exit_code == 9, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "未执行清空" in result.stderr


def test_clear_asks_before_destructive_action(monkeypatch) -> None:
    """不带 `--yes` 时先确认；拒绝 → typer Abort（退出 1），不拨 mem0。"""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"deleted_ids": []})

    _fake_client(monkeypatch, handler)
    result = runner.invoke(app, ["ai", "memory-clear"], input="n\n")
    assert result.exit_code == 1
    assert "不可恢复" in result.stdout
    assert not called
