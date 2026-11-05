"""QUOT-433 / DEC-064：持仓收益率（TWR / 资金加权 / 净值序列）纯函数口径。

本文件钉住 issue 验收里点名的六件事：

1. 单笔买入持有 → TWR 与价格涨幅一致；
2. 中途加仓 / 减仓 → TWR 剔除现金流，MWR 与手算（NPV 归零）一致；
3. 不满一年 `xirr = null`，不拿区间数冒充年化；
4. 缺某只票历史行情 → 进 `excluded` + `warnings`，不补价、不补 0；
5. 空流水 → 没有分组（接口层回 200 + 空组）；
6. 分红计入收益、送转不改金额；停牌日按最近收盘价、不插值。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ladderstock.core.portfolio import Trade
from ladderstock.core.portfolio_perf import EMPTY_NOTE, compute_view

SYMBOL = "600000"
OTHER = "000001"


def _trade(
    trade_id: int,
    *,
    type_: str = "buy",
    symbol: str = SYMBOL,
    day: str,
    shares: float | None = None,
    price: float | None = None,
    fee: float | None = None,
    amount: float | None = None,
    ratio: float | None = None,
    name: str = "浦发银行",
) -> Trade:
    return Trade(
        id=trade_id,
        symbol=symbol,
        name=name,
        type=type_,
        date=day,
        shares=shares,
        price=price,
        fee=fee,
        amount=amount,
        ratio=ratio,
        created_at=f"2026-01-01T00:00:{trade_id:02d}",
    )


def _points(start: str, closes: list[float], *, step: int = 1) -> dict[str, float]:
    day = date.fromisoformat(start)
    out: dict[str, float] = {}
    for index, close in enumerate(closes):
        out[(day + timedelta(days=index * step)).isoformat()] = close
    return out


# --------------------------------------------------------------------------- #
# 1. 单笔买入持有
# --------------------------------------------------------------------------- #


def test_single_buy_hold_twr_matches_price_change() -> None:
    trades = [_trade(1, day="2025-01-02", shares=100, price=10)]
    closes = {SYMBOL: _points("2025-01-02", [10, 11, 12])}

    view = compute_view(trades, closes, as_of=date(2025, 1, 6))
    group = view["groups"][0]

    assert group["ok"] is True
    assert group["market"] == "A股"
    assert group["currency"] == "CNY"
    assert group["start"] == "2025-01-02"
    assert group["end"] == "2025-01-04"
    assert group["span_days"] == 2
    assert group["twr"] == pytest.approx(0.2)
    assert group["series"]["dates"] == ["2025-01-02", "2025-01-03", "2025-01-04"]
    assert group["series"]["twr_index"] == pytest.approx([1.0, 1.1, 1.2])
    assert group["invested"] == pytest.approx(1000)
    assert group["proceeds"] == 0
    assert group["dividends"] == 0
    assert group["fees"] == 0
    assert group["end_value"] == pytest.approx(1200)
    assert group["gain"] == pytest.approx(200)
    assert group["symbols"] == [{"symbol": SYMBOL, "name": "浦发银行"}]
    assert group["excluded"] == []
    assert group["warnings"] == []
    assert view["skipped"] == []
    assert view["gaps"] == []
    assert view["as_of"] == "2025-01-04"


def test_under_one_year_no_xirr_and_no_annualized() -> None:
    trades = [_trade(1, day="2025-01-02", shares=100, price=10)]
    closes = {SYMBOL: _points("2025-01-02", [10, 11], step=182)}

    group = compute_view(trades, closes, as_of=date(2025, 7, 3))["groups"][0]

    assert group["span_days"] == 182
    assert group["xirr"] is None, "不满一年不许给年化"
    assert group["twr_annualized"] is None
    assert group["mwr_period"] == pytest.approx(0.1)


# --------------------------------------------------------------------------- #
# 2. 加仓 / 减仓：TWR 剔除现金流，MWR 与手算一致
# --------------------------------------------------------------------------- #


def test_add_position_twr_excludes_cash_flow() -> None:
    trades = [
        _trade(1, day="2025-01-02", shares=100, price=10),
        _trade(2, day="2025-01-06", shares=100, price=12),
    ]
    closes = {
        SYMBOL: {
            "2025-01-02": 10.0,
            "2025-01-03": 11.0,
            "2025-01-06": 12.0,
            "2025-01-07": 13.0,
        }
    }

    group = compute_view(trades, closes, as_of=date(2025, 1, 7))["groups"][0]

    assert group["invested"] == pytest.approx(2200)
    assert group["end_value"] == pytest.approx(2600)
    assert group["gain"] == pytest.approx(400)
    # 价格 10 → 13 是 +30%；加仓那 1200 不能算进收益
    assert group["twr"] == pytest.approx(0.3)
    assert group["twr"] != pytest.approx((2600 - 2200) / 2200)


def test_reduce_position_twr_and_mwr_hand_check() -> None:
    trades = [
        _trade(1, day="2025-01-02", shares=100, price=10),
        _trade(2, type_="sell", day="2025-07-02", shares=50, price=12),
    ]
    closes = {
        SYMBOL: {
            "2025-01-02": 10.0,
            "2025-07-02": 12.0,
            "2026-01-02": 14.0,
        }
    }

    group = compute_view(trades, closes, as_of=date(2026, 1, 2))["groups"][0]

    assert group["span_days"] == 365
    assert group["invested"] == pytest.approx(1000)
    assert group["proceeds"] == pytest.approx(600)
    assert group["end_value"] == pytest.approx(700)
    assert group["gain"] == pytest.approx(300)
    # 12/10 × 14/12 − 1 = 40%，减仓那 600 不影响
    assert group["twr"] == pytest.approx(0.4)
    assert group["mwr_period"] == pytest.approx(group["xirr"], abs=1e-6)

    # 手算校验：把报出来的年化 IRR 代回现金流，NPV 应归零（xirr 已四舍五入到 6 位）
    flows = [
        (date(2025, 1, 2), -1000.0),
        (date(2025, 7, 2), 600.0),
        (date(2026, 1, 2), 700.0),
    ]
    base = flows[0][0]
    npv = sum(
        amount / (1.0 + group["xirr"]) ** ((day - base).days / 365.0)
        for day, amount in flows
    )
    assert npv == pytest.approx(0.0, abs=1e-3)


# --------------------------------------------------------------------------- #
# 3. 缺行情：excluded + warnings，不补价
# --------------------------------------------------------------------------- #


def test_symbol_without_history_is_excluded_not_priced() -> None:
    trades = [
        _trade(1, day="2025-01-02", shares=100, price=10),
        _trade(2, symbol=OTHER, day="2025-01-02", shares=200, price=5, name="平安银行"),
    ]
    closes = {SYMBOL: _points("2025-01-02", [10, 11, 12])}

    view = compute_view(trades, closes, as_of=date(2025, 1, 6))
    group = view["groups"][0]

    assert group["ok"] is True
    assert group["symbols"] == [{"symbol": SYMBOL, "name": "浦发银行"}]
    assert [row["symbol"] for row in group["excluded"]] == [OTHER]
    assert "daily_k" in group["excluded"][0]["reason"]
    assert any(OTHER in warning for warning in group["warnings"])
    # 没行情的票整只排除：它的 1000 块投入不能混进来
    assert group["invested"] == pytest.approx(1000)
    assert group["end_value"] == pytest.approx(1200)
    assert group["twr"] == pytest.approx(0.2)
    assert [row["symbol"] for row in view["gaps"]] == [OTHER]


def test_symbol_whose_history_starts_after_first_trade_is_excluded() -> None:
    trades = [
        _trade(1, day="2025-01-02", shares=100, price=10),
        _trade(2, symbol=OTHER, day="2025-01-02", shares=200, price=5, name="平安银行"),
    ]
    closes = {
        SYMBOL: _points("2025-01-02", [10, 11]),
        OTHER: _points("2025-06-02", [5, 6]),
    }

    group = compute_view(trades, closes, as_of=date(2025, 6, 3))["groups"][0]

    assert [row["symbol"] for row in group["excluded"]] == [OTHER]
    assert "晚于首笔流水" in group["excluded"][0]["reason"]
    assert group["end"] == "2025-01-03", "组区间只按能估值的票算"


def test_no_price_cache_at_all_marks_group_uncomputable() -> None:
    trades = [_trade(1, day="2025-01-02", shares=100, price=10)]

    view = compute_view(trades, {}, as_of=date(2025, 1, 6))

    assert len(view["groups"]) == 1
    group = view["groups"][0]
    assert group["ok"] is False
    assert group["reason"]
    assert group["twr"] is None
    assert group["end_value"] is None
    assert group["series"] is None
    assert [row["symbol"] for row in group["excluded"]] == [SYMBOL]
    assert [row["symbol"] for row in view["gaps"]] == [SYMBOL]


# --------------------------------------------------------------------------- #
# 4. 空流水 / 数据不对的流水
# --------------------------------------------------------------------------- #


def test_empty_ledger_has_no_group() -> None:
    view = compute_view([], {}, as_of=date(2026, 11, 6))

    assert view["groups"] == []
    assert view["skipped"] == []
    assert EMPTY_NOTE in view["notes"]
    assert view["as_of"] is None


def test_bad_trade_is_skipped_and_does_not_break_the_group() -> None:
    trades = [
        _trade(1, day="2025-01-02", shares=100, price=10),
        _trade(2, day="2025-01-03", shares=100),  # 缺成交价
        _trade(3, symbol="900901", day="2025-01-03", shares=10, price=1),
    ]

    view = compute_view(trades, {SYMBOL: _points("2025-01-02", [10, 11])}, as_of=date(2025, 1, 6))

    assert [row["id"] for row in view["skipped"]] == [2, 3]
    assert view["groups"][0]["ok"] is True
    assert view["groups"][0]["invested"] == pytest.approx(1000)


# --------------------------------------------------------------------------- #
# 5. 分红 / 送转 / 停牌
# --------------------------------------------------------------------------- #


def test_dividend_counts_as_return_not_cash_flow() -> None:
    trades = [
        _trade(1, day="2025-01-02", shares=100, price=10),
        _trade(2, type_="dividend", day="2025-01-03", amount=50),
    ]
    closes = {SYMBOL: _points("2025-01-02", [10, 10, 11])}

    group = compute_view(trades, closes, as_of=date(2025, 1, 4))["groups"][0]

    # 5% 分红 + 10% 价格 = 1.05 × 1.10
    assert group["twr"] == pytest.approx(0.155)
    assert group["dividends"] == pytest.approx(50)
    assert group["gain"] == pytest.approx(150)
    assert group["invested"] == pytest.approx(1000), "分红不改成本"


def test_split_adjusts_shares_without_changing_value() -> None:
    trades = [
        _trade(1, type_="opening", day="2025-01-02", shares=100, price=10),
        _trade(2, type_="split", day="2025-01-03", ratio=2),
    ]
    closes = {SYMBOL: _points("2025-01-02", [10, 5, 6])}

    group = compute_view(trades, closes, as_of=date(2025, 1, 4))["groups"][0]

    assert group["twr"] == pytest.approx(0.2)
    assert group["invested"] == pytest.approx(1000)
    assert group["end_value"] == pytest.approx(1200)


def test_suspension_day_uses_last_close_without_interpolation() -> None:
    trades = [
        _trade(1, day="2025-01-02", shares=100, price=10),
        _trade(2, symbol=OTHER, day="2025-01-02", shares=100, price=5, name="平安银行"),
    ]
    closes = {
        SYMBOL: {"2025-01-02": 10.0, "2025-01-06": 12.0},
        OTHER: {"2025-01-02": 5.0, "2025-01-03": 5.0, "2025-01-06": 5.0},
    }

    group = compute_view(trades, closes, as_of=date(2025, 1, 6))["groups"][0]

    # 01-03 没有本票的行，按 01-02 的收盘价估值，不插值也不补 0
    assert group["series"]["dates"] == ["2025-01-02", "2025-01-03", "2025-01-06"]
    assert group["twr"] == pytest.approx(200 / 1500, abs=1e-6)


def test_single_trading_day_has_no_series() -> None:
    trades = [_trade(1, day="2025-01-02", shares=100, price=10)]

    group = compute_view(
        trades, {SYMBOL: _points("2025-01-02", [10])}, as_of=date(2025, 1, 2)
    )["groups"][0]

    assert group["ok"] is True
    assert group["series"] is None
    assert group["twr"] == 0.0
    assert group["mwr_period"] is None
    assert any("1 个交易日" in warning for warning in group["warnings"])


# --------------------------------------------------------------------------- #
# 6. days 只裁剪走势，不改指标区间
# --------------------------------------------------------------------------- #


def test_days_only_trims_the_series() -> None:
    length = 400
    closes = {SYMBOL: _points("2024-01-01", [10 + index * 0.01 for index in range(length)])}
    end = date(2024, 1, 1) + timedelta(days=length - 1)
    trades = [_trade(1, day="2024-01-01", shares=100, price=10)]

    group = compute_view(trades, closes, as_of=end, days=30)["groups"][0]

    assert group["start"] == "2024-01-01"
    assert group["span_days"] == length - 1
    assert len(group["series"]["dates"]) == 30
    assert group["series"]["dates"][-1] == end.isoformat()
    assert any("走势只画最近 30 天" in note for note in group["notes"])


def test_trades_after_last_close_are_reported_not_priced() -> None:
    trades = [
        _trade(1, day="2025-01-02", shares=100, price=10),
        _trade(2, day="2025-01-08", shares=100, price=10),
    ]
    closes = {SYMBOL: _points("2025-01-02", [10, 10, 10])}

    group = compute_view(trades, closes, as_of=date(2025, 1, 8))["groups"][0]

    assert group["end"] == "2025-01-04"
    assert group["invested"] == pytest.approx(1000)
    assert any("晚于本地日线最后一根 K 线" in warning for warning in group["warnings"])
