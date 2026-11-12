"""QUOT-580：对话模式会话端点（`/api/conversation`）与块装配的单测。

只测适配层：`ask()` 换成假通道，不联网、不读密钥；落库走临时 `data/chat.sqlite`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.ai import answer_blocks
from ladderstock.data import chat_store
from ladderstock.web import assistant as assistant_module
from ladderstock.web.server import create_app

BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)
HEADERS = {"X-LDST-Client": "1"}

WITHOUT_DATA = answer_blocks.SOURCE_NOTE_WITHOUT_DATA


class _NullControlPlane:
    peers: dict[str, Any] = {}


class _FakeClient:
    def __init__(
        self,
        reply: str = "中短期以震荡为主，量能没有明显放大。",
        exc: BaseException | None = None,
    ) -> None:
        self.reply = reply
        self.exc = exc
        self.calls: list[tuple[str, str, str, bool]] = []
        self.timeout = 0.0

    def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
        self.calls.append((model, system, user, json_mode))
        if self.exc is not None:
            raise self.exc
        return self.reply


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        data_root=tmp_path,
        dist_dir=tmp_path / "no-dist",
        control_plane=_NullControlPlane(),
    )
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK)


@pytest.fixture()
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    fake = _FakeClient()
    monkeypatch.setattr(
        assistant_module, "_load_client", lambda *_a, **_k: (fake, "openai:test-model")
    )
    return fake


def _source(name: str = "本地 /api/control/peers（工作区状态）") -> dict[str, str]:
    return {
        "name": name,
        "fetched_at": "2026-11-07T09:49:31+00:00",
        "url": "http://127.0.0.1:5963/api/control/peers",
    }


# --------------------------------------------------------------------------- #
# _conversation_blocks：复用现役 `reply_to_structured()` → `build_blocks()`，只加 6 上限
# --------------------------------------------------------------------------- #


def _blocks(
    reply: str, sources: list[Any] | None = None, failure: Any = None
) -> list[dict[str, Any]]:
    return assistant_module._conversation_blocks(
        reply=reply, sources=sources or [], failure=failure
    )


def test_blocks_are_conclusion_first_and_capped() -> None:
    blocks = _blocks("中短期以震荡为主，量能没有明显放大。")
    assert blocks[0]["type"] == answer_blocks.BLOCK_CONCLUSION
    assert len(blocks) <= assistant_module.MAX_ANSWER_BLOCKS
    assert assistant_module.MAX_ANSWER_BLOCKS == 6
    answer_blocks.validate_blocks(blocks)


def test_blocks_capped_even_when_engine_allows_eight() -> None:
    # 现役引擎在无 metrics 时：结论 + 指标 missing + 2 条风险 + 补充 + 来源 = 6；
    # 引擎上限是 8，会话侧再收到 6——这条护栏在引擎放宽后仍要成立。
    reply = "结论在第一句。风险需要注意回撤。补一句别的。"
    blocks = _blocks(reply)
    assert answer_blocks.MAX_BLOCKS == 8
    assert len(blocks) <= assistant_module.MAX_ANSWER_BLOCKS
    assert assistant_module.MAX_ANSWER_BLOCKS == 6
    assert len(blocks) == len(assistant_module._blocks_for(reply, [])[:6])
    answer_blocks.validate_blocks(blocks)


def test_conversation_cap_truncates_engine_output_beyond_six(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = [
        answer_blocks.build_block(
            answer_blocks.BLOCK_CONCLUSION, {"text": "结论。", "tone": "neutral"}
        )
    ]
    while len(canonical) < answer_blocks.MAX_BLOCKS:
        canonical.append(
            answer_blocks.build_block(
                answer_blocks.BLOCK_NOTICE,
                {"kind": "degraded", "section": "补充说明", "message": "补充。"},
            )
        )
    assert len(canonical) == 8
    monkeypatch.setattr(assistant_module, "_blocks_for", lambda *_a, **_k: canonical)
    blocks = _blocks("结论。")
    assert len(blocks) == assistant_module.MAX_ANSWER_BLOCKS == 6
    assert blocks == canonical[:6]


def test_blocks_reuse_canonical_chain_and_strip_numbers() -> None:
    sources = [assistant_module.ChatSource(**_source())]
    reply = "近 20 日震荡偏弱，现价 9.87 元。"
    blocks = _blocks(reply, sources)
    assert blocks == assistant_module._blocks_for(reply, sources)[:6]
    conclusion = blocks[0]["payload"]
    assert conclusion["tone"] == "neutral"
    assert not answer_blocks.contains_number(conclusion["text"])
    assert "20" not in conclusion["text"]
    citation = next(block for block in blocks if block["type"] == answer_blocks.BLOCK_CITATION)
    assert citation["payload"]["items"][0]["name"] == _source()["name"]
    # 有来源就不出「来源缺失」notice；metrics 缺失的提示仍由引擎出（不伪造数）
    sections = [
        block["payload"]["section"]
        for block in blocks
        if block["type"] == answer_blocks.BLOCK_NOTICE
    ]
    assert "来源" not in sections
    assert "指标" in sections


def test_blocks_add_missing_data_notice_when_no_sources() -> None:
    blocks = _blocks("中短期以震荡为主。")
    notices = [block["payload"] for block in blocks if block["type"] == answer_blocks.BLOCK_NOTICE]
    assert all(notice["kind"] == answer_blocks.NOTICE_MISSING_DATA for notice in notices)
    source_notice = next(notice for notice in notices if notice["section"] == "来源")
    assert source_notice["message"] == WITHOUT_DATA
    assert not any(block["type"] == answer_blocks.BLOCK_CITATION for block in blocks)


def test_blocks_failure_is_single_notice() -> None:
    failure = assistant_module.ChatFailure(
        502,
        code="ai_network",
        reason="network",
        kind="network",
        message="AI 请求未送达（网络/代理）。",
        retryable=True,
    )
    blocks = _blocks("", failure=failure)
    assert len(blocks) == 1
    assert blocks[0]["type"] == answer_blocks.BLOCK_NOTICE
    assert blocks[0]["payload"]["kind"] == answer_blocks.NOTICE_MISSING_DATA
    assert blocks[0]["payload"]["message"] == failure.message
    answer_blocks.validate_blocks(blocks)


def test_missing_data_kind_is_new_not_reused() -> None:
    assert answer_blocks.NOTICE_MISSING_DATA not in answer_blocks.NOTICE_KINDS


# --------------------------------------------------------------------------- #
# HTTP：三段验收（建会话 → 三轮 → 重开读回）+ 淘汰 + 重命名
# --------------------------------------------------------------------------- #


def _post_turn(client: TestClient, **body: Any) -> Any:
    payload = {"message": "大盘怎么样？", **body}
    return client.post("/api/conversation/turns", json=payload, headers=HEADERS)


def test_three_turns_then_read_back_after_reopen(tmp_path: Path, fake_client: _FakeClient) -> None:
    client = _client(tmp_path)
    session_id = "sess-http-0001"
    # 结论句会被现役引擎剥掉阿拉伯数字（红线 4），历史里只留中文数字的结论才能原样读回
    ordinals = ["第一", "第二", "第三"]
    seqs = []
    for ordinal in ordinals:
        conclusion = f"{ordinal}轮的结论。"
        fake_client.reply = conclusion
        response = _post_turn(
            client,
            message=f"{ordinal}个问题",
            session_id=session_id,
            context={"page": "watch", "data": {"close": 10.1}, "sources": [_source()]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is True
        seqs.append(body["turn_seq"])
        assert body["session_id"] == session_id
        assert body["reply"].startswith(conclusion)
    assert seqs == [1, 2, 3]

    # 第二轮起，提示词里带上了本会话的历史（只取结论句，不塞整串 blocks），
    # 且走现役 `AsyncStructuredContext.history` 的分段与渲染
    _model, _system, user, _json = fake_client.calls[-1]
    assert "【上一轮已生成内容】" in user
    assert "用户：第一个问题" in user
    assert "助手：第一轮的结论。" in user

    # 「重启服务」= 换一个全新的 app 实例，仍指向同一个 data_root
    reopened = _client(tmp_path)
    listed = reopened.get("/api/conversation/sessions", headers=HEADERS)
    assert listed.status_code == 200
    sessions = listed.json()["sessions"]
    assert [item["session_id"] for item in sessions] == [session_id]
    assert sessions[0]["title"] == "第一个问题"
    assert sessions[0]["turns"] == 3

    turns = reopened.get(f"/api/conversation/sessions/{session_id}/turns", headers=HEADERS)
    assert turns.status_code == 200
    payload = turns.json()
    assert payload["session"]["title"] == "第一个问题"
    assert [turn["seq"] for turn in payload["turns"]] == [1, 2, 3]
    created = [turn["created_at"] for turn in payload["turns"]]
    assert created == sorted(created)
    answer_blocks.validate_blocks(payload["turns"][0]["blocks"])


def test_blocks_json_round_trips_through_http(tmp_path: Path, fake_client: _FakeClient) -> None:
    client = _client(tmp_path)
    response = _post_turn(
        client,
        message="大盘怎么样",
        session_id="sess-roundtrip",
        context={"sources": [_source()]},
    )
    assert response.status_code == 200
    posted_blocks = response.json()["blocks"]

    conn = chat_store.connect(tmp_path)
    assert conn is not None
    try:
        rows = chat_store.session_turns(conn, "sess-roundtrip")
    finally:
        chat_store.close(conn)
    assert json.loads(rows[0]["blocks_json"]) == posted_blocks

    read_back = client.get(
        "/api/conversation/sessions/sess-roundtrip/turns", headers=HEADERS
    ).json()
    assert read_back["turns"][0]["blocks"] == posted_blocks


def test_long_first_question_title_follows_documented_rule(
    tmp_path: Path, fake_client: _FakeClient
) -> None:
    client = _client(tmp_path)
    question = "关" * 55
    response = _post_turn(client, message=question, session_id="sess-title")
    assert response.status_code == 200
    session = client.get("/api/conversation/sessions", headers=HEADERS).json()["sessions"][0]
    assert session["title"] == question[: chat_store.TITLE_MAX_CHARS]
    assert len(session["title"]) == 40


def test_rename_endpoint_and_unknown_session_404(
    tmp_path: Path, fake_client: _FakeClient
) -> None:
    client = _client(tmp_path)
    _post_turn(client, message="首问", session_id="sess-rename")

    renamed = client.patch(
        "/api/conversation/sessions/sess-rename", json={"title": "换个名字"}, headers=HEADERS
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "换个名字"

    missing = client.patch(
        "/api/conversation/sessions/sess-missing", json={"title": "x"}, headers=HEADERS
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"

    unknown_turns = client.get(
        "/api/conversation/sessions/sess-missing/turns", headers=HEADERS
    )
    assert unknown_turns.status_code == 404


def test_no_database_yet_lists_empty(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/api/conversation/sessions", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"sessions": []}


def test_generated_session_id_when_absent(tmp_path: Path, fake_client: _FakeClient) -> None:
    client = _client(tmp_path)
    response = _post_turn(client, message="没有传会话 ID")
    assert response.status_code == 200
    session_id = response.json()["session_id"]
    assert assistant_module.SESSION_ID_RE.match(session_id)


def test_invalid_session_id_rejected(tmp_path: Path, fake_client: _FakeClient) -> None:
    client = _client(tmp_path)
    response = _post_turn(client, message="问题", session_id="bad id!")
    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert body["code"] == "invalid_session"


def test_empty_message_rejected_without_touching_store(
    tmp_path: Path, fake_client: _FakeClient
) -> None:
    client = _client(tmp_path)
    response = _post_turn(client, message="   ", session_id="sess-empty")
    assert response.status_code == 400
    assert response.json()["code"] == "empty_message"
    assert fake_client.calls == []
    assert not chat_store.db_path(tmp_path).exists()


def test_ai_failure_uses_chat_failure_shape_and_writes_no_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient(exc=OSError("connection reset"))
    monkeypatch.setattr(
        assistant_module, "_load_client", lambda *_a, **_k: (fake, "openai:test-model")
    )
    client = _client(tmp_path)
    response = _post_turn(client, message="会失败的提问", session_id="sess-fail")
    assert response.status_code == 502
    body = response.json()
    assert body["ok"] is False
    assert body["session_id"] == "sess-fail"
    assert body["code"] == "ai_network"
    assert body["failure"]["kind"] == "network"
    assert body["retryable"] is True
    # 失败不落账：本轮既没建会话也没写轮次，库文件都没被创建
    assert not chat_store.db_path(tmp_path).exists()
