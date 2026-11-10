"""QUOT-500 A 股规则各一条测试（涨停买不进 / 跌停卖不出 / 停牌跳过 / T+1）。

真实 600000（2024-03 ~ 2026-11）没有任何涨停/跌停/停牌日，所以这四条**只能**用人造 K 线
钉死。每条测试都同时断言「引擎行为」和「日志标记」——只看收益数字很容易被别的规则
顶替成红，必须确认 `limit_up_blocked` / `limit_down_blocked` / `suspended` 被真正置位。

口径（``match.py`` 模块说明）：

- 涨跌停以**昨收**为基准，主板 10%；开盘价四舍五入到 0.01 后 >= 涨停价 → 买不进；
- 跌停同理：开盘价 <= 跌停价 → 卖不出（价格已在板里，出不来）；
- 停牌口径 ``volume == 0``：当日不下单、持仓不动、不补价、不延后；
- T+1：当日买入股数当日不可卖，次一交易日起解冻。
"""

from __future__ import annotations

from ladderstock.backtest.match import CostModel, Engine, limit_prices
from ladderstock.backtest.universe import classify
from tests.unit.backtest.conftest import Scripted, flat_bar

NO_COST = CostModel(commission=0.0, slippage=0.0, stamp_duty=0.0)
REAL_COST = CostModel(commission=0.00025, slippage=0.001, stamp_duty=0.001)
CASH = 1_000_000.0


def _engine(strategy: Scripted, *, costs: CostModel = NO_COST, limit_pct: float = 0.10) -> Engine:
    return Engine(strategy=strategy, costs=costs, initial_cash=CASH, limit_pct=limit_pct)


# --------------------------------------------------------------------------- #
# 1. 涨停买不进
# --------------------------------------------------------------------------- #

def test_limit_up_blocks_buy() -> None:
    # 昨收 10.00 ⇒ 涨停价 11.00；执行日开盘正好 11.00（一字涨停）⇒ 买不进
    bars = [
        flat_bar("2025-05-06", 10.0),
        flat_bar("2025-05-07", 11.0),
        flat_bar("2025-05-08", 11.5),
    ]
    assert limit_prices(10.0, 0.10) == (11.0, 9.0)  # 先钉死基准价算对了
    # 只在 05-06 给满仓信号；05-07 / 05-08 都回到空仓 ⇒ 整个区间不该有任何成交
    strategy = Scripted({"2025-05-06": 1.0})
    result = _engine(strategy).run(bars)

    blocked = next(row for row in result.log if row.date == "2025-05-07")
    assert blocked.limit_up_blocked is True
    assert blocked.executed_side == ""
    assert blocked.shares_after == 0
    # 被顶掉的信号不会在下一根「补成交」（策略 05-07 已经不再看多）
    assert all(row.executed_side != "buy" for row in result.log)
    assert result.trades == []


def test_limit_up_does_not_block_buy_below_limit() -> None:
    # 同样的信号，开盘 10.99（差一分钱没封死）⇒ 必须成交，不能把涨停口径当成一刀切
    bars = [
        flat_bar("2025-05-06", 10.0),
        flat_bar("2025-05-07", 10.99, open_price=10.99, high=11.0),
        flat_bar("2025-05-08", 11.0),
    ]
    strategy = Scripted({"2025-05-06": 1.0})
    result = _engine(strategy).run(bars)
    bought = next(row for row in result.log if row.executed_side == "buy")
    assert bought.date == "2025-05-07"
    assert bought.limit_up_blocked is False


# --------------------------------------------------------------------------- #
# 2. 跌停卖不出
# --------------------------------------------------------------------------- #

