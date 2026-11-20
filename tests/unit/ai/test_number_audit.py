"""F6 数字自检：只查小数/百分比，对不上只标注不删改。"""

from __future__ import annotations

import pytest

from ladderstock.ai import number_audit
from ladderstock.ai import answer_blocks as ab


METRICS = {
    "close": {"value": 1485.75, "unit": "元", "source": "tickhub-cn", "as_of": "2026-11-20"},
    "pct_chg": {"value": 0.0175, "unit": "", "source": "tickhub-cn", "as_of": "2026-11-20"},
    "amount": {"value": 1.4808e9, "unit": "元", "source": "tickhub-cn", "as_of": "2026-11-20"},
}


def test_close_price_matches() -> None:
    result = number_audit.audit("收盘 1485.75 元，量能平稳。", METRICS)
    assert result["checked"] == 1
    assert result["matched"] == 1
    assert result["unmatched"] == []


def test_percent_matches_fraction() -> None:
    """模型写 1.75%，metric 存 0.0175，同一个数。"""
    result = number_audit.audit("今日涨 1.75%，表现偏强。", METRICS)
    assert result["matched"] == 1
    assert result["unmatched"] == []


def test_yi_scale_matches_raw() -> None:
    result = number_audit.audit("成交额约 14.81 亿。", METRICS)
    assert result["unmatched"] == []


def test_unknown_decimal_is_flagged() -> None:
    result = number_audit.audit("收盘 1485.75 元，市盈率约 30.42 倍。", METRICS)
    assert result["checked"] == 2
    assert result["matched"] == 1
    assert result["unmatched"] == ["30.42"]


@pytest.mark.parametrize(
    "text",
    [
        "2026 年三季报披露",  # 年份
        "11-20 收盘",  # 月日
        "600519 的行情",  # 代码：整数，不参与
        "第 3 季度毛利率",  # 期次
        "近 5 日走势",  # 序数
    ],
)
def test_non_data_numbers_are_skipped(text: str) -> None:
    assert number_audit.audit(text, METRICS)["checked"] == 0


def test_no_metrics_means_no_check() -> None:
    result = number_audit.audit("涨 1.75%，收 1485.75。", {})
    assert result == {"checked": 0, "matched": 0, "unmatched": []}


def test_notice_text_names_the_numbers() -> None:
    result = {"checked": 1, "matched": 0, "unmatched": ["30.42"]}
    note = number_audit.notice_text(result)
    assert note is not None
    assert "30.42" in note
    assert number_audit.notice_text({"unmatched": []}) is None


def test_notice_block_allows_numbers_only_for_unverified() -> None:
    payload = ab.validate_notice(
        {"kind": ab.NOTICE_UNVERIFIED, "section": "数字核对", "message": "30.42 没对回本轮取数"}
    )
    assert payload["kind"] == ab.NOTICE_UNVERIFIED
    # 其它 kind 仍按红线 4 拒绝数字
    with pytest.raises(ab.AnswerBlockError):
        ab.validate_notice({"kind": ab.NOTICE_NOTE, "message": "30.42"})
