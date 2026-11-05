"""QUOT-430 / DEC-056：交易流水重放的纯函数契约。

这里钉住参考实现（`orchestrator/src/finance/holdings.ts`）的每一条口径：

1. **两种成本法**在「买入 → 卖出 → 再买入」下持有数量相同、成本与已实现盈亏不同；
2. **送转**放大股数、摊薄单位成本，总成本不变；同一天先送转再成交；
3. **超卖**进 `issues`、该笔不参与计算，之后流水照常重放；
4. **分红**单独记收入，不改持仓成本；清仓后代码进 `closed`；
5. **费用**计入买入成本、从卖出所得里扣；
6. **期初成本可为负**（分红 / 送转摊薄后真实存在）；
7. 字段不完整的流水逐条进 `issues`，不猜不补；B 股明确拒收；
8. 输出金额四舍五入到 4 位，顺序确定（日期 → 先送转 → `created_at` → `id`）。
"""

from __future__ import annotations

import pytest

from ladderstock.core.portfolio import (
    B_SHARE_UNSUPPORTED,
    COST_METHODS,
    Trade,
    replay,
    replay_order_key,
    trade_problem,
)


def trade(
    trade_id: int,
    kind: str,
    day: str = "2026-01-05",
    *,
    symbol: str = "600519",
    shares: float | None = None,
    price: float | None = None,
    fee: float | None = None,
    amount: float | None = None,
    ratio: float | None = None,
    created_at: str = "2026-01-05T09:30:00+08:00",
    name: str = "",
) -> Trade:
    return Trade(
        id=trade_id,
        symbol=symbol,
        name=name,
        type=kind,
        date=day,
        shares=shares,
        price=price,
        fee=fee,
        amount=amount,
        ratio=ratio,
        created_at=created_at,
    )


# --------------------------------------------------------------------------- #
# 两种成本法
# --------------------------------------------------------------------------- #


def test_fifo_and_average_differ_in_cost_but_hold_same_shares() -> None:
    """买入 → 卖出 → 再买入：数量相同，成本与已实现盈亏按成本法不同。"""
    trades = [
        trade(1, "buy", "2026-01-01", shares=100, price=10),
        trade(2, "buy", "2026-01-02", shares=100, price=20),
        trade(3, "sell", "2026-01-03", shares=100, price=30),
        trade(4, "buy", "2026-01-04", shares=100, price=5),
    ]

    fifo = replay(trades, "fifo")
    average = replay(trades, "average")

    fifo_row = fifo.holdings[0]
    average_row = average.holdings[0]
    assert fifo_row.shares == average_row.shares == 200.0
    # FIFO 卖掉的是 10 元那一批；平均值法按 15 元均价卖
    assert fifo.realized[0].cost == 1000.0
    assert fifo.realized[0].pnl == 2000.0
    assert average.realized[0].cost == 1500.0
    assert average.realized[0].pnl == 1500.0
    # 剩下的批次：FIFO 剩 20×100 + 5×100；平均值法剩 15×100 + 5×100
    assert fifo_row.cost_total == 2500.0
    assert fifo_row.unit_cost == 12.5
    assert average_row.cost_total == 2000.0
    assert average_row.unit_cost == 10.0
    assert [lot.unit_cost for lot in fifo_row.lots] == [20.0, 5.0]
    assert average_row.lots == []
    assert fifo.method == "fifo"
    assert set(COST_METHODS) == {"average", "fifo"}


def test_fifo_consumes_lots_in_order() -> None:
    trades = [
        trade(1, "buy", "2026-01-01", shares=100, price=10),
        trade(2, "buy", "2026-01-02", shares=100, price=20),
        trade(3, "sell", "2026-01-03", shares=150, price=30),
    ]
    row = replay(trades, "fifo").holdings[0]
    assert row.shares == 50.0
    assert row.lots[0].shares == 50.0
    assert row.lots[0].unit_cost == 20.0
    assert row.cost_total == 1000.0


# --------------------------------------------------------------------------- #
# 送转
# --------------------------------------------------------------------------- #


def test_split_scales_shares_and_dilutes_unit_cost() -> None:
    trades = [
        trade(1, "buy", "2026-01-01", shares=100, price=10),
        trade(2, "split", "2026-01-02", ratio=1.5),
    ]
    for method in ("fifo", "average"):
        row = replay(trades, method).holdings[0]
        assert row.shares == 150.0
        assert row.unit_cost == 6.6667, "单位成本四舍五入到 4 位"
        assert row.cost_total == 1000.0


def test_same_day_split_is_applied_before_trades() -> None:
    """除权日的成交已按新股本计价：同日先送转，再按录入顺序。"""
    trades = [
        trade(1, "buy", "2026-01-01", shares=100, price=10),
        trade(2, "sell", "2026-01-10", shares=200, price=7),
        trade(3, "split", "2026-01-10", ratio=2),
    ]
    result = replay(trades, "average")
    assert result.issues == [], "先送转之后 200 股才够卖，不该报卖超"
    assert result.holdings == []
    assert result.closed[0].symbol == "600519"
    assert result.realized[0].shares == 200.0
    assert result.realized[0].pnl == 400.0


# --------------------------------------------------------------------------- #
# 超卖 / 问题流水
# --------------------------------------------------------------------------- #


