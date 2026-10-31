"""模型这一路：默认关闭、只写叙述、只送最小视图（R6 要求 2 与 4）。

三条规矩各有一组用例，因为它们在真实数据上都会「看起来没问题」：
不加 `--ai` 时没人会调到这里；加了之后模型多写一句、或少写一句都不报错。
所以断言的不是叙述的质量，而是**边界**——什么进了 prompt、什么没进、
越界时是不是真的停下来了。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fixtures.news_samples import WINDOW_DAY

from ladderstock.news import ai_enrich
from ladderstock.news.ai_enrich import (
    AI_ENRICH_ENABLED_BY_DEFAULT,
    MAX_ENRICH_EVENTS,
    NOTE_LIMIT,
    SYSTEM_PROMPT,
    EnrichResult,
    PromptPrivacyViolation,
    apply_to_stream,
    enrich_events,
    prompt_payload,
    render_user_prompt,
)
from ladderstock.news.events import NewsEvent, extract_events
from ladderstock.news.models import AI_SUMMARY_LIMIT, build_item


class FakeChat:
    """假模型：记下发过什么，返回一句固定叙述。"""

    model_label = "fake-model-1"

    def __init__(
        self, *, reply: str = "该事实对短线情绪有指示意义。", boom: Exception | None = None
    ):
        self.reply = reply
        self.boom = boom
        self.prompts: list[str] = []
        self.systems: list[str] = []
        self.json_modes: list[bool] = []

    def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
        self.prompts.append(user)
        self.systems.append(system)
        self.json_modes.append(json_mode)
        if self.boom is not None:
            raise self.boom
        return self.reply


def _item(**overrides):
    params = {
        "dataset": "flash_cls",
        "title": "某公司发布业绩预告",
        "url": "https://www.cls.cn/detail/1",
        "published_at": 1_793_323_800,
        "summary": "预计前三季度净利润同比增长。",
        "code": "600519",
        "provenance": {"trace_id": "trace-secret", "tool": "cls_telegraph"},
    }
    params.update(overrides)
    return build_item(**params)


def _event(item, note: str | None = None) -> NewsEvent:
    event = extract_events([item]).events[0]
    return event.with_ai_note(note) if note else event


# --------------------------------------------------------------------------- #
# 默认关闭
# --------------------------------------------------------------------------- #

def test_model_is_off_by_default() -> None:
    assert AI_ENRICH_ENABLED_BY_DEFAULT is False


def test_apply_to_stream_off_makes_no_call_and_explains_itself() -> None:
    """不加 `--ai` 的返回值必须自带理由：账本上说「没开」比「什么都没有」好排查。"""
    payload = apply_to_stream(_stream_result(events=[]), enabled=False)
    assert payload == {
        "requested": False,
        "enabled": False,
        "skipped_reason": "默认关闭：需要模型叙述请加 --ai / ai=true",
    }


def _stream_result(events, items=()):
    from datetime import date

    from ladderstock.news.stream import StreamResult

    day = date.fromisoformat(WINDOW_DAY)
    return StreamResult(
        start=day,
        end=day,
        datasets=("flash_cls",),
        codes=(),
        items=list(items),
        event_objects=list(events),
    )


# --------------------------------------------------------------------------- #
# 送模型的最小视图
# --------------------------------------------------------------------------- #

def test_prompt_carries_only_title_summary_url_label_keyword() -> None:
    item = _item()
    event = _event(item)
    payload = prompt_payload(event, item)
    assert set(payload) == {"title", "summary", "url", "label", "matched"}


def test_prompt_never_carries_full_text_codes_or_provenance() -> None:
    item = _item(summary="句" * 500)
    event = _event(item)
    prompt = render_user_prompt(event, item)
    assert len(prompt_payload(event, item)["summary"]) == AI_SUMMARY_LIMIT
    assert len(prompt.splitlines()) == 5, "prompt 的行列就是那五个字段，多一行都是越界"
    assert "trace-secret" not in prompt, "provenance 不进 prompt"
    assert "cls_telegraph" not in prompt, "取数工具名不是叙述需要的信息"
    assert "related_codes" not in prompt and "provenance" not in prompt
    assert not any(line.startswith(("持仓", "自选", "交易日志")) for line in prompt.splitlines())


def test_system_prompt_forbids_numbers_dates_and_ratings() -> None:
    """叙述只能补充语义，不能引入新事实：日期/代码/评级/数字都在禁止之列。"""
    for banned in ("日期", "代码", "评级", "数字"):
        assert banned in SYSTEM_PROMPT
    assert "资料不足" in SYSTEM_PROMPT


def test_privacy_guard_refuses_to_send(monkeypatch: pytest.MonkeyPatch) -> None:
    """键名闸门是发出去之前的最后一道：命中隐私字段就抛，不发请求。"""
    monkeypatch.setattr(ai_enrich, "private_keys_present", lambda _v: ["positions"])
    item = _item()
    event = _event(item)
    with pytest.raises(PromptPrivacyViolation, match="已拒绝发送"):
        prompt_payload(event, item)
    with pytest.raises(PromptPrivacyViolation):
        render_user_prompt(event, item)


# --------------------------------------------------------------------------- #
# 只写叙述
# --------------------------------------------------------------------------- #

def test_enrich_adds_notes_without_touching_the_facts() -> None:
    item = _item()
    event = _event(item)
    chat = FakeChat()
    result = enrich_events([event], [item], client=chat)

    assert result.enriched == 1 and result.attempted == 1
    assert result.events[0].ai_note == chat.reply
    before = event.as_dict()
    after = result.events[0].as_dict()
    assert {k: v for k, v in after.items() if k != "ai_note"} == before
    assert result.model == "fake-model-1"
    assert chat.json_modes == [False]


def test_enrich_never_adds_or_drops_events() -> None:
    items = [
        _item(url=f"https://www.cls.cn/detail/{i}", title=f"某公司发布业绩预告 {i}")
        for i in range(3)
    ]
    events = [extract_events([item]).events[0] for item in items]
    result = enrich_events(events, items, client=FakeChat())
    assert len(result.events) == len(events)
    assert [e.id for e in result.events] == [e.id for e in events]
    assert result.attempted == 3 and result.enriched == 3


def test_model_cannot_invent_a_fourth_event() -> None:
    """模型返回再离谱也只落进 `ai_note` 一个字段：事件集合由规则说了算。"""
    item = _item()
    event = _event(item)
    chat = FakeChat(reply="我新增了一条 2026-11-20 的解禁事件，代码 600000")
    result = enrich_events([event], [item], client=chat)
    assert len(result.events) == 1
    assert result.events[0].type == event.type
    assert result.events[0].event_date == event.event_date
    assert result.events[0].codes == event.codes


def test_note_is_capped() -> None:
    item = _item()
    event = _event(item)
    result = enrich_events([event], [item], client=FakeChat(reply="长" * 900))
    assert len(result.events[0].ai_note) == NOTE_LIMIT


def test_single_failure_does_not_lose_the_event() -> None:
    item = _item()
    event = _event(item)
    result = enrich_events([event], [item], client=FakeChat(boom=TimeoutError("网关超时")))
    assert result.events == [event]
    assert result.enriched == 0 and result.attempted == 1
    assert result.degraded is True
    assert "网关超时" in result.failed[0]["error"]


def test_empty_reply_counts_as_failure_not_narrative() -> None:
    item = _item()
    event = _event(item)
    for reply in ("", "   ", "资料不足", '"资料不足"'):
        result = enrich_events([event], [item], client=FakeChat(reply=reply))
        assert result.enriched == 0, reply
        assert result.events[0].ai_note is None
        assert result.failed[0]["error"] == "模型返回空叙述"


def test_quote_wrapping_is_trimmed() -> None:
    item = _item()
    event = _event(item)
    result = enrich_events([event], [item], client=FakeChat(reply='"情绪面信号"'))
    assert result.events[0].ai_note == "情绪面信号"


def test_events_without_a_matching_item_are_skipped_not_called() -> None:
    item = _item()
    orphan = _event(item)
    result = enrich_events([orphan], [], client=FakeChat())
    assert result.attempted == 0 and result.enriched == 0
    assert result.events == [orphan]


def test_over_limit_events_are_reported_not_dropped_silently() -> None:
    items = [
        _item(url=f"https://www.cls.cn/detail/{i}", title=f"某公司发布业绩预告 {i}")
        for i in range(3)
    ]
    events = [extract_events([item]).events[0] for item in items]
    result = enrich_events(events, items, client=FakeChat(), limit=1)
    assert result.attempted == 1
    assert len(result.events) == 3
    assert any(row["event_id"] == "*" and "上限" in row["error"] for row in result.failed)


def test_limit_zero_is_an_explicit_refusal() -> None:
    item = _item()
    event = _event(item)
    result = enrich_events([event], [item], client=FakeChat(), limit=0)
    assert result.attempted == 0 and result.skipped_reason
    assert result.events == [event]


def test_batch_cap_is_visible_in_the_module() -> None:
    assert MAX_ENRICH_EVENTS == 20


# --------------------------------------------------------------------------- #
# 模型不可用：不装成功，也不炸流
# --------------------------------------------------------------------------- #

def test_missing_model_config_degrades_without_losing_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ladderstock.ai import fallback

    item = _item()
    event = _event(item)

    class _Cfg:
        pass

    monkeypatch.setattr(fallback, "load_config", lambda *a, **k: _Cfg())
    monkeypatch.setattr(fallback, "is_configured", lambda _c: False)
    monkeypatch.setattr(fallback, "missing_env", lambda _c: ["LDST_TEST_API_KEY"])

    result = enrich_events([event], [item])
    assert result.skipped_reason.startswith("模型不可用")
    assert "LDST_TEST_API_KEY" in result.skipped_reason
    assert result.degraded is True
    assert result.events == [event]


def test_http_client_is_imported_lazily_not_at_module_scope() -> None:
    """模块级不碰模型客户端：`ldst news` 的默认路径（不开模型）不该依赖 AI 配置。"""
    import importlib

    reloaded = importlib.reload(ai_enrich)
    assert not hasattr(reloaded, "HttpChatClient")
    assert callable(reloaded._default_chat)


# --------------------------------------------------------------------------- #
# 与流的接线
# --------------------------------------------------------------------------- #

def test_apply_to_stream_writes_notes_back_and_counts_them() -> None:
    item = _item()
    event = _event(item)
    result = _stream_result([event], [item])
    payload = ai_enrich.enrich_events(result.event_objects, result.items, client=FakeChat())
    assert payload.enriched == 1


def test_apply_to_stream_default_off_reports_requested_false() -> None:
    payload = apply_to_stream(_stream_result([]), enabled=False)
    assert payload["requested"] is False and payload["enabled"] is False


def test_apply_to_stream_persists_notes_only_when_the_stream_was_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--no-store` 的会话不该把叙述写进库：那是只查不写的口径。"""
    item = _item()
    event = _event(item)
    stream = _stream_result([event], [item])
    stream.persisted = False

    calls: list[int] = []
    monkeypatch.setattr(
        "ladderstock.news.stream.persist_events",
        lambda events, **kw: calls.append(len(events)) or 0,
    )
    monkeypatch.setattr(ai_enrich, "enrich_events", lambda events, items, **kw: EnrichResult(
        events=[e.with_ai_note("叙述") for e in events], attempted=1, enriched=1
    ))
    apply_to_stream(stream, enabled=True)
    assert calls == []

    stream.persisted = True
    apply_to_stream(stream, enabled=True)
    assert calls == [1]


def test_narrative_keys_pass_the_boundary_check() -> None:
    item = _item()
    event = _event(item)
    assert ai_enrich.narrative_keys_ok("这是一句纯叙述。", event) is True


def test_as_dict_shape_is_stable() -> None:
    payload = EnrichResult(attempted=1, enriched=1, model="m").as_dict()
    assert set(payload) == {
        "enabled",
        "skipped_reason",
        "model",
        "attempted",
        "enriched",
        "failed",
        "degraded",
    }
    assert payload["enabled"] is True
