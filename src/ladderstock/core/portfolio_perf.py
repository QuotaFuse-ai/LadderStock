"""持仓收益率：时间加权（TWR）与资金加权收益，输出参考库 `perfView.ts` 的分组结果（QUOT-433）。

口径（DEC-064）：

1. **逐日重建持仓市值**：把账本流水按日期重放到每个本地交易日，
   `MV_t = Σ 持有股数 × 当日收盘价`。停牌日该票没有行，按**最近一个收盘价**
   估值——不插值、不拿成本价顶替；本地日线根本没覆盖到的票整只排除
   （`excluded` + `gaps`），不补价。
2. **TWR**：`r_t = (MV_t + 分红_t − MV_{t−1} − CF_t) / MV_{t−1}`，`CF_t` 是当天的
   净申购（买入为正、卖出为负），分红算收益、不算外部现金流；日收益连乘，
   `twr_index[0] = 1`。前一交易日没有市值（空仓）时当天不计收益，指数不动。
   不足 2 个交易日的组不给序列（`series = null`）。
3. **资金加权**：每笔买入 / 期初持仓当流出，卖出与分红当流入，期末市值当最后一笔
   流入，按日折现求年化 IRR（标准库二分，不引第三方）。`mwr_period` 是把该 IRR
   折回区间收益 `(1 + irr)^(span_days/365) − 1`；**不满一年 `xirr = null`**，
   不拿区间数冒充年化。
4. **金额**：`invested / proceeds / dividends / fees / end_value / gain` 全部来自
   流水与估值，`gain = end_value + proceeds + dividends − invested`，不另算第二套。
5. **分组**：本期只有 `A股 / CNY` 一组，不做汇率换算、不跨币种相加
   （B 股与外币流水由 `core.portfolio.trade_problem()` 挡在 `skipped` 里）。
6. **days**：只裁剪走势序列的回看天数（默认 365，`0` = 全历史）；
   收益指标始终按全部流水算，`start / end / span_days` 也是全部流水的区间。

本模块的纯函数（`compute_view`）只吃「流水 + 收盘价序列」，不读盘；
`load_close_series()` 是唯一的只读缓存入口（本机 `daily_k`，不联网、不落盘）。
"""

from __future__ import annotations

import json
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ladderstock.core.ladder import as_float, pick
from ladderstock.core.portfolio import EPS, Trade, replay, replay_order_key, round4
from ladderstock.core.quotes import PRICE_DATASET, PRICE_KEYS
from ladderstock.data import duckdb_store

#: 走势默认回看天数（接口 `?days=` 的缺省值）
DEFAULT_DAYS = 365

#: 走势回看上限：再长也只是把同一份日线摊平，不提供更细的信息
MAX_DAYS = 3650

#: 本期只有一个分组：A 股 / 人民币
MARKET = "A股"
CURRENCY = "CNY"
LABEL = "人民币 · A 股"

TOP_NOTES: tuple[str, ...] = (
    "只算 A 股 / 人民币：B 股与外币流水跳过，不做汇率换算、不跨币种相加",
    "价格只读本机 daily_k 缓存：不联网、不补价；缺行情的票见 gaps / excluded",
)

EMPTY_NOTE = "本机还没有交易流水，记一笔之后会自动计算收益率"

GROUP_NOTES: tuple[str, ...] = (
    "时间加权剔除申赎现金流；资金加权按每笔现金流求年化 IRR，分红算收益",
    "买入费用算进投入，卖出费用从卖出所得里扣；分红不改持仓成本",
    "停牌日按该票最近一个收盘价估值；本地日线缺覆盖的票整只排除，不补价",
)


@dataclass
class _Book:
    """一个代码在本次计算里的累计量（只含被算进本组的流水）。"""

    symbol: str
    name: str = ""
    shares: float = 0.0
    invested: float = 0.0
    proceeds: float = 0.0
    dividends: float = 0.0
    fees: float = 0.0
    first_day: str = ""


@dataclass
class _Simulated:
    """逐日模拟的结果：市值、现金流、分红、金额合计。"""

    dates: list[date] = field(default_factory=list)
    market_values: list[float] = field(default_factory=list)
    flows: list[float] = field(default_factory=list)
    incomes: list[float] = field(default_factory=list)
    cashflows: list[tuple[date, float]] = field(default_factory=list)


