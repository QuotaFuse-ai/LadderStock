from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from ladderstock.calc.indicators import (
    DEFAULT_MA_PERIODS,
    KlineInputError,
    atr,
    bollinger,
    chip_concentration,
    macd,
    moving_average,
    parse_kline_rows,
    rsi,
)
from ladderstock.calc.result import BAD_ARGUMENT, DENOMINATOR_NON_POSITIVE, NOT_MEANINGFUL


def _rows(
    closes: list[float],
    *,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
) -> list[dict[str, Any]]:
    start = date(2026, 1, 1)
    highs = highs if highs is not None else [close + 0.5 for close in closes]
    lows = lows if lows is not None else [close - 0.5 for close in closes]
    volumes = volumes if volumes is not None else [1.0] * len(closes)
    return [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
        for index, (close, high, low, volume) in enumerate(
            zip(closes, highs, lows, volumes, strict=True)
        )
    ]


# ---------------------------------------------------------------- 均线


def test_ma5_hand_calculation() -> None:
    result = moving_average(_rows([10.0, 11.0, 12.0, 13.0, 14.0]), 5)
    assert result.ok
    assert result.value == 12.0
    assert result.details["period"] == 5


def test_ma_five_ten_twenty_sixty_on_ramp() -> None:
    closes = [float(value) for value in range(1, 61)]
    rows = _rows(closes)
    expected = {5: 58.0, 10: 55.5, 20: 50.5, 60: 30.5}
    assert set(DEFAULT_MA_PERIODS) == set(expected)
    for period, value in expected.items():
        result = moving_average(rows, period)
        assert result.ok, (period, result.details)
        assert result.value == value, period


def test_ma_insufficient_sample_reports_missing_bars() -> None:
    result = moving_average(_rows([10.0, 11.0, 12.0, 13.0]), 5)
    assert not result.ok
    assert result.reason == NOT_MEANINGFUL
    assert result.value is None
    assert result.details["required"] == 5
    assert result.details["actual"] == 4
    assert result.details["missing"] == 1
    assert result.details["message"] == "样本不足：需 5 根，实得 4 根，差 1 根"


def test_invalid_rows_are_bad_argument() -> None:
    bad_price = _rows([10.0, 10.0, 10.0, 10.0, 0.0])
    assert moving_average(bad_price, 5).reason == BAD_ARGUMENT

    duplicate = _rows([10.0, 11.0, 12.0, 13.0, 14.0])
    duplicate[1]["date"] = duplicate[0]["date"]
    assert moving_average(duplicate, 5).reason == BAD_ARGUMENT

    with pytest.raises(KlineInputError):
        parse_kline_rows([{"date": "2026-01-01", "close": float("nan")}], ("close",))


def test_data_fingerprint_is_order_independent() -> None:
    rows = _rows([10.0, 11.0, 12.0, 13.0, 14.0])
    forward = moving_average(rows, 5)
    backward = moving_average(list(reversed(rows)), 5)
    assert forward.details["data_fp"] == backward.details["data_fp"]


# ---------------------------------------------------------------- RSI


def test_rsi14_hand_calculation() -> None:
    # 8 个 +1 后 6 个 -1：平均涨幅 8/14、平均跌幅 6/14，
    # RSI = 100 - 100 / (1 + 8/6) = 400/7。
    closes = [float(100 + value) for value in range(0, 9)] + [
        float(value) for value in (107, 106, 105, 104, 103, 102)
    ]
    result = rsi(_rows(closes), 14)
    assert result.ok
    assert result.value == pytest.approx(400.0 / 7.0, abs=1e-12)
    assert result.details["average_gain"] == pytest.approx(8.0 / 14.0, abs=1e-12)
    assert result.details["average_loss"] == pytest.approx(6.0 / 14.0, abs=1e-12)


def test_rsi_all_gains_is_100() -> None:
    closes = [float(value) for value in range(100, 115)]
    result = rsi(_rows(closes), 14)
    assert result.ok
    assert result.value == 100.0


def test_rsi_insufficient_sample() -> None:
    closes = [float(value) for value in range(100, 114)]
    result = rsi(_rows(closes), 14)
    assert not result.ok and result.reason == NOT_MEANINGFUL
    assert result.details["required"] == 15
    assert result.details["actual"] == 14
    assert result.details["message"] == "样本不足：需 15 根，实得 14 根，差 1 根"


# ---------------------------------------------------------------- MACD


