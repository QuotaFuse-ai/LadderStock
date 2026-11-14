"""QUOT-581：`run_turn` 的 SSE 传输层（`event: block` / `event: done`）。

块协议 = 阶段 A 的 `ladderstock.ai.answer_blocks`（PI 2026-11-12 拍板 A）。钉住六件事：

1. 开关：只有 `Accept: text/event-stream` 走 SSE，其余仍走非流式 JSON；
2. 事件层：`block` 的 `seq` 与 `type` / `payload` 平级、从 1 连续递增；`done` 带
   `last_seq` / `completed`；
3. 顺序：`conclusion` 在首、`citation` 在尾、`notice` 不在首位、总数 ≤ 6；
4. 一致：流式块的指标/来源逐项等于程序算出来的那份，`done.reply` 与非流式 `reply` 同源；
5. 失败半流（拍板 B）：已发块保留、不追加 notice，`done{completed:false, reason}`；
6. 6.4 第 5 条只约束非流式路径：非流式失败仍是「只有一条 notice、reply 为空」。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.ai import answer_blocks
from ladderstock.web import chat as chat_channel
from ladderstock.web.server import create_app

BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)
WRITE_HEADERS = {"X-LDST-Client": "1"}
CHAT = "/api/control/ai/chat"
SSE_HEADERS = {**WRITE_HEADERS, "Accept": "text/event-stream"}

#: 上游适配（QUOT-579）给的结构化叙述；块只从这里来，不从 `reply` 字符串拼
FAKE_AI: dict[str, list[str]] = {
    "narrative": ["今天量能比昨日温和放大，个股分化明显。", "高位板接力不顺。"],
    "highlights": ["成交额较昨日回升"],
    "risks": ["尾盘炸板率抬升"],
    "evidence": ["腾讯行情快照"],
}

#: 指标只认带已核实出处的那份（没有出处的数值不进 metric 块，红线 6）
PROVENANCE_METRICS: dict[str, Any] = {
    "limit_up_count": {
        "value": 48,
        "unit": "家",
        "source": "computed",
        "fetched_at": "2026-11-07T09:20:00+08:00",
        "as_of": "2026-11-07",
        "endpoint": "review.pipeline.collect_metrics",
        "verified": True,
        "metric": "limit_up_count",
    }
}


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


def _blocks(frames: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [data for event, data in frames if event == "block"]


def _done(frames: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    assert frames[-1][0] == "done", frames[-1]
    return frames[-1][1]


def _no_numbers(text: str) -> bool:
    return not answer_blocks.contains_number(text)


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
# 事件层：seq / last_seq / completed
# --------------------------------------------------------------------------- #

def test_block_events_carry_monotonic_seq_on_event_layer() -> None:
    blocks = chat_channel.answer_block_payloads(ai=FAKE_AI, metrics=PROVENANCE_METRICS)

    frames = [
        _parse_sse(chat_channel._block_event(index + 1, block))[0]
        for index, block in enumerate(blocks)
    ]

    seqs = [data["seq"] for _, data in frames]
    assert seqs == list(range(1, len(blocks) + 1))
    for event, data in frames:
        assert event == "block"
        # `seq` 与 `type` / `payload` 平级，不进 payload
        assert set(data) == {"seq", "type", "payload"}
        assert "seq" not in data["payload"]


def test_stream_seq_and_done_last_seq(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, "量能温和放大。")

    response = _client(tmp_path).post(
        CHAT, json={"message": "你好"}, headers=SSE_HEADERS
    )

    frames = _parse_sse(response.text)
    blocks = _blocks(frames)
    assert [block["seq"] for block in blocks] == list(range(1, len(blocks) + 1))
    done = _done(frames)
    assert done["last_seq"] == len(blocks)
    assert done["completed"] is True
    assert "reason" not in done


# --------------------------------------------------------------------------- #
# 顺序与内容（任务书 6.3 / 6.4）
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
    blocks = _blocks(frames)
    # 没有程序算出的指标时，指标段是一条 missing_data notice，而不是编数
    assert [block["type"] for block in blocks] == ["conclusion", "notice", "citation"]
    assert blocks[0]["type"] == "conclusion"
    assert blocks[1]["payload"]["section"] == "指标"
    assert blocks[-1]["type"] == "citation"
    item = blocks[-1]["payload"]["items"][0]
    assert item["name"] == "腾讯行情快照"
    assert item["fetched_at"] == "2026-11-07T09:20:00+08:00"
    # 查询串（可能带 token）不进块
    assert item["url"] == "https://qt.gtimg.cn/q=sh000001"


def test_stream_without_sources_ends_with_missing_data_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, "建议关注量能变化。")

    response = _client(tmp_path).post(
        CHAT, json={"message": "随便聊聊"}, headers=SSE_HEADERS
    )

    blocks = _blocks(_parse_sse(response.text))
    assert blocks[0]["type"] == "conclusion"
    assert blocks[-1]["type"] == "notice"
    notice = blocks[-1]["payload"]
    # `missing_data` 只留给「来源为空」（D7）
    assert notice["kind"] == answer_blocks.NOTICE_MISSING_DATA
    assert notice["section"] == "来源"
    # 没带页内数据 ≠ 带了页内数据没取数记录：两句说明不能混用
    assert notice["message"] == answer_blocks.SOURCE_NOTE_WITHOUT_DATA


def test_stream_with_page_data_but_no_sources_uses_no_record_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, "建议关注量能变化。")

    response = _client(tmp_path).post(
        CHAT,
        json={"message": "看看今天盘面", "context": {"page": "home", "data": {"turnover": 1.2e12}}},
        headers=SSE_HEADERS,
    )

    blocks = _blocks(_parse_sse(response.text))
    notices = [b["payload"] for b in blocks if b["type"] == "notice"]
    messages = [n["message"] for n in notices if n.get("section") == "来源"]
    # 与非流式 reply 尾部那句逐字一致，且只出一次
    assert messages == [answer_blocks.SOURCE_NOTE_NO_RECORD]
    assert messages != [answer_blocks.SOURCE_NOTE_WITHOUT_DATA]


def test_conclusion_text_has_no_numbers() -> None:
    blocks = chat_channel.answer_block_payloads(
        ai={"narrative": ["今天量能比昨日放大 12%，涨停 48 家。"]},
    )

    conclusion = blocks[0]["payload"]["text"]
    # 首句 + 剥数字：结论正文由阶段 A 说了算，不原样搬
    assert "12" not in conclusion and "48" not in conclusion
    assert "今天量能比昨日放大" in conclusion
    assert _no_numbers(conclusion)


def test_metric_block_only_from_program_metrics() -> None:
    blocks = chat_channel.answer_block_payloads(
        ai=FAKE_AI, metrics=PROVENANCE_METRICS
    )
    types = [block["type"] for block in blocks]
    metric = blocks[types.index("metric")]["payload"]["items"]

    assert [item["label"] for item in metric] == ["涨停家数"]
    assert metric[0]["value"] == 48
    assert metric[0]["as_of"] == "2026-11-07"


def test_block_count_never_exceeds_six_with_many_sources() -> None:
    sources = [
        {"name": f"来源{i}", "fetched_at": "2026-11-07T09:20:00+08:00"}
        for i in range(20)
    ]

    blocks = chat_channel.answer_block_payloads(
        ai=FAKE_AI, sources=sources, metrics=PROVENANCE_METRICS
    )

    assert len(blocks) <= chat_channel.MAX_ANSWER_BLOCKS == 6
    assert blocks[0]["type"] == "conclusion"
    assert blocks[-1]["type"] == "citation"
    answer_blocks.check_ordering(blocks)


def test_block_count_with_many_sources_over_http(
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

    blocks = _blocks(_parse_sse(response.text))
    assert len(blocks) <= 6
    assert blocks[0]["type"] == "conclusion"
    assert blocks[-1]["type"] == "citation"


# --------------------------------------------------------------------------- #
# 一致性：流式块 vs 非流式返回
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
    frames = _parse_sse(
        _client(tmp_path).post(CHAT, json=body, headers=SSE_HEADERS).text
    )

    done = _done(frames)
    stream_blocks = _blocks(frames)
    citation = stream_blocks[-1]["payload"]["items"]

    # 流式 conclusion 必须等于非流式装配的首块（同一份 reply_to_structured 派生）
    from ladderstock.web.assistant import _blocks_for

    non_stream_first = _blocks_for(one_shot["reply"], one_shot["sources"])[0]
    assert stream_blocks[0]["type"] == "conclusion"
    assert stream_blocks[0]["payload"]["text"] == non_stream_first["payload"]["text"]
    # 流式 conclusion 绝不能是占位句
    assert stream_blocks[0]["payload"]["text"] not in (
        "本轮没有生成结论。",
        "本轮没有可展示的结论。",
    )
    # `done.reply` 与非流式同源
    assert done["reply"] == one_shot["reply"]
    assert done["ok"] == one_shot["ok"] is True
    assert done["model"] == one_shot["model"]
    assert done["session_id"] == one_shot["session_id"]
    assert (citation[0]["name"], citation[0]["fetched_at"], citation[0]["url"]) == (
        one_shot["sources"][0]["name"],
        one_shot["sources"][0]["fetched_at"],
        one_shot["sources"][0]["url"],
    )


def test_stream_done_strips_source_note_already_sent_as_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """来源说明在流式里是 notice 块；`done.reply` 不再重复同一句，会话落账仍完整。"""
    _stub(monkeypatch, "建议关注量能变化。")
    body = {"message": "随便聊聊", "session_id": "session-note-001"}

    one_shot = _client(tmp_path).post(CHAT, json=body, headers=WRITE_HEADERS).json()
    chat_channel.reset_sessions()
    frames = _parse_sse(
        _client(tmp_path).post(CHAT, json=body, headers=SSE_HEADERS).text
    )

    done = _done(frames)
    note = answer_blocks.SOURCE_NOTE_WITHOUT_DATA
    assert one_shot["reply"].endswith("\n\n" + note)
    assert done["reply"] == one_shot["reply"][: -(len(note) + 2)]
    assert note not in done["reply"]
    # 块里只出一次，且正文与 done.reply 一致
    messages = [
        b["payload"]["message"]
        for b in _blocks(frames)
        if b["type"] == "notice" and b["payload"].get("section") == "来源"
    ]
    assert messages == [note]


def test_stream_metrics_match_build_blocks_assembly() -> None:
    """同一份 metrics / ai / sources，流式吐的块与直接调 `build_blocks` 逐字相同。"""
    sources = [{"name": "腾讯行情快照", "fetched_at": "2026-11-07T09:20:00+08:00"}]
    expected = answer_blocks.build_blocks(
        dict(PROVENANCE_METRICS), FAKE_AI, sources
    )

    actual = chat_channel.answer_block_payloads(
        ai=FAKE_AI, sources=sources, metrics=PROVENANCE_METRICS
    )

    assert actual == expected


# --------------------------------------------------------------------------- #
# 失败与中断
# --------------------------------------------------------------------------- #

def test_no_channel_failure_is_single_stage_a_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失败 kind 走阶段 A 的 6 种枚举；流式路径首轮失败 = 只一条 notice。"""

    def broken_channel(_root=None):
        raise chat_channel.ChatFailure(
            503,
            code="no_channel",
            reason="channel",
            kind="internal",
            message="未配置 AI 通道",
            retryable=False,
        )

    monkeypatch.setattr(chat_channel, "load_channel", broken_channel)

    frames = _parse_sse(
        _client(tmp_path).post(CHAT, json={"message": "你好"}, headers=SSE_HEADERS).text
    )

    blocks = _blocks(frames)
    assert [block["type"] for block in blocks] == ["notice"]
    assert blocks[0]["payload"]["kind"] in answer_blocks.NOTICE_KINDS
    done = _done(frames)
    assert done["ok"] is False
    assert done["reply"] == ""
    assert done["code"] == "no_channel"
    assert done["last_seq"] == 1