def _as_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _close_series(closes: Mapping[str, Mapping[str, float]]) -> dict[str, list[tuple[date, float]]]:
    """`{code: {iso: price}}` → `{code: [(date, price)]}`，按日期升序、同日取首条。"""
    out: dict[str, list[tuple[date, float]]] = {}
    for symbol, series in closes.items():
        points: dict[date, float] = {}
        for raw_day, price in series.items():
            day = _as_date(raw_day)
            if day is None:
                continue
            value = as_float(price)
            if value is None or value <= 0:
                continue
            points.setdefault(day, value)
        if points:
            out[str(symbol).strip().upper()] = sorted(points.items())
    return out


def _last_close(points: list[tuple[date, float]], day: date) -> float | None:
    """`day` 当天或之前最近一个收盘价（停牌按最近价，不插值）。"""
    index = bisect_right([point[0] for point in points], day) - 1
    if index < 0:
        return None
    return points[index][1]


def _irr(flows: Sequence[tuple[date, float]]) -> float | None:
    """按 `(1 + r)^(days/365)` 折现求年化 IRR；无正负号或求不出根时返回 `None`。"""
    if not flows:
        return None
    if not any(amount < 0 for _, amount in flows):
        return None
    if not any(amount > 0 for _, amount in flows):
        return None
    base = min(day for day, _ in flows)
    points = [((day - base).days / 365.0, amount) for day, amount in flows]

    def npv(rate: float) -> float:
        return sum(amount / (1.0 + rate) ** years for years, amount in points)

    low, high = -0.999999, 1.0
    while npv(low) * npv(high) > 0 and high < 1e6:
        high *= 10
    if npv(low) * npv(high) > 0:
        return None
    for _ in range(200):
        middle = (low + high) / 2
        if npv(low) * npv(middle) <= 0:
            high = middle
        else:
            low = middle
    return (low + high) / 2


def _trim(dates: list[date], indexes: list[float], days: int) -> tuple[list[date], list[float]]:
    if days <= 0 or not dates:
        return dates, indexes
    floor = dates[-1] - timedelta(days=days - 1)
    start = 0
    while start < len(dates) and dates[start] < floor:
        start += 1
    return dates[start:], indexes[start:]


def _group_notes(dates: list[date], days: int) -> list[str]:
    notes = list(GROUP_NOTES)
    if days > 0 and dates and (dates[-1] - dates[0]).days + 1 > days:
        notes.append(
            f"走势只画最近 {days} 天；收益指标按 {dates[0].isoformat()} 至 "
            f"{dates[-1].isoformat()} 的全部流水计算"
        )
    return notes


