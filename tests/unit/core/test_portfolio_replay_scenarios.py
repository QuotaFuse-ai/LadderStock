"""QUOT-466：情景回放的核心口径。

钉住 issue 验收里点名的四件事：

1. 单只持仓的区间收益与手算一致；
2. 最大回撤的峰 / 谷日期取路径上的日期；
3. 缺窗口端点 → 整只进 `gaps`，组合只按其余持仓算；
4. 覆盖率不足 → `unavailable`，`portfolio` / `path` 都为 `null`。

纯函数 `compute_replay()` 只吃「流水 + 收盘价序列」，不碰磁盘。
"""

from __future__ import annotations

import pytest

from ladderstock.core.portfolio import Trade
from ladderstock.core.portfolio_replay import MIN_COVERAGE, compute_replay

SCENARIO = "2015-crash"


def _holding(symbol: str, shares: float, price: float, name: str = "") -> Trade:
    return Trade(
        id=1,
        symbol=symbol,
        name=name or symbol,
        type="opening",
        date="2015-01-05",
        shares=shares,
        price=price,
    )


def _only(body: dict) -> dict:
    return body["groups"][0]["scenarios"][0]


def test_single_holding_return_matches_hand_calculation() -> None:
    trades = [_holding("600000", 100, 10.0, "浦发银行")]
    closes = {
        "600000": {
            "2015-06-12": 10.0,
            "2015-07-01": 9.0,
            "2015-08-26": 8.0,
        }
    }

    body = compute_replay(trades, closes, scenario=SCENARIO)
    scenario = _only(body)

    assert scenario["unavailable"] is None
    assert scenario["portfolio"]["return"] == pytest.approx(-0.2)
    assert scenario["coverage"] == pytest.approx(1.0)
    assert scenario["included"] == 1
    assert scenario["holdings"][0]["return"] == pytest.approx(-0.2)
    assert scenario["holdings"][0]["contribution"] == pytest.approx(-0.2)
    assert scenario["portfolio"]["days"] == 3
    assert scenario["path"]["portfolio"][0] == pytest.approx(1.0)
    assert scenario["path"]["portfolio"][-1] == pytest.approx(0.8)


def test_max_drawdown_peak_and_trough_are_path_dates() -> None:
    trades = [_holding("600000", 100, 10.0)]
    closes = {
        "600000": {
            "2015-06-12": 10.0,
            "2015-06-15": 12.0,
            "2015-06-16": 9.0,
            "2015-08-26": 11.0,
        }
    }

    scenario = _only(compute_replay(trades, closes, scenario=SCENARIO))

    assert scenario["portfolio"]["max_drawdown"] == pytest.approx(9.0 / 12.0 - 1.0)
    assert scenario["portfolio"]["max_drawdown_peak"] == "2015-06-15"
    assert scenario["portfolio"]["max_drawdown_trough"] == "2015-06-16"
    assert scenario["portfolio"]["worst_day"] == pytest.approx(9.0 / 12.0 - 1.0)
    assert scenario["portfolio"]["worst_day_date"] == "2015-06-16"
    assert scenario["portfolio"]["worst_day_date"] in scenario["path"]["dates"]
    assert scenario["portfolio"]["days"] == len(scenario["path"]["dates"])


def test_holding_missing_window_endpoint_goes_to_gaps() -> None:
    trades = [
        _holding("600000", 100, 10.0, "浦发银行"),
        Trade(
            id=2,
            symbol="000001",
            name="平安银行",
            type="opening",
            date="2015-01-05",
            shares=10,
            price=2.0,
        ),
    ]
    closes = {
        "600000": {"2015-06-12": 10.0, "2015-06-15": 10.5, "2015-08-26": 11.0},
        "000001": {"2015-06-15": 2.0, "2015-08-26": 2.2},
    }

    scenario = _only(compute_replay(trades, closes, scenario=SCENARIO))

    assert [row["symbol"] for row in scenario["holdings"]] == ["600000"]
    assert [row["symbol"] for row in scenario["gaps"]] == ["000001"]
    assert "窗口首个交易日 2015-06-12 没有收盘价" in scenario["gaps"][0]["reason"]
    assert scenario["coverage"] == pytest.approx(1100.0 / 1122.0)
    assert scenario["portfolio"]["return"] == pytest.approx(0.1)
    assert scenario["included"] == 1


def test_coverage_below_floor_hides_all_portfolio_numbers() -> None:
    trades = [
        _holding("600000", 100, 3.0, "浦发银行"),
        Trade(
            id=2,
            symbol="000001",
            name="平安银行",
            type="opening",
            date="2015-01-05",
            shares=100,
            price=7.0,
        ),
    ]
    closes = {
        "600000": {"2015-06-12": 3.0, "2015-08-26": 3.0},
        "000001": {"2015-08-26": 7.0},
    }

    scenario = _only(compute_replay(trades, closes, scenario=SCENARIO))

    assert scenario["coverage"] < MIN_COVERAGE
    assert scenario["unavailable"]
    assert scenario["portfolio"] is None
    assert scenario["path"] is None
    assert scenario["included"] == 1
    assert [row["symbol"] for row in scenario["holdings"]] == ["600000"]
    assert [row["symbol"] for row in scenario["gaps"]] == ["000001"]


def test_no_daily_cache_marks_every_holding_as_gap() -> None:
    trades = [_holding("600000", 100, 10.0)]

    body = compute_replay(trades, {}, scenario=SCENARIO)
    scenario = _only(body)

    assert body["as_of"] is None
    assert body["groups"][0]["total_value"] is None
    assert scenario["unavailable"]
    assert scenario["gaps"][0]["reason"] == "本地 daily_k 没有收盘价"
    assert scenario["portfolio"] is None
    assert scenario["path"] is None


def test_empty_ledger_returns_empty_groups() -> None:
    body = compute_replay([], {}, scenario="all")

    assert body["groups"] == []
    assert body["as_of"] is None
    assert "还没有交易流水" in body["notes"][0]
    assert body["sources"] == []


def test_unknown_scenario_raises_value_error() -> None:
    with pytest.raises(ValueError):
        compute_replay([], {}, scenario="nope")
