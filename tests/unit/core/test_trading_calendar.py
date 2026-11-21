"""交易日历：周末 + 可配置节假日（缺文件只按周末判，不假装知道节假日）。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ladderstock.core.trading_calendar import (
    STATUS_HOLIDAY,
    STATUS_TRADING,
    STATUS_WEEKEND,
    holidays,
    is_trading_day,
    market_status,
)


def test_weekend_is_closed(tmp_path: Path) -> None:
    assert market_status(date(2026, 11, 21), tmp_path) == STATUS_WEEKEND
    assert market_status(date(2026, 11, 22), tmp_path) == STATUS_WEEKEND
    assert is_trading_day(date(2026, 11, 21), tmp_path) is False


def test_weekday_without_calendar_file_is_trading(tmp_path: Path) -> None:
    """没有节假日表时按工作日判交易日：宁可暴露真实缺口，也不猜休市。"""
    assert holidays(tmp_path) == set()
    assert market_status(date(2026, 11, 20), tmp_path) == STATUS_TRADING
    assert is_trading_day(date(2026, 11, 20), tmp_path) is True


def test_holiday_file_marks_weekday_closed(tmp_path: Path) -> None:
    (tmp_path / "trading_holidays.json").write_text(
        json.dumps({"holidays": ["2026-10-01", "2026-10-02"]}), encoding="utf-8"
    )

    assert market_status(date(2026, 10, 1), tmp_path) == STATUS_HOLIDAY
    assert is_trading_day(date(2026, 10, 1), tmp_path) is False
    # 表里没写的普通工作日仍是交易日
    assert market_status(date(2026, 10, 9), tmp_path) == STATUS_TRADING


def test_broken_or_odd_holiday_file_degrades_to_weekend_only(tmp_path: Path) -> None:
    (tmp_path / "trading_holidays.json").write_text("{ not json", encoding="utf-8")
    assert holidays(tmp_path) == set()
    assert market_status(date(2026, 10, 1), tmp_path) == STATUS_TRADING

    # 裸数组也接受；非法日期条目被跳过，不影响其它条目
    (tmp_path / "trading_holidays.json").write_text(
        json.dumps(["2026-10-01", "not-a-date"]), encoding="utf-8"
    )
    assert holidays(tmp_path) == {date(2026, 10, 1)}
