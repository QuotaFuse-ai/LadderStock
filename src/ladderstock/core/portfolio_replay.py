"""历史情景回放：按**当前持仓市值权重**，把组合放回四段固定的 A 股压力窗口（QUOT-466）。

只回放历史，不预测、不给买卖建议、不输出「如果当时买入」。窗口写死在模块里，
不可配置、不可通过接口改——审计时只看这一份常量。

口径：

1. **当前持仓** = 流水按 `average` 成本法重放后的 `holdings`（`shares > EPS`），
   不读旧 `positions` 表。
2. **当前价** = 该票本机 `daily_k` 中 `<= today` 的最后一个收盘价；没有就是
   `gaps` +「本地 daily_k 没有收盘价」，**不拿成本价顶替**。
3. `total_value = Σ 当前持仓市值`（只含取到当前价的），`weight_i = mv_i / total_value`。
4. **窗口交易日历** = 本窗口内所有候选持仓在 `daily_k` 里出现过的日期并集（升序）。
   少于 2 个 → 该情景 `unavailable`，不给任何组合收益。
5. **逐只纳入条件**（任一不满足 → 整只进 `gaps`，不插值、不补价）：窗口首 / 末交易日
   必须有收盘价；窗口内缺价天数占比超过 `MAX_HALTED_RATIO`（视为长期停牌）排除；
   整窗无数据时如实写最早 / 最晚可用日期。
6. **组合路径**：每个交易日 `nav_t = Σ w_i × close_i,t / close_i,first`。这里的 `w_i`
   是**纳入持仓之间归一后的权重**（Σ = 1），所以首日恒为 1.0——注意与 `holdings[].weight`
   不同，后者是相对全部当前市值的权重（Σ = `coverage`）。某只当天没有价格就沿用该票
   最近一个收盘价（不插值）。
7. `return = nav_last - 1`；`max_drawdown` 相对历史峰值（≤ 0），峰 / 谷取路径日期；
   `worst_day` = 相邻交易日净值比的最小值 − 1；`days` = 路径点数。
8. `coverage = Σ 纳入持仓当前市值 / total_value`；低于 `MIN_COVERAGE` → 该情景
   `unavailable`，`portfolio` / `path` 均为 `null`，但仍返回 `coverage / included /
   holdings / gaps`。
9. 不输出基准：`benchmark` 固定 `null`，`benchmark_error` 固定「本机没有历史指数序列」。
10. 金额四舍五入 4 位（`portfolio.round4`），比率 / 净值保留 6 位；缺失一律 `null`。
11. 只读：只读 `portfolio_trades` 与 `daily_k`，不联网、不落盘、不建库。本模块的
    `compute_replay()` 是纯函数（只吃流水 + 收盘价序列），`build_replay()` 是唯一
    的读盘入口，复用 `portfolio_perf.load_close_series()`，不另写日线读取器。
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ladderstock.core.portfolio import EPS, HoldingRow, Trade, replay, round4
from ladderstock.core.portfolio_perf import load_close_series
from ladderstock.core.quotes import PRICE_DATASET

MARKET = "a_share"
CURRENCY = "CNY"
LABEL = "A 股 · 人民币"

#: 有完整窗口价格的持仓市值占比下限；不足就整段情景不给组合收益
MIN_COVERAGE = 0.6
#: 窗口内缺价交易日占比上限；超过视为长期停牌，整只排除
MAX_HALTED_RATIO = 0.2


@dataclass(frozen=True)
class Scenario:
    """一段固定的历史压力窗口；开始 / 结束都是自然日，交易日以本机日线为准。"""

    id: str
    label: str
    start: str
    end: str


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(id="2015-crash", label="2015 股灾", start="2015-06-12", end="2015-08-26"),
    Scenario(id="2018-bear", label="2018 熊市", start="2018-01-29", end="2018-12-28"),
    Scenario(id="2020-covid", label="2020 疫情", start="2020-01-23", end="2020-03-23"),
    Scenario(id="2024-liquidity", label="2024 流动性", start="2024-01-02", end="2024-02-05"),
)

SCENARIO_IDS: tuple[str, ...] = tuple(scenario.id for scenario in SCENARIOS)

BENCHMARK_ERROR = "本机没有历史指数序列"
EMPTY_NOTE = "本机还没有交易流水，记一笔之后会自动回放"
TOP_NOTES: tuple[str, ...] = (
    "只回放历史，不预测、不给买卖建议",
    "按当前持仓市值权重回放；缺端点或长期停牌的持仓整只排除，不插值、不补价",
    "停牌日按该票最近一个收盘价延续；价格只读本机 daily_k 缓存",
)


def _round6(value: float) -> float:
    return round(float(value), 6)


@dataclass(frozen=True)
class _Held:
    """一只当前持仓 + 它的本机收盘价序列（已按日期升序）。"""

    symbol: str
    name: str
    shares: float
    market_value: float
    latest_day: str
    days: list[str]
    closes: dict[str, float]

    def close_on(self, day: str) -> float:
        """`day` 当天或之前最近一个收盘价（停牌按最近价，不插值）。"""
        index = bisect_right(self.days, day) - 1
        return self.closes[self.days[index]]

    def window_days(self, start: str, end: str) -> list[str]:
        return [day for day in self.days if start <= day <= end]


def _current_holdings(trades: Sequence[Trade]) -> list[HoldingRow]:
    """流水重放后的当前持仓（`shares > EPS`），按代码排序保证同输入同输出。"""
    result = replay(list(trades), "average")
    rows = [row for row in result.holdings if row.shares > EPS]
    return sorted(rows, key=lambda row: row.symbol)


def _clean_series(raw: Mapping[str, Any] | None) -> dict[str, float]:
    points: dict[str, float] = {}
    for raw_day, raw_price in (raw or {}).items():
        day = str(raw_day)[:10]
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            continue
        if price > 0 and day:
            points.setdefault(day, price)
    return points


def _price_holdings(
    holdings: Sequence[HoldingRow], closes: Mapping[str, Mapping[str, Any]]
) -> tuple[list[_Held], list[dict[str, str]], float, str | None]:
    """拼当前价：取到价的进候选，取不到进 gaps（不拿成本价顶替）。"""
    held: list[_Held] = []
    gaps: list[dict[str, str]] = []
    total_value = 0.0
    as_of: str | None = None
    for row in holdings:
        series = _clean_series(closes.get(row.symbol))
        if not series:
            gaps.append(
                {
                    "symbol": row.symbol,
                    "name": row.name,
                    "reason": "本地 daily_k 没有收盘价",
                }
            )
            continue
        days = sorted(series)
        latest_day = days[-1]
        market_value = row.shares * series[latest_day]
        held.append(
            _Held(
                symbol=row.symbol,
                name=row.name,
                shares=row.shares,
                market_value=market_value,
                latest_day=latest_day,
                days=days,
                closes=series,
            )
        )
        total_value += market_value
        if as_of is None or latest_day > as_of:
            as_of = latest_day
    return held, gaps, total_value, as_of


def _empty_view(scenario: str) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "as_of": None,
        "groups": [],
        "notes": [EMPTY_NOTE, *TOP_NOTES],
        "sources": [],
    }


def _unavailable(
    scenario: Scenario,
    *,
    reason: str,
    coverage: float | None,
    included: int,
    holdings: list[dict[str, Any]],
    gaps: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "id": scenario.id,
        "label": scenario.label,
        "start": scenario.start,
        "end": scenario.end,
        "unavailable": reason,
        "benchmark": None,
        "benchmark_error": BENCHMARK_ERROR,
        "portfolio": None,
        "coverage": coverage,
        "included": included,
        "holdings": holdings,
        "gaps": gaps,
        "path": None,
    }


def _scenario_view(
    scenario: Scenario,
    held: Sequence[_Held],
    base_gaps: Sequence[Mapping[str, str]],
    total_value: float,
) -> dict[str, Any]:
    """回放单个情景；所有「算不成」都写原因，不补数字。"""
    gaps: list[dict[str, str]] = [dict(gap) for gap in base_gaps]
    if not held or total_value <= 0:
        return _unavailable(
            scenario,
            reason="当前持仓都没有本地收盘价，无法按市值权重回放",
            coverage=None,
            included=0,
            holdings=[],
            gaps=gaps,
        )

    calendar = sorted(
        {
            day
            for item in held
            for day in item.window_days(scenario.start, scenario.end)
        }
    )
    if len(calendar) < 2:
        return _unavailable(
            scenario,
            reason=f"本机 daily_k 在窗口内只有 {len(calendar)} 个交易日",
            coverage=0.0,
            included=0,
            holdings=[],
            gaps=gaps,
        )

    first_day, last_day = calendar[0], calendar[-1]
    included_rows: list[tuple[_Held, float, float, int]] = []
    for item in held:
        window = item.window_days(scenario.start, scenario.end)
        if not window:
            gaps.append(
                {
                    "symbol": item.symbol,
                    "name": item.name,
                    "reason": (
                        f"窗口内没有本机日线（最早可用 {item.days[0]}，"
                        f"最晚可用 {item.days[-1]}）"
                    ),
                }
            )
            continue
        if first_day not in item.closes:
            gaps.append(
                {
                    "symbol": item.symbol,
                    "name": item.name,
                    "reason": f"窗口首个交易日 {first_day} 没有收盘价",
                }
            )
            continue
        if last_day not in item.closes:
            gaps.append(
                {
                    "symbol": item.symbol,
                    "name": item.name,
                    "reason": f"末个交易日 {last_day} 没有收盘价",
                }
            )
            continue
        halted = sum(1 for day in calendar if day not in item.closes)
        if halted / len(calendar) > MAX_HALTED_RATIO:
            gaps.append(
                {
                    "symbol": item.symbol,
                    "name": item.name,
                    "reason": (
                        f"窗口内 {halted}/{len(calendar)} 个交易日没有价格，"
                        "可能长期停牌"
                    ),
                }
            )
            continue
        first_close = item.closes[first_day]
        last_close = item.closes[last_day]
        included_rows.append((item, first_close, last_close, halted))

    included_value = sum(item.market_value for item, _, _, _ in included_rows)
    coverage = included_value / total_value if total_value > 0 else None

    holding_views: list[dict[str, Any]] = []
    for item, first_close, last_close, halted in included_rows:
        weight = item.market_value / total_value
        row_return = last_close / first_close - 1.0
        holding_views.append(
            {
                "symbol": item.symbol,
                "name": item.name,
                "weight": _round6(weight),
                "return": _round6(row_return),
                "contribution": _round6(weight * row_return),
                "first_date": first_day,
                "last_date": last_day,
                "halted_days": halted,
            }
        )

    if coverage is None or coverage < MIN_COVERAGE:
        return _unavailable(
            scenario,
            reason=(
                f"有完整窗口价格的持仓只占当前市值 {coverage:.2%}，"
                f"低于 {MIN_COVERAGE:.0%} 下限"
            )
            if coverage is not None
            else "当前持仓都没有可用的本地收盘价，无法计算覆盖率",
            coverage=_round6(coverage) if coverage is not None else None,
            included=len(included_rows),
            holdings=holding_views,
            gaps=gaps,
        )

    weights = [(item.market_value / total_value) / coverage for item, _, _, _ in included_rows]
    navs: list[float] = []
    for day in calendar:
        nav = 0.0
        for weight, (item, first_close, _, _) in zip(
            weights, included_rows, strict=True
        ):
            nav += weight * item.close_on(day) / first_close
        navs.append(nav)

    peak = navs[0]
    peak_index = 0
    max_drawdown = 0.0
    peak_day: str | None = None
    trough_day: str | None = None
    for index, nav in enumerate(navs):
        if nav > peak:
            peak = nav
            peak_index = index
        drawdown = nav / peak - 1.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            peak_day = calendar[peak_index]
            trough_day = calendar[index]

    worst_day = navs[1] / navs[0] - 1.0
    worst_day_date = calendar[1]
    for index in range(2, len(navs)):
        change = navs[index] / navs[index - 1] - 1.0
        if change < worst_day:
            worst_day = change
            worst_day_date = calendar[index]

    total_return = navs[-1] - 1.0
    return {
        "id": scenario.id,
        "label": scenario.label,
        "start": scenario.start,
        "end": scenario.end,
        "unavailable": None,
        "benchmark": None,
        "benchmark_error": BENCHMARK_ERROR,
        "portfolio": {
            "return": _round6(total_return),
            "max_drawdown": _round6(max_drawdown),
            "max_drawdown_peak": peak_day,
            "max_drawdown_trough": trough_day,
            "worst_day": _round6(worst_day),
            "worst_day_date": worst_day_date,
            "days": len(calendar),
            "included_value": round4(included_value),
            "value_change": round4(included_value * total_return),
        },
        "coverage": _round6(coverage),
        "included": len(included_rows),
        "holdings": holding_views,
        "gaps": gaps,
        "path": {
            "dates": list(calendar),
            "portfolio": [_round6(nav) for nav in navs],
        },
    }


def _selected(raw: str) -> tuple[Scenario, ...]:
    if raw == "all":
        return SCENARIOS
    for scenario in SCENARIOS:
        if scenario.id == raw:
            return (scenario,)
    raise ValueError(
        f"仅支持 {', '.join(SCENARIO_IDS)} 或 all，收到 {raw!r}"
    )


def compute_replay(
    trades: Sequence[Trade],
    closes: Mapping[str, Mapping[str, Any]],
    *,
    scenario: str = "all",
) -> dict[str, Any]:
    """纯函数：流水 + 收盘价序列 → 接口载荷（不读盘）。"""
    selected = _selected(scenario)
    holdings = _current_holdings(trades)
    if not holdings:
        return _empty_view(scenario)

    held, gaps, total_value, as_of = _price_holdings(holdings, closes)
    return {
        "scenario": scenario,
        "as_of": as_of,
        "groups": [
            {
                "market": MARKET,
                "currency": CURRENCY,
                "label": LABEL,
                "total_value": round4(total_value) if total_value > 0 else None,
                "input": len(holdings),
                "scenarios": [
                    _scenario_view(item, held, gaps, total_value) for item in selected
                ],
            }
        ],
        "notes": list(TOP_NOTES),
        "sources": [PRICE_DATASET] if held else [],
    }


def build_replay(
    trades: Sequence[Trade],
    *,
    root: Path | None = None,
    scenario: str = "all",
    today: date | None = None,
) -> dict[str, Any]:
    """接口层入口：读本机 `daily_k` → `compute_replay()`；不联网、不落盘、不建库。"""
    holdings = _current_holdings(trades)
    if not holdings:
        return compute_replay([], {}, scenario=scenario)
    symbols = [row.symbol for row in holdings]
    closes = load_close_series(symbols, root=root, as_of=today or date.today())
    return compute_replay(trades, closes, scenario=scenario)


__all__ = [
    "BENCHMARK_ERROR",
    "CURRENCY",
    "EMPTY_NOTE",
    "LABEL",
    "MARKET",
    "MAX_HALTED_RATIO",
    "MIN_COVERAGE",
    "SCENARIOS",
    "SCENARIO_IDS",
    "TOP_NOTES",
    "Scenario",
    "build_replay",
    "compute_replay",
]
