"""MCP Streamable HTTP 传输契约（QUOT-232）。

真实 TickHub-CN 的协议形态（`tests/fixtures/mcp_streamable/` 是脱敏后的真响应）：

- `initialize` 回一条 SSE，并在响应头给 `Mcp-Session-Id`；
- 之后每个请求都要带会话头，否则 400 `Missing session ID`；
- 工具结果在 SSE 的 `data:` 帧里，长调用期间每 15s 一条 `: ping` 保活注释；
- 会话过期回 404 `Session not found`，客户端要重新握手并重发一次。

这里的回环桩按上述形态应答，不联外网；旧的回环桩（`FakeTransport`）不走本层的
HTTP/SSE，但 `tools/call` 的拆壳与 `_unwrap_mcp_result` 共用同一个函数（QUOT-328）。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from ladderstock.data.provider_registry import (
    HttpMcpTransport,
    PeerEndpoint,
    SourceUnavailable,
    _sse_frames,
    _unwrap_mcp_result,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_streamable"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _with_id(sse: str, request_id: Any) -> bytes:
    """把 fixture 里的响应 id 换成这次请求的 id（真实 peer 回显请求 id）。"""
    lines: list[str] = []
    for line in sse.splitlines():
        if line.startswith("data: "):
            message = json.loads(line[6:])
            if isinstance(message, dict) and "id" in message:
                message["id"] = request_id
            lines.append("data: " + json.dumps(message, ensure_ascii=False))
        else:
            lines.append(line)
    return ("\n".join(lines) + "\n").encode()


class _State:
    def __init__(self, *, issue_session: bool = True) -> None:
        self.issue_session = issue_session
        self.require_session = issue_session
        self.init_count = 0
        self.active: set[str] = set()
        self.requests: list[dict[str, Any]] = []

    def methods(self) -> list[str]:
        return [str(item["body"].get("method")) for item in self.requests]


class _Handler(BaseHTTPRequestHandler):
    state: _State

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # noqa: D401 - 测试不出访问日志
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.state.requests.append(
            {"body": body, "headers": {k.lower(): v for k, v in self.headers.items()}}
        )
        method = body.get("method")
        if method == "initialize":
            self.state.init_count += 1
            extra: dict[str, str] = {}
            if self.state.issue_session:
                session_id = f"fixture-session-{self.state.init_count}"
                self.state.active.add(session_id)
                extra["Mcp-Session-Id"] = session_id
            self._sse(_with_id(_fixture("initialize.sse"), body.get("id")), extra)
            return
        session_id = self.headers.get("mcp-session-id")
        if self.state.require_session and not session_id:
            self._json(
                400,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Bad Request: Missing session ID"},
                },
            )
            return
        if session_id and session_id not in self.state.active:
            self._json(404, json.loads(_fixture("session_expired.json")))
            return
        if method == "notifications/initialized":
            self._json(202, None)
            return
        if method == "tools/list":
            self._sse(
                _with_id(_fixture("tools_list.sse"), body.get("id")),
                {"Mcp-Session-Id": session_id or ""},
            )
            return
        if method == "tools/call":
            params = body.get("params") or {}
            arguments = params.get("arguments") or {}
            name = params.get("name")
            bad_call = name == "tencent_kline" and not arguments.get("code")
            fixture = "tools_call_error.sse" if bad_call else "tools_call_kline.sse"
            headers = {"Mcp-Session-Id": session_id} if session_id else {}
            self._sse(_with_id(_fixture(fixture), body.get("id")), headers)
            return
        self._json(
            400,
            {
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "error": {"code": -32601, "message": "Method not found"},
            },
        )

    def _sse(self, payload: bytes, headers: dict[str, str]) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache, no-transform")
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, body: dict[str, Any] | None) -> None:
        payload = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)


def _serve(state: _State) -> Iterator[tuple[PeerEndpoint, _State]]:
    handler = type("Handler", (_Handler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    peer = PeerEndpoint(
        key="tickhub",
        product="TickHub-CN",
        mcp_name="tickhub-cn",
        url=f"http://127.0.0.1:{server.server_address[1]}/mcp",
        timeout_sec=5.0,
    )
    try:
        yield peer, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.fixture()
def peer_server() -> Iterator[tuple[PeerEndpoint, _State]]:
    yield from _serve(_State())


@pytest.fixture()
def stateless_server() -> Iterator[tuple[PeerEndpoint, _State]]:
    """不回 `Mcp-Session-Id` 的实现：握手照做，但不带会话头也能继续。"""
    yield from _serve(_State(issue_session=False))


# --------------------------------------------------------------------------- #
# 握手与会话续用
# --------------------------------------------------------------------------- #

def test_probe_initializes_once_and_reuses_session(
    peer_server: tuple[PeerEndpoint, _State],
) -> None:
    peer, state = peer_server
    transport = HttpMcpTransport()

    first = transport.probe(peer, timeout=5)
    second = transport.probe(peer, timeout=5)

    assert first == second == {"peer": "tickhub", "tools": ["tencent_kline", "limit_up_pool"]}
    assert state.init_count == 1, "同一 peer 只该握手一次"
    init = state.requests[0]
    assert init["body"]["method"] == "initialize"
    assert init["body"]["params"]["protocolVersion"] == "2025-06-18"
    assert init["headers"]["accept"] == "application/json, text/event-stream"

    assert state.methods().count("notifications/initialized") == 1
    lists = [item for item in state.requests if item["body"].get("method") == "tools/list"]
    assert len(lists) == 2
    for item in lists:
        assert item["headers"]["mcp-session-id"] == "fixture-session-1"
        assert item["headers"]["mcp-protocol-version"] == "2025-06-18"


def test_stateless_peer_skips_session_header(
    stateless_server: tuple[PeerEndpoint, _State],
) -> None:
    peer, state = stateless_server
    transport = HttpMcpTransport()

    assert transport.probe(peer, timeout=5)["tools"] == ["tencent_kline", "limit_up_pool"]
    assert transport.probe(peer, timeout=5)["tools"] == ["tencent_kline", "limit_up_pool"]
    assert state.init_count == 1
    lists = [item for item in state.requests if item["body"].get("method") == "tools/list"]
    assert all("mcp-session-id" not in item["headers"] for item in lists)


def test_expired_session_rehandshakes_and_retries_once(
    peer_server: tuple[PeerEndpoint, _State],
) -> None:
    peer, state = peer_server
    transport = HttpMcpTransport()
    transport.probe(peer, timeout=5)

    state.active.clear()  # 服务端把会话踢了（真实 peer 回 404 Session not found）

    payload = transport.call(peer, "tencent_kline", {"code": "600000"}, timeout=5)

    assert payload["ok"] is True
    assert state.init_count == 2, "会话过期后应重新握手一次"
    assert state.methods().count("tools/call") == 2, "原请求应被重发一次"


# --------------------------------------------------------------------------- #
# SSE 解析
# --------------------------------------------------------------------------- #

def test_call_parses_sse_frames_and_skips_keepalive(
    peer_server: tuple[PeerEndpoint, _State],
) -> None:
    peer, _state = peer_server
    transport = HttpMcpTransport()

    payload = transport.call(peer, "tencent_kline", {"code": "600000", "limit": 2}, timeout=5)

    assert payload["ok"] is True
    assert payload["meta"]["source"] == "tencent"
    assert payload["meta"]["trace_id"] == "trace-fixture-1"
    assert payload["data"]["code"] == "600000"
    dates = [row["date"] for row in payload["data"]["klines"]]
    assert dates == ["2026-10-28", "2026-10-29", "2026-10-30"]
    assert "structuredContent" not in payload, "structuredContent 的外壳应已拆掉"


def test_tool_error_surfaces_peer_detail(
    peer_server: tuple[PeerEndpoint, _State],
) -> None:
    peer, _state = peer_server
    transport = HttpMcpTransport()

    with pytest.raises(SourceUnavailable) as excinfo:
        transport.call(peer, "tencent_kline", {}, timeout=5)

    message = str(excinfo.value)
    assert "tencent_kline" in message
    assert "validation error" in message and "Field required" in message


def test_structured_content_string_result_is_unwrapped() -> None:
    """真实 Wire 的四个工具回 `envelope.model_dump_json()`，FastMCP 把这串 JSON
    放进 `structuredContent.result`（QUOT-328 实测形状）。

    只认「`result` 是对象」这一种的话，`theme_news` 落库就是一行空壳：
    参数修对了也照样没有叙事，屏上的题材字段还是空的。
    """
    envelope = {"data_as_of": "2026-11-01", "payload": {"topic": "ai", "summary": "情绪回暖"}}
    result = _unwrap_mcp_result(
        {"structuredContent": {"result": json.dumps(envelope, ensure_ascii=False)}}
    )

    assert result == envelope
    assert result["payload"]["summary"] == "情绪回暖"


def test_sse_frames_join_multiline_data_and_ignore_comments() -> None:
    lines = [
        "event: message",
        'data: {"a":',
        "data: 1}",
        "",
        ": ping - 2026-10-30T10:26:45.090612+00:00",
        "",
        'data: {"b":2}',
        "",
    ]
    frames = list(_sse_frames(lines, deadline=time.monotonic() + 5))
    assert frames == ['{"a":\n1}', '{"b":2}']


def test_sse_deadline_is_wall_clock() -> None:
    """保活注释会不断续命连接，超时必须按整体墙钟判，不能被 ping 拖住。"""
    with pytest.raises(httpx.ReadTimeout):
        list(_sse_frames([": ping - t"], deadline=time.monotonic() - 1))
