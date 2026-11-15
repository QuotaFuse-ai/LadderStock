"""`ldst ai memory-clear`（QUOT-487）：只清本命名空间，三条退出码路径。

不起真引擎：QUOT-611 起是进程内 mem0 SDK，测试把 `MemoryClient` 换成注入假
引擎的同款实例。退出码口径来自 docs/CLI.md §K：成功 0、通道未启用 3、引擎
不可用 9。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from ladderstock.ai import memory
from ladderstock.cli.main import app

runner = CliRunner()


class _Engine:
    def __init__(self, *, all_results: list[dict] | None = None, error: Exception | None = None):
        self.all_results = all_results or []
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def get_all(self, **kwargs: Any) -> dict:
        self.calls.append({"op": "get_all", **kwargs})
        if self.error is not None:
            raise self.error
        return {"results": self.all_results}

    def delete_all(self, **kwargs: Any) -> dict:
        self.calls.append({"op": "delete_all", **kwargs})
        if self.error is not None:
            raise self.error
        return {"message": "All relevant memories deleted"}


@pytest.fixture(autouse=True)
def _channel(monkeypatch):
    """通道配置钉在测试名下（conftest 的隔离 fixture 会先置空，这里覆盖回测试值）。"""
    monkeypatch.setenv("LDST_MEMORY_BASE_URL", "http://mem0.test")
    monkeypatch.setenv("LDST_MEMORY_USER_ID", "cli-clear-user")


def _fake_client(monkeypatch, engine: _Engine) -> None:
    original = memory.MemoryClient

    def factory(settings: memory.MemorySettings, **kwargs: Any) -> memory.MemoryClient:
        return original(settings, engine=engine)

    monkeypatch.setattr(memory, "MemoryClient", factory)


def test_clear_requires_enabled_channel(monkeypatch) -> None:
    """`LDST_MEMORY_BASE_URL` 显式置空 → 3（无事可做，不假装清了）。"""
    monkeypatch.setenv("LDST_MEMORY_BASE_URL", "")
    result = runner.invoke(app, ["ai", "memory-clear", "--yes"])
    assert result.exit_code == 3, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "记忆通道未启用" in result.stderr


def test_clear_success_payload(monkeypatch) -> None:
    """清空命中 → 0 + 结构化载荷；命名空间是本 user_id + ladderstock-assistant。"""
    engine = _Engine(all_results=[{"memory": "m1"}, {"memory": "m2"}])
    _fake_client(monkeypatch, engine)
    result = runner.invoke(app, ["ai", "memory-clear", "--yes", "--output", "json"])
    assert result.exit_code == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    payload = json.loads(result.stdout)
    assert payload["cleared"] is True
    assert payload["removed"] == 2
    assert payload["agent_id"] == memory.AGENT_ID
    assert payload["user_id"] == "cli-clear-user"
    ops = [call["op"] for call in engine.calls]
    assert ops == ["get_all", "delete_all"]
    assert engine.calls[0]["filters"]["user_id"] == "cli-clear-user"
    assert engine.calls[0]["filters"]["agent_id"] == memory.AGENT_ID
    assert engine.calls[1]["user_id"] == "cli-clear-user"
    assert engine.calls[1]["agent_id"] == memory.AGENT_ID


def test_clear_engine_unavailable(monkeypatch) -> None:
    """引擎不可用 → 9，且明确说「未执行清空」。"""
    engine = _Engine(error=RuntimeError("engine down"))
    _fake_client(monkeypatch, engine)
    result = runner.invoke(app, ["ai", "memory-clear", "--yes", "--output", "json"])
    assert result.exit_code == 9, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "未执行清空" in result.stderr


def test_clear_asks_before_destructive_action(monkeypatch) -> None:
    """不带 `--yes` 时先确认；拒绝 → typer Abort（退出 1），不触引擎。"""
    engine = _Engine()
    _fake_client(monkeypatch, engine)
    result = runner.invoke(app, ["ai", "memory-clear"], input="n\n")
    assert result.exit_code == 1
    assert "不可恢复" in result.stdout
    assert engine.calls == []