# --------------------------------------------------------------------------- #
# 失败半流（拍板 B）：已发块保留、不追加 notice、done{completed:false, reason}
# --------------------------------------------------------------------------- #

def test_midstream_failure_keeps_sent_blocks_and_marks_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub(monkeypatch, "今天量能比昨日放大。")
    probes = 0

    async def disconnect_after_first_block() -> bool:
        nonlocal probes
        probes += 1
        return probes > 1

    async def collect() -> list[str]:
        return [
            frame
            async for frame in chat_channel.stream_turn(
                message="今天大盘怎么样？",
                session_id="session-half-stream",
                is_disconnected=disconnect_after_first_block,
                ai=FAKE_AI,
                metrics=PROVENANCE_METRICS,
            )
        ]

    frames = _parse_sse("".join(asyncio.run(collect())))
    blocks = _blocks(frames)
    assert len(blocks) == 1
    assert blocks[0]["type"] == "conclusion"
    done = _done(frames)
    assert done["ok"] is False
    assert done["completed"] is False
    assert done["reason"] == "cancelled"
    assert done["last_seq"] == 1
    # 不追加 notice：失败信息只在 done 里
    assert all(block["type"] != "notice" for block in blocks)
    # 已落账的问答不回滚：两问同一个 session 能带上历史
    assert len(chat_channel._history("session-half-stream")) == 1


