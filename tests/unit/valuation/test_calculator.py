"""估值计算器单测（E 包 / DEC-091 §4）：口径 + 护栏 + 手算对照。"""

from __future__ import annotations

import pytest

from ladderstock.errors import EXIT_USAGE
from ladderstock.valuation import calculator


def test_pe_pb_ps_basics() -> None:
    assert calculator.pe(10.8, 0.72)["value"] == pytest.approx(15.0)
    assert calculator.pb(8.0, 4.0)["value"] == pytest.approx(2.0)
    assert calculator.ps(30.0, 10.0)["value"] == pytest.approx(3.0)
    # 每个结果都带免责声明（用户面工具的红线）
    for payload in (
        calculator.pe(10.0, 1.0),
        calculator.pb(10.0, 5.0),
    ):
        assert "换算器" in payload["disclaimer"]


def test_dividend_and_earnings_yield() -> None:
    rate = calculator.dividend_yield(10.0, 0.5)
    assert rate["value"] == pytest.approx(0.05)
    assert rate["value_pct"] == "5.00%"
    ey = calculator.earnings_yield(8.0)
    assert ey["value"] == pytest.approx(0.125)
    assert ey["value_pct"] == "12.50%"


def test_rejects_non_positive_inputs() -> None:
    for fn, args in (
        (calculator.pe, (0.0, 1.0)),
        (calculator.pe, (10.0, 0.0)),
        (calculator.pb, (-1.0, 2.0)),
        (calculator.dividend_yield, (10.0, -0.1)),
        (calculator.earnings_yield, (0.0,)),
    ):
        with pytest.raises(Exception) as err:
            fn(*args)
        assert getattr(err.value, "code", None) == EXIT_USAGE


def test_dcf_hand_check() -> None:
    """手算对照：FCF=100，g=0，r=10%，n=10 → 显式期就是 100×年金现值系数。"""
    result = calculator.dcf_simple(100.0, 0.0, 0.0, 0.10, 10)
    annuity = sum(100.0 / 1.1**year for year in range(1, 11))
    assert result["pv_explicit"] == pytest.approx(annuity, rel=1e-4)
    # 终值 = 100 × 1.0 / 0.10 = 1000，折现 10 年
    assert result["pv_terminal"] == pytest.approx(1000.0 / 1.1**10, rel=1e-4)
    assert result["value"] == pytest.approx(result["pv_explicit"] + result["pv_terminal"])
    share = result["pv_terminal"] / result["value"]
    assert result["terminal_share"] == pytest.approx(round(share, 4))


def test_dcf_guards() -> None:
    # r ≤ g_T：永续值无意义
    with pytest.raises(Exception) as err:
        calculator.dcf_simple(100.0, 0.05, 0.10, 0.09, 10)
    assert err.value.code == EXIT_USAGE
    # 期数越界
    with pytest.raises(Exception):
        calculator.dcf_simple(100.0, 0.05, 0.02, 0.09, 21)
    # 折现率护栏
    with pytest.raises(Exception):
        calculator.dcf_simple(100.0, 0.05, 0.02, 0.6, 10)


def test_dcf_terminal_share_warning() -> None:
    result = calculator.dcf_simple(100.0, 0.0, 0.03, 0.04, 5)
    assert result["terminal_share"] is not None and result["terminal_share"] > 0.6
    assert result["warnings"], "终值占比 >60% 必须给警告"