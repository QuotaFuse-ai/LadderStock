"""持仓当日盈亏的唯一算法 `daily_change_map()`：close(T) − close(T−1)，只读本机缓存。

QUOT-380 拍板口径 4：不改持仓现价口径（`core/quotes.py` 仍然不联网），
停牌日显 `missing` + 原因，**绝不**退回到更早交易日假装那是当日盈亏。
"""

from __future__ import annotations

from datetime import date

import pytest

from ladderstock.core import quotes
from ladderstock.data import duckdb_store
from ladderstock.errors import LadderStockError


def _seed(root, rows: list[dict]) -> None:
    duckdb_store.write_dataset("daily_k", rows, source="tickhub_kline", root=root)


def test_change_is_close_today_minus_close_previous_day(tmp_path) -> None:
    _seed(
        tmp_path,
        [
            {"code": "600000", "trade_date": "2026-10-23", "close": 10.0},
            {"code": "600000", "trade_date": "2026-10-26", "close": 10.5},
            {"code": "000001", "trade_date": "2026-10-23", "close": 11.0},
            {"code": "000001", "trade_date": "2026-10-26", "close": 10.4},
        ],
    )

    daily, status = quotes.daily_change_map(["600000", "000001"], "2026-10-26", root=tmp_path)

    assert status["date"] == "2026-10-26"
    assert status["dataset"] == "daily_k"
    assert status["reason"] is None
    # 周一的上一条是上周五，中间那个没缓存的周末不算一天
    assert status["prev_date"] == "2026-10-23"
    assert daily["600000"] == {
        "status": "ok",
        "close": 10.5,
        "prev_close": 10.0,
        "change": 0.5,
        "reason": None,
    }
    assert daily["000001"]["change"] == -0.6


def test_suspended_code_is_missing_and_never_falls_back_to_an_earlier_day(tmp_path) -> None:
    """`600000` 在 T 日没有行（停牌）：如实 `missing`，不拿 10-23 那天的价当今天。"""
    _seed(
        tmp_path,
        [
            {"code": "600000", "trade_date": "2026-10-23", "close": 10.0},
            {"code": "600000", "trade_date": "2026-10-26", "close": 10.5},
            {"code": "000001", "trade_date": "2026-10-23", "close": 11.0},
        ],
    )

    daily, status = quotes.daily_change_map(["000001"], "2026-10-26", root=tmp_path)

    assert status["date"] == "2026-10-26"
    entry = daily["000001"]
    assert entry["status"] == "missing"
    assert entry["close"] is None
    assert entry["prev_close"] == 11.0
    assert entry["change"] is None
    assert "000001" in entry["reason"]
    assert "2026-10-26" in entry["reason"]


def test_code_with_no_previous_record_cannot_be_a_daily_change(tmp_path) -> None:
    _seed(tmp_path, [{"code": "600519", "trade_date": "2026-10-26", "close": 100.0}])

    daily, status = quotes.daily_change_map(["600519"], "2026-10-26", root=tmp_path)

    assert status["prev_date"] is None
    assert status["reason"]
    entry = daily["600519"]
    assert entry["status"] == "missing"
    assert entry["close"] == 100.0
    assert entry["prev_close"] is None
    assert entry["change"] is None
    assert "close(T) − close(T−1)" in entry["reason"]


def test_empty_cache_says_which_command_to_run(tmp_path) -> None:
    daily, status = quotes.daily_change_map(["600000"], None, root=tmp_path)

    assert daily == {}
    assert status["date"] is None
    assert "ldst sync --dataset daily_k" in status["reason"]


def test_no_positions_no_work(tmp_path) -> None:
    daily, status = quotes.daily_change_map([], None, root=tmp_path)

    assert daily == {}
    assert "没有持仓" in status["reason"]


def test_default_day_is_the_latest_cached_one(tmp_path) -> None:
    _seed(
        tmp_path,
        [
            {"code": "600000", "trade_date": "2026-10-23", "close": 10.0},
            {"code": "600000", "trade_date": "2026-10-26", "close": 10.5},
        ],
    )

    daily, status = quotes.daily_change_map(["600000"], None, root=tmp_path)

    assert status["date"] == "2026-10-26"
    assert daily["600000"]["change"] == 0.5


def test_only_requested_codes_come_back(tmp_path) -> None:
    _seed(
        tmp_path,
        [
            {"code": "600000", "trade_date": "2026-10-23", "close": 10.0},
            {"code": "600000", "trade_date": "2026-10-26", "close": 10.5},
            {"code": "000001", "trade_date": "2026-10-23", "close": 11.0},
            {"code": "000001", "trade_date": "2026-10-26", "close": 12.0},
        ],
    )

    daily, _ = quotes.daily_change_map(["600000"], None, root=tmp_path)

    assert list(daily) == ["600000"]


def test_bad_day_is_a_usage_error(tmp_path) -> None:
    with pytest.raises(LadderStockError):
        quotes.daily_change_map(["600000"], "2026/10/26", root=tmp_path)


def test_previous_day_lookback_never_returns_none_day(tmp_path) -> None:
    # 没有任何分区时不回看无限远，直接 None（不猜节日）
    assert quotes._previous_cached_day(date(2026, 10, 26), root=tmp_path) is None
