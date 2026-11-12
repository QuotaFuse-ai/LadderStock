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

from ladderstock.ai.answer_blocks import MAX_BLOCKS, check_ordering
from ladderstock.ai.answer_blocks import build_blocks as _build_blocks
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
    # 新增只允许这两键：blocks + ai_structured；既有四键一个不少
    assert set(body) == {"reply", "model", "ai", "fetched_at", "blocks", "ai_structured"}
    assert isinstance(body["blocks"], list) and body["blocks"]
    assert body["blocks"][0]["type"] == "conclusion"


def test_string_context_reply_matches_recorded_snapshot() -> None:
    """贴死的快照文案与阶段 A 的 full_answer 装配入口自洽（防有人偷改文案求绿）。"""
    adapted = assistant_module.reply_to_structured(SNAPSHOT_REPLY)
    ai = adapted["ai_structured"]
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


def test_reply_to_structured_splits_sentences_and_leaves_other_fields_empty() -> None:
    adapted = assistant_module.reply_to_structured("第一句。第二句！第三句？")
    assert adapted["ai_structured"] == {
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


# --------------------------------------------------------------------------- #
# 6. `reply` → 最小结构化 `ai` 适配函数（QUOT-581 stream_turn 直接接）
# --------------------------------------------------------------------------- #


def _long_mixed_reply() -> str:
    """长回答：3 条风险 + 1 条亮点 + 20 句正文（句子级切分必然超过块上限）。"""
    return (
        "现价落在区间内，近几日横盘震荡。"
        "风险一：跌破前低。"
        "风险二：量能萎缩。"
        "风险三：板块走弱。"
        "亮点是分红率保持稳定。" + "补充说明一句。" * 20
    )


@pytest.mark.parametrize(
    "reply", [SNAPSHOT_REPLY, SNAPSHOT_SKILLS, SNAPSHOT_PAGE, _long_mixed_reply()]
)
def test_adapter_is_deterministic_field_by_field(reply: str) -> None:
    first = assistant_module.reply_to_structured(reply)
    second = assistant_module.reply_to_structured(reply)

    assert first == second
    assert json.dumps(first, ensure_ascii=False) == json.dumps(second, ensure_ascii=False)
    # 不含时间戳类字段（结构只有四个键）
    assert set(first["ai_structured"]) == {"narrative", "highlights", "risks", "evidence"}


@pytest.mark.parametrize("reply", [SNAPSHOT_REPLY, _long_mixed_reply(), "", "单句没有句号"])
def test_adapter_output_is_directly_consumable_by_build_blocks(reply: str) -> None:
    """适配结果原样喂 `build_blocks`：不报错、不超过块上限、编号有序。"""
    adapted = assistant_module.reply_to_structured(reply)
    build_input = adapted["build_input"]
    assert set(build_input) == {"narrative", "highlights", "risks", "evidence"}
    assert all(isinstance(v, list) for v in build_input.values())

    blocks = _build_blocks({}, build_input, [])
    assert 0 < len(blocks) <= MAX_BLOCKS
    check_ordering(blocks)
    # 字段名不与 581 的 seq / last_seq / completed 冲突
    assert not ({"seq", "last_seq", "completed"} & set(build_input))
    assert not ({"seq", "last_seq", "completed"} & set(adapted["ai_structured"]))


def test_adapter_underivable_fields_are_empty_not_fabricated() -> None:
    """「切不干净」两个真实用例：宁留空，不编造。"""
    # 用例 1：风险写在句中（不以线索词开头）→ 不硬猜成 risks，整句留在 narrative
    mid_risk = "整体横盘。若跌破前低，风险会明显加大。"
    # 用例 2：序号分点（①②③）→ 线条索对不上，全部留 narrative
    enumerated = "①量能温和放大；② 板块联动一般；③ 情绪偏谨慎。"

    out_mid = assistant_module.reply_to_structured(mid_risk)["ai_structured"]
    out_enum = assistant_module.reply_to_structured(enumerated)["ai_structured"]

    assert out_mid == {
        "narrative": ["整体横盘。", "若跌破前低，风险会明显加大。"],
        "highlights": [],
        "risks": [],
        "evidence": [],
    }
    assert out_enum == {
        "narrative": ["①量能温和放大；", "② 板块联动一般；", "③ 情绪偏谨慎。"],
        "highlights": [],
        "risks": [],
        "evidence": [],
    }
    # evidence 恒空：纯文本里无法可靠区分取数记录，出处交给 sources
    assert out_mid["evidence"] == [] and out_enum["evidence"] == []
    # 首句固定进 narrative（结论位），不会被分类逻辑搬走
    risk_headed = "整体横盘。风险方面，若跌破前低会明显加大。成交量没有放大。"
    out_risk = assistant_module.reply_to_structured(risk_headed)["ai_structured"]
    assert out_risk["narrative"] == ["整体横盘。", "成交量没有放大。"]
    assert out_risk["risks"] == ["风险方面，若跌破前低会明显加大。"]


def test_adapter_produces_no_numbers() -> None:
    """适配器不改写正文（数字剥离由 `build_blocks` 统一做），自身不新增任何数值。"""
    adapted = assistant_module.reply_to_structured(SNAPSHOT_REPLY)
    joined = "".join(
        line
        for field in ("narrative", "highlights", "risks")
        for line in adapted["ai_structured"][field]
    )
    assert joined == SNAPSHOT_REPLY
    banner = _build_blocks({}, adapted["build_input"], [])[0]
    assert banner["type"] == "conclusion"
    assert "9.87" not in banner["payload"]["text"]
    assert not any(ch.isdigit() for ch in banner["payload"]["text"])


def test_adapter_makes_zero_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """适配函数纯本地：不构造客户端、不发起任何模型调用（调用次数为 0）。"""
    built: list[str] = []

    def _forbidden(module: str = "x") -> Any:
        built.append(module)
        raise AssertionError("适配函数不得构造 AI 客户端")

    monkeypatch.setattr(assistant_module, "_load_client", _forbidden)

    adapted = assistant_module.reply_to_structured(_long_mixed_reply())

    assert built == []
    assert adapted["ai_structured"]["narrative"]


def test_adapter_does_not_replace_reply_or_ai_echo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """前后对照：`reply` 与 `ai` 对外的值与改造前逐字一致，新结构只旁挂。"""
    fake = _FakeClient(SNAPSHOT_PAGE)
    client = _patch(tmp_path, monkeypatch, fake)

    body = _post(client, "/api/page/assistant", {"question": "今天表现？", "context": "x"}).json()

    assert body["reply"] == SNAPSHOT_PAGE  # 改造前 ask() 的返回就是这个字符串
    # 改造前 `ai` = `_ai_echo(module, model)`；这里是同一函数在同等入参下的回显
    assert body["ai"] == assistant_module._ai_echo(
        assistant_module.DEFAULT_MODULE, "openai:model-a"
    )
    assert set(body["ai"]) == {"model", "source"}
    # 结构化叙述不顶替，只旁挂
    assert body["ai_structured"]["narrative"][0] in body["reply"]
    assert "".join(body["ai_structured"]["narrative"]) == SNAPSHOT_PAGE


def test_adapter_does_not_raise_on_long_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """长回答（句子数远超块上限）走完三路由不 500；块数守上限。"""
    fake = _FakeClient(_long_mixed_reply())
    client = _patch(tmp_path, monkeypatch, fake)

    response = _post(client, "/api/page/assistant", {"question": "细说？", "context": "x"})

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == _long_mixed_reply()
    assert len(body["blocks"]) <= MAX_BLOCKS
    assert body["blocks"][0]["type"] == "conclusion"


# --------------------------------------------------------------------------- #
# 7. `context.history`（18:09 评论落点 A）：可选、只进文本、不进来源
# --------------------------------------------------------------------------- #


def test_history_absent_keeps_user_message_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient(SNAPSHOT_PAGE)
    client = _patch(tmp_path, monkeypatch, fake)
    base = {"question": "今天表现？", "page_key": "watch"}

    _post(client, "/api/page/assistant", {**base, "context": {"page": "watch", "data": {"a": 1}}})
    _post(
        client,
        "/api/page/assistant",
        {**base, "context": {"page": "watch", "data": {"a": 1}, "history": []}},
    )

    assert fake.calls[0] == fake.calls[1]


def test_history_lands_in_context_text_but_not_in_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient(SNAPSHOT_PAGE)
    client = _patch(tmp_path, monkeypatch, fake)

    body = _post(
        client,
        "/api/page/assistant",
        {
            "question": "换个方向接着说？",
            "page_key": "watch",
            "context": {
                "page": "watch",
                "history": [
                    {"role": "user", "text": "上一轮问题", "blocks_seq": [0, 1]},
                    {"role": "assistant", "text": "上一轮半截回答", "blocks_seq": [2, 3]},
                ],
            },
        },
    ).json()

    _, _, user, _ = fake.calls[0]
    assert "【上一轮已生成内容】" in user
    assert "用户：上一轮问题" in user
    assert "助手：上一轮半截回答" in user
    # 历史不进 sources：无来源 notice 照旧
    citations = [b for b in body["blocks"] if b["type"] == "citation"]
    assert citations == []
    source_notices = [
        b for b in body["blocks"] if b["type"] == "notice" and b["payload"].get("section") == "来源"
    ]
    assert len(source_notices) == 1
    assert source_notices[0]["payload"]["message"] == chat_module._source_note(
        has_data=False, sources=[]
    )
    # 历史文字不进 conclusion（结论只取本轮 reply）
    assert "上一轮" not in body["blocks"][0]["payload"]["text"]


def test_history_text_does_not_leak_numbers_into_conclusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient(SNAPSHOT_PAGE)
    client = _patch(tmp_path, monkeypatch, fake)

    body = _post(
        client,
        "/api/page/assistant",
        {
            "question": "接着说？",
            "page_key": "watch",
            "context": {
                "page": "watch",
                "history": [{"role": "assistant", "text": "上一轮提到 9.87 元与 12.5%"}],
            },
        },
    ).json()

    conclusion = body["blocks"][0]["payload"]["text"]
    assert "9.87" not in conclusion and "12.5" not in conclusion
    assert not any(ch.isdigit() for ch in conclusion)