def test_format_failure_after_turn_rolls_back_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub(monkeypatch, "今天量能比昨日放大。")

    async def collect() -> list[str]:
        return [
            frame
            async for frame in chat_channel.stream_turn(
                message="你好",
                session_id="session-bad-ai",
                ai={"narrative": ["好吧"], "turnover": 1.2e12},
            )
        ]

    frames = _parse_sse("".join(asyncio.run(collect())))
    assert _blocks(frames) == []
    done = _done(frames)
    assert done["ok"] is False
    assert done["completed"] is False
    assert done["reason"] == "format"
    assert done["failure"]["kind"] == "format"
    assert done["last_seq"] == 0
    assert done["followups"] == list(chat_channel._FOLLOWUP_ORDER)
    # 块没装配出来，这一问不算数：会话记忆里不留
    assert chat_channel._history("session-bad-ai") == []


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
    assert _blocks(frames)[0]["payload"]["kind"] in answer_blocks.NOTICE_KINDS
    assert _done(frames)["code"] == "invalid_session"


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

    # QUOT-581 动作流水账之后：中止发生在 model 动作，received/history 等
    # status 帧已合法发出。中止的真实契约是——没有块、没有 done、不落账。
    frames = _parse_sse("".join(asyncio.run(collect())))
    assert [event for event, _ in frames if event != "status"] == []
    assert chat_channel._history("session-abort-1") == []


