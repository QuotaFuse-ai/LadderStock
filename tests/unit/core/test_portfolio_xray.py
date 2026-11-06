"""QUOT-437：持仓 X 光的纯计算口径。

钉住 issue 点名的四件事：

1. 权重按现价市值、按已取价的市值合计做分母；`top1/top3/top5` 是同一份权重的前缀和；
2. 缺价的持仓**不进分母**、`weight=None`，并出现在 `gaps` 里；
3. 行业只用标注：没标注的归「未标注」，不按代码猜；标注为空串与缺键同一个待遇；
4. 相关系数只用共同交易日 ≥ 20 的两只；不足就 `available=false` + 原因。
"""

from __future__ import annotations

import pytest

from ladderstock.core.portfolio import HoldingRow
from ladderstock.core.portfolio_xray import (
    MIN_CORR_DAYS,
    NO_PAIR_REASON,
    UNLABELED,
    build_view,
)


def _holding(symbol: str, shares: float, name: str = "") -> HoldingRow:
    return HoldingRow(
        symbol=symbol,
        name=name or symbol,
        shares=shares,
        unit_cost=1.0,
        cost_total=shares,
        opened_at="2025-01-02",
        lots=[],
        realized_pnl=0.0,
        dividends=0.0,
        fees=0.0,
        trade_count=1,
    )


def _series(days: int, start: float = 10.0, step: float = 0.1) -> dict[str, float]:
    return {
        f"2025-01-{index + 1:02d}": round(start + index * step, 4)
        for index in range(days)
    }


# --------------------------------------------------------------------------- #
# 权重与 topN
# --------------------------------------------------------------------------- #


def test_weights_use_market_value_over_priced_total() -> None:
    holdings = [_holding("600000", 100), _holding("000001", 200)]
    prices = {"600000": 10.0, "000001": 5.0}

    view = build_view(holdings, prices, {}, {}, as_of="2025-01-31")

    assert view["as_of"] == "2025-01-31"
    assert view["total_value"] == pytest.approx(2000)
    by_symbol = {item["symbol"]: item for item in view["items"]}
    assert by_symbol["600000"]["market_value"] == pytest.approx(1000)
    assert by_symbol["600000"]["weight"] == pytest.approx(0.5)
    assert by_symbol["000001"]["weight"] == pytest.approx(0.5)
    assert view["top1"] == pytest.approx(0.5)
    assert view["top3"] == pytest.approx(1.0)
    assert view["top5"] == pytest.approx(1.0)
    assert [row for row in view["gaps"] if row["affects"] == "weight"] == []


def test_topn_sums_the_largest_weights_first() -> None:
    holdings = [
        _holding("600000", 100),
        _holding("600001", 50),
        _holding("600002", 30),
        _holding("600003", 20),
    ]
    prices = {row.symbol: 10.0 for row in holdings}

    view = build_view(holdings, prices, {}, {}, as_of="2025-01-31")

    weights = sorted((item["weight"] for item in view["items"]), reverse=True)
    assert weights == pytest.approx([0.5, 0.25, 0.15, 0.1])
    assert view["top1"] == pytest.approx(0.5)
    assert view["top3"] == pytest.approx(0.9)
    assert view["top5"] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# 缺价：不进分母，列进 gaps
# --------------------------------------------------------------------------- #


def test_unpriced_holding_is_excluded_from_the_denominator() -> None:
    holdings = [_holding("600000", 100), _holding("000001", 100)]
    prices = {"600000": 10.0}  # 000001 没有现价

    view = build_view(holdings, prices, {}, {}, as_of="2025-01-31")

    by_symbol = {item["symbol"]: item for item in view["items"]}
    assert by_symbol["600000"]["weight"] == pytest.approx(1.0)
    assert by_symbol["000001"]["market_value"] is None
    assert by_symbol["000001"]["weight"] is None
    assert view["total_value"] == pytest.approx(1000)
    assert view["top1"] == pytest.approx(1.0)
    gap = next(row for row in view["gaps"] if row["symbol"] == "000001")
    assert gap["affects"] == "weight"
    assert "daily_k" in gap["reason"]
    assert "没算进权重分母" in gap["reason"]


def test_all_unpriced_returns_nulls_not_zeros() -> None:
    view = build_view([_holding("600000", 100)], {}, {}, {}, as_of=None)

    assert view["total_value"] is None
    assert view["top1"] is None
    assert view["top3"] is None
    assert view["items"][0]["weight"] is None
    assert view["industry_buckets"] == []
    assert view["metrics"]["corr"]["available"] is False
    assert "现价" in view["metrics"]["corr"]["reason"]


