"""F5 翻转条件：从正文抽一行挂到结论卡，正文不重复、不占块数额度。"""

from __future__ import annotations

import pytest

from ladderstock.ai import answer_blocks as ab


BODY = (
    "茅台今天收涨，量能比上周放大，短线偏强。\n"
    "- 均线仍在多头排列\n"
    "翻转条件：收盘跌破 1400 或三季报毛利率低于 89%\n"
)


def test_extract_picks_the_line_and_removes_it() -> None:
    falsifier, body = ab.extract_falsifier(BODY)
    assert falsifier == "收盘跌破 1400 或三季报毛利率低于 89%"
    assert "翻转条件" not in body
    assert "收盘跌破" not in body
    # 其余正文原样保留，不做二次加工
    assert "均线仍在多头排列" in body


@pytest.mark.parametrize(
    "line",
    [
        "翻转条件：跌破 1400 元",
        "- 翻转条件：跌破 1400 元",
        "**翻转条件**：跌破 1400 元",
        "证伪条件: 跌破 1400 元",
        "  失效条件：跌破 1400 元  ",
        "> 翻转条件：跌破 1400 元",
    ],
)
def test_marker_variants(line: str) -> None:
    falsifier, body = ab.extract_falsifier(line)
    assert falsifier == "跌破 1400 元"
    assert body == ""


def test_no_marker_returns_original() -> None:
    falsifier, body = ab.extract_falsifier("今天涨了。\n不写条件。")
    assert falsifier is None
    assert body == "今天涨了。\n不写条件。"


def test_empty_input() -> None:
    assert ab.extract_falsifier("") == (None, "")


def test_only_first_condition_is_kept() -> None:
    text = "翻转条件：A\n正文\n翻转条件：B"
    falsifier, body = ab.extract_falsifier(text)
    assert falsifier == "A"
    assert body == "正文"


def test_condition_may_carry_numbers() -> None:
    """翻转条件是「条件」不是「结论」：红线 4 只约束 conclusion.text。"""
    payload = ab.validate_conclusion(
        {"text": "短线偏强", "tone": "positive", "falsifier": "跌破 1400 元"}
    )
    assert payload["falsifier"] == "跌破 1400 元"


def test_empty_falsifier_is_dropped() -> None:
    payload = ab.validate_conclusion({"text": "中性", "tone": "neutral", "falsifier": "  "})
    assert "falsifier" not in payload


def test_build_blocks_attaches_falsifier_without_extra_block() -> None:
    blocks = ab.build_blocks(
        {},
        {
            "narrative": ["短线偏强", "量能放大"],
            "highlights": [],
            "risks": [],
            "evidence": [],
        },
        [],
        falsifier="跌破 1400 元",
    )
    conclusion = blocks[0]
    assert conclusion["type"] == ab.BLOCK_CONCLUSION
    assert conclusion["payload"]["falsifier"] == "跌破 1400 元"
    # 结论正文照旧剥数字；翻转条件不在正文里
    assert "1400" not in conclusion["payload"]["text"]
    # 块数没被这一句吃掉：仍是结论 + 缺失提示，不含额外块
    assert all(block["type"] != ab.BLOCK_CONCLUSION or block is conclusion for block in blocks)


def test_build_blocks_without_falsifier_has_no_key() -> None:
    blocks = ab.build_blocks(
        {},
        {"narrative": ["短线偏强"], "highlights": [], "risks": [], "evidence": []},
        [],
    )
    assert "falsifier" not in blocks[0]["payload"]
