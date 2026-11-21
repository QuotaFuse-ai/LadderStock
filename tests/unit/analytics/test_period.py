"""周期报告单测：周期解析（月/季/年 + 缺省上期 + 非法格式）与空库事实包。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ladderstock.analytics import period
from ladderstock.errors import EXIT_USAGE, LadderStockError


def test_resolve_month_and_quarter_and_year() -> None:
    start, end, label, tag = period.resolve_period("month", "2026-11")
    assert (start, end, tag) == (date(2026, 11, 1), date(2026, 11, 30), "2026-11")
    assert label == "2026-11"

    start, end, _label, tag = period.resolve_period("month", "2026-02")
    assert (start, end) == (date(2026, 2, 1), date(2026, 2, 28))
    assert tag == "2026-02"

    start, end, _label, tag = period.resolve_period("quarter", "2026-Q4")
    assert (start, end) == (date(2026, 10, 1), date(2026, 12, 31))
    assert tag == "2026-Q4"

    start, end, _label, tag = period.resolve_period("quarter", "2026-Q1")
    assert (start, end) == (date(2026, 1, 1), date(2026, 3, 31))

    start, end, _label, tag = period.resolve_period("year", "2025")
    assert (start, end) == (date(2025, 1, 1), date(2025, 12, 31))
    assert tag == "2025"


def test_resolve_defaults_to_previous_complete_period() -> None:
    today = date.today()
    start, end, _label, _tag = period.resolve_period("month", None)
    # 上一个月：end 是本月 1 日的前一天，start 是上月 1 日
    assert end == today.replace(day=1) - period.timedelta(days=1)
    assert start == end.replace(day=1)

    start, end, _label, _tag = period.resolve_period("year", None)
    assert end == date(today.year - 1, 12, 31)
    assert start == date(today.year - 1, 1, 1)


def test_resolve_rejects_illegal_input() -> None:
    cases = (
        ("week", "2026-11"),
        ("month", "2026-13"),
        ("month", "2026/11"),
        ("quarter", "2026-Q5"),
        ("year", "26"),
    )
    for kind, of in cases:
        with pytest.raises(LadderStockError) as err:
            period.resolve_period(kind, of)
        assert err.value.code == EXIT_USAGE


def test_build_report_on_empty_data_is_honest(tmp_path: Path) -> None:
    report = period.build_report("month", "2026-11", root=tmp_path)
    assert report["ok"] is True
    assert report["trades_available"] is False
    assert report["portfolio"]["realized_count"] == 0
    assert report["portfolio"]["win_rate"] is None
    assert "没有已实现交易" in report["portfolio"]["reason"]
    assert report["attribution"] == []
    # 市场情绪缺复盘产物：available=False + 原因，不补数
    assert report["market"]["available"] is False
    assert report["market"]["reason"]

    text = period.render_markdown(report)
    assert "2026-11 月报" in text
    assert "本期没有已实现交易" in text
    assert "不可用" in text  # 市场情绪一节如实说明


def test_write_report_lands_json_and_md(tmp_path: Path) -> None:
    written = period.write_report("quarter", "2026-Q3", root=tmp_path)
    json_path = Path(written["json_path"])
    md_path = Path(written["md_path"])
    assert json_path.is_file() and md_path.is_file()
    assert json_path.parent.name == "quarter"
    assert json_path.name == "2026-Q3.json"
    assert "2026-Q3 季报" in md_path.read_text(encoding="utf-8")