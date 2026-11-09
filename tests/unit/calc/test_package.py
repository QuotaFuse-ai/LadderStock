from __future__ import annotations

import ladderstock.calc as calc


def test_calc_version() -> None:
    assert calc.CALC_VERSION == "0.1.0"


def test_public_exports_match_all() -> None:
    for name in calc.__all__:
        assert hasattr(calc, name), name


def test_no_business_operators_in_baseline() -> None:
    # 基础层只有四件套；业务算子不在本单范围
    exported = set(calc.__all__)
    assert not any(name.startswith(("pe_", "ma_", "rsi_", "valuation_")) for name in exported)