def test_limit_down_blocks_sell() -> None:
    # Scripted 按**信号日**查表、成交落在下一根（信号 t / 执行 t+1）：
    #   05-06 信号满仓 ⇒ 05-07 开盘 10.00 买入（当日 T+1，不可卖）；
    #   05-07 信号空仓 ⇒ 05-08 想卖，但 05-07 收盘 10.00 ⇒ 跌停价 9.00，05-08 开盘 9.00 ⇒ 卖不出；
    #   05-09 开盘 9.20 不再跌停，空仓信号仍有效 ⇒ 这天真正卖出。
    bars = [
        flat_bar("2025-05-06", 10.0),
        flat_bar("2025-05-07", 10.0),
        flat_bar("2025-05-08", 9.0),
        flat_bar("2025-05-09", 9.2),
    ]
    strategy = Scripted({"2025-05-06": 1.0, "2025-05-07": 0.0, "2025-05-08": 0.0})
    result = _engine(strategy).run(bars)

    buy_day = next(row for row in result.log if row.date == "2025-05-07")
    assert buy_day.executed_side == "buy"

    sell_day = next(row for row in result.log if row.date == "2025-05-08")
    assert sell_day.limit_down_blocked is True
    assert sell_day.t_plus_one_blocked is False  # 挡住了，但挡它的是跌停不是 T+1
    assert sell_day.executed_side == ""
    assert sell_day.shares_after > 0  # 没卖出去，持仓还在

    next_day = next(row for row in result.log if row.date == "2025-05-09")
    assert next_day.executed_side == "sell"
    assert next_day.shares_after == 0
    assert len(result.trades) == 1


def test_limit_down_allows_sell_above_limit_price() -> None:
    # 跌停价 9.00（昨收 10.00），开盘 9.01 差一分钱没封死 ⇒ 必须卖得出去
    bars = [
        flat_bar("2025-05-06", 10.0),
        flat_bar("2025-05-07", 10.0),
        flat_bar("2025-05-08", 9.01),
        flat_bar("2025-05-09", 9.2),
    ]
    strategy = Scripted({"2025-05-06": 1.0, "2025-05-07": 0.0})
    result = _engine(strategy).run(bars)
    sell_day = next(row for row in result.log if row.date == "2025-05-08")
    assert sell_day.executed_side == "sell"
    assert sell_day.limit_down_blocked is False
    assert sell_day.shares_after == 0


# --------------------------------------------------------------------------- #
# 3. 停牌跳过
# --------------------------------------------------------------------------- #

def test_suspension_skips_day_without_filling() -> None:
    # 06-03 成交量 0（停牌）：那天本应执行 06-02 的满仓信号，但停牌不下单、也不延后；
    # 06-03 收盘策略已回到空仓 ⇒ 06-04 不补成交。
    bars = [
        flat_bar("2025-06-02", 10.0),
        flat_bar("2025-06-03", 10.0, volume=0.0),
        flat_bar("2025-06-04", 10.1),
    ]
    strategy = Scripted({"2025-06-02": 1.0, "2025-06-03": 0.0})
    result = _engine(strategy).run(bars)

    halted = next(row for row in result.log if row.date == "2025-06-03")
    assert halted.suspended is True
    assert halted.executed_side == ""
    assert halted.shares_after == 0
    # 停牌不延后：06-04 不会把 06-02 的信号补回来
    next_day = next(row for row in result.log if row.date == "2025-06-04")
    assert next_day.executed_side == ""
    assert result.trades == []

def test_suspension_keeps_existing_position_untouched() -> None:
    # 先买入，再停牌：停牌日持仓保持，权益按停牌日收盘价（= 前收）不动；
    # 恢复交易后的**下一根**可交易日才按（仍然有效的）空仓信号卖出。
    bars = [
        flat_bar("2025-06-09", 10.0),
        flat_bar("2025-06-10", 10.2),
        flat_bar("2025-06-11", 10.2, volume=0.0),
        flat_bar("2025-06-12", 10.4),
    ]
    strategy = Scripted({"2025-06-09": 1.0, "2025-06-10": 0.0})  # 06-11 想卖，但停牌
    result = _engine(strategy).run(bars)

    bought = next(row for row in result.log if row.date == "2025-06-10")
    assert bought.executed_side == "buy"
    halted = next(row for row in result.log if row.date == "2025-06-11")
    assert halted.suspended is True
    assert halted.executed_side == ""
    assert halted.shares_after == bought.shares_after
    assert halted.equity_after == bought.equity_after  # 价格没变，权益也不变
    # 06-12 恢复交易：空仓信号仍然有效 ⇒ 卖出（停牌不吞掉信号）
    resumed = next(row for row in result.log if row.date == "2025-06-12")
    assert resumed.suspended is False
    assert resumed.executed_side == "sell"
    assert len(result.trades) == 1


