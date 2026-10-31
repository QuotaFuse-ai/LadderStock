"""已实现盈亏（移动加权平均成本）的手算对照（QUOT-292 / DEC-038）。

数字全部预先手算，不拿跑出来的值反过来写测试（同 `test_position.py` 的纪律）：

- **部分卖出后单价不变**：买 1000@10 + 买 500@16 → 单价 12.0；卖 600@14 →
  `8400 − 7200 = +1200`，剩余 900 股单价仍是 12.0；
- **加仓摊薄**：买 1000@10 + 买 1000@20 → 单价 15.0；卖 1000@18 →
  `18000 − 15000 = +3000`（FIFO 会算成 `(18 − 10) × 1000 = 8000`）；
- **亏损卖出**：买 100@10，卖 50@8；再买 100@13 → 单价 12.0，卖 150@11 →
  `(8 − 10) × 50 = −100`、`(11 − 12) × 150 = −150`，合计 `−250`；
- **无成交**：空流水 → `total_pnl = null`（**不是 0**：没记过不等于不赚不亏）；
- **只买未卖**：买 100@10 → `0.0`（有成交、实现了 0，与「没有数据」是两件事）；
- **卖出超过在手**：卖 200（前面没有买）；买 100@10 后卖 150@12 →
  只结转在手 100 得 `+200`，未配对 `250`，**不编造成本**；
- **全部清仓**：买 1000@10 + 买 500@16，卖 1500@14 → `21000 − 18000 = +3000`，
  与结转法无关，所以等于 FIFO 的数。
"""

from __future__ import annotations

from typing import Any

from ladderstock.journal.realized import METHOD, compute_realized


def _trade(
    side: str,
    price: float,
    qty: float,
    *,
    code: str = "600000",
) -> dict[str, Any]:
    """构造一条 `list_trades` 形状的行（回放只用 code / side / price / qty）。"""
    return {"code": code, "side": side, "price": price, "qty": qty}


def _pnl(summary: dict[str, Any], code: str = "600000") -> float:
    return float(summary["codes"][code]["realized_pnl"])


# --------------------------------------------------------------------------- #
# 1. 移动加权的核心：结转按卖出时点单价，剩余单价不变
# --------------------------------------------------------------------------- #


def test_partial_sell_carries_rolling_cost_and_keeps_unit_price() -> None:
    summary = compute_realized(
        [_trade("buy", 10.0, 1000.0), _trade("buy", 16.0, 500.0), _trade("sell", 14.0, 600.0)]
    )

    # (10000 + 8000) / 1500 = 12.0 → 600 × 12 = 7200 结转成本
    assert _pnl(summary) == 1200.0
    assert summary["codes"]["600000"]["matched_sell_qty"] == 600.0
    assert summary["codes"]["600000"]["buy_count"] == 2
    assert summary["codes"]["600000"]["sell_count"] == 1

    # 把剩下的 900 股按 12.0 卖掉：正好不赚不亏，才证明剩余成本单价没被改动。
    # FIFO 在这里会算成 (12 − 13.33…) × 900 ≈ −1200（剩余那批是 400@10 + 500@16）。
    closed = compute_realized(
        [
            _trade("buy", 10.0, 1000.0),
            _trade("buy", 16.0, 500.0),
            _trade("sell", 14.0, 600.0),
            _trade("sell", 12.0, 900.0),
        ]
    )
    assert closed["total_pnl"] == 1200.0


def test_add_on_dilutes_cost_instead_of_consuming_the_oldest_lot() -> None:
    summary = compute_realized(
        [_trade("buy", 10.0, 1000.0), _trade("buy", 20.0, 1000.0), _trade("sell", 18.0, 1000.0)]
    )

    # 单价 15.0 → 18000 − 15000；FIFO 口径会是 8000，两种方法在余下 1000 股上差 5000
    assert _pnl(summary) == 3000.0


def test_loss_sell_is_negative() -> None:
    summary = compute_realized(
        [
            _trade("buy", 10.0, 100.0),
            _trade("sell", 8.0, 50.0),  # 单价 10 → (8−10)×50 = −100，剩 50@10
            _trade("buy", 13.0, 100.0),  # (500 + 1300) / 150 = 单价 12.0
            _trade("sell", 11.0, 150.0),  # (11−12)×150 = −150
        ]
    )

    assert _pnl(summary) == -250.0
    assert summary["total_pnl"] == -250.0
    assert summary["matched_sell_qty"] == 200.0


