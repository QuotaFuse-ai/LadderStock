from __future__ import annotations

import math

import pytest

from ladderstock.calc.result import (
    BAD_ARGUMENT,
    DENOMINATOR_NON_POSITIVE,
    NOT_MEANINGFUL,
    OK,
    OUT_OF_DOMAIN,
    RESULT_LIMITED,
)
from ladderstock.calc.valuation import (
    forward_cagr,
    pe_deducted_x4,
    pe_digestion_years,
    pe_forward,
    pe_ttm,
    peg,
)

# ---------------------------------------------------------------- PE_TTM


def test_pe_ttm_success_benchmark_100yi_over_5_4yi() -> None:
    # 手工可复算基准：市值 100 亿 / TTM 净利 5.4 亿 = 18.52 倍
    r = pe_ttm(market_cap=1e10, net_profit_ttm=5.4e8)
    assert r.ok and r.reason == OK
    assert r.unit == "倍"
    assert r.value == pytest.approx(100 / 5.4)
    assert round(float(r.value), 2) == 18.52
    assert r.display == "18.52 倍"


@pytest.mark.parametrize("profit", [0.0, -1e8])
def test_pe_ttm_not_meaningful_when_profit_non_positive(profit: float) -> None:
    r = pe_ttm(market_cap=1e10, net_profit_ttm=profit)
    assert r.reason == NOT_MEANINGFUL and r.value is None
    assert r.details["not_meaningful_kind"] == DENOMINATOR_NON_POSITIVE
    assert r.details["field"] == "net_profit_ttm"


@pytest.mark.parametrize(
    ("market_cap", "profit"),
    [(0.0, 5.4e8), (-1.0, 5.4e8), (math.nan, 5.4e8), (1e10, math.inf)],
)
def test_pe_ttm_bad_argument_on_invalid_inputs(market_cap: float, profit: float) -> None:
    r = pe_ttm(market_cap=market_cap, net_profit_ttm=profit)
    assert r.reason == BAD_ARGUMENT and r.value is None
    assert r.details["field"] in {"market_cap", "net_profit_ttm"}


# ---------------------------------------------------------------- 扣非×4 PE


def test_pe_deducted_x4_success_matches_manual_annualization() -> None:
    # 单季扣非 1.35 亿 × 4 = 5.4 亿，与 TTM 基准同为一个 18.52 倍
    r = pe_deducted_x4(market_cap=1e10, latest_quarter_deducted_profit=1.35e8)
    assert r.ok and r.value == pytest.approx(100 / 5.4)
    assert r.details["annualized_deducted_profit"] == 5.4e8
    assert r.display == "18.52 倍"


@pytest.mark.parametrize("profit", [0.0, -1.35e8])
def test_pe_deducted_x4_not_meaningful_when_quarter_profit_non_positive(profit: float) -> None:
    r = pe_deducted_x4(market_cap=1e10, latest_quarter_deducted_profit=profit)
    assert r.reason == NOT_MEANINGFUL and r.value is None
    assert r.details["field"] == "latest_quarter_deducted_profit"


@pytest.mark.parametrize(
    ("market_cap", "profit"),
    [(-1.0, 1.35e8), (1e10, math.nan), (1e10, math.inf)],
)
def test_pe_deducted_x4_bad_argument_on_invalid_inputs(market_cap: float, profit: float) -> None:
    r = pe_deducted_x4(market_cap=market_cap, latest_quarter_deducted_profit=profit)
    assert r.reason == BAD_ARGUMENT and r.value is None


# ---------------------------------------------------------------- 前瞻 PE


def test_pe_forward_success_uses_consensus_profit() -> None:
    r = pe_forward(market_cap=1e10, forecast_net_profit=6.25e8)
    assert r.ok and r.value == pytest.approx(16.0)
    assert r.details["forecast_net_profit"] == 6.25e8


@pytest.mark.parametrize("profit", [0.0, -1.0])
def test_pe_forward_not_meaningful_when_forecast_non_positive(profit: float) -> None:
    r = pe_forward(market_cap=1e10, forecast_net_profit=profit)
    assert r.reason == NOT_MEANINGFUL and r.value is None


@pytest.mark.parametrize("profit", [math.nan, math.inf])
def test_pe_forward_bad_argument_on_non_finite_forecast(profit: float) -> None:
    r = pe_forward(market_cap=1e10, forecast_net_profit=profit)
    assert r.reason == BAD_ARGUMENT and r.details["field"] == "forecast_net_profit"


# ---------------------------------------------------------------- PEG


def test_peg_success_pe20_on_25pct_growth_is_0_8() -> None:
    r = peg(pe=20.0, ttm_yoy=0.25)
    assert r.ok and r.value == pytest.approx(0.8)
    assert r.details["growth_percent"] == pytest.approx(25.0)
    assert r.display == "0.80 倍"


