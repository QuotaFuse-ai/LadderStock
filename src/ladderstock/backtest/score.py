"""绩效指标（调 ``ladderstock.calc`` 并逐指标记录 ``calc_id``，QUOT-500 §5.1 / §5.2）。

**为什么不自己写第二套公式**：规格 §4.5 的硬约束是「业务模块不许再手写第二套公式」。
即使回测指标（夏普、卡玛、回撤）在 ``calc/`` 里没有现成算子，本模块也只在**唯一一个**
指标函数里按定义实现一次，再通过 ``calc.calc_id`` 为每个指标生成一个稳定指纹。
``calc_id`` 绑定 ``op + CALC_VERSION + 规范化参数 + 数据指纹``——只要数据指纹不变、
参数不变，同一指标在任何模块重算都会得到同一 id；业务侧改口径会自然换 id（不误认为
同一结果）。

对外输出形态（运行卡 ``metrics`` / ``metrics_calc_ids``）：

::

    {
      "metrics": {
        "cumulative_return": 0.087,
        "annualized_return": 0.033,
        ...
      },
      "metrics_calc_ids": {
        "cumulative_return": "c_xxxx",
        ...
      },
      "metrics_display": {
        "cumulative_return": "8.70%",
        ...
      }
    }

每个指标自己带 ``calc_id``（不是整个回测一个 id），规格要求「每个指标各自的 calc_id」。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from ladderstock.backtest.feed import Bar
from ladderstock.backtest.match import MatchResult
from ladderstock.calc import CALC_VERSION
from ladderstock.calc.fingerprint import calc_id as calc_fingerprint
from ladderstock.calc.present import format_number, format_percent

#: 交易日数（年化约定：每自然年 252 个交易日，A 股惯例）
TRADING_DAYS: Final = 252

#: 指标名 ↔ 展示用的单位（决定 display 用百分号还是普通数字）
_PERCENT_METRICS: Final[frozenset[str]] = frozenset(
    {
        "cumulative_return",
        "annualized_return",
        "max_drawdown",
        "win_rate",
        "avg_turnover",
        "exposure_ratio",
    }
)


class ScoreError(ValueError):
    """输入不足以计算绩效（净值序列为空 / 时间跨度不合法等）。"""


@dataclass(slots=True)
class Metrics:
    """一次回测的绩效快照；字段名与运行卡 ``metrics`` 一一对应（不额外嵌套）。"""

    cumulative_return: float
    annualized_return: float
    max_drawdown: float
    sharpe: float | None
    calmar: float | None
    win_rate: float | None
    profit_loss_ratio: float | None
    trade_count: int
    avg_turnover: float
    avg_holding_days: float
    exposure_ratio: float
    #: 每个指标各自的 calc_id
    calc_ids: dict[str, str] = field(default_factory=dict)

    def values(self) -> dict[str, Any]:
        """运行卡 ``metrics``：`{name: value}`；非 ok 的指标值为 `None`。"""
        return {
            "cumulative_return": self.cumulative_return,
            "annualized_return": self.annualized_return,
            "max_drawdown": self.max_drawdown,
            "sharpe": self.sharpe,
            "calmar": self.calmar,
            "win_rate": self.win_rate,
            "profit_loss_ratio": self.profit_loss_ratio,
            "trade_count": self.trade_count,
            "avg_turnover": self.avg_turnover,
            "avg_holding_days": self.avg_holding_days,
            "exposure_ratio": self.exposure_ratio,
        }

    def display(self) -> dict[str, str | None]:
        values = self.values()
        out: dict[str, str | None] = {}
        for name, value in values.items():
            if value is None:
                out[name] = None
            elif name in _PERCENT_METRICS:
                out[name] = format_percent(float(value))
            else:
                out[name] = format_number(float(value), decimals=2)
        return out


def _equity_series(match: MatchResult) -> list[float]:
    if not match.log:
        raise ScoreError("撮合结果为空的净值序列")
    return [row.equity_after for row in match.log]


def _drawdown(equity: Sequence[float]) -> float:
    peak = equity[0]
    worst = 0.0
    for value in equity:
        if value > peak:
            peak = value
        if peak > 0:
            drawdown = (peak - value) / peak
            if drawdown > worst:
                worst = drawdown
    return worst


def _daily_returns(equity: Sequence[float]) -> list[float]:
    out: list[float] = []
    for prev, curr in zip(equity, equity[1:], strict=False):
        if prev <= 0:
            continue
        out.append(curr / prev - 1.0)
    return out


def _annualized(equity: Sequence[float], trading_days: int) -> float:
    if len(equity) < 2 or equity[0] <= 0:
        return 0.0
    total = equity[-1] / equity[0]
    if total <= 0:
        return -1.0
    years = (len(equity) - 1) / trading_days
    if years <= 0:
        return 0.0
    return total ** (1.0 / years) - 1.0


def _sharpe(returns: Sequence[float], trading_days: int) -> float | None:
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    sd = math.sqrt(var)
    if sd == 0 or not math.isfinite(sd):
        return None
    # 无风险利率按 0（M1 不做资金成本建模；口径写进运行卡 assumptions）
    return (mean / sd) * math.sqrt(trading_days)


def _profit_loss(match: MatchResult) -> tuple[float | None, float | None]:
    """胜率 = 赢的回合 / 总回合数；盈亏比 = 平均盈利 / |平均亏损|（无亏损 -> None）。"""
    if not match.trades:
        return None, None
    wins = [t.pnl for t in match.trades if t.pnl > 0]
    losses = [t.pnl for t in match.trades if t.pnl < 0]
    win_rate = len([t for t in match.trades if t.pnl > 0]) / len(match.trades)
    if not wins or not losses:
        return win_rate, None
    avg_win = sum(wins) / len(wins)
    avg_loss = -sum(losses) / len(losses)
    if avg_loss == 0:
        return win_rate, None
    return win_rate, avg_win / avg_loss


def _turnover(log: Sequence[Any]) -> float:
    """平均换手：单边成交额 ÷ 当日权益，把全区间成交加总后除以总交易日数。"""
    if not log:
        return 0.0
    total_turnover_ratio = 0.0
    for row in log:
        if row.executed_shares > 0 and row.equity_after > 0:
            total_turnover_ratio += row.notional / row.equity_after
    return total_turnover_ratio / len(log)


def _holding_days(log: Sequence[Any], match: MatchResult) -> float:
    """完成回合的平均交易日数（按序差；含买不含卖即 round-trip，两者日期差就是持仓天数）。"""
    if not match.trades:
        return 0.0
    index_by_date = {row.date: i for i, row in enumerate(log)}
    spans: list[int] = []
    for trade in match.trades:
        start = index_by_date.get(trade.open_date)
        end = index_by_date.get(trade.close_date)
        if start is not None and end is not None and end > start:
            spans.append(end - start)
    if not spans:
        return 0.0
    return sum(spans) / len(spans)


def _exposure(log: Sequence[Any]) -> float:
    if not log:
        return 0.0
    in_market = sum(1 for row in log if row.shares_after > 0)
    return in_market / len(log)


def _make_calc_id(op: str, *, params: dict[str, Any], data_fp: str) -> str:
    return calc_fingerprint(op, params, data_fp=data_fp)


def compute(
    match: MatchResult,
    bars: Sequence[Bar],
    *,
    data_fp: str,
    trading_days: int = TRADING_DAYS,
) -> Metrics:
    """一次回测的全部绩效指标 + 每指标的 ``calc_id``。

    ``data_fp`` 必须传入（由 ``feed.BarSeries.data_fp`` 产出），保证 calc_id 跟随数据。
    ``bars`` 只用来取首末日期做参数；行情本身已经在 ``match`` 里。
    """
    equity = _equity_series(match)
    if not bars:
        raise ScoreError("bars 为空")
    returns = _daily_returns(equity)
    cumulative = equity[-1] / equity[0] - 1.0 if equity[0] > 0 else 0.0
    annualized = _annualized(equity, trading_days)
    drawdown = _drawdown(equity)
    sharpe = _sharpe(returns, trading_days)
    calmar = annualized / drawdown if drawdown > 0 and math.isfinite(annualized) else None
    win_rate, profit_loss_ratio = _profit_loss(match)
    turnover = _turnover(match.log)
    holding = _holding_days(match.log, match)
    exposure = _exposure(match.log)

    base_params: dict[str, Any] = {
        "data_fp": data_fp,
        "calc_version": CALC_VERSION,
        "start": bars[0].date,
        "end": bars[-1].date,
        "trading_days": trading_days,
    }
    # 每个指标各自带不同的 op 名 + 完整的参数映射；同数据、同口径 => 稳定
    specs: dict[str, dict[str, Any]] = {
        "cumulative_return": {"section": "return"},
        "annualized_return": {"section": "return"},
        "max_drawdown": {"section": "risk"},
        "sharpe": {"section": "risk", "risk_free": 0.0, "annualize": trading_days},
        "calmar": {"section": "risk"},
        "win_rate": {"section": "trade", "min_pnl": 0.0, "trades": len(match.trades)},
        "profit_loss_ratio": {"section": "trade", "trades": len(match.trades)},
        "trade_count": {"section": "trade"},
        "avg_turnover": {"section": "trade"},
        "avg_holding_days": {"section": "trade"},
        "exposure_ratio": {"section": "trade"},
    }
    calc_ids = {
        name: _make_calc_id(f"backtest_{name}", params={**base_params, **spec}, data_fp=data_fp)
        for name, spec in specs.items()
    }

    metrics = Metrics(
        cumulative_return=cumulative,
        annualized_return=annualized,
        max_drawdown=drawdown,
        sharpe=sharpe,
        calmar=calmar,
        win_rate=win_rate,
        profit_loss_ratio=profit_loss_ratio,
        trade_count=len(match.trades),
        avg_turnover=turnover,
        avg_holding_days=holding,
        exposure_ratio=exposure,
        calc_ids=calc_ids,
    )
    return metrics


def equity_curve(match: MatchResult) -> list[dict[str, Any]]:
    """权益曲线（运行卡 / 前端都从这里读，不在前端重算）。"""
    return [
        {
            "date": row.date,
            "equity": row.equity_after,
            "drawdown": 0.0,  # 占位，下面填充
            "shares": row.shares_after,
        }
        for row in match.log
    ]


def equity_curve_with_drawdown(match: MatchResult) -> list[dict[str, Any]]:
    curve = equity_curve(match)
    peak = -math.inf
    for point in curve:
        value = float(point["equity"])
        peak = max(peak, value)
        point["drawdown"] = (peak - value) / peak if peak > 0 else 0.0
    return curve


def benchmark_buy_and_hold(bars: Sequence[Bar], *, initial_cash: float) -> list[dict[str, Any]]:
    """基准：在该区间内「第一天收盘买入并持有」（M1 没有 SSE 指数时的替代口径）。

    注意：基准与策略的比较**不试图对齐成本**。基准按 ``initial_cash / first_close`` 换算成
    「等值股数」的纯价格比较，**不计佣金/滑点/印花税**（这是基准的口径，写进运行卡）；
    所以策略相对基准的超额收益在生产上必须同时看成本敏感性，当前三档就是干这个的。
    """
    if not bars:
        return []
    base = bars[0].close
    if base <= 0:
        return []
    shares = initial_cash / base
    return [
        {
            "date": bar.date,
            "equity": shares * bar.close,
        }
        for bar in bars
    ]


__all__ = [
    "Metrics",
    "ScoreError",
    "TRADING_DAYS",
    "benchmark_buy_and_hold",
    "compute",
    "equity_curve",
    "equity_curve_with_drawdown",
]
