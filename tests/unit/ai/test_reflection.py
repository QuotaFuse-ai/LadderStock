"""自我反思判据（`ai/reflection.py`）：什么时候该回去补证据。

这一层的价值全在「不该触发时不触发」——每误触发一次就是多跑一轮 agent，
用户也要多等十几秒。所以用例分两组：该补查的四种情形、不该补查的六种情形。
"""

from __future__ import annotations

import pytest

from ladderstock.ai import reflection


@pytest.fixture(autouse=True)
def _clear_switch(monkeypatch):
    monkeypatch.delenv(reflection.ENV_SWITCH, raising=False)


# --------------------------------------------------------------------------- #
# 该补查
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("reply", "reason_head"),
    [
        ("浦发银行现在 10.73 元，比昨天高一些。", "没调用工具"),
        ("今天道琼斯涨了 +0.10%，纳指跌了 -0.91% 左右。", "没调用工具"),
    ],
)
def test_number_claim_without_tools(reply: str, reason_head: str) -> None:
    decision = reflection.decide(question="浦发怎么样", reply=reply, used_tools=False, metrics={})
    assert decision.needed is True
    assert reason_head in decision.reason


def test_data_question_answered_in_data_voice_without_tools() -> None:
    """问的是资金流，答的是「净流入明显」——一个数都没有，但这是数据口吻。"""
    decision = reflection.decide(
        question="今天资金流怎么样",
        reply="主力资金净流入比较明显，换手也放大了。",
        used_tools=False,
        metrics={},
    )
    assert decision.needed is True


# --------------------------------------------------------------------------- #
# 不该补查
# --------------------------------------------------------------------------- #


def test_no_reflection_when_tools_were_used() -> None:
    decision = reflection.decide(
        question="浦发多少钱", reply="10.73 元。", used_tools=True, metrics={}
    )
    assert decision.needed is False
    assert "已调用工具" in decision.reason


def test_no_reflection_when_metrics_present() -> None:
    decision = reflection.decide(
        question="浦发多少钱", reply="10.73 元。", used_tools=False, metrics={"price": 10.73}
    )
    assert decision.needed is False


def test_no_reflection_for_greeting() -> None:
    decision = reflection.decide(
        question="介绍下自己",
        reply="你好，我是 LadderStock 的研究助手，可以帮你查行情、看板块。",
        used_tools=False,
        metrics={},
    )
    assert decision.needed is False


def test_no_reflection_for_concept_question() -> None:
    decision = reflection.decide(
        question="什么是市盈率",
        reply="市盈率是股价与每股收益之比，用来衡量估值高低。",
        used_tools=False,
        metrics={},
    )
    assert decision.needed is False


def test_no_reflection_for_empty_reply() -> None:
    decision = reflection.decide(question="浦发多少钱", reply="", used_tools=False, metrics={})
    assert decision.needed is False


def test_switch_off_disables(monkeypatch) -> None:
    monkeypatch.setenv(reflection.ENV_SWITCH, "0")
    decision = reflection.decide(
        question="浦发多少钱", reply="10.73 元。", used_tools=False, metrics={}
    )
    assert decision.needed is False
    assert "关闭" in decision.reason


# --------------------------------------------------------------------------- #
# 补查提问与替换策略
# --------------------------------------------------------------------------- #


def test_followup_keeps_question_and_states_requirement() -> None:
    text = reflection.followup("浦发多少钱", reason="没调用工具却给了具体价格")
    assert "浦发多少钱" in text
    assert "必须先用工具" in text
    assert "不要给估计值" in text


def test_should_replace_prefers_evidence_bearing_answer() -> None:
    assert reflection.should_replace("大概十块多吧。", "浦发银行现价 10.73 元，涨 3.17%。") is True


def test_should_replace_rejects_empty() -> None:
    assert reflection.should_replace("一段有效回答", "") is False


def test_should_replace_keeps_longer_original() -> None:
    long_first = "这是一段很长的有效回答。" * 10
    assert reflection.should_replace(long_first, "短答案。") is False
