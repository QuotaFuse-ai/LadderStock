"""校验门：样本内/外切分 + 三档成本敏感性 + 最小交易次数（QUOT-500 §5.2 / §5.3）。

校验门不过，运行卡**必须**显式标红——不管主回测的数字多好看。三道闸：

1. **样本切分**（按时间顺序，不许随机）：前 ``in_sample_ratio`` 为样本内，其余样本外；
   两段各跑一次。判定依据（任一失败 → 红）：

   - 样本外累计收益 ``<= 0``（样本内赚钱、样本外亏钱 = 过拟合的典型信号）；
   - 样本外**交易次数 = 0**（等于样本外根本没人测）。

2. **最小交易次数**：主回测完整回合数 ``>= min_trades``；不够 → 红（统计噪音太大，
   不该把它当结论）。

3. **成本敏感性**（在**全区间**上按档位重跑，不是对主回测结果做算术缩放）：每档把
   ``commission`` 换成该档，滑点 / 印花税不动（与 ``config/backtest.yaml`` 同一规则）。
   判定：**最高档**的累计收益 ``<= 0`` → 红（策略只在低佣金假设下成立，实盘一上成本就翻负）。
   每一档都是真的跑了一遍撮合（同样 t / t+1 规则），所以同一档位的数字与
   `runner` 主回测一定能互相印证。

「为什么把成本敏感性重跑而不是算个近似」——撮合里股数按整百手取整、涨停不可成交，
这些非线性的地方对成本的响应不是「收益 − 成交额 × 成本」一条直线；近似算法会在
「因成本差一点没能买进一手」这种边界上给出错误的结论。重跑的代价是每档多一次
撮合遍历，与一次回测同量级。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from ladderstock.backtest.config import BacktestConfig
from ladderstock.backtest.feed import Bar
from ladderstock.backtest.match import CostModel, Engine, MatchResult
from ladderstock.backtest.score import Metrics, compute
from ladderstock.backtest.strategy import Strategy
from ladderstock.calc.present import format_percent

PASS: Final = "pass"
FAIL: Final = "fail"


class GuardError(ValueError):
    """校验门输入不足以给出结论（区间太短导致样本外为空等）。"""


@dataclass(slots=True)
class Check:
    """一项闸的结论；``ok=False`` 时 ``reason`` 必须写清楚「为什么红」。"""

    key: str
    label: str
    ok: bool
    reason: str = ""
    observed: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "status": PASS if self.ok else FAIL,
            "reason": self.reason,
            "observed": self.observed,
        }


@dataclass(slots=True)
class Segment:
    """样本切分里的一段时间；``metrics`` 由该段内 K 线单独回测得到。"""

    label: str
    start: str
    end: str
    bars: int
    metrics: Metrics
    data_fp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "start": self.start,
            "end": self.end,
            "bars": self.bars,
            "metrics": self.metrics.values(),
            "metrics_display": self.metrics.display(),
            "metrics_calc_ids": dict(self.metrics.calc_ids),
            "data_fp": self.data_fp,
        }


@dataclass(slots=True)
class CostTier:
    """一档成本情形：佣金率取该档，滑点 / 印花税与主回测相同。"""

    commission: float
    cumulative_return: float
    annualized_return: float
    max_drawdown: float
    trade_count: int
    final_equity: float
    data_fp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "commission": self.commission,
            "cumulative_return": self.cumulative_return,
            "annualized_return": self.annualized_return,
            "max_drawdown": self.max_drawdown,
            "trade_count": self.trade_count,
            "final_equity": self.final_equity,
        }


@dataclass(slots=True)
class GateVerdict:
    passed: bool
    checks: list[Check]
    segments: list[Segment] = field(default_factory=list)
    cost_tiers: list[CostTier] = field(default_factory=list)
    #: 附带的「非阻塞提醒」：结论仍通过，但值得人看。不改变 ``passed``
    warnings: list[str] = field(default_factory=list)

    def red_reasons(self) -> list[str]:
        return [f"{c.label}：{c.reason}" for c in self.checks if not c.ok]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "status": PASS if self.passed else FAIL,
            "red_reasons": self.red_reasons(),
            "warnings": list(self.warnings),
            "checks": [c.to_dict() for c in self.checks],
            "segments": [s.to_dict() for s in self.segments],
            "cost_tiers": [t.to_dict() for t in self.cost_tiers],
        }


def split_index(total: int, ratio: float) -> int:
    """按时间顺序切分：返回样本内的最后一根下标 + 1（样本外从它开始）。

    ``ratio`` 越界 / 切完某段为空（一根都没有）都报错——不给「样本外零根」这种假通过。
    """
    if not (0 < ratio < 1):
        raise GuardError(f"in_sample_ratio 必须在 (0, 1) 内，收到 {ratio!r}")
    if total < 3:
        raise GuardError(f"至少需要 3 根 K 线才能切样本内/外，收到 {total}")
    # 切完两侧都至少 1 根：先把比例算出的下标夹在 [1, total-1]
    split = max(1, min(total - 1, int(round(total * ratio))))
    return split


def _run_segment(
    bars: Sequence[Bar],
    *,
    strategy_factory: Callable[[], Strategy],
    costs: CostModel,
    initial_cash: float,
    limit_pct: float,
    data_fp: str,
) -> tuple[MatchResult, Metrics]:
    engine = Engine(
        strategy=strategy_factory(), costs=costs, initial_cash=initial_cash, limit_pct=limit_pct
    )
    match = engine.run(bars)
    # 每段单独一个 data_fp：段内 K 线不同，id 必须跟着数据变。
    metrics = compute(match, bars, data_fp=data_fp)
    return match, metrics


def _segment_data_fp(rows: Sequence[Bar], full_fp: str) -> str:
    """段级数据指纹：``<全区间指纹>#<段内行数>:<段首日期>:<段末日期>``。

    主序列指纹只描述全区间；两段共用它会让「样本内」和「样本外」的指标 id 撞车。
    加上段边界后同段同数据稳定、跨段必不同；同时仍能一眼看出源自哪份全区间数据。
    """
    return f"{full_fp}#{len(rows)}:{rows[0].date}:{rows[-1].date}"


def evaluate(
    bars: Sequence[Bar],
    *,
    strategy_factory: Callable[[], Strategy],
    config: BacktestConfig,
    initial_cash: float,
    limit_pct: float,
    data_fp: str,
    baseline: Metrics,
) -> GateVerdict:
    """跑全部闸；返回结构化结论（``passed`` 为 False 时 ``red_reasons`` 非空）。

    ``baseline`` 是主回测（全区间、默认成本档）的指标，用来判「最小交易次数」；
    其余两项都**重新跑撮合**，不复用主回测的数字来算术推导。
    """
    if not bars:
        raise GuardError("bars 为空，无法做校验")
    checks: list[Check] = []
    warnings: list[str] = []

    # —— 闸 1：最小交易次数（判断主回测，而不是样本内的一段）
    enough = baseline.trade_count >= config.min_trades
    checks.append(
        Check(
            key="min_trades",
            label="最小交易次数",
            ok=enough,
            reason=(
                ""
                if enough
                else (
                    f"完整回合仅 {baseline.trade_count} 次 < 阈值 {config.min_trades}；"
                    "样本太小，任何收益率都是噪音，不应作为结论"
                )
            ),
            observed={
                "trade_count": baseline.trade_count,
                "min_trades": config.min_trades,
            },
        )
    )

    # —— 闸 2：样本内 / 样本外（时间顺序，不许随机）
    split = split_index(len(bars), config.in_sample_ratio)
    in_bars = list(bars[:split])
    out_bars = list(bars[split:])
    cost_model = CostModel(
        commission=config.commission, slippage=config.slippage, stamp_duty=config.stamp_duty
    )
    in_match, in_metrics = _run_segment(
        in_bars,
        strategy_factory=strategy_factory,
        costs=cost_model,
        initial_cash=initial_cash,
        limit_pct=limit_pct,
        data_fp=_segment_data_fp(in_bars, data_fp),
    )
    _out_match, out_metrics = _run_segment(
        out_bars,
        strategy_factory=strategy_factory,
        costs=cost_model,
        initial_cash=initial_cash,
        limit_pct=limit_pct,
        data_fp=_segment_data_fp(out_bars, data_fp),
    )
    segments = [
        Segment(
            label="样本内",
            start=in_bars[0].date,
            end=in_bars[-1].date,
            bars=len(in_bars),
            metrics=in_metrics,
            data_fp=_segment_data_fp(in_bars, data_fp),
        ),
        Segment(
            label="样本外",
            start=out_bars[0].date,
            end=out_bars[-1].date,
            bars=len(out_bars),
            metrics=out_metrics,
            data_fp=_segment_data_fp(out_bars, data_fp),
        ),
    ]
    in_ok = in_match.final_equity > 0  # 样本内本身就是主回测的一段，只做对账
    out_return = out_metrics.cumulative_return
    out_positive = out_return > 0
    out_traded = out_metrics.trade_count > 0
    checks.append(
        Check(
            key="out_of_sample",
            label="样本外收益",
            ok=out_positive and out_traded,
            reason=(
                ""
                if out_positive and out_traded
                else _oos_reason(out_return, out_metrics.trade_count)
            ),
            observed={
                "split_index": split,
                "in_sample_end": in_bars[-1].date,
                "out_of_sample_start": out_bars[0].date,
                "in_sample_return": in_metrics.cumulative_return,
                "out_of_sample_return": out_return,
                "out_of_sample_trades": out_metrics.trade_count,
                "in_sample_final_equity_positive": in_ok,
            },
        )
    )
    if not in_ok:
        warnings.append("样本内最终权益 <= 0（股数 / 现金不可能同时为负，请检查数据）")
    if in_metrics.cumulative_return > 0 and out_return < 0:
        warnings.append(
            f"样本内 {_fmt_pct(in_metrics.cumulative_return)}，"
            f"样本外 {_fmt_pct(out_return)}——典型过拟合信号"
        )

    # —— 闸 3：三档成本敏感性（全区间重跑）
    tiers: list[CostTier] = []
    for rate in config.cost_tiers:
        tier_costs = CostModel(
            commission=rate, slippage=config.slippage, stamp_duty=config.stamp_duty
        )
        tier_match = Engine(
            strategy=strategy_factory(),
            costs=tier_costs,
            initial_cash=initial_cash,
            limit_pct=limit_pct,
        ).run(bars)
        tier_metrics = compute(tier_match, bars, data_fp=data_fp)
        tiers.append(
            CostTier(
                commission=rate,
                cumulative_return=tier_metrics.cumulative_return,
                annualized_return=tier_metrics.annualized_return,
                max_drawdown=tier_metrics.max_drawdown,
                trade_count=tier_metrics.trade_count,
                final_equity=tier_match.final_equity,
                data_fp=data_fp,
            )
        )
    top = tiers[-1]
    top_positive = top.cumulative_return > 0
    checks.append(
        Check(
            key="cost_sensitivity",
            label="最高成本档收益",
            ok=top_positive,
            reason=(
                ""
                if top_positive
                else (
                    f"佣金 {_fmt_rate(top.commission)} 时累计收益 "
                    f"{_fmt_pct(top.cumulative_return)} <= 0；策略只在更低成本假设下成立"
                )
            ),
            observed={
                "top_commission": top.commission,
                "top_cumulative_return": top.cumulative_return,
                "all_tiers": [t.to_dict() for t in tiers],
            },
        )
    )

    # 非阻塞警告：默认佣金落到档位外、交易次数偏少（介于 1 倍和 1.5 倍阈值之间）
    if config.commission not in config.cost_tiers:
        warnings.append(
            f"默认佣金 {_fmt_rate(config.commission)} 不在成本档位 "
            f"{[_fmt_rate(r) for r in config.cost_tiers]} 内，三档对不上主回测"
        )
    if config.min_trades <= baseline.trade_count < config.min_trades * 1.5:
        warnings.append(
            f"交易次数 {baseline.trade_count} 刚过阈值 {config.min_trades}，样本仍偏小"
        )

    return GateVerdict(
        passed=all(c.ok for c in checks),
        checks=checks,
        segments=segments,
        cost_tiers=tiers,
        warnings=warnings,
    )


def _oos_reason(cumulative: float, trades: int) -> str:
    parts: list[str] = []
    if trades == 0:
        parts.append("样本外一笔都没成交")
    if cumulative <= 0:
        parts.append(f"样本外累计收益 {_fmt_pct(cumulative)} <= 0")
    return "；".join(parts) or "样本外未通过"


def _fmt_pct(value: float) -> str:
    return format_percent(value)


def _fmt_rate(value: float) -> str:
    return f"{value * 10000:g}‱"


__all__ = [
    "Check",
    "CostTier",
    "FAIL",
    "GateVerdict",
    "GuardError",
    "PASS",
    "Segment",
    "evaluate",
    "split_index",
]