def test_no_holdings_is_an_empty_view() -> None:
    view = build_view([], {}, {}, {}, as_of=None)

    assert view["items"] == []
    assert view["total_value"] is None
    assert view["top1"] is None
    assert view["industry_buckets"] == []
    assert view["gaps"] == []
    assert view["metrics"]["corr"]["available"] is False


# --------------------------------------------------------------------------- #
# 行业：只用标注，不猜
# --------------------------------------------------------------------------- #


def test_unlabeled_industry_goes_to_the_unlabeled_bucket() -> None:
    holdings = [
        _holding("600000", 100),
        _holding("000001", 100),
        _holding("600519", 100),
    ]
    prices = {row.symbol: 10.0 for row in holdings}
    industries = {"600000": "银行", "000001": "  ", "600519": ""}

    view = build_view(holdings, prices, industries, {}, as_of="2025-01-31")

    buckets = {row["industry"]: row for row in view["industry_buckets"]}
    assert set(buckets) == {"银行", UNLABELED}
    assert buckets["银行"]["labeled"] is True
    assert buckets["银行"]["weight"] == pytest.approx(round(1 / 3, 4))
    assert buckets[UNLABELED]["labeled"] is False
    assert buckets[UNLABELED]["weight"] == pytest.approx(round(2 / 3, 4))
    assert buckets[UNLABELED]["symbols"] == ["000001", "600519"]
    assert view["industry_labeled_count"] == 1
    assert view["industry_unlabeled_count"] == 2


def test_industry_never_guesses_from_the_code() -> None:
    holdings = [_holding("600000", 100), _holding("000001", 100)]
    prices = {row.symbol: 10.0 for row in holdings}

    view = build_view(holdings, prices, {}, {}, as_of="2025-01-31")

    assert [row["industry"] for row in view["industry_buckets"]] == [UNLABELED]


# --------------------------------------------------------------------------- #
# 相关系数
# --------------------------------------------------------------------------- #


def test_correlation_requires_both_series_to_have_enough_days() -> None:
    holdings = [_holding("600000", 100), _holding("000001", 100)]
    prices = {row.symbol: 10.0 for row in holdings}
    closes = {
        "600000": _series(MIN_CORR_DAYS + 5),
        "000001": _series(MIN_CORR_DAYS + 5, step=0.2),
    }

    view = build_view(holdings, prices, {}, closes, as_of="2025-01-31")

    corr = view["metrics"]["corr"]
    assert corr["available"] is True
    assert corr["pairs_count"] == 1
    pair = corr["max_pair"]
    assert set(pair["pair"]) == {"600000", "000001"}
    assert pair["samples"] == MIN_CORR_DAYS + 4  # 收盘点 → 收益点少一天
    assert -1.0 <= pair["corr"] <= 1.0
    assert corr["avg_abs_corr"] is not None
    assert corr["window"]["first"] is not None


def test_short_series_is_a_gap_with_reason() -> None:
    holdings = [_holding("600000", 100), _holding("000001", 100)]
    prices = {row.symbol: 10.0 for row in holdings}
    closes = {"600000": _series(5), "000001": _series(MIN_CORR_DAYS + 1)}

    view = build_view(holdings, prices, {}, closes, as_of="2025-01-31")

    corr = view["metrics"]["corr"]
    assert corr["available"] is False
    assert corr["reason"] == NO_PAIR_REASON
    assert corr["max_pair"] is None
    assert corr["pairs"] == []
    gap = next(row for row in view["gaps"] if row["symbol"] == "600000")
    assert gap["affects"] == "correlation"
    assert "权重不受影响" in gap["reason"]


def test_single_holding_explains_there_is_nothing_to_compare() -> None:
    view = build_view(
        [_holding("600000", 100)], {"600000": 10.0}, {}, {}, as_of="2025-01-31"
    )

    corr = view["metrics"]["corr"]
    assert corr["available"] is False
    assert "少于 2 只" in corr["reason"]


def test_correlation_is_none_when_one_series_is_flat() -> None:
    holdings = [_holding("600000", 100), _holding("000001", 100)]
    prices = {row.symbol: 10.0 for row in holdings}
    flat = {f"2025-01-{index + 1:02d}": 10.0 for index in range(30)}
    closes = {"600000": flat, "000001": _series(30)}

    corr = build_view(holdings, prices, {}, closes, as_of="2025-01-31")["metrics"]["corr"]

    assert corr["available"] is False
    assert corr["max_pair"] is None
