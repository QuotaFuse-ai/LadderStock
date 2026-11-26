"""应用内 MCP 面（DEC-092）：开关、环境端口、占用不挡启动。"""

from __future__ import annotations

import socket
from threading import Event

from ladderstock.web.app_mcp import (
    MCP_DEFAULT_PORT,
    MCP_TEST_PORT,
    app_mcp_enabled,
    choose_mcp_port,
    start_mcp_face,
)


def test_enabled_gate(monkeypatch) -> None:
    monkeypatch.delenv("LDST_APP_MCP", raising=False)
    assert app_mcp_enabled() is False
    monkeypatch.setenv("LDST_APP_MCP", "1")
    assert app_mcp_enabled() is True
    monkeypatch.setenv("LDST_APP_MCP", "0")
    assert app_mcp_enabled() is False


def test_choose_port_by_env() -> None:
    assert choose_mcp_port("prod") == MCP_DEFAULT_PORT == 5951
    assert choose_mcp_port("test") == MCP_TEST_PORT == 15951
    # 缺省 = 当前环境（LDST_ENV 未设时 prod）
    assert choose_mcp_port(None) in {MCP_DEFAULT_PORT, MCP_TEST_PORT}


def test_busy_port_does_not_block_and_errors_informative() -> None:
    """端口被占 → started=False + 人话原因，不抛异常（看板照常可用）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        busy_port = holder.getsockname()[1]
        result = start_mcp_face(port=busy_port)
    assert result["started"] is False
    assert str(busy_port) in str(result["detail"])


def test_start_with_fake_app_starts_thread() -> None:
    started = Event()

    class _FakeServer:
        def run(self) -> None:
            started.set()

    import ladderstock.web.app_mcp as app_mcp

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # 注入假 server 工厂：不真起 uvicorn，只验证线程被拉起 + 幂等
    result = start_mcp_face(port=port, server_factory=_FakeServer)
    assert result["started"] is True
    assert started.wait(3)
    # 幂等：再来一次返回既有结论，不重复起线程
    again = start_mcp_face(port=port)
    assert again["started"] is True
    assert again["detail"] == "already_running"
    # 单测进程内复位单例，避免影响其它用例
    app_mcp._FACE.update({"started": False, "port": None, "thread": None})
