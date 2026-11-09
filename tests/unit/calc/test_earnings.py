from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from ladderstock.calc.earnings import earnings_reaction, earnings_surprise
from ladderstock.calc.result import BAD_ARGUMENT, DENOMINATOR_NON_POSITIVE, NOT_MEANINGFUL


def _rows(closes: list[float], *, start: date = date(2026, 1, 5)) -> list[dict[str, Any]]:
    return [
        {"date": (start + timedelta(days=index)).isoformat(), "close": close}
        for index, close in enumerate(closes)
    ]


# ---------------------------------------------------------------- 反应日涨跌


def test_after_close_reaction_gives_event_day_and_next_day() -> None:
    rows = _rows([100.0, 110.0, 99.0, 99.0])  # 01-05, 01-06, 01-07, 01-08
    result = earnings_reaction(rows, "2026-01-05", timing="after_close")
    assert result.ok
    assert result.value == pytest.approx(0.1, abs=1e-12)
    event_day = result.details["event_day"]
    assert event_day["date"] == "2026-01-06"
    assert event_day["previous_date"] == "2026-01-05"
    assert event_day["previous_close"] == 100.0
    assert event_day["close"] == 110.0
    assert event_day["change"] == pytest.approx(0.1, abs=1e-12)
    assert result.details["next_day"]["date"] == "2026-01-07"
    assert result.details["next_day"]["change"] == pytest.approx(-0.1, abs=1e-12)


def test_before_open_reaction_uses_event_day_itself() -> None:
    rows = _rows([100.0, 110.0, 99.0])
    result = earnings_reaction(rows, "2026-01-06", timing="before_open")
    assert result.ok
    assert result.details["event_day"]["date"] == "2026-01-06"
    assert result.details["next_day"]["date"] == "2026-01-07"
    assert result.value == pytest.approx(0.1, abs=1e-12)


def test_missing_next_day_is_not_meaningful() -> None:
    rows = _rows([100.0, 110.0, 99.0])
    result = earnings_reaction(rows, "2026-01-06", timing="after_close")
    assert not result.ok and result.reason == NOT_MEANINGFUL
    assert result.details["missing"] == 1
    assert result.details["next_day"] is None
    assert result.details["event_day"]["date"] == "2026-01-07"
    assert "差 1 根" in result.details["message"]


def test_reaction_day_not_reached_is_pending() -> None:
    rows = _rows([100.0, 110.0])
    result = earnings_reaction(rows, "2026-01-06", timing="after_close")
    assert not result.ok and result.reason == NOT_MEANINGFUL
    assert result.details["pending"] is True


def test_event_day_without_previous_close_is_not_meaningful() -> None:
    rows = _rows([100.0, 110.0])
    result = earnings_reaction(rows, "2026-01-05", timing="before_open")
    assert not result.ok and result.reason == NOT_MEANINGFUL
    assert result.details["pending"] is False


def test_invalid_event_inputs_are_bad_argument() -> None:
    rows = _rows([100.0, 110.0])
    assert earnings_reaction(rows, "2026/01/05").reason == BAD_ARGUMENT
    assert earnings_reaction(rows, "2026-01-05", timing="whenever").reason == BAD_ARGUMENT


# ---------------------------------------------------------------- 实际 vs 预期


def test_surprise_gives_difference_and_difference_percent() -> None:
    result = earnings_surprise(110.0, 100.0, unit="亿元")
    assert result.ok
    assert result.value == pytest.approx(0.1, abs=1e-12)
    assert result.details["difference"] == pytest.approx(10.0, abs=1e-12)
    assert result.details["difference_pct"] == pytest.approx(0.1, abs=1e-12)
    assert result.details["unit_in"] == "亿元"


def test_surprise_uses_absolute_base_for_negative_estimate() -> None:
    result = earnings_surprise(90.0, -100.0)
    assert result.ok
    assert result.value == pytest.approx(1.9, abs=1e-12)
    assert result.details["difference"] == pytest.approx(190.0, abs=1e-12)


def test_zero_estimate_is_not_meaningful_but_keeps_difference() -> None:
    result = earnings_surprise(5.0, 0.0)
    assert not result.ok
    assert result.reason == NOT_MEANINGFUL
    assert result.details["not_meaningful_kind"] == DENOMINATOR_NON_POSITIVE
    assert result.details["difference"] == 5.0
    assert "difference_pct" not in result.details


def test_surprise_rejects_non_finite_actual() -> None:
    assert earnings_surprise(float("nan"), 1.0).reason == BAD_ARGUMENT
