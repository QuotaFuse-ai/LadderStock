from __future__ import annotations

import math
import random

import pytest

from ladderstock.calc.money import UNIT_TO_YUAN, amount, convert
from ladderstock.calc.result import BAD_ARGUMENT, OK


def test_same_currency_unit_conversion() -> None:
    r = amount(1.5, currency="CNY", unit="亿元", display_unit="万元")
    assert r.ok and r.value == pytest.approx(1.5 * 1e8 / 1e4)
    assert r.unit == "万元"
    assert r.display is not None and "万元" in r.display and "CNY" in r.display


def test_yuan_to_wan_to_yi_roundtrip() -> None:
    r = amount(25_000_000.0, currency="CNY", unit="元", display_unit="亿元")
    assert r.value == pytest.approx(0.25)
    assert r.unit == "亿元"


def test_unit_always_present() -> None:
    r = amount(100.0, currency="HKD", unit="元")
    assert r.ok and r.unit == "元"


# ------------------------------------------------------------------ 跨币种缺汇率


@pytest.mark.parametrize("kwargs", [{}, {"fx_rate": 0.92}, {"fx_unit": "CNY/HKD"}])
def test_cross_currency_missing_fx_is_bad_argument(kwargs: dict) -> None:
    r = amount(100.0, currency="HKD", unit="元", target_currency="CNY", **kwargs)
    assert not r.ok and r.reason == BAD_ARGUMENT
    assert r.value is None


def test_fx_without_target_currency_is_still_rejected() -> None:
    # target 缺省即同币种，给了汇率就是多给
    r = amount(100.0, currency="CNY", unit="元", fx_rate=7.1, fx_unit="CNY/USD")
    assert r.reason == BAD_ARGUMENT


def test_same_currency_with_fx_is_bad_argument() -> None:
    r = amount(100.0, currency="CNY", unit="元", fx_rate=7.1, fx_unit="CNY/USD")
    assert not r.ok and r.reason == BAD_ARGUMENT
    assert "多给" in r.details["message"]


def test_same_currency_with_only_rate_is_bad_argument() -> None:
    r = amount(100.0, currency="USD", unit="元", target_currency="USD", fx_rate=7.1)
    assert r.reason == BAD_ARGUMENT


# ------------------------------------------------------------------ 正常跨币种


def test_cross_currency_uses_base_over_quote() -> None:
    # fx_unit="CNY/HKD"，fx_rate=1.08 → 1 CNY = 1.08 HKD
    r = amount(
        1_000_000.0,
        currency="CNY",
        unit="元",
        display_unit="万元",
        target_currency="HKD",
        fx_rate=1.08,
        fx_unit="CNY/HKD",
    )
    assert r.ok and r.reason == OK
    assert r.value == pytest.approx(108.0)  # 万元
    assert r.unit == "万元"
    assert r.display is not None and r.display.endswith("万元 HKD")
    assert r.details["currency"] == "HKD"
    assert r.details["source_currency"] == "CNY"


def test_cross_currency_inverted_pair() -> None:
    forward = amount(
        100.0, currency="USD", unit="元", target_currency="CNY", fx_rate=7.1, fx_unit="USD/CNY"
    )
    inverse = amount(
        100.0,
        currency="USD",
        unit="元",
        target_currency="CNY",
        fx_rate=1 / 7.1,
        fx_unit="CNY/USD",
    )
    assert forward.ok and inverse.ok
    assert forward.value == pytest.approx(inverse.value, rel=1e-12)
    assert forward.value == pytest.approx(710.0)


def test_mismatched_fx_pair_is_bad_argument() -> None:
    r = amount(
        100.0, currency="USD", unit="元", target_currency="CNY", fx_rate=1.08, fx_unit="CNY/HKD"
    )
    assert r.reason == BAD_ARGUMENT


@pytest.mark.parametrize("fx_unit", ["CNY", "CNY/", "/HKD", "CNY/HKD/USD", "CNY/CNY", "CNY/EUR"])
@pytest.mark.parametrize("fx_rate", [1.0, 0.0, -1.0, math.inf, math.nan])
def test_malformed_fx_is_bad_argument(fx_unit: str, fx_rate: float) -> None:
    r = amount(
        100.0, currency="CNY", unit="元", target_currency="HKD", fx_rate=fx_rate, fx_unit=fx_unit
    )
    assert r.reason == BAD_ARGUMENT and r.value is None


@pytest.mark.parametrize("unit", ["", "千元", " dollar"])
def test_unknown_unit_is_bad_argument(unit: str) -> None:
    assert amount(1.0, currency="CNY", unit=unit).reason == BAD_ARGUMENT


def test_unknown_currency_is_bad_argument() -> None:
    assert amount(1.0, currency="JPY", unit="元").reason == BAD_ARGUMENT


@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
def test_non_finite_amount_is_bad_argument(bad: float) -> None:
    r = amount(bad, currency="CNY", unit="元")
    assert r.reason == BAD_ARGUMENT


def test_convert_roundtrip_returns_same_value() -> None:
    base = amount(2.5, currency="USD", unit="亿元")
    back = convert(base, target_currency="CNY", fx_rate=7.0, fx_unit="USD/CNY")
    assert back.ok and back.value == pytest.approx(17.5) and back.unit == "亿元"
    assert back.details["currency"] == "CNY"


def test_convert_same_currency_with_rate_rejected() -> None:
    base = amount(2.5, currency="CNY", unit="万元")
    assert convert(base, fx_rate=1.0, fx_unit="CNY/USD").reason == BAD_ARGUMENT


def test_convert_non_money_result_is_bad_argument() -> None:
    from ladderstock.calc.result import CalcResult

    r = CalcResult.success(1.0, unit="倍")
    assert convert(r).reason == BAD_ARGUMENT


# ------------------------------------------------------------------ 模糊测试


@pytest.mark.parametrize("seed", range(20))
def test_fuzz_amounts_stay_finite(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(100):
        value = rng.uniform(-1, 1) * 10 ** rng.randint(-30, 161)
        unit = rng.choice(sorted(UNIT_TO_YUAN))
        out_unit = rng.choice(sorted(UNIT_TO_YUAN))
        r = amount(
            value,
            currency="CNY",
            unit=unit,
            display_unit=out_unit,
            target_currency="HKD",
            fx_rate=10 ** rng.uniform(-3, 3),
            fx_unit="CNY/HKD",
        )
        if r.ok:
            assert r.value is not None and math.isfinite(r.value)
            assert r.display and "HKD" in r.display
        else:
            assert r.reason == BAD_ARGUMENT and r.value is None