# --------------------------------------------------------------------------- #
# 4. T+1
# --------------------------------------------------------------------------- #

def test_t_plus_one_blocks_same_day_sell() -> None:
    # 05-12 收盘信号满仓 ⇒ 05-13 开盘买入；05-13 收盘已翻空 ⇒ 05-14 开盘卖出（T+1 刚好放行）。
    # 若引擎允许当日回转，05-13 会先买后卖、最终空仓 —— 用 t_plus_one_blocked 与成交日钉死。
    bars = [
        flat_bar("2025-05-12", 10.0),
        flat_bar("2025-05-13", 10.1),
        flat_bar("2025-05-14", 10.2),
    ]
    strategy = Scripted({"2025-05-12": 1.0, "2025-05-13": 0.0})
    result = _engine(strategy).run(bars)

    buy_day = next(row for row in result.log if row.date == "2025-05-13")
    assert buy_day.executed_side == "buy"
    # 买入当根不得卖出：同一根 K 线既买又卖是不可能的（日频 + T+1）
    assert all(
        not (row.executed_side == "buy" and row.shares_after == 0) for row in result.log
    )
    sell_day = next(row for row in result.log if row.date == "2025-05-14")
    assert sell_day.executed_side == "sell"
    assert sell_day.t_plus_one_blocked is False  # 次日已解冻，卖单不再被 T+1 挡
    trade = result.trades[0]
    assert trade.open_date == "2025-05-13"
    assert trade.close_date == "2025-05-14"
    assert trade.open_date < trade.close_date


def test_t_plus_one_never_has_intraday_round_trip() -> None:
    # 交替信号：任何一笔回合的开、平仓日都不可能同一天
    bars = [
        flat_bar(f"2025-07-{day:02d}", 10.0 + (day % 3) * 0.1) for day in range(1, 15)
    ]
    table = {bar.date: (1.0 if index % 2 == 0 else 0.0) for index, bar in enumerate(bars[:-1])}
    result = _engine(Scripted(table), costs=REAL_COST).run(bars)
    assert result.trades
    assert all(trade.open_date != trade.close_date for trade in result.trades)
    assert all(trade.open_date < trade.close_date for trade in result.trades)


# --------------------------------------------------------------------------- #
# 涨跌停幅度：主板 10% / 创业板 20%（同一撮合引擎，幅度由标的决定）
# --------------------------------------------------------------------------- #

def test_chinext_limit_is_20_percent() -> None:
    main = classify("600000")
    chinext = classify("300750")
    assert main.limit_pct() == 0.10
    assert chinext.limit_pct() == 0.20
    # 主板 10% 涨停（11.00）在创业板不算涨停：同样的开盘价必须能买进
    bars = [
        flat_bar("2025-08-04", 10.0),
        flat_bar("2025-08-05", 11.0),
        flat_bar("2025-08-06", 11.2),
    ]
    strategy = Scripted({"2025-08-04": 1.0})
    main_result = _engine(strategy, limit_pct=main.limit_pct()).run(bars)
    chinext_result = _engine(Scripted({"2025-08-04": 1.0}), limit_pct=chinext.limit_pct()).run(bars)
    main_day = next(row for row in main_result.log if row.date == "2025-08-05")
    chinext_day = next(row for row in chinext_result.log if row.date == "2025-08-05")
    assert main_day.limit_up_blocked is True
    assert main_day.executed_side == ""
    assert chinext_day.limit_up_blocked is False
    assert chinext_day.executed_side == "buy"
