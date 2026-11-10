"""撮合：手续费 / 滑点 / 印花税 / T+1 / 涨跌停不可成交 / 停牌跳过（QUOT-500 §5.2）。

**信号 t，成交 t+1**：``Engine`` 遍历每根 K 线 ``k``：

1. 先用 ``history = bars[:k]`` 让策略生成 ``target_k``（只看到 ``t = k−1`` 及之前）；
2. ``k`` 是**执行日**：拿开盘价 + 滑点作为成交价；
3. 若 ``k`` 停牌（撮合层按「A 股停牌」定义判断：``volume == 0`` 即视为停牌，**不补价、不延后**
   简单处理——持仓保持不动、不下单，直到下一根可交易日再重新判断目标）；M1 里的停牌口径是
   ``volume == 0``：日 K 无法严格判断停牌（缺少「停牌原因」），按成交量=0 视为不可成交日；
   额外用「开盘价=最高价=最低价=收盘价且成交量>0」视作一字板（涨/跌停被锁死），这类不可成交
   由涨跌停规则处理，不与停牌混淆。
4. 涨跌停：以 ``prev_close`` 为基准算 ``limit_pct``；若今天**开盘价**已触及涨停价并往上跳，
   买单不可成交；触及跌停价并往下跳，卖单不可成交（开盘价就是成交价，已经在板里就进不去/出不来）。
5. T+1：当天买入的股数当天不能卖；卖出需要「持仓中已解冻的股数」。

规则口径来源：参考 A 股交易所公开规则；实现自研，不复制参考库代码。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from ladderstock.backtest.feed import Bar
from ladderstock.backtest.strategy import Strategy

_EPS: Final = 1e-9


class MatchError(ValueError):
    """撮合参数非法（成本为负、初始资金 <= 0、目标仓位越界）。"""


@dataclass(frozen=True, slots=True)
class CostModel:
    """撮合成本：佣金（单笔取 max(额×率, 下限)）+ 过户费（双边）+ 印花税（仅卖出）。

    ``commission`` / ``stamp_duty`` / ``transfer_fee`` 都是**小数口径**（0.00009 = 万 0.9）；
    ``commission_floor`` 是**元**。旧调用方只传前三个参数时，下限 = 0、过户费 = 0，
    行为与 QUOT-500 完全一致。
    """

    commission: float
    slippage: float
    stamp_duty: float
    commission_floor: float = 0.0
    transfer_fee: float = 0.0

    def __post_init__(self) -> None:
        for name in ("commission", "slippage", "stamp_duty", "commission_floor", "transfer_fee"):
            value = getattr(self, name)
            if value < 0:
                raise MatchError(f"{name} 不能为负，收到 {value!r}")
            if not math.isfinite(value):
                raise MatchError(f"{name} 必须是有限数值，收到 {value!r}")

    def buy_price(self, open_price: float) -> float:
        return open_price * (1.0 + self.slippage)

    def sell_price(self, open_price: float) -> float:
        return open_price * (1.0 - self.slippage)

    def commission_on(self, notional: float) -> float:
        """单笔佣金：``max(成交额 × 佣金率, 单笔最低)``；零成交额不收费。"""
        if notional <= 0:
            return 0.0
        return max(notional * self.commission, self.commission_floor)

    def buy_cost(self, notional: float) -> float:
        if notional <= 0:
            return 0.0
        return self.commission_on(notional) + notional * self.transfer_fee

    def sell_cost(self, notional: float) -> float:
        if notional <= 0:
            return 0.0
        return (
            self.commission_on(notional)
            + notional * self.transfer_fee
            + notional * self.stamp_duty
        )


@dataclass(slots=True)
class Trade:
    """一次完整回合（开→平）的汇总；用于胜率/盈亏比/持仓天数。"""

    open_date: str
    close_date: str
    shares: int
    open_price: float
    close_price: float
    open_notional: float
    close_notional: float
    open_cost: float
    close_cost: float
    pnl: float
    regime_entry: str  # "signal_t1"：成交假设=信号 t 执行 t+1（约束写进运行卡）
    reason: str = "signal"

    def to_dict(self) -> dict[str, object]:
        return {
            "open_date": self.open_date,
            "close_date": self.close_date,
            "shares": self.shares,
            "open_price": self.open_price,
            "close_price": self.close_price,
            "open_notional": self.open_notional,
            "close_notional": self.close_notional,
            "open_cost": self.open_cost,
            "close_cost": self.close_cost,
            "pnl": self.pnl,
            "regime_entry": self.regime_entry,
            "reason": self.reason,
        }


@dataclass(slots=True)
class TradeLog:
    """每根 K 线一行：策略目标 / 执行后持仓 / 当根成本；运行卡与净值曲线都用它。"""

    date: str
    close: float
    target: float
    executed_side: str  # "" | "buy" | "sell"
    executed_shares: int
    executed_price: float
    notional: float
    cost: float
    cash_after: float
    shares_after: int
    equity_after: float
    limit_up_blocked: bool = False
    limit_down_blocked: bool = False
    t_plus_one_blocked: bool = False
    suspended: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "date": self.date,
            "close": self.close,
            "target": self.target,
            "executed_side": self.executed_side,
            "executed_shares": self.executed_shares,
            "executed_price": self.executed_price,
            "notional": self.notional,
            "cost": self.cost,
            "cash_after": self.cash_after,
            "shares_after": self.shares_after,
            "equity_after": self.equity_after,
            "limit_up_blocked": self.limit_up_blocked,
            "limit_down_blocked": self.limit_down_blocked,
            "t_plus_one_blocked": self.t_plus_one_blocked,
            "suspended": self.suspended,
        }


@dataclass(slots=True)
class MatchResult:
    log: list[TradeLog] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    # final equity（最后一根 K 线收盘结算）、初始资金
    final_equity: float = 0.0
    initial_cash: float = 0.0
    # 未完成的持仓（最后一天仍持有时）的浮动盈亏（不算完成回合）
    open_position_pnl: float = 0.0


def _round_price(value: float) -> float:
    # A 股价格最小变动单位 0.01 元；涨跌停按 0.01 元四舍五入后再比较，
    # 与交易所展示口径一致（否则 limit_up 判断会因浮点误差错位）
    return round(value + _EPS, 2)


def limit_prices(prev_close: float, limit_pct: float) -> tuple[float, float]:
    """上一交易日收盘 → (涨停价, 跌停价)，按 0.01 元四舍五入。"""
    return _round_price(prev_close * (1 + limit_pct)), _round_price(prev_close * (1 - limit_pct))


def _hits_limit_down(bar: Bar, limit_down_price: float | None) -> bool:
    """开盘价已在跌停价或以下 -> 卖单出不去（开盘即封板）。"""
    return limit_down_price is not None and _round_price(bar.open) <= limit_down_price


def _is_suspended(bar: Bar) -> bool:
    # M1 停牌口径：成交量为 0 视为不可成交（日 K 无停牌原因字段，``volume=0`` 是
    # 「这一天没有成交」最直接、可复算的信号；若数据源后来补了停牌标记，按标记覆盖）。
    return bar.volume <= 0


def _size_buy(open_price: float, cash: float, costs: CostModel) -> tuple[int, float, float]:
    """在给定 open_price 下，用可用 cash 能买的最大股数（整百），返回 (shares, notional, cost)。

    单笔最低佣金让「成交额 + 费用 <= cash」不再是一条直线，按整手逐次收敛：
    先按无下限口径估一个上界，再按超额所需的股数（向上取整到整手）回退，
    最多几次就收敛（最低佣金相对一手金额很小）；退到 0 手 = 这笔买不进。
    """
    price = costs.buy_price(open_price)
    if price <= 0:
        return 0, 0.0, 0.0
    shares = int(cash // price)
    shares -= shares % 100
    while shares > 0:
        notional = price * shares
        total = notional + costs.buy_cost(notional)
        if total <= cash + 1e-6:
            return shares, notional, costs.buy_cost(notional)
        excess = total - cash
        drop = max(100, math.ceil(excess / price / 100) * 100)
        shares -= drop
    return 0, 0.0, 0.0


def _size_sell(
    shares_available: int, open_price: float, costs: CostModel
) -> tuple[int, float, float, float]:
    """最多卖出 ``shares_available`` 股；返回 (shares, notional, cost, proceeds)。"""
    if shares_available <= 0:
        return 0, 0.0, 0.0, 0.0
    price = costs.sell_price(open_price)
    notional = price * shares_available
    cost = costs.sell_cost(notional)
    proceeds = notional - cost
    return shares_available, notional, cost, proceeds


class Engine:
    """撮合引擎：策略只看 t，成交 t+1；状态纯字段（不藏外部可变状态）。"""

    def __init__(
        self,
        *,
        strategy: Strategy,
        costs: CostModel,
        initial_cash: float,
        limit_pct: float,
    ) -> None:
        if initial_cash <= 0:
            raise MatchError(f"initial_cash 必须 > 0，收到 {initial_cash!r}")
        if limit_pct <= 0 or limit_pct >= 1:
            raise MatchError(f"limit_pct 必须在 (0, 1) 内，收到 {limit_pct!r}")
        self.strategy = strategy
        self.costs = costs
        self.initial_cash = initial_cash
        self.limit_pct = limit_pct

    def run(self, bars: Sequence[Bar]) -> MatchResult:
        if len(bars) < 2:
            raise MatchError(f"至少需要 2 根 K 线，收到 {len(bars)}")

        result = MatchResult(initial_cash=self.initial_cash)
        cash = self.initial_cash
        shares = 0
        # 当前持股的买入价 & 买入日（一次开仓一次平仓，持仓只有一个回合）
        open_avg_price = 0.0
        open_date = ""
        open_open_notional = 0.0
        open_open_cost = 0.0
        prev_close: float | None = None

        for index, bar in enumerate(bars):
            suspended = _is_suspended(bar)
            # 策略输入 = bars[:index]（不含执行日 k），即信号时点 t = k−1
            history = bars[:index]
            target = 0.0 if index == 0 else float(self.strategy.target(history))

            limit_up_price: float | None = None
            limit_down_price: float | None = None
            if prev_close is not None:
                limit_up_price, limit_down_price = limit_prices(prev_close, self.limit_pct)

            executed_side = ""
            executed_shares = 0
            executed_price = 0.0
            notional = 0.0
            cost = 0.0
            limit_up_blocked = False
            limit_down_blocked = False
            t_plus_one_blocked = False

            if index > 0 and not suspended:
                want_buy = target >= 1.0 - _EPS and shares == 0
                want_sell = target <= _EPS and shares > 0
                if want_buy:
                    # 涨停不可成交：开盘价已经 >= 涨停价 -> 视为买不进（开盘即封板）
                    if limit_up_price is not None and _round_price(bar.open) >= limit_up_price:
                        limit_up_blocked = True
                    else:
                        n, buy_notional, buy_cost = _size_buy(bar.open, cash, self.costs)
                        if n > 0:
                            cash -= buy_notional + buy_cost
                            shares += n
                            executed_side = "buy"
                            executed_shares = n
                            executed_price = self.costs.buy_price(bar.open)
                            notional = buy_notional
                            cost = buy_cost
                            open_avg_price = executed_price
                            open_date = bar.date
                            open_open_notional = buy_notional
                            open_open_cost = buy_cost
                elif want_sell:
                    # T+1：本根同时出现买入与卖出意图时只能先买、不能当根回转。
                    # want_buy 与 want_sell 互斥（shares==0 vs shares>0），所以一笔
                    # 买入落在第 k 根、卖出最早落在第 k+1 根开盘 —— 当日回转无法发生。
                    if executed_side == "buy":
                        t_plus_one_blocked = True
                    # 跌停不可成交：开盘价已经 <= 跌停价 -> 卖不出
                    elif _hits_limit_down(bar, limit_down_price):
                        limit_down_blocked = True
                    else:
                        n, sell_notional, sell_cost, proceeds = _size_sell(
                            shares, bar.open, self.costs
                        )
                        if n > 0:
                            cash += proceeds
                            executed_side = "sell"
                            executed_shares = n
                            executed_price = self.costs.sell_price(bar.open)
                            notional = sell_notional
                            cost = sell_cost
                            # 平仓盈亏 = 价差 + 开仓成本 + 平仓成本
                            pnl = (
                                (executed_price - open_avg_price) * n
                                - open_open_cost
                                - sell_cost
                            )
                            result.trades.append(
                                Trade(
                                    open_date=open_date,
                                    close_date=bar.date,
                                    shares=n,
                                    open_price=open_avg_price,
                                    close_price=executed_price,
                                    open_notional=open_open_notional,
                                    close_notional=sell_notional,
                                    open_cost=open_open_cost,
                                    close_cost=sell_cost,
                                    pnl=pnl,
                                    regime_entry="signal_t1",
                                )
                            )
                            shares -= n
                            if shares == 0:
                                open_avg_price = 0.0
                                open_date = ""
                                open_open_notional = 0.0
                                open_open_cost = 0.0

            equity = cash + shares * bar.close
            result.log.append(
                TradeLog(
                    date=bar.date,
                    close=bar.close,
                    target=target,
                    executed_side=executed_side,
                    executed_shares=executed_shares,
                    executed_price=executed_price,
                    notional=notional,
                    cost=cost,
                    cash_after=cash,
                    shares_after=shares,
                    equity_after=equity,
                    limit_up_blocked=limit_up_blocked,
                    limit_down_blocked=limit_down_blocked,
                    t_plus_one_blocked=t_plus_one_blocked,
                    suspended=suspended,
                )
            )

            prev_close = bar.close

        # 末尾未平仓：以最后收盘价计浮盈
        if shares > 0:
            result.open_position_pnl = (bars[-1].close - open_avg_price) * shares
        result.final_equity = cash + shares * bars[-1].close
        return result


__all__ = [
    "CostModel",
    "Engine",
    "MatchError",
    "MatchResult",
    "Trade",
    "TradeLog",
    "limit_prices",
]