@pytest.mark.parametrize("growth", [0.0, -0.1])
def test_peg_not_meaningful_when_growth_non_positive(growth: float) -> None:
    r = peg(pe=20.0, ttm_yoy=growth)
    assert r.reason == NOT_MEANINGFUL and r.value is None
    assert r.details["not_meaningful_kind"] == OUT_OF_DOMAIN


def test_peg_bad_argument_when_pe_non_positive() -> None:
    r = peg(pe=0.0, ttm_yoy=0.25)
    assert r.reason == BAD_ARGUMENT and r.details["field"] == "pe"


def test_peg_bad_argument_on_percent_misuse_ttm_yoy_80() -> None:
    # 反例 1：把 80% 传成 80（|80| > 20）
    r = peg(pe=20.0, ttm_yoy=80.0)
    assert r.reason == BAD_ARGUMENT and r.value is None
    assert r.details["field"] == "ttm_yoy"
    assert r.details["limit"] == 20.0


# ---------------------------------------------------------------- PE 消化年数


def test_pe_digestion_years_success_matches_log_formula() -> None:
    # 30 倍按 25% 增速消化到 15 倍：ln(2) / ln(1.25) ≈ 3.11 年
    r = pe_digestion_years(pe=30.0, target_pe=15.0, cagr=0.25)
    assert r.ok and r.unit == "年"
    assert r.value == pytest.approx(math.log(2.0) / math.log1p(0.25))
    assert round(float(r.value), 2) == 3.11
    assert r.display == "3.11 年"


def test_pe_digestion_years_success_is_zero_when_already_at_target() -> None:
    r = pe_digestion_years(pe=10.0, target_pe=15.0, cagr=0.25)
    assert r.ok and r.value == 0.0
    assert r.details["already_at_target"] is True


@pytest.mark.parametrize("growth", [0.0, -0.5, -1.0])
def test_pe_digestion_years_not_meaningful_when_growth_cannot_converge(growth: float) -> None:
    r = pe_digestion_years(pe=30.0, target_pe=15.0, cagr=growth)
    assert r.reason == NOT_MEANINGFUL and r.value is None
    assert r.details["field"] == "cagr"


def test_pe_digestion_years_bad_argument_when_target_pe_non_positive() -> None:
    r = pe_digestion_years(pe=30.0, target_pe=0.0, cagr=0.25)
    assert r.reason == BAD_ARGUMENT and r.details["field"] == "target_pe"


def test_pe_digestion_years_bad_argument_on_percent_misuse_cagr_25() -> None:
    # 反例 2：把 25% 传成 25（|25| > 5）
    r = pe_digestion_years(pe=30.0, target_pe=15.0, cagr=25.0)
    assert r.reason == BAD_ARGUMENT and r.value is None
    assert r.details["field"] == "cagr"
    assert r.details["limit"] == 5.0


def test_pe_digestion_years_guard_boundary_allows_five_times_growth() -> None:
    # 恰好 5（500%）不算误传，按上限开区间处理
    r = pe_digestion_years(pe=32.0, target_pe=1.0, cagr=5.0)
    assert r.ok and r.value is not None


# ---------------------------------------------------------------- 前瞻 CAGR


def test_forward_cagr_success_doubling_in_three_years() -> None:
    r = forward_cagr(current_profit=5e8, target_profit=1e9, years=3.0)
    assert r.ok and r.value == pytest.approx(2 ** (1 / 3) - 1)
    assert round(float(r.value), 4) == 0.2599


@pytest.mark.parametrize("current", [0.0, -5e8])
def test_forward_cagr_not_meaningful_when_base_non_positive(current: float) -> None:
    r = forward_cagr(current_profit=current, target_profit=1e9, years=3.0)
    assert r.reason == NOT_MEANINGFUL and r.value is None
    assert r.details["not_meaningful_kind"] == DENOMINATOR_NON_POSITIVE


def test_forward_cagr_not_meaningful_when_target_non_positive() -> None:
    r = forward_cagr(current_profit=5e8, target_profit=0.0, years=3.0)
    assert r.reason == NOT_MEANINGFUL and r.details["not_meaningful_kind"] == OUT_OF_DOMAIN


@pytest.mark.parametrize("years", [0.0, -3.0, math.nan])
def test_forward_cagr_bad_argument_on_invalid_years(years: float) -> None:
    r = forward_cagr(current_profit=5e8, target_profit=1e9, years=years)
    assert r.reason == BAD_ARGUMENT and r.details["field"] == "years"


def test_forward_cagr_overflowing_ratio_is_not_meaningful() -> None:
    r = forward_cagr(current_profit=1e-300, target_profit=1e300, years=0.5)
    assert r.reason == NOT_MEANINGFUL
    assert r.details["not_meaningful_kind"] == RESULT_LIMITED