def compute_view(
    trades: Sequence[Trade],
    closes: Mapping[str, Mapping[str, float]],
    *,
    as_of: date | None = None,
    days: int = DEFAULT_DAYS,
) -> dict[str, Any]:
    """流水 + 收盘价序列 → `perfView.ts` 的 `PerfView` 形状（纯函数）。"""
    today = as_of or date.today()
    replayed = replay(list(trades), "average")
    skipped = [
        {"id": row.id, "symbol": row.symbol, "why": row.why} for row in replayed.issues
    ]
    bad_ids = {row.id for row in replayed.issues}
    valid = sorted((trade for trade in trades if trade.id not in bad_ids), key=replay_order_key)

    view: dict[str, Any] = {
        "as_of": None,
        "groups": [],
        "skipped": skipped,
        "gaps": [],
        "sources": [],
        "notes": list(TOP_NOTES),
    }
    if not valid:
        view["notes"] = [EMPTY_NOTE, *TOP_NOTES]
        return view

    series_by_symbol = _close_series(closes)
    max_close = max((points[-1][0] for points in series_by_symbol.values()), default=None)
    if max_close is None:
        group = _empty_group(
            trades=valid,
            excluded=[
                {
                    "symbol": symbol,
                    "name": _names(valid).get(symbol, ""),
                    "reason": f"本地 daily_k 缓存里没有 {symbol} 的收盘价",
                }
                for symbol in sorted(_names(valid))
            ],
        )
        view["groups"] = [group]
        view["gaps"] = [
            {"symbol": row["symbol"], "reason": row["reason"]} for row in group["excluded"]
        ]
        return view

    data_end = min(max_close, today)
    within = [trade for trade in valid if _trade_day(trade) <= data_end]
    if not within:
        group = _empty_group(
            trades=valid,
            reason=(
                f"全部流水都晚于本地 daily_k 最后一根 K 线（{data_end.isoformat()}），"
                "收益率算不到这些日子"
            ),
        )
        view["groups"] = [group]
        view["as_of"] = data_end.isoformat()
        return view

    books: dict[str, _Book] = {}
    for trade in within:
        symbol = trade.symbol.strip().upper()
        book = books.setdefault(symbol, _Book(symbol=symbol))
        if not book.first_day:
            book.first_day = trade.date
        if trade.name:
            book.name = trade.name

    included: list[_Book] = []
    excluded: list[dict[str, str]] = []
    for symbol in sorted(books):
        book = books[symbol]
        points = series_by_symbol.get(symbol) or []
        if not points:
            reason = f"本地 daily_k 缓存里没有 {symbol} 的收盘价"
        elif points[0][0].isoformat() > book.first_day:
            reason = (
                f"本地 daily_k 最早只有 {points[0][0].isoformat()} 的收盘价，"
                f"晚于首笔流水 {book.first_day}；不拿后来的价格倒推"
            )
        else:
            included.append(book)
            continue
        excluded.append({"symbol": symbol, "name": book.name, "reason": reason})

    if not included:
        group = _empty_group(
            trades=within,
            excluded=excluded,
            reason="没有能算收益率的持仓：本地日线缺覆盖（原因见 excluded）",
        )
        view["groups"] = [group]
        view["gaps"] = [{"symbol": row["symbol"], "reason": row["reason"]} for row in excluded]
        view["as_of"] = data_end.isoformat()
        return view

    trade_start = min(_trade_day(trade) for trade in within if trade.symbol.strip().upper() in {
        book.symbol for book in included
    })
    calendar = sorted(
        {
            day
            for book in included
            for day, _price in series_by_symbol[book.symbol]
            if trade_start <= day <= data_end
        }
    )
    if not calendar:
        group = _empty_group(
            trades=within,
            excluded=excluded,
            reason=(
                f"本地 daily_k 在 {trade_start.isoformat()} 至 {data_end.isoformat()} "
                "没有可用交易日"
            ),
        )
        view["groups"] = [group]
        view["gaps"] = [{"symbol": row["symbol"], "reason": row["reason"]} for row in excluded]
        view["as_of"] = data_end.isoformat()
        return view

    simulated = _simulate(within, included, books, series_by_symbol, calendar)
    dates = simulated.dates
    end = dates[-1]
    span_days = (end - dates[0]).days

    index = 1.0
    indexes = [1.0]
    for position in range(1, len(simulated.market_values)):
        previous = simulated.market_values[position - 1]
        if previous <= EPS:
            indexes.append(index)
            continue
        change = (
            simulated.market_values[position]
            + simulated.incomes[position]
            - previous
            - simulated.flows[position]
        )
        index *= 1.0 + change / previous
        indexes.append(index)

    invested = sum(book.invested for book in included)
    proceeds = sum(book.proceeds for book in included)
    dividends = sum(book.dividends for book in included)
    fees = sum(book.fees for book in included)
    end_value = simulated.market_values[-1]
    gain = end_value + proceeds + dividends - invested

    cashflows = list(simulated.cashflows)
    if end_value > EPS:
        cashflows.append((end, end_value))
    rate = _irr(cashflows)
    mwr_period = None
    xirr = None
    if rate is not None and span_days > 0:
        growth = (1.0 + rate) ** (span_days / 365.0)
        mwr_period = growth - 1.0
        if span_days >= 365:
            xirr = rate
    twr_annualized = None
    if span_days >= 365 and index > 0:
        twr_annualized = index ** (365.0 / span_days) - 1.0

    warnings = [
        f"{row['name'] or row['symbol']} 没算进收益率：{row['reason']}" for row in excluded
    ]
    if span_days < 1:
        warnings.append("本地日线只覆盖 1 个交易日，没给净值走势与年化")
    late = len(valid) - len(within)
    if late:
        warnings.append(
            f"{late} 笔流水晚于本地日线最后一根 K 线（{data_end.isoformat()}），"
            "没算进本次收益率"
        )

    series_dates, series_index = _trim(dates, indexes, days)
    series = (
        {
            "dates": [day.isoformat() for day in series_dates],
            "twr_index": [round(value, 6) for value in series_index],
        }
        if len(series_dates) >= 2
        else None
    )

    group = {
        "market": MARKET,
        "currency": CURRENCY,
        "label": LABEL,
        "ok": True,
        "reason": "",
        "start": dates[0].isoformat(),
        "end": end.isoformat(),
        "span_days": span_days,
        "symbols": [
            {"symbol": book.symbol, "name": book.name}
            for book in sorted(included, key=lambda item: item.symbol)
        ],
        "excluded": excluded,
        "twr": round(index - 1.0, 6),
        "twr_annualized": None if twr_annualized is None else round(twr_annualized, 6),
        "xirr": None if xirr is None else round(xirr, 6),
        "mwr_period": None if mwr_period is None else round(mwr_period, 6),
        "invested": round4(invested),
        "proceeds": round4(proceeds),
        "dividends": round4(dividends),
        "fees": round4(fees),
        "end_value": round4(end_value),
        "gain": round4(gain),
        "notes": _group_notes(dates, days),
        "warnings": warnings,
        "series": series,
    }
    view["groups"] = [group]
    view["gaps"] = [{"symbol": row["symbol"], "reason": row["reason"]} for row in excluded]
    view["as_of"] = end.isoformat()
    return view