def test_macd12_26_9_with_first_value_ema_seed() -> None:
    # 前 25 根恒为 10，第 26 根跳到 20：
    # EMA12 = 150/13，EMA26 = 290/27，dif = 280/351；
    # dea = 0.2 × dif = 56/351；hist = 2 × (280-56)/351 = 448/351。
    closes = [10.0] * 25 + [20.0]
    result = macd(_rows(closes), 12, 26, 9)
    assert result.ok
    assert result.details["dif"] == pytest.approx(280.0 / 351.0, abs=1e-12)
    assert result.details["dea"] == pytest.approx(56.0 / 351.0, abs=1e-12)
    assert result.details["hist"] == pytest.approx(448.0 / 351.0, abs=1e-12)
    assert result.value == pytest.approx(448.0 / 351.0, abs=1e-12)
    assert result.details["ema_seed"] == "first_value"


def test_macd_with_18_bars_is_not_meaningful_and_says_how_many_missing() -> None:
    result = macd(_rows([float(value) for value in range(1, 19)]), 12, 26, 9)
    assert not result.ok
    assert result.reason == NOT_MEANINGFUL
    assert result.value is None
    assert result.details["required"] == 26
    assert result.details["actual"] == 18
    assert result.details["missing"] == 8
    assert result.details["message"] == "样本不足：需 26 根，实得 18 根，差 8 根"


def test_macd_rejects_fast_not_below_slow() -> None:
    result = macd(_rows([10.0] * 30), 26, 12, 9)
    assert result.reason == BAD_ARGUMENT


# ---------------------------------------------------------------- ATR


def test_atr14_hand_calculation() -> None:
    rows = _rows(
        [10.0] * 15,
        highs=[11.0] * 15,
        lows=[10.0] * 15,
    )
    result = atr(rows, 14)
    assert result.ok
    assert result.value == 1.0


def test_atr14_uses_previous_close_for_gaps() -> None:
    closes = [10.0] * 15
    highs = [11.0] * 14 + [12.0]
    lows = [10.0] * 15
    result = atr(_rows(closes, highs=highs, lows=lows), 14)
    assert result.ok
    # 13 个 TR=1，最后一个 TR=max(2, |12-10|, 0)=2 → (13 + 2) / 14。
    assert result.value == pytest.approx(15.0 / 14.0, abs=1e-12)


def test_atr_insufficient_sample() -> None:
    result = atr(_rows([10.0] * 14, highs=[11.0] * 14, lows=[10.0] * 14), 14)
    assert not result.ok and result.reason == NOT_MEANINGFUL
    assert result.details["required"] == 15
    assert result.details["missing"] == 1


# ---------------------------------------------------------------- BOLL


def test_bollinger20_population_std() -> None:
    closes = [9.0, 11.0] * 10
    result = bollinger(_rows(closes), 20, 2.0)
    assert result.ok
    assert result.value == 10.0
    assert result.details["std"] == 1.0
    assert result.details["upper"] == 12.0
    assert result.details["lower"] == 8.0
    assert result.details["bandwidth"] == pytest.approx(0.4, abs=1e-12)


def test_bollinger_insufficient_sample() -> None:
    result = bollinger(_rows([10.0] * 19), 20, 2.0)
    assert not result.ok and result.reason == NOT_MEANINGFUL
    assert result.details["required"] == 20
    assert result.details["actual"] == 19


# ---------------------------------------------------------------- 筹码集中度


def test_chip_concentration_volume_weighted_quantiles() -> None:
    # 9 根 10 元 + 11 根 20 元，各 1 手：P15=10、P85=20 → (20-10)/(20+10)=1/3。
    rows = _rows([10.0] * 9 + [20.0] * 11)
    result = chip_concentration(rows)
    assert result.ok
    assert result.value == pytest.approx(1.0 / 3.0, abs=1e-12)
    assert result.details["low_price"] == 10.0
    assert result.details["high_price"] == 20.0
    assert result.details["vwap"] == 15.5


def test_chip_concentration_single_price_is_zero() -> None:
    result = chip_concentration(_rows([10.0] * 20))
    assert result.ok
    assert result.value == 0.0


def test_chip_concentration_insufficient_sample() -> None:
    result = chip_concentration(_rows([10.0] * 19))
    assert not result.ok and result.reason == NOT_MEANINGFUL
    assert result.details["required"] == 20
    assert result.details["missing"] == 1


def test_chip_concentration_zero_volume_is_not_meaningful() -> None:
    result = chip_concentration(_rows([10.0] * 20, volumes=[0.0] * 20))
    assert not result.ok
    assert result.details["not_meaningful_kind"] == DENOMINATOR_NON_POSITIVE
