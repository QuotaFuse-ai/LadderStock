from __future__ import annotations

import pytest

from ladderstock.calc.present import (
    format_money,
    format_number,
    format_percent,
    format_quantity,
    format_result,
)
from ladderstock.calc.result import DENOMINATOR_NON_POSITIVE, CalcResult

# ---------------------------------------------------------------- 完整字符串断言


def test_format_number_fixed_separators() -> None:
    assert format_number(1234.5) == "1,234.50"
    assert format_number(-1234.5) == "-1,234.50"
    assert format_number(1234.5678, decimals=3) == "1,234.568"
    assert format_number(0.0) == "0.00"


def test_format_number_normalizes_negative_zero() -> None:
    assert format_number(-0.004) == "0.00"
    assert format_number(-0.4, decimals=0) == "0"


def test_format_percent_full_string() -> None:
    assert format_percent(0.1842) == "18.42%"
    assert format_percent(-0.05) == "-5.00%"


def test_format_quantity_full_string() -> None:
    assert format_quantity(18.42, "倍") == "18.42 倍"
    assert format_quantity(0.1842, "小数") == "18.42%"
    assert format_quantity(7.5, "%") == "7.50%"
    assert format_quantity(1.0, "") == "1.00"
    assert format_quantity(2.5, "自定义单位") == "2.50 自定义单位"


def test_format_money_scales_by_magnitude() -> None:
    assert format_money(123456789.0) == "1.23 亿元"
    assert format_money(32500.0, currency="HKD") == "3.25 万港元"
    assert format_money(943.0) == "943.00 元"
    assert format_money(-123456789.0) == "-1.23 亿元"


def test_format_result_uses_value_and_unit() -> None:
    assert format_result(CalcResult.success(18.42, unit="倍")) == "18.42 倍"
    assert format_result(CalcResult.success(0.1842, unit="小数")) == "18.42%"
    assert format_result(CalcResult.success(18.42, unit="倍", display="18.42 倍")) == "18.42 倍"


def test_format_result_not_meaningful_has_no_display() -> None:
    result = CalcResult.not_meaningful(DENOMINATOR_NON_POSITIVE)
    assert format_result(result) is None


# ---------------------------------------------------------------- 确定性


def test_same_input_twice_gives_identical_strings() -> None:
    first = format_quantity(18.42, "倍")
    second = format_quantity(18.42, "倍")
    assert first == second
    assert first == "18.42 倍"
    assert format_result(CalcResult.success(0.1842, unit="小数")) == "18.42%"


def test_known_values_are_stable_across_calls() -> None:
    expected = ["1,234.50", "18.42%", "3.25 万港元"]
    actual = [
        format_number(1234.5),
        format_percent(0.1842),
        format_money(32500.0, currency="HKD"),
    ]
    assert actual == expected


# ---------------------------------------------------------------- 非法输入


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan"), True])
def test_non_finite_or_bool_rejected(value: float) -> None:
    with pytest.raises(ValueError):
        format_number(value)


def test_invalid_arguments_rejected() -> None:
    with pytest.raises(ValueError):
        format_number(1.0, decimals=-1)
    with pytest.raises(ValueError):
        format_money(1.0, currency="EUR")
    with pytest.raises(ValueError):
        format_quantity(1.0, 1)  # type: ignore[arg-type]