def _names(trades: Sequence[Trade]) -> dict[str, str]:
    names: dict[str, str] = {}
    for trade in sorted(trades, key=replay_order_key):
        symbol = trade.symbol.strip().upper()
        if symbol and trade.name:
            names[symbol] = trade.name
        elif symbol:
            names.setdefault(symbol, "")
    return names


def _trade_day(trade: Trade) -> date:
    day = _as_date(trade.date)
    if day is None:  # replay 已把非法日期挡在 skipped 里，这里只保证类型非空
        return date.min
    return day


def _simulate(
    trades: Sequence[Trade],
    included: Sequence[_Book],
    books: Mapping[str, _Book],
    series_by_symbol: Mapping[str, list[tuple[date, float]]],
    calendar: Sequence[date],
) -> _Simulated:
    """按交易日推进：先记账（含当天现金流），再按收盘价给持仓估值。"""
    symbols = {book.symbol for book in included}
    ordered = sorted(trades, key=replay_order_key)
    result = _Simulated()
    held = {symbol: 0.0 for symbol in symbols}
    cursor = 0
    pending_flow = 0.0
    pending_income = 0.0

    for day in calendar:
        while cursor < len(ordered) and _trade_day(ordered[cursor]) <= day:
            trade = ordered[cursor]
            cursor += 1
            symbol = trade.symbol.strip().upper()
            if symbol not in symbols:
                continue
            flow, income = _apply(trade, books[symbol], held, result.cashflows)
            pending_flow += flow
            pending_income += income
        value = 0.0
        for book in included:
            shares = held[book.symbol]
            if shares <= EPS:
                continue
            price = _last_close(series_by_symbol[book.symbol], day)
            if price is None:  # 覆盖检查保证不会发生；真发生就按 0 估值会造假，跳过整组
                continue
            value += shares * price
        result.dates.append(day)
        result.market_values.append(value)
        result.flows.append(pending_flow)
        result.incomes.append(pending_income)
        pending_flow = 0.0
        pending_income = 0.0
    return result


def _apply(
    trade: Trade,
    book: _Book,
    held: dict[str, float],
    cashflows: list[tuple[date, float]],
) -> tuple[float, float]:
    """把一笔流水记进累计量，返回 `(外部现金流, 当日分红收入)`。"""
    day = _trade_day(trade)
    if trade.type in ("opening", "buy"):
        shares = float(trade.shares or 0.0)
        fee = float(trade.fee or 0.0) if trade.type == "buy" else 0.0
        cost = shares * float(trade.price or 0.0) + fee
        held[book.symbol] += shares
        book.invested += cost
        book.fees += fee
        cashflows.append((day, -cost))
        return cost, 0.0
    if trade.type == "sell":
        shares = float(trade.shares or 0.0)
        fee = float(trade.fee or 0.0)
        net = shares * float(trade.price or 0.0) - fee
        held[book.symbol] -= shares
        book.proceeds += net
        book.fees += fee
        cashflows.append((day, net))
        return -net, 0.0
    if trade.type == "dividend":
        amount = float(trade.amount or 0.0)
        book.dividends += amount
        cashflows.append((day, amount))
        return 0.0, amount
    held[book.symbol] *= float(trade.ratio or 0.0)
    return 0.0, 0.0


