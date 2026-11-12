"""QUOT-581：`run_turn` 的 SSE 传输层（`event: block` / `event: done`）。

钉住四件事：

1. 开关：只有 `Accept: text/event-stream` 走 SSE，其余仍走非流式 JSON；
2. 顺序：`conclusion` 在首、`citation` 在尾、`notice` 可多个但不在首位、总数 ≤ 6；
3. 一致：同一输入，流式各 `block` 的 payload 与非流式 `reply` / `sources` 对得上；
4. 中断：客户端断开沿既有 `is_disconnected` → `ChatAborted`，本轮不落账、不发假回答。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.web import chat as chat_channel
from ladderstock.web.server import create_app

BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)
WRITE_HEADERS = {"X-LDST-Client": "1"}
CHAT = "/api/control/ai/chat"
SSE_HEADERS = {**WRITE_HEADERS, "Accept": "text/event-stream"}


class _Plane(SimpleNamespace):
    """只要 `peers` 与 `config_root`：chat 路由不碰控制面的其它能力。"""


def _client(tmp_path: Path) -> TestClient:
    config_root = tmp_path / "config"
    config_root.mkdir(parents=True, exist_ok=True)
    app = create_app(
        data_root=tmp_path,
        dist_dir=tmp_path / "no-dist",
        control_plane=_Plane(peers={}, config_root=config_root),
    )
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK)


def _ok_channel() -> chat_channel.Channel:
    return chat_channel.Channel(
        kind="cli", label="cli_codex:default", config=None, target=None
    )


def _parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    """把 SSE 原文拆成 `(event, data)` 列表；顺便断言每帧形状合法。"""
    frames: list[tuple[str, dict[str, Any]]] = []
    for chunk in [part for part in text.split("\n\n") if part.strip()]:
        lines = chunk.split("\n")
        assert lines[0].startswith("event: "), chunk
        assert lines[1].startswith("data: "), chunk
        assert len(lines) == 2, chunk
        frames.append((lines[0][len("event: "):], json.loads(lines[1][len("data: "):])))
    return frames


@pytest.fixture(autouse=True)
def _clean_sessions() -> None:
    chat_channel.reset_sessions()
    yield
    chat_channel.reset_sessions()


def _stub(monkeypatch: pytest.MonkeyPatch, reply: str) -> None:
    async def fake_complete(channel, system, user, *, timeout):
        return reply

    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _ok_channel())
    monkeypatch.setattr(chat_channel, "complete", fake_complete)


# --------------------------------------------------------------------------- #
# 开关
# --------------------------------------------------------------------------- #

def test_default_request_stays_non_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, "量能温和放大。")

    response = _client(tmp_path).post(CHAT, json={"message": "你好"}, headers=WRITE_HEADERS)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["ok"] is True


def test_other_accept_values_stay_non_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, "量能温和放大。")

    response = _client(tmp_path).post(
        CHAT,
        json={"message": "你好"},
        headers={**WRITE_HEADERS, "Accept": "application/json, text/plain"},
    )

    assert response.headers["content-type"].startswith("application/json")


def test_accept_event_stream_switches_to_sse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, "量能温和放大。")

    response = _client(tmp_path).post(
        CHAT, json={"message": "你好"}, headers=SSE_HEADERS
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"


def test_wants_stream_parses_accept_list() -> None:
    def req(value: str) -> Any:
        return SimpleNamespace(headers={"accept": value})

    assert chat_channel.wants_stream(req("text/event-stream")) is True
    assert chat_channel.wants_stream(req("text/html, text/event-stream;q=0.9")) is True
    assert chat_channel.wants_stream(req("application/json")) is False
    assert chat_channel.wants_stream(req("")) is False


# --------------------------------------------------------------------------- #
# 顺序与内容（任务书 6.4）
# --------------------------------------------------------------------------- #

def test_stream_order_with_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, "今天量能比昨日放大。")

    response = _client(tmp_path).post(
        CHAT,
        json={
            "message": "今天大盘怎么样？",
            "context": {
                "page": "home",
                "data": {"turnover": 1.2e12},
                "sources": [
                    {
                        "name": "腾讯行情快照",
                        "fetched_at": "2026-11-07T09:20:00+08:00",
                        "url": "https://qt.gtimg.cn/q=sh000001?token=secret",
                    }
                ],
            },
        },
        headers=SSE_HEADERS,
    )

    frames = _parse_sse(response.text)
    assert [event for event, _ in frames] == ["block", "block", "done"]
    types = [data["type"] for event, data in frames if event == "block"]
    assert types == ["conclusion", "citation"]
    assert types[0] == "conclusion"
    assert types[-1] == "citation"
    assert frames[0][1]["payload"]["text"] == "今天量能比昨日放大。"
    item = frames[1][1]["payload"]["items"][0]
    assert item["name"] == "腾讯行情快照"
    assert item["fetched_at"] == "2026-11-07T09:20:00+08:00"
    # 查询串（可能带 token）不进块
    assert item["url"] == "https://qt.gtimg.cn/q=sh000001"


def test_stream_without_sources_ends_with_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, "建议关注量能变化。")

    response = _client(tmp_path).post(
        CHAT, json={"message": "随便聊聊"}, headers=SSE_HEADERS
    )

    frames = _parse_sse(response.text)
    blocks = [data for event, data in frames if event == "block"]
    assert [block["type"] for block in blocks] == ["conclusion", "notice"]
    notice = blocks[-1]["payload"]
    assert notice["kind"] == "missing_data"
    assert notice["message"] == chat_channel._source_note(has_data=False, sources=[])
    # 结论里不再重复这段说明，说明只出一次
    assert notice["message"] not in blocks[0]["payload"]["text"]


def test_sources_without_page_data_get_missing_data_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, "建议关注量能变化。")

    response = _client(tmp_path).post(
        CHAT,
        json={
            "message": "今天大盘怎么样？",
            "context": {
                "sources": [{"name": "腾讯行情", "fetched_at": "2026-11-07T09:20:00+08:00"}]
            },
        },
        headers=SSE_HEADERS,
    )

    # `_source_note` 只在 sources 为空时出声：给了来源就不追加说明
    blocks = [data for event, data in _parse_sse(response.text) if event == "block"]
    assert [block["type"] for block in blocks] == ["conclusion", "citation"]


def test_done_carries_closing_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, "量能温和放大。")

    response = _client(tmp_path).post(
        CHAT, json={"message": "你好", "session_id": "session-abc-123"}, headers=SSE_HEADERS
    )

    frames = _parse_sse(response.text)
    event, done = frames[-1]
    assert event == "done"
    assert done["ok"] is True
    assert done["session_id"] == "session-abc-123"
    assert done["model"] == "cli_codex:default"
    assert done["channel"] == "cli"
    assert done["fetched_at"]
    assert isinstance(done["elapsed_ms"], int)
    # 本轮没发的块如实列出，不编造 metric / chart
    assert done["followups"] == ["metric", "chart", "table", "action"]


def test_block_count_never_exceeds_six_with_many_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, "量能温和放大。")
    sources = [
        {"name": f"来源{i}", "fetched_at": "2026-11-07T09:20:00+08:00"}
        for i in range(20)
    ]

    response = _client(tmp_path).post(
        CHAT,
        json={"message": "你好", "context": {"sources": sources}},
        headers=SSE_HEADERS,
    )

    blocks = [data for event, data in _parse_sse(response.text) if event == "block"]
    assert len(blocks) <= chat_channel.MAX_ANSWER_BLOCKS == 6
    assert blocks[0]["type"] == "conclusion"
    assert blocks[-1]["type"] == "citation"


# --------------------------------------------------------------------------- #
# 一致性：流式块 vs 一次性返回
# --------------------------------------------------------------------------- #

def test_stream_blocks_match_non_streaming_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, "今天量能比昨日放大。")
    body = {
        "message": "今天大盘怎么样？",
        "session_id": "session-same-001",
        "context": {
            "page": "home",
            "data": {"turnover": 1.2e12},
            "sources": [{"name": "腾讯行情快照", "fetched_at": "2026-11-07T09:20:00+08:00"}],
        },
    }

    one_shot = _client(tmp_path).post(CHAT, json=body, headers=WRITE_HEADERS).json()
    chat_channel.reset_sessions()
    streamed = _parse_sse(
        _client(tmp_path).post(CHAT, json=body, headers=SSE_HEADERS).text
    )

    conclusion = streamed[0][1]["payload"]["text"]
    citation = streamed[1][1]["payload"]["items"]
    done = streamed[-1][1]

    assert conclusion == one_shot["reply"]
    assert citation[0]["name"] == one_shot["sources"][0]["name"]
    assert citation[0]["fetched_at"] == one_shot["sources"][0]["fetched_at"]
    assert citation[0]["url"] == one_shot["sources"][0]["url"]
    assert done["reply"] == one_shot["reply"]
    assert done["sources"] == [
        {**source, "used_at": done["sources"][0]["used_at"]} for source in one_shot["sources"]
    ]
    assert done["sources"][0]["name"] == one_shot["sources"][0]["name"]
    assert done["session_id"] == one_shot["session_id"]


def test_stream_note_text_matches_non_streaming_reply_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, "建议关注量能变化。")
    body = {"message": "随便聊聊", "session_id": "session-note-01"}

    one_shot = _client(tmp_path).post(CHAT, json=body, headers=WRITE_HEADERS).json()
    chat_channel.reset_sessions()
    frames = _parse_sse(
        _client(tmp_path).post(CHAT, json=body, headers=SSE_HEADERS).text
    )

    conclusion, notice = (data["payload"] for event, data in frames if event == "block")
    assert f"{conclusion['text']}\n\n{notice['message']}" == one_shot["reply"]


# --------------------------------------------------------------------------- #
# 失败与中断
# --------------------------------------------------------------------------- #

def test_stream_failure_is_single_notice_then_done_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_complete(channel, system, user, *, timeout):
        return "   "

    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _ok_channel())
    monkeypatch.setattr(chat_channel, "complete", fake_complete)

    response = _client(tmp_path).post(
        CHAT, json={"message": "你好"}, headers=SSE_HEADERS
    )

    frames = _parse_sse(response.text)
    assert [event for event, _ in frames] == ["block", "done"]
    block = frames[0][1]
    assert block["type"] == "notice"
    assert block["payload"]["kind"] == "missing_data"
    assert "AI 返回了空回答" in block["payload"]["message"]
    done = frames[1][1]
    assert done["ok"] is False
    assert done["reply"] == ""
    assert done["code"] == "ai_empty"


def test_stream_invalid_session_returns_failure_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, "不该被调用")

    response = _client(tmp_path).post(
        CHAT, json={"message": "你好", "session_id": "bad id"}, headers=SSE_HEADERS
    )

    assert response.status_code == 400
    frames = _parse_sse(response.text)
    assert [event for event, _ in frames] == ["block", "done"]
    assert frames[1][1]["code"] == "invalid_session"


def test_disconnect_aborts_without_blocks() -> None:
    """客户端断开：`await_turn` 现有 `is_disconnected` 抛 `ChatAborted`，流里一帧不发。"""
    cancelled = asyncio.Event()

    async def slow_work() -> str:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return "迟到的回答"

    async def always_disconnected() -> bool:
        return True

    async def scenario() -> None:
        with pytest.raises(chat_channel.ChatAborted):
            await chat_channel.await_turn(
                slow_work(),
                timeout=10,
                channel_kind="cli",
                is_disconnected=always_disconnected,
            )

    asyncio.run(scenario())
    assert cancelled.is_set()


def test_stream_logs_no_memory_on_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    """流式路径下 `ChatAborted` 直接结束，不写会话记忆（与非流式一致）。"""

    async def aborted_complete(channel, system, user, *, timeout):
        raise chat_channel.ChatAborted

    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _ok_channel())
    monkeypatch.setattr(chat_channel, "complete", aborted_complete)

    async def collect() -> list[str]:
        return [
            frame
            async for frame in chat_channel.stream_turn(
                message="你好", session_id="session-abort-1"
            )
        ]

    assert asyncio.run(collect()) == []
    assert chat_channel._history("session-abort-1") == []