def test_oversell_goes_to_issues_and_does_not_block_later_trades() -> None:
    trades = [
        trade(1, "buy", "2026-01-01", shares=100, price=10),
        trade(2, "sell", "2026-01-02", shares=200, price=12),
        trade(3, "buy", "2026-01-03", shares=100, price=11),
    ]
    result = replay(trades, "average")
    assert [issue.id for issue in result.issues] == [2]
    assert "超过当时持有" in result.issues[0].why
    assert result.holdings[0].shares == 200.0
    assert result.realized == []


def test_float_tolerance_does_not_report_oversell() -> None:
    trades = [
        trade(1, "buy", "2026-01-01", shares=100, price=10),
        trade(2, "split", "2026-01-02", ratio=3),
        trade(3, "sell", "2026-01-03", shares=300, price=4),
    ]
    result = replay(trades, "average")
    assert result.issues == []
    assert result.holdings == []


@pytest.mark.parametrize(
    ("bad", "why"),
    [
        (trade(1, "buy", "2026-13-01", shares=100, price=10), "不是有效日期"),
        (trade(1, "buy", "2026-02-30", shares=100, price=10), "不是有效日期"),
        (trade(1, "buy", shares=0, price=10), "大于 0 的数量"),
        (trade(1, "buy", shares=100, price=0), "大于 0 的成交价"),
        (trade(1, "dividend", amount=0), "到账金额"),
        (trade(1, "split", ratio=0), "比例"),
        (trade(1, "wat", shares=1, price=1), "类型"),
        (trade(1, "buy", shares=1, price=1, symbol="900901"), B_SHARE_UNSUPPORTED),
    ],
)
def test_invalid_trade_is_reported_not_silently_skipped(bad: Trade, why: str) -> None:
    result = replay([bad], "average")
    assert len(result.issues) == 1
    assert why in result.issues[0].why
    assert result.holdings == []


def test_trade_problem_accepts_negative_opening_cost() -> None:
    assert trade_problem(trade(1, "opening", shares=100, price=-2.5)) is None
    result = replay([trade(1, "opening", shares=100, price=-2.5)], "average")
    row = result.holdings[0]
    assert row.cost_total == -250.0
    assert row.unit_cost == -2.5


# --------------------------------------------------------------------------- #
# 分红 / 费用 / 清仓
# --------------------------------------------------------------------------- #


def test_dividend_is_income_and_does_not_change_cost() -> None:
    trades = [
        trade(1, "buy", "2026-01-01", shares=100, price=10),
        trade(2, "dividend", "2026-01-02", amount=88),
    ]
    row = replay(trades, "average").holdings[0]
    assert row.dividends == 88.0
    assert row.cost_total == 1000.0
    assert row.unit_cost == 10.0


def test_buy_fee_enters_cost_and_sell_fee_reduces_proceeds() -> None:
    trades = [
        trade(1, "buy", "2026-01-01", shares=100, price=10, fee=5),
        trade(2, "sell", "2026-01-02", shares=100, price=12, fee=6),
    ]
    result = replay(trades, "average")
    assert result.holdings == []
    realized = result.realized[0]
    assert realized.proceeds == 1194.0
    assert realized.cost == 1005.0
    assert realized.pnl == 189.0
    closed = result.closed[0]
    assert closed.realized_pnl == 189.0
    assert closed.fees == 11.0
    assert closed.last_date == "2026-01-02"


def test_closed_row_only_when_realized_or_dividends() -> None:
    """没赚钱也没分红的代码不进 `closed`（普通买卖不算「有过历史」）。"""
    trades = [
        trade(1, "buy", "2026-01-01", shares=100, price=10),
        trade(2, "sell", "2026-01-02", shares=100, price=10),
    ]
    result = replay(trades, "average")
    assert result.holdings == []
    assert result.closed == []


# --------------------------------------------------------------------------- #
# 顺序 / 输出精度
# --------------------------------------------------------------------------- #


def test_order_is_date_then_split_then_created_at_then_id() -> None:
    early = trade(2, "sell", "2026-01-02", shares=10, price=10, created_at="2026-01-02T09:00:00")
    late = trade(1, "buy", "2026-01-02", shares=10, price=10, created_at="2026-01-02T10:00:00")
    split = trade(3, "split", "2026-01-02", ratio=2, created_at="2026-01-02T23:00:00")
    ordered = sorted([early, late, split], key=replay_order_key)
    assert [item.id for item in ordered] == [3, 2, 1], "同日先送转，其余按 created_at"


def test_amounts_are_rounded_to_four_decimals() -> None:
    trades = [
        trade(1, "buy", "2026-01-01", shares=3, price=10, fee=0.00001),
        trade(2, "sell", "2026-01-02", shares=1, price=3.333333),
    ]
    result = replay(trades, "average")
    row = result.holdings[0]
    for value in (row.shares, row.unit_cost, row.cost_total, row.realized_pnl):
        assert value == round(value, 4)
    assert result.realized[0].proceeds == round(3.333333, 4)


def test_replay_is_deterministic_for_same_input() -> None:
    trades: list[Trade] = [
        trade(1, "buy", "2026-01-01", shares=100, price=10),
        trade(2, "dividend", "2026-01-02", amount=1),
    ]
    assert replay(trades, "fifo") == replay(list(trades), "fifo")


def test_unknown_cost_method_raises() -> None:
    with pytest.raises(ValueError):
        replay([], "weighted")  # type: ignore[arg-type]


def test_name_follows_latest_non_empty() -> None:
    result = replay(
        [
            trade(1, "buy", "2026-01-01", shares=1, price=1, name="贵州茅台"),
            trade(2, "dividend", "2026-01-02", amount=1, name=""),
        ],
        "average",
    )
    assert result.holdings[0].name == "贵州茅台"