# --------------------------------------------------------------------------- #
# 6.4 第 5 条：只约束非流式路径
# --------------------------------------------------------------------------- #

def test_non_streaming_failure_stays_single_notice_with_empty_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, "   ")

    response = _client(tmp_path).post(
        CHAT, json={"message": "你好"}, headers=WRITE_HEADERS
    )

    body = response.json()
    assert response.status_code == 502
    assert body["ok"] is False
    assert body["reply"] == ""
    assert body["sources"] == []
    assert body["code"] == "ai_empty"
    assert body["failure"]["kind"] in answer_blocks.NOTICE_KINDS
    # 非流式失败体没有块字段：6.4 第 5 条的「只有一条 notice」由前端按 failure 渲染
    assert "blocks" not in body


# --------------------------------------------------------------------------- #
# QUOT-581 真流式：增量消费 + model_error 半流 + 心跳（monkeypatch 注入，零环境开关）
# --------------------------------------------------------------------------- #

def _http_channel() -> chat_channel.Channel:
    return chat_channel.Channel(
        kind="openai", label="openai:deepseek-v4.1-flash", config=None, target=None
    )


def _stub_incremental(monkeypatch: pytest.MonkeyPatch, chunks: list[str]) -> None:
    """把 `complete_iter` 打桩成逐段吐 `chunks` 的真流；通道换成 HTTP 触发增量路径。"""

    async def fake_iter(channel, system, user, *, timeout):
        for chunk in chunks:
            yield chunk

    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _http_channel())
    monkeypatch.setattr(chat_channel, "complete_iter", fake_iter)


