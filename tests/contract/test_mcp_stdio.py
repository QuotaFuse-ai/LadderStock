"""MCP stdio 契约：**stdout 只有协议报文**，工具清单带 `LadderEnvelope` 输出 schema。

这是 `docs/CLI.md` §F 里「`ldst mcp stdio` 的 stdout 只输出 MCP 协议报文」的
可执行版本。测试真的起一个子进程，按 JSON-RPC 走一遍
`initialize → notifications/initialized → tools/list`，然后逐行校验 stdout：

- 每一行都必须能被 `json.loads` 解析成带 `jsonrpc: "2.0"` 的对象；
  出现一行日志（`ldst: ...`、traceback、进度提示）就算违约；
- 日志必须落在 stderr（`ldst:` 前缀），且不能污染协议流；
- 工具清单与 `mcp/tools.py` 的登记一致（12 个），每个都声明输出 schema，
  字段与本项目 `LadderEnvelope` 对齐；
- 交易日志 / 持仓等隐私字段不得出现在工具清单里。

全程离线：stdio 服务只读本地缓存，不发网络请求。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_VERSION = "2025-06-18"
HANDSHAKE_TIMEOUT = 30.0

ENVELOPE_KEYS = {
    "domain",
    "topic",
    "event_id",
    "data_as_of",
    "payload",
    "source",
    "source_detail",
    "fetched_at",
    "freshness",
    "confidence",
    "source_status",
}

DOCUMENTED_TOOLS = {
    "ladder_get_watch",
    "ladder_get_sentiment_series",
    "ladder_get_limit_ladder",
    "ladder_get_theme_rotation",
    "ladder_get_dragon_tiger",
    "ladder_get_leader",
    "ladder_get_sources_health",
    "ladder_start_research",
    "ladder_get_sentiment",
    "ladder_get_validation",
    "ladder_get_review",
    # R1-1（QUOT-190）新增：读个股研究六段进度
    "research_status",
    "candidate_pool",
    # R5（QUOT-143）新增：申万一级行业五维画像
    "industry_snapshot",
}

#: 以证券代码（而非 date）定位快照的工具，见 tests/contract/test_mcp_tools.py 同名片段
CODE_SCOPED_TOOLS = {"research_status"}

#: 以行业名定位快照的工具（R5）：入参没有 date，理由同 `tests/contract/test_mcp_tools.py`
INDUSTRY_SCOPED_TOOLS = {"industry_snapshot"}

PRIVATE_WORDS = ("journal", "position", "watchlist", "交易日志", "持仓", "自选")


class StdioSession:
    """对 `ldst mcp stdio` 子进程的最小 JSON-RPC 客户端。"""

    def __init__(self, env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "ladderstock", "mcp", "stdio"],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._lines: queue.Queue[str | None] = queue.Queue()
        self.raw_stdout: list[str] = []
        self.raw_stderr: list[str] = []
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._pump_stderr, daemon=True)
        self._stderr_reader.start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.raw_stdout.append(line)
            self._lines.put(line)
        self._lines.put(None)

    def _pump_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.raw_stderr.append(line)

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def recv(self, timeout: float = HANDSHAKE_TIMEOUT) -> dict[str, Any]:
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:  # pragma: no cover - 失败路径只在协议卡死时触发
            raise AssertionError(
                f"{timeout:.0f}s 内没有收到响应；stderr:\n{''.join(self.raw_stderr)}"
            ) from None
        assert line is not None, f"stdout 提前关闭；stderr:\n{''.join(self.raw_stderr)}"
        return json.loads(line)  # 非法 JSON 直接让测试失败：stdout 被污染了

    def close(self) -> int:
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        try:
            code = self.proc.wait(timeout=HANDSHAKE_TIMEOUT)
        except subprocess.TimeoutExpired:  # pragma: no cover - 只在服务卡死时触发
            self.proc.kill()
            code = self.proc.wait(timeout=10)
        self._reader.join(timeout=5)
        self._stderr_reader.join(timeout=5)
        return code


@pytest.fixture()
def session(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env = dict(os.environ)
    env["LDST_DATA_DIR"] = str(tmp_path)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.pop("LDST_LLM_API_KEY", None)
    live = StdioSession(env)
    try:
        yield live
    finally:
        if live.proc.poll() is None:
            live.proc.kill()
            live.proc.wait(timeout=10)


def _handshake(session: StdioSession) -> dict[str, Any]:
    session.send(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        }
    )
    response = session.recv()
    assert response.get("id") == 1
    return response["result"]


def test_stdio_handshake_and_tool_list(session: StdioSession) -> None:
    result = _handshake(session)

    assert result["serverInfo"]["name"] == "ladderstock"
    assert result["protocolVersion"]
    assert "tools" in result["capabilities"]

    session.send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    session.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = session.recv()["result"]["tools"]

    assert {tool["name"] for tool in tools} == DOCUMENTED_TOOLS
    # 14 = 11 个 P0 + R1-1 `research_status` + R7 `candidate_pool` + R5 `industry_snapshot`；
    # 数量与清单一致才算没有重复注册
    assert len(tools) == len(DOCUMENTED_TOOLS) == 14

    code = session.close()
    assert code == 0


def test_stdout_only_carries_jsonrpc_messages(session: StdioSession) -> None:
    _handshake(session)
    session.send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    session.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    session.recv()
    session.close()

    assert session.raw_stdout, "stdout 完全没有输出？"
    for line in session.raw_stdout:
        text = line.strip()
        assert text, "stdout 出现了空行"
        message = json.loads(text)
        assert message.get("jsonrpc") == "2.0", f"stdout 混入了非协议内容：{text!r}"
        assert "method" in message or "result" in message or "error" in message

    # 日志必须走 stderr，且确实是本项目的日志前缀
    stderr = "".join(session.raw_stderr)
    assert "MCP stdio 已启动" in stderr
    assert "stdout 仅协议报文" in stderr
    assert "ldst:" in stderr
    assert "ldst:" not in "".join(session.raw_stdout)


def test_tool_list_declares_envelope_output_schema(session: StdioSession) -> None:
    _handshake(session)
    session.send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    session.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = {tool["name"]: tool for tool in session.recv()["result"]["tools"]}
    session.close()

    for name, tool in tools.items():
        schema = tool.get("outputSchema")
        assert schema is not None, f"{name} 没有声明 outputSchema"
        assert set(schema["properties"]) == ENVELOPE_KEYS, name
        assert schema["properties"]["source_status"]["enum"] == ["ok", "degraded", "partial"]
        assert schema["properties"]["freshness"]["enum"] == ["realtime", "cached", "archive"]
        assert schema["properties"]["confidence"]["enum"] == ["high", "medium", "low"]

        # meta 工具（sources health）没有 date 参数；以代码定位进度的工具（R1-1
        # research_status）required 是 ["code"]、date 可选；其余都必须要求 date
        if name == "ladder_get_sources_health":
            continue
        if name in CODE_SCOPED_TOOLS:
            assert tool["inputSchema"]["required"] == ["code"], name
            continue
        if name in INDUSTRY_SCOPED_TOOLS:
            assert tool["inputSchema"]["required"] == ["industry"], name
            continue
        assert tool["inputSchema"]["required"] == ["date"], name


def test_tool_list_has_no_privacy_leak(session: StdioSession) -> None:
    _handshake(session)
    session.send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    session.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    blob = json.dumps(session.recv()["result"], ensure_ascii=False).lower()
    session.close()

    for word in PRIVATE_WORDS:
        assert word.lower() not in blob, f"MCP 工具清单暴露了隐私关键字：{word}"


def test_unknown_tool_call_returns_jsonrpc_error(session: StdioSession) -> None:
    """协议层错误也要走 stdout 的 JSON-RPC error，而不是打印一段日志。"""
    _handshake(session)
    session.send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    session.send(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "ladder_no_such_tool", "arguments": {"date": "2026-10-22"}},
        }
    )
    message = session.recv()
    session.close()

    assert message.get("id") == 3
    # 未登记工具由 SDK 或项目分发层拒绝：要么 JSON-RPC error，要么 result.isError
    assert "error" in message or message["result"].get("isError") is True
