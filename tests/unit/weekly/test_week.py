"""ISO 周标签工具（`weekly/week.py`）：解析、归一、换算、非法即拒。

周标签直接进文件名（`data/weekly/<week>.json`、`state/weekly_checkpoint/<week>.json`），
所以「非法标签必须在拼路径之前被拒」是这里最重要的一条断言。
"""

from __future__ import annotations

from datetime import date

import pytest

from ladderstock.weekly import week as week_module


def test_format_and_normalize_round_trip() -> None:
    assert week_module.format_week(date(2026, 10, 26)) == "2026-W44"
    assert week_module.format_week(date(2026, 11, 1)) == "2026-W44"
    for value in ("2026-W44", "2026-w44", " 2026-W44 ", "2026-10-28", date(2026, 10, 28)):
        assert week_module.normalize_week(value) == "2026-W44"


def test_bounds_days_and_membership() -> None:
    assert week_module.week_bounds("2026-W44") == (date(2026, 10, 26), date(2026, 11, 1))
    assert week_module.week_start("2026-W44") == date(2026, 10, 26)
    assert len(week_module.week_days("2026-W44")) == 7
    # 工作日只到周五：周末没有行情，不该进缺数据清单
    assert [day.isoformat() for day in week_module.weekday_days("2026-W44")] == [
        "2026-10-26",
        "2026-10-27",
        "2026-10-28",
        "2026-10-29",
        "2026-10-30",
    ]
    assert week_module.in_week("2026-W44", date(2026, 11, 1)) is True
    assert week_module.in_week("2026-W44", date(2026, 11, 2)) is False
    assert week_module.parse_week("2026-W44") == (2026, 44)


def test_year_boundary_weeks() -> None:
    """跨年那一周用 ISO 年，不是日历年：2024-12-30 属于 2025-W01。"""
    assert week_module.format_week(date(2024, 12, 30)) == "2025-W01"
    assert week_module.normalize_week("2025-W01") == "2025-W01"
    assert week_module.week_start("2025-W01") == date(2024, 12, 30)


@pytest.mark.parametrize(
    "value",
    [
        "",
        None,
        "   ",
        "2026-W0",
        "2026-W00",
        "2026-W54",
        "W44",
        "2026W44",
        "2026-10-32",
        "../evil",
        "2026-W44/../../etc/passwd",
    ],
)
def test_illegal_labels_raise_before_any_path_is_built(value: object) -> None:
    with pytest.raises(ValueError):
        week_module.normalize_week(value)


def test_year_without_that_week_is_rejected() -> None:
    """2025 年只有 52 周：W53 语法对但日历上不存在，必须拒（否则 API 会去读幽灵文件）。"""
    with pytest.raises(ValueError, match="没有第 53 周"):
        week_module.normalize_week("2025-W53")


def test_current_week_is_injectable() -> None:
    assert week_module.current_week("2026-10-28") == "2026-W44"
    assert week_module.current_week(date(2026, 11, 1)) == "2026-W44"
    with pytest.raises(TypeError):
        week_module.current_week(20261028)