def test_incremental_stream_blocks_are_prefix_of_non_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真流式逐段吐正文：块增量发出，流式块序列 = 非流式块序列的前缀 + 尾块收尾。"""
    _stub_incremental(
        monkeypatch,
        ["今天量能比昨日温和放大，", "个股分化明显。", "高位板接力不顺。"],
    )

    async def collect() -> list[str]:
        return [
            frame
            async for frame in chat_channel.stream_turn(
                message="今天大盘怎么样？",
                session_id="session-incremental-1",
                metrics=PROVENANCE_METRICS,
            )
        ]

    frames = _parse_sse("".join(asyncio.run(collect())))
    # 动作流水账：每个动作 running→done 成对出现
    statuses = [data for event, data in frames if event == "status"]
    actions = [data["action"] for data in statuses]
    for name in ("received", "context", "history", "model", "assemble", "persist"):
        assert actions.count(name) >= 2, (name, actions)
        assert statuses[actions.index(name)]["state"] == "running"
    # 增量期就该发出块（而非全攒到收尾）；首 status 之后很快出块
    assert [event for event, _ in frames][0] == "status"
    block_positions = [i for i, (event, _) in enumerate(frames) if event == "block"]
    assert frames[block_positions[0]][0] == "block"
    done = _done(frames)
    assert done["ok"] is True
    assert done["completed"] is True
    assert done["stream_mode"] == "stream"
    assert done["last_seq"] == len(_blocks(frames))


def test_midstream_model_error_after_three_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """第 3 块已发后模型断流：前 3 块保留 + done{completed:false, reason:model_error}。"""

    sent = {"n": 0}

    async def failing_iter(channel, system, user, *, timeout):
        stream_reply = "今天量能比昨日温和放大。高位板接力不顺。尾盘炸板率抬升。"
        # 借真实装配数出块：先吐三句让三个块发出去，再断流
        for chunk in stream_reply:
            yield chunk
            sent["n"] += 1
        raise RuntimeError("模拟上游连接中断")

    async def fake_complete(channel, system, user, *, timeout):
        raise AssertionError("增量路径不应回落到一次性 complete")

    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _http_channel())
    monkeypatch.setattr(chat_channel, "complete_iter", failing_iter)
    monkeypatch.setattr(chat_channel, "complete", fake_complete)

    async def collect() -> list[str]:
        return [
            frame
            async for frame in chat_channel.stream_turn(
                message="今天大盘怎么样？",
                session_id="session-model-error",
                metrics=PROVENANCE_METRICS,
            )
        ]

    frames = _parse_sse("".join(asyncio.run(collect())))
    blocks = _blocks(frames)
    # 「补充说明」块由 narrative[1:] 合成、会随定稿句子增长，流中 hold-back；
    # 稳定块 = conclusion + 指标 notice（metrics 在场时）
    assert len(blocks) >= 2, [b["type"] for b in blocks]
    done = _done(frames)
    assert done["ok"] is False
    assert done["completed"] is False
    assert done["reason"] == "model_error"
    assert done["last_seq"] == len(blocks)
    # 失败不追加**新的** notice：失败信息只在 done 里；已发块（含流中 notice）保留
    assert chat_channel._history("session-model-error") == []


def test_slow_model_heartbeat_keeps_gaps_under_two_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """慢模型 15s 才吐首字：期间帧间隔全部 ≤2s（heartbeat 兜底，E5 实证）。"""
    import time as _time

    async def slow_iter(channel, system, user, *, timeout):
        await asyncio.sleep(15)
        yield "今天量能比昨日温和放大。"

    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _http_channel())
    monkeypatch.setattr(chat_channel, "complete_iter", slow_iter)

    async def collect() -> list[tuple[str, float]]:
        stamps: list[tuple[str, float]] = []
        aiter = chat_channel.stream_turn(
            message="今天大盘怎么样？", session_id="session-heartbeat"
        ).__aiter__()
        while True:
            started = _time.monotonic()
            try:
                frame = await asyncio.wait_for(aiter.__anext__(), timeout=10)
            except StopAsyncIteration:
                break
            except TimeoutError:
                stamps.append(("consumer-timeout-10s", _time.monotonic() - started))
                continue
            event = frame.split("\n", 1)[0][len("event: "):]
            stamps.append((event, _time.monotonic() - started))
        return stamps

    stamps = asyncio.run(collect())
    events = [event for event, _ in stamps]
    assert "heartbeat" in events, events
    # 任何一帧的等待都不该超过 2.6s（心跳周期 0.5s + 判定 2s + 余量）
    worst = max(elapsed for event, elapsed in stamps if event != "consumer-timeout-10s")
    assert worst <= 2.6, (events, stamps)


def test_incremental_blocks_match_non_streaming_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """流式增量块与非流式三路由的对外值同源（done.reply 与非流式 reply 一致）。"""
    reply = "今天量能比昨日温和放大。高位板接力不顺。"
    _stub_incremental(monkeypatch, [reply[i:i + 5] for i in range(0, len(reply), 5)])

    async def collect() -> list[str]:
        return [
            frame
            async for frame in chat_channel.stream_turn(
                message="今天大盘怎么样？",
                session_id="session-prefix-match",
                metrics=PROVENANCE_METRICS,
            )
        ]

    frames = _parse_sse("".join(asyncio.run(collect())))
    done = _done(frames)
    assert done["stream_mode"] == "stream"
    # done.reply 与非流式同源：去掉块里已发的来源说明后等于非流式 reply
    assert done["reply"] == reply
    blocks = _blocks(frames)
    assert [b["type"] for b in blocks][0] == "conclusion"
    # 结论块不含数字（红线 4：数值只来自 metrics，本场景没传 metrics）
    conclusion = blocks[0]
    assert _no_numbers(conclusion["payload"]["text"])
