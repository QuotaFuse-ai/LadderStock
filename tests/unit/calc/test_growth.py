from __future__ import annotations

import math

import pytest

from ladderstock.calc.growth import (
    convert_amount,
    debt_ratio,
    expense_ratio,
    gross_margin,
    qoq,
    yoy,
)
from ladderstock.calc.money import amount
from ladderstock.calc.result import (
    BAD_ARGUMENT,
    DENOMINATOR_NON_POSITIVE,
    NOT_MEANINGFUL,
    OK,
    CalcResult,
)

# ---------------------------------------------------------------- 同比


def test_yoy_success_twenty_percent() -> None:
    r = yoy(current=120.0, prior=100.0)
    assert r.ok and r.reason == OK
    assert r.unit == "小数"
    assert r.value == pytest.approx(0.2)
    assert r.display == "20.00%"


@pytest.mark.parametrize("prior", [0.0, -50.0])
def test_yoy_not_meaningful_when_base_non_positive(prior: float) -> None:
    r = yoy(current=120.0, prior=prior)
    assert r.reason == NOT_MEANINGFUL and r.value is None
    assert r.details["not_meaningful_kind"] == DENOMINATOR_NON_POSITIVE
    assert r.details["field"] == "prior"


@pytest.mark.parametrize(("current", "prior"), [(math.nan, 100.0), (120.0, math.inf)])
def test_yoy_bad_argument_on_non_finite_inputs(current: float, prior: float) -> None:
    r = yoy(current=current, prior=prior)
    assert r.reason == BAD_ARGUMENT and r.value is None
    assert r.details["field"] in {"current", "prior"}


# ---------------------------------------------------------------- 环比


def test_qoq_success_ten_percent() -> None:
    r = qoq(current=110.0, prior=100.0)
    assert r.ok and r.value == pytest.approx(0.1)
    assert r.display == "10.00%"


def test_qoq_not_meaningful_when_base_zero() -> None:
    r = qoq(current=110.0, prior=0.0)
    assert r.reason == NOT_MEANINGFUL
    assert r.details["not_meaningful_kind"] == DENOMINATOR_NON_POSITIVE


def test_qoq_bad_argument_on_non_finite_prior() -> None:
    r = qoq(current=110.0, prior=math.nan)
    assert r.reason == BAD_ARGUMENT and r.details["field"] == "prior"


# ---------------------------------------------------------------- 毛利率


def test_gross_margin_success_is_decimal() -> None:
    r = gross_margin(gross_profit=25.0, revenue=100.0)
    assert r.ok and r.value == pytest.approx(0.25)
    assert r.unit == "小数"
    assert r.display == "25.00%"


@pytest.mark.parametrize("revenue", [0.0, -100.0])
def test_gross_margin_not_meaningful_when_revenue_non_positive(revenue: float) -> None:
    r = gross_margin(gross_profit=25.0, revenue=revenue)
    assert r.reason == NOT_MEANINGFUL and r.value is None
    assert r.details["field"] == "revenue"


def test_gross_margin_bad_argument_on_non_finite_numerator() -> None:
    r = gross_margin(gross_profit=math.nan, revenue=100.0)
    assert r.reason == BAD_ARGUMENT and r.details["field"] == "gross_profit"


# ---------------------------------------------------------------- 费用率


def test_expense_ratio_success_twelve_percent() -> None:
    r = expense_ratio(expense=12.0, revenue=100.0)
    assert r.ok and r.value == pytest.approx(0.12)
    assert r.display == "12.00%"


def test_expense_ratio_not_meaningful_when_revenue_zero() -> None:
    r = expense_ratio(expense=12.0, revenue=0.0)
    assert r.reason == NOT_MEANINGFUL and r.details["field"] == "revenue"


def test_expense_ratio_bad_argument_on_non_finite_expense() -> None:
    r = expense_ratio(expense=math.inf, revenue=100.0)
    assert r.reason == BAD_ARGUMENT and r.details["field"] == "expense"


# ---------------------------------------------------------------- 负债率


def test_debt_ratio_success_forty_percent() -> None:
    r = debt_ratio(liabilities=40.0, total_assets=100.0)
    assert r.ok and r.value == pytest.approx(0.4)
    assert r.display == "40.00%"


@pytest.mark.parametrize("assets", [0.0, -100.0])
def test_debt_ratio_not_meaningful_when_assets_non_positive(assets: float) -> None:
    r = debt_ratio(liabilities=40.0, total_assets=assets)
    assert r.reason == NOT_MEANINGFUL and r.details["field"] == "total_assets"


def test_debt_ratio_bad_argument_on_non_finite_assets() -> None:
    r = debt_ratio(liabilities=40.0, total_assets=math.nan)
    assert r.reason == BAD_ARGUMENT and r.details["field"] == "total_assets"


# ---------------------------------------------------------------- 汇率换算


def test_convert_amount_success_reuses_money_fx_contract() -> None:
    hkd = amount(100.0, currency="HKD", unit="元")
    r = convert_amount(hkd, target_currency="CNY", fx_rate=1.087, fx_unit="HKD/CNY")
    assert r.ok and r.value == pytest.approx(108.7)
    assert r.unit == "元"
    assert r.details["currency"] == "CNY"
    assert r.details["fx_rate"] == 1.087
    assert r.details["fx_unit"] == "HKD/CNY"


def test_convert_amount_not_meaningful_result_passes_through() -> None:
    nm = CalcResult.not_meaningful(DENOMINATOR_NON_POSITIVE)
    r = convert_amount(nm, target_currency="CNY", fx_rate=1.087, fx_unit="HKD/CNY")
    assert r.reason == NOT_MEANINGFUL and r.value is None
    assert r.details["not_meaningful_kind"] == DENOMINATOR_NON_POSITIVE


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_currency": "CNY"},
        {"target_currency": "HKD", "fx_rate": 1.087, "fx_unit": "HKD/CNY"},
    ],
    ids=["missing_fx", "same_currency_with_fx"],
)
def test_convert_amount_bad_argument_on_contract_violation(kwargs: dict) -> None:
    hkd = amount(100.0, currency="HKD", unit="元")
    r = convert_amount(hkd, **kwargs)
    assert r.reason == BAD_ARGUMENT and r.value is None