# --------------------------------------------------------------------------- #
# 2. 空态与「没卖出去」：null 与 0 是两件事
# --------------------------------------------------------------------------- #


def test_no_trades_returns_null_not_zero() -> None:
    summary = compute_realized([])

    assert summary["total_pnl"] is None  # 给 0 就等于把「没记过」显示成「不赚不亏」
    assert summary["status"] == "missing"
    assert summary["reason"] and "null" in summary["reason"]
    assert summary["codes"] == {}
    assert summary["buy_count"] == 0 and summary["sell_count"] == 0


def test_buys_without_sells_is_zero() -> None:
    summary = compute_realized([_trade("buy", 10.0, 100.0)])

    assert summary["total_pnl"] == 0.0
    assert summary["status"] == "ok"
    assert summary["codes"]["600000"]["realized_pnl"] == 0.0
    assert summary["matched_sell_qty"] == 0.0


def test_method_and_fee_basis_are_stated_on_the_block() -> None:
    summary = compute_realized([_trade("buy", 10.0, 100.0)])

    # `trades` 表没有费用列（DEC-038 §2.4）：口径必须自己写明是毛额，别被当成对账单最终数
    assert summary["method"] == METHOD == "moving_weighted_average"
    assert summary["fee_included"] is False


# --------------------------------------------------------------------------- #
# 3. 卖出超过在手：只结转有成本的部分
# --------------------------------------------------------------------------- #


def test_oversell_does_not_invent_cost() -> None:
    summary = compute_realized(
        [
            _trade("sell", 10.0, 200.0),  # 流水从持仓中途开始记：没有买入可结转
            _trade("buy", 10.0, 100.0),
            _trade("sell", 12.0, 150.0),  # 在手 100 → (12−10)×100 = +200，另 50 未配对
        ]
    )

    assert _pnl(summary) == 200.0
    assert summary["unmatched_sell_qty"] == 250.0
    assert summary["matched_sell_qty"] == 100.0
    assert summary["codes"]["600000"]["unmatched_sell_qty"] == 250.0


def test_full_close_leaves_no_float_residue() -> None:
    """清仓后残余的浮点尾巴必须归零：否则下一次买入接着一个 -1e-16 的成本额。"""
    summary = compute_realized(
        [
            _trade("buy", 10.0, 3.0),
            _trade("sell", 10.0, 3.0),
            _trade("buy", 7.0, 1.0),
            _trade("sell", 7.0, 1.0),
        ]
    )

    assert _pnl(summary) == 0.0


# --------------------------------------------------------------------------- #
# 4. 分 code 与「与成本法无关」的那个数
# --------------------------------------------------------------------------- #


def test_codes_do_not_borrow_each_other_cost() -> None:
    summary = compute_realized(
        [
            _trade("buy", 10.0, 100.0, code="600000"),
            _trade("buy", 16.0, 100.0, code="000001"),
            _trade("sell", 12.0, 100.0, code="600000"),  # (12−10)×100 = +200
        ]
    )

    assert summary["codes"]["600000"]["realized_pnl"] == 200.0
    assert summary["codes"]["000001"]["realized_pnl"] == 0.0
    assert summary["total_pnl"] == 200.0
    assert summary["buy_count"] == 2 and summary["sell_count"] == 1


def test_full_liquidation_is_method_independent() -> None:
    """全部清仓时已实现 = 卖出总额 − 买入总额，与结转法无关：这条是与 FIFO 对账的锚。"""
    trades = [
        _trade("buy", 10.0, 1000.0),
        _trade("buy", 16.0, 500.0),
        _trade("sell", 14.0, 1500.0),
    ]
    summary = compute_realized(trades)

    proceeds = sum(t["qty"] * t["price"] for t in trades if t["side"] == "sell")
    cost = sum(t["qty"] * t["price"] for t in trades if t["side"] == "buy")
    assert summary["total_pnl"] == 3000.0 == proceeds - cost
    assert summary["unmatched_sell_qty"] == 0.0
