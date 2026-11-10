"""QUOT-500 无未来函数证伪测试：**「知道明天」的策略必须跑不出收益**。

验收口径（用户红线原话）：不允许未来函数（信号 t、执行 t+1，「知道明天」测试策略必须跑不出收益）。

三层证伪，从弱到强：

1. **信号与执行错一天**——策略在 ``t`` 收盘后看好，成交只能落在 ``t+1``；断言成交日
   严格晚于信号日（不是同日、更不是前一日）。
2. **策略看不到执行日**——引擎喂给策略的 ``history`` 最后一根必须 <= 执行日前一根；
   记录策略调用序列，执行日 ``k`` 对应的最后一次可见日期必须是 ``bars[k-1].date``。
3. **「知道明天」跑不出收益**——锯齿序列上，任何「用今天推断明天」的满仓/空仓切换，
   只要成交被推迟一天，就是**一买就跌、一卖就涨**；断言累计收益 < 0，且跑输买入持有。
"""

from __future__ import annotations

from ladderstock.backtest.feed import Bar
from ladderstock.backtest.match import CostModel, Engine
from ladderstock.backtest.score import compute
from tests.unit.backtest.conftest import Scripted, TomorrowOracle, flat_bar, series

NO_COST = CostModel(commission=0.0, slippage=0.0, stamp_duty=0.0)
REAL_COST = CostModel(commission=0.00025, slippage=0.001, stamp_duty=0.001)


# --------------------------------------------------------------------------- #
# 1. 信号 t / 执行 t+1
# --------------------------------------------------------------------------- #

def test_signal_and_fill_are_one_day_apart() -> None:
    bars = series([10.0] * 6, start="2025-01-02")
    # 2025-01-04 收盘后发出「满仓」信号；执行只能落在 2025-01-05
    strategy = Scripted({"2025-01-04": 1.0})
    result = Engine(strategy=strategy, costs=REAL_COST, initial_cash=100_000, limit_pct=0.10).run(
        bars
    )

    buys = [row for row in result.log if row.executed_side == "buy"]
    assert len(buys) == 1, [row.to_dict() for row in result.log]
    assert buys[0].date == "2025-01-05"
    # 信号日当天不得成交（否则就是「收盘信号 + 同日开盘成交」的未来函数）
    signal_row = next(row for row in result.log if row.date == "2025-01-04")
    assert signal_row.executed_side == ""


# --------------------------------------------------------------------------- #
# 2. 策略永远看不到执行日
# --------------------------------------------------------------------------- #

def _recording_strategy(bars: list[Bar]) -> Scripted:
    table = {bar.date: 0.0 for bar in bars}
    return Scripted(table)


def test_strategy_history_never_includes_execution_bar() -> None:
    bars = series([10.0 + index * 0.1 for index in range(8)], start="2025-02-03")
    strategy = _recording_strategy(bars)
    Engine(strategy=strategy, costs=NO_COST, initial_cash=100_000, limit_pct=0.10).run(bars)

    # 第一根不喂策略（index==0 时 target 直接 0），之后每根执行日 k 喂的最后一根都是 bars[k-1]
    expected = [bar.date for bar in bars[:-1]]
    assert strategy.calls == expected
    for execution_day, last_seen in zip((b.date for b in bars[1:]), strategy.calls, strict=True):
        assert last_seen < execution_day


def test_fill_price_uses_open_of_next_day_not_today_close() -> None:
    # 今天的收盘 10.0，明天的开盘跳到 10.5（+5%，没到涨停）：成交价必须以「明天开盘」为基准
    bars = [
        flat_bar("2025-03-03", 10.0),
        flat_bar("2025-03-04", 10.5, open_price=10.5),
        flat_bar("2025-03-05", 10.3),
    ]
    strategy = Scripted({"2025-03-03": 1.0})
    costs = CostModel(commission=0.0, slippage=0.0, stamp_duty=0.0)
    result = Engine(strategy=strategy, costs=costs, initial_cash=1_000_000, limit_pct=0.10).run(
        bars
    )

    buy = next(row for row in result.log if row.executed_side == "buy")
    assert buy.date == "2025-03-04"
    assert buy.executed_price == 10.5  # 用 3-04 的开盘，不是 3-03 的收盘 10.0


# --------------------------------------------------------------------------- #
# 3. 「知道明天」的锯齿作弊策略：净收益必须为负
# --------------------------------------------------------------------------- #

def _sawtooth(count: int, *, base: float = 10.0, step: float = 0.5) -> list[Bar]:
    """隔天交替 ±step 的锯齿：上一根跌 ⇒ 下一根必涨，反之亦然（纯价格世界里的 100% 先验）。"""
    bars: list[Bar] = []
    price = base
    from datetime import date, timedelta

    begin = date.fromisoformat("2025-04-01")
    for index in range(count):
        if index:
            price += step if index % 2 else -step
        bars.append(flat_bar((begin + timedelta(days=index)).isoformat(), round(price, 2)))
    return bars


def test_tomorrow_oracle_loses_money_when_fill_is_delayed() -> None:
    bars = _sawtooth(60)
    result = Engine(
        strategy=TomorrowOracle(), costs=REAL_COST, initial_cash=1_000_000, limit_pct=0.10
    ).run(bars)

    assert result.trades, "锯齿策略应当会来回成交；一次都没成交说明数据造错了"
    buy_and_hold = bars[-1].close / bars[0].close - 1.0
    strategy_return = result.final_equity / result.initial_cash - 1.0

    # 红线：知道明天的锯齿策略，把成交推迟一天以后，必须亏钱，且跑输躺平
    assert strategy_return < 0, f"「知道明天」策略收益 {strategy_return:.2%} 不应为正"
    assert strategy_return < buy_and_hold


def test_tomorrow_oracle_trades_are_all_losses_on_sawtooth() -> None:
    bars = _sawtooth(40)
    result = Engine(
        strategy=TomorrowOracle(), costs=NO_COST, initial_cash=1_000_000, limit_pct=0.10
    ).run(bars)
    # 无成本时，锯齿上的每一笔「预测式」买入都买在局部高点、卖在局部低点：全部亏损
    assert result.trades
    assert all(trade.pnl < 0 for trade in result.trades), [
        round(trade.pnl, 4) for trade in result.trades
    ]


def test_zero_cost_oracle_cannot_compound_positive_return() -> None:
    """即使成本全为 0，只要成交滞后一天，「知道明天」的收益依然 <= 0。"""
    bars = _sawtooth(120, step=1.0)
    result = Engine(
        strategy=TomorrowOracle(), costs=NO_COST, initial_cash=1_000_000, limit_pct=0.10
    ).run(bars)
    strategy_return = result.final_equity / result.initial_cash - 1.0
    assert strategy_return <= 0


def test_metrics_on_oracle_run_are_negative() -> None:
    """整条链路（撮合 → 计分）口径一致：作弊策略的累计/年化收益都必须 <= 0。"""
    bars = _sawtooth(80)
    result = Engine(
        strategy=TomorrowOracle(), costs=REAL_COST, initial_cash=1_000_000, limit_pct=0.10
    ).run(bars)
    metrics = compute(result, bars, data_fp="test:lookahead")
    assert metrics.cumulative_return < 0
    assert metrics.annualized_return < 0
