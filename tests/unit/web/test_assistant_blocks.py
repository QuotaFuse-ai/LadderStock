"""QUOT-579 阶段 B：现役三路由追加 `blocks` + `context` 双形态的契约测试。

验收对照（本单描述「验收标准」）：

- 三路由响应体都**追加**了 `blocks`，`reply/model/ai/fetched_at` 四键类型与语义不变；
- 旧字符串形态 `reply` 与改造前快照**逐字一致**；
- 结构化形态 `citation` 带上 `name / fetched_at / url`（有 `symbol/as_of` 也带上）；
- 通道不可用时 `blocks` 只有一条 `notice`，`reply` 为空（三路由各一）；
- `sources=[]` 时提示文案与 `chat.py:434 _source_note` 一致。

模型调用一律用假客户端，不联网、不读密钥。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ladderstock.web import assistant as assistant_module
from ladderstock.web import chat as chat_module
from ladderstock.web.server import create_app

BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)

#: 改造前实盘快照的文案（`scripts_quot579_snapshot.py` 抓取；此处逐字固定，防回归）
SNAPSHOT_REPLY = "页面里的现价是 9.87 元，近几日横盘震荡，量能没有明显放大。"
SNAPSHOT_SKILLS = "这一步先确认标的所属行业，再对照同业估值做横向比较。"
SNAPSHOT_PAGE = "今天自选股整体涨跌互现，跌的稍多，成交没有明显放大。"


class _NullControlPlane:
    peers: dict[str, Any] = {}


class _FakeClient:
    def __init__(self, reply: str, exc: BaseException | None = None) -> None:
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


def _patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> TestClient:
    monkeypatch.setattr(
        assistant_module, "_load_client", lambda *_a, **_k: (fake, "openai:model-a")
    )
    # 记忆通道不可达：本组只测响应形状，不测记忆（另有 test_assistant_memory.py 覆盖）
    monkeypatch.setattr(assistant_module, "memory_scope_for", lambda _m: None)
    return _client(tmp_path)


def _post(client: TestClient, path: str, payload: dict[str, Any]) -> Any:
    return client.post(path, json=payload, headers={"X-LDST-Client": "1"})


# --------------------------------------------------------------------------- #
# 1. 旧字符串形态：四键类型不变 + reply 逐字一致 + blocks 追加
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("path", "reply", "extra"),
    [
        (
            "/api/research/600000/assistant",
            SNAPSHOT_REPLY,
            {"context": "标的：浦发银行（600000）", "date": "2026-11-06", "tab": "overview"},
        ),
        (
            "/api/skills/assistant",
            SNAPSHOT_SKILLS,
            {
                "context": '{"step": "行业对比"}',
                "page_key": "/skills",
                "bound_skill": "a-share-tape",
            },
        ),
        (
            "/api/page/assistant",
            SNAPSHOT_PAGE,
            {
                "context": '{"rows": [{"code": "600519", "change": -0.032}]}',
                "page_key": "watch",
                "bound_skill": "a-share-tape",
            },
        ),
    ],
)
def test_string_context_reply_is_verbatim_and_blocks_appended(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    reply: str,
    extra: dict[str, Any],
) -> None:
    fake = _FakeClient(reply)
    client = _patch(tmp_path, monkeypatch, fake)

    response = _post(client, path, {"question": "这只最近的走势怎么样？", **extra})

    assert response.status_code == 200
    body = response.json()
    # 四键：类型与语义不变（reply 与改造前快照逐字一致）
    assert body["reply"] == reply
    assert isinstance(body["model"], str) and body["model"]
    assert isinstance(body["ai"], dict) and set(body["ai"]) == {"model", "source"}
    assert isinstance(body["fetched_at"], str) and body["fetched_at"]
    # 唯一新增：blocks
    assert set(body) == {"reply", "model", "ai", "fetched_at", "blocks"}
    assert isinstance(body["blocks"], list) and body["blocks"]
    assert body["blocks"][0]["type"] == "conclusion"


def test_string_context_reply_matches_recorded_snapshot() -> None:
    """贴死的快照文案与阶段 A 的 full_answer 装配入口自洽（防有人偷改文案求绿）。"""
    ai = assistant_module.reply_to_ai(SNAPSHOT_REPLY)
    assert ai["narrative"], "快照 reply 必须能切出至少一句"
    assert "".join(ai["narrative"]) == SNAPSHOT_REPLY


# --------------------------------------------------------------------------- #
# 2. 结构化形态：citation 带 name / fetched_at / url（有 symbol / as_of 也带上）
# --------------------------------------------------------------------------- #


def test_structured_context_feeds_sources_into_citation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient(SNAPSHOT_PAGE)
    client = _patch(tmp_path, monkeypatch, fake)

    response = _post(
        client,
        "/api/page/assistant",
        {
            "question": "今天自选股整体表现怎么样？",
            "page_key": "watch",
            "context": {
                "page": "watch",
                "data": {"rows": [{"code": "600519", "change": -0.032}]},
                "sources": [
                    {
                        "name": "腾讯行情",
                        "fetched_at": "2026-11-11T15:02:01+08:00",
                        "url": "https://example.com/quote/600519",
                        "symbol": "600519",
                        "as_of": "2026-11-11",
                    }
                ],
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == SNAPSHOT_PAGE
    citation = [b for b in body["blocks"] if b["type"] == "citation"]
    assert len(citation) == 1
    item = citation[0]["payload"]["items"][0]
    assert item["name"] == "腾讯行情"
    assert item["fetched_at"] == "2026-11-11T15:02:01+08:00"
    assert item["url"] == "https://example.com/quote/600519"
    assert item["symbol"] == "600519"
    assert item["as_of"] == "2026-11-11"
    # 页内数据进了模型上下文（结构化 → 字符串）
    _, _, user, _ = fake.calls[0]
    assert '"code": "600519"' in user
    assert "页面：watch" in user


# --------------------------------------------------------------------------- #
# 3. 三路由失败态：blocks 只有一条 notice，reply 为空，HTTP 码与既有字段不变
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("path", "payload", "status", "code"),
    [
        (
            "/api/research/600000/assistant",
            {"question": "走势？", "context": "x"},
            504,
            "ai_timeout",
        ),
        ("/api/skills/assistant", {"question": "走势？", "context": "x"}, 504, "ai_timeout"),
        ("/api/page/assistant", {"question": "走势？", "context": "x"}, 504, "ai_timeout"),
    ],
)
def test_failure_response_carries_single_notice_and_empty_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload: dict[str, Any],
    status: int,
    code: str,
) -> None:
    fake = _FakeClient("", exc=httpx.ReadTimeout("timed out"))
    client = _patch(tmp_path, monkeypatch, fake)

    response = _post(client, path, payload)

    assert response.status_code == status
    body = response.json()
    # 既有错误体字段一个不动
    assert body["code"] == code
    assert body["retryable"] is True
    assert "超时" in body["detail"]
    # 追加的 reply + blocks
    assert body["reply"] == ""
    assert len(body["blocks"]) == 1
    assert body["blocks"][0]["type"] == "notice"
    assert body["blocks"][0]["payload"]["kind"] == "missing_data"


def test_failure_blocks_message_has_no_digits() -> None:
    exc = assistant_module.AssistantFailure(
        422, "question_too_long", "问题过长（上限 2000 字）", retryable=False
    )
    blocks = assistant_module._failure_blocks(exc)
    assert len(blocks) == 1
    assert blocks[0]["type"] == "notice"
    assert "2000" not in blocks[0]["payload"]["message"]


# --------------------------------------------------------------------------- #
# 4. sources=[] → 提示文案与 chat.py:434 _source_note 逐字一致
# --------------------------------------------------------------------------- #


def test_empty_sources_notice_matches_chat_source_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient(SNAPSHOT_PAGE)
    client = _patch(tmp_path, monkeypatch, fake)

    response = _post(
        client, "/api/page/assistant", {"question": "今天表现？", "context": "涨跌家数 1183/5237"}
    )

    assert response.status_code == 200
    notices = [b for b in response.json()["blocks"] if b["type"] == "notice"]
    source_notices = [b for b in notices if b["payload"].get("section") == "来源"]
    assert len(source_notices) == 1
    expected = chat_module._source_note(has_data=False, sources=[])
    assert source_notices[0]["payload"]["message"] == expected


# --------------------------------------------------------------------------- #
# 5. 其余红线：模型不产块、旧行为不回归
# --------------------------------------------------------------------------- #


def test_ask_still_uses_non_json_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(SNAPSHOT_PAGE)
    client = _patch(tmp_path, monkeypatch, fake)

    _post(client, "/api/page/assistant", {"question": "今天表现？", "context": "x"})

    _, _, _, json_mode = fake.calls[0]
    assert json_mode is False


def test_reply_to_ai_splits_sentences_and_leaves_other_fields_empty() -> None:
    ai = assistant_module.reply_to_ai("第一句。第二句！第三句？")
    assert ai == {
        "narrative": ["第一句。", "第二句！", "第三句？"],
        "highlights": [],
        "risks": [],
        "evidence": [],
    }


def test_structured_context_overlong_is_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    overlong = {"data": {"blob": "长" * (assistant_module.MAX_CONTEXT_CHARS + 1)}}
    response = _post(client, "/api/page/assistant", {"question": "x", "context": overlong})
    assert response.status_code == 422
    assert response.json()["code"] == "context_too_long"


def test_blocks_payload_is_valid_json_serializable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient(SNAPSHOT_PAGE)
    client = _patch(tmp_path, monkeypatch, fake)

    response = _post(client, "/api/page/assistant", {"question": "今天表现？", "context": "x"})

    # 响应可被严格 JSON 往返（前端契约）
    assert json.loads(json.dumps(response.json(), ensure_ascii=False)) == response.json()
