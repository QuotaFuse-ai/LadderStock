from __future__ import annotations

import math

import pytest

from ladderstock.calc.periods import cumulative_to_single, ttm, ttm_yoy
from ladderstock.calc.result import (
    BAD_ARGUMENT,
    DENOMINATOR_NON_POSITIVE,
    NOT_MEANINGFUL,
    OK,
    OUT_OF_DOMAIN,
)

# ---------------------------------------------------------------- 累计转单季（自然财年）


def test_cumulative_to_single_success_natural_fiscal_year() -> None:
    r = cumulative_to_single(
        fiscal_year=2024,
        fiscal_start_month=1,
        cumulative=[10.0, 30.0, 60.0, 100.0],
        unit="亿元",
    )
    assert r.ok and r.reason == OK
    assert r.unit == "亿元"
    assert r.value == 40.0
    rows = r.details["single_quarters"]
    assert [row["single"] for row in rows] == [10.0, 20.0, 30.0, 40.0]
    assert [row["quarter_end"] for row in rows] == [
        "2024-03-31",
        "2024-06-30",
        "2024-09-30",
        "2024-12-31",
    ]


# ---------------------------------------------------------------- 累计转单季（非自然财年）


def test_cumulative_to_single_success_april_fiscal_year() -> None:
    # 4-3-31 财年：累计 12 / 27 / 45 / 68 → 单季 12 / 15 / 18 / 23（手算）
    r = cumulative_to_single(
        fiscal_year=2024,
        fiscal_start_month=4,
        cumulative=[12.0, 27.0, 45.0, 68.0],
        unit="亿元",
    )
    assert r.ok and r.value == 23.0
    rows = r.details["single_quarters"]
    assert [row["single"] for row in rows] == [12.0, 15.0, 18.0, 23.0]
    assert [row["quarter_end"] for row in rows] == [
        "2024-06-30",
        "2024-09-30",
        "2024-12-31",
        "2025-03-31",
    ]
    assert sum(row["single"] for row in rows) == pytest.approx(68.0)


def test_cumulative_to_single_success_partial_series() -> None:
    r = cumulative_to_single(
        fiscal_year=2024, fiscal_start_month=4, cumulative=[12.0, 27.0], unit="亿元"
    )
    assert r.ok and r.value == 15.0
    assert len(r.details["single_quarters"]) == 2


def test_cumulative_to_single_not_meaningful_when_period_missing() -> None:
    r = cumulative_to_single(
        fiscal_year=2024, fiscal_start_month=4, cumulative=[12.0, None, 45.0], unit="亿元"
    )
    assert r.reason == NOT_MEANINGFUL and r.value is None
    assert r.details["not_meaningful_kind"] == OUT_OF_DOMAIN
    assert r.details["missing_indices"] == [1]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fiscal_year": 1800, "cumulative": [1.0]},
        {"fiscal_year": 2024, "fiscal_start_month": 13, "cumulative": [1.0]},
        {"fiscal_year": 2024, "cumulative": []},
        {"fiscal_year": 2024, "cumulative": [1.0, 2.0, 3.0, 4.0, 5.0]},
        {"fiscal_year": 2024, "cumulative": [1.0, math.nan]},
        {"fiscal_year": 2024, "cumulative": [1.0], "unit": ""},
    ],
    ids=["year", "start_month", "empty", "too_many", "non_finite", "empty_unit"],
)
def test_cumulative_to_single_bad_argument(kwargs: dict) -> None:
    r = cumulative_to_single(**kwargs)
    assert r.reason == BAD_ARGUMENT and r.value is None
    assert r.details["field"] in {
        "fiscal_year",
        "fiscal_start_month",
        "cumulative",
        "cumulative[1]",
        "unit",
    }


# ---------------------------------------------------------------- TTM


def test_ttm_success_sums_four_quarters() -> None:
    r = ttm(single_quarters=[10.0, 20.0, 30.0, 40.0], unit="亿元")
    assert r.ok and r.reason == OK
    assert r.value == pytest.approx(100.0)
    assert r.unit == "亿元"
    assert r.details["window"] == 4


def test_ttm_not_meaningful_when_window_has_missing_quarter() -> None:
    r = ttm(single_quarters=[10.0, None, 30.0, 40.0])
    assert r.reason == NOT_MEANINGFUL and r.value is None
    assert r.details["not_meaningful_kind"] == OUT_OF_DOMAIN
    assert r.details["missing_indices"] == [1]


@pytest.mark.parametrize(
    "quarters",
    [[10.0, 20.0, 30.0], [10.0, 20.0, 30.0, 40.0, 50.0], [10.0, math.inf, 30.0, 40.0]],
    ids=["too_few", "too_many", "non_finite"],
)
def test_ttm_bad_argument_on_invalid_window(quarters: list[float]) -> None:
    r = ttm(single_quarters=quarters)
    assert r.reason == BAD_ARGUMENT and r.value is None
    assert r.details["field"].startswith("single_quarters")


# ---------------------------------------------------------------- TTM 同比


def test_ttm_yoy_success_twenty_percent() -> None:
    r = ttm_yoy(current_ttm=120.0, prior_ttm=100.0)
    assert r.ok and r.reason == OK
    assert r.unit == "小数"
    assert r.value == pytest.approx(0.2)
    assert r.display == "20.00%"


@pytest.mark.parametrize("prior", [0.0, -5.0])
def test_ttm_yoy_not_meaningful_when_prior_non_positive(prior: float) -> None:
    r = ttm_yoy(current_ttm=120.0, prior_ttm=prior)
    assert r.reason == NOT_MEANINGFUL and r.value is None
    assert r.details["not_meaningful_kind"] == DENOMINATOR_NON_POSITIVE
    assert r.details["field"] == "prior_ttm"


@pytest.mark.parametrize(("current", "prior"), [(math.nan, 100.0), (120.0, math.inf)])
def test_ttm_yoy_bad_argument_on_non_finite_inputs(current: float, prior: float) -> None:
    r = ttm_yoy(current_ttm=current, prior_ttm=prior)
    assert r.reason == BAD_ARGUMENT and r.value is None
    assert r.details["field"] in {"current_ttm", "prior_ttm"}