def _empty_group(
    *,
    trades: Sequence[Trade],
    excluded: Sequence[dict[str, str]] | None = None,
    reason: str = "本地 daily_k 缓存里没有可用的收盘价",
) -> dict[str, Any]:
    """算不成的组：结构完整、`ok=false`、原因写清楚，不伪造任何数字。"""
    names = _names(trades)
    return {
        "market": MARKET,
        "currency": CURRENCY,
        "label": LABEL,
        "ok": False,
        "reason": reason,
        "start": None,
        "end": None,
        "span_days": None,
        "symbols": [{"symbol": symbol, "name": name} for symbol, name in sorted(names.items())],
        "excluded": list(excluded or []),
        "twr": None,
        "twr_annualized": None,
        "xirr": None,
        "mwr_period": None,
        "invested": None,
        "proceeds": None,
        "dividends": None,
        "fees": None,
        "end_value": None,
        "gain": None,
        "notes": list(GROUP_NOTES),
        "warnings": [
            f"{row['name'] or row['symbol']} 没算进收益率：{row['reason']}"
            for row in (excluded or [])
        ],
        "series": None,
    }


# --------------------------------------------------------------------------- #
# 只读缓存：本机 daily_k 收盘价序列
# --------------------------------------------------------------------------- #

def load_close_series(
    symbols: Sequence[str], *, root: Path | None = None, as_of: date | None = None
) -> dict[str, dict[str, float]]:
    """从本机 `daily_k` 缓存读回「代码 → {交易日: 收盘价}」；不联网、不写库。

    读一次 DuckDB 连接、一条按代码过滤的查询；库里没有这张表 / 没有库文件时返回空
    （调用方据此把整组标成算不成，而不是补价）。同一天多行取第一条，与
    `core.quotes.price_map()` 同一条口径。
    """
    wanted = {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}
    if not wanted:
        return {}
    path = duckdb_store.db_path(root)
    if not path.is_file():
        return {}
    conn = duckdb_store.connect(path, read_only=True)
    try:
        if not duckdb_store.table_exists(conn, PRICE_DATASET):
            return {}
        marks = ", ".join("?" for _ in wanted)
        rows = conn.execute(
            f'SELECT code, trade_date, payload FROM "{PRICE_DATASET}" '
            f"WHERE code IN ({marks}) ORDER BY trade_date",
            sorted(wanted),
        ).fetchall()
    finally:
        duckdb_store.close(conn)

    ceiling = as_of.isoformat() if as_of is not None else None
    series: dict[str, dict[str, float]] = {}
    for raw_code, raw_day, payload in rows:
        code = str(raw_code or "").strip().upper()
        if code not in wanted:
            continue
        day = str(raw_day)[:10]
        if ceiling is not None and day > ceiling:
            continue
        try:
            record = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        price = as_float(pick(record, PRICE_KEYS))
        if price is None or price <= 0:
            continue
        series.setdefault(code, {}).setdefault(day, price)
    return series


def build_perf(
    trades: Sequence[Trade],
    *,
    root: Path | None = None,
    as_of: date | None = None,
    days: int = DEFAULT_DAYS,
) -> dict[str, Any]:
    """接口层入口：读本机 `daily_k` → `compute_view()`，再补 `sources`。"""
    symbols = sorted({trade.symbol.strip().upper() for trade in trades if trade.symbol})
    if not symbols:
        return {
            "as_of": None,
            "groups": [],
            "skipped": [],
            "gaps": [],
            "sources": [],
            "notes": [EMPTY_NOTE, *TOP_NOTES],
        }
    closes = load_close_series(symbols, root=root, as_of=as_of)
    view = compute_view(trades, closes, as_of=as_of, days=days)
    view["sources"] = [PRICE_DATASET] if closes else []
    return view


__all__ = [
    "CURRENCY",
    "DEFAULT_DAYS",
    "EMPTY_NOTE",
    "LABEL",
    "MARKET",
    "MAX_DAYS",
    "TOP_NOTES",
    "build_perf",
    "compute_view",
    "load_close_series",
]
