"""组合统计引擎（B 包）：组合指标 / 基准对比 / 交易归因 / 行为统计 / 错题本。

纯本地计算：原料全部来自现役链路，本模块不联网、不写库。

- 流水与持仓：`journal/portfolio.list_trades`（本机隐私库，只读）+
  `core/portfolio.replay`（已实现 / 分红 / 费用 / 批次）；
- 日线：本机 `daily_k`（DuckDB，只读；与 `core/portfolio_perf.load_close_series`
  同一条口径），额外解出 high / low 供行为分档；
- 基准：本机 `index_daily` 的沪深300（`000300`）。**不联网取指数**——本机没有
  缓存就回 `null` + 原因，不拿别的指数或估算顶替（DEC-006 同口径）。

指标口径（写死在这里，页面与 CLI 共用一份）：

| 指标 | 算法 | 缺数据时 |
|---|---|---|
| 最大回撤 | 组合 TWR 净值序列的 peak-to-trough 最大跌幅 | 净值序列 <2 点 → `null` |
| 夏普 | TWR 日收益 `mean/std × √252`，无风险利率取 0 | 样本 <20 或 std=0 → `null` |
| 年化波动 | 日收益 std × √252 | 同上 |
| 胜率 | 已实现卖出中 `pnl > 0` 的占比（平局不进分母） | 无已实现 → `null` |
| 盈亏比 | 已实现盈利合计 / abs(已实现亏损合计) | 无亏损或盈利 → `null` |
| 基准收益 | 沪深300 与本组合净值**同起同止**区间内的涨跌幅 | 本机无指数缓存 → `null` + 原因 |
| 超额收益 | 组合 TWR − 基准收益 | 任一侧缺失 → `null` |
| 行为分档 | 买入价在当日 `[low, high]` 中的位置：>0.8 追高、<0.2 低吸、其余中性；
统计各自「买入价 → 第 5 个交易日收盘」的收益 | 当日缺 OHLC → 该笔不计入 |
| 错题本 | 已实现亏损最大的前 N 笔（持有天数 = 该票在卖出前最早一笔买入的间隔） | 无亏损 → 空列表 |

**「缺数就是 `null` + 原因」，不补 0、不用别的东西顶替**——这是全仓同一条红线。
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from typing import Any

from ladderstock.core import portfolio_perf
from ladderstock.core.ladder import as_float, pick
from ladderstock.core.portfolio import replay as replay_portfolio
from ladderstock.core.quotes import PRICE_KEYS
from ladderstock.data import duckdb_store
from ladderstock.journal import portfolio as portfolio_store

INDEX_DATASET = "index_daily"
BENCHMARK_CODE = "000300"  # 沪深300
BENCHMARK_LABEL = "沪深300"

HIGH_KEYS: tuple[str, ...] = ("high", "high_price", "最高")
LOW_KEYS: tuple[str, ...] = ("low", "low_price", "最低")

#: 夏普样本下限（不足就没有统计意义）
MIN_SHARPE_DAYS = 20
#: 行为分档阈值：买入价在当日振幅中的位置
CHASE_THRESHOLD = 0.8
DIP_THRESHOLD = 0.2
#: 行为检验的前瞻窗口（交易日）
FORWARD_DAYS = 5
#: 错题本容量
MISTAKE_LIMIT = 10


# --------------------------------------------------------------------------- #
# 纯函数
# --------------------------------------------------------------------------- #


def max_drawdown(values: list[float]) -> float | None:
    """净值序列的 peak-to-trough 最大跌幅（正数，0.12 = −12%）。"""
    if len(values) < 2:
        return None
    peak = values[0]
    worst = 0.0
    for value in values[1:]:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return round(worst, 6)


def daily_returns(values: list[float]) -> list[float]:
    returns: list[float] = []
    for previous, current in zip(values, values[1:], strict=False):
        if previous > 0:
            returns.append(current / previous - 1.0)
    return returns


def sharpe(values: list[float]) -> tuple[float | None, float | None]:
    """`(sharpe, annual_vol)`；样本不足或零波动返回 `(None, None)`。"""
    returns = daily_returns(values)
    if len(returns) < MIN_SHARPE_DAYS:
        return None, None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    if std <= 0:
        return None, None
    return round(mean / std * math.sqrt(252), 4), round(std * math.sqrt(252), 6)


def summarize_realized(realized: list[Any]) -> dict[str, Any]:
    """已实现交易的胜负统计（`realized` 是 `core.portfolio.RealizedRow` 列表）。"""
    wins = [row.pnl for row in realized if row.pnl > 0]
    losses = [row.pnl for row in realized if row.pnl < 0]
    decided = len(wins) + len(losses)
    win_rate = round(len(wins) / decided, 4) if decided else None
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = round(gross_win / gross_loss, 4) if gross_loss > 0 else None
    avg_win = round(gross_win / len(wins), 4) if wins else None
    avg_loss = round(-gross_loss / len(losses), 4) if losses else None
    return {
        "count": len(realized),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "gross_win": round(gross_win, 4),
        "gross_loss": round(gross_loss, 4),
    }


def bucket_position(price: float, low: float, high: float) -> str | None:
    """买入价在当日振幅中的位置分档；振幅为 0 无法判定。"""
    span = high - low
    if span <= 1e-9:
        return None
    position = (price - low) / span
    if position > CHASE_THRESHOLD:
        return "chase"
    if position < DIP_THRESHOLD:
        return "dip"
    return "neutral"


# --------------------------------------------------------------------------- #
# 原料加载（全部只读本机）
# --------------------------------------------------------------------------- #


def load_daily_bars(
    symbols: list[str], *, root: Path | None = None, as_of: date | None = None
) -> dict[str, dict[str, dict[str, float]]]:
    """`daily_k` → `{code: {day: {close, high, low}}}`（缺的字段不补）。"""
    wanted = {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}
    if not wanted:
        return {}
    path = duckdb_store.db_path(root)
    if not path.is_file():
        return {}
    conn = duckdb_store.connect(path, read_only=True)
    try:
        if not duckdb_store.table_exists(conn, "daily_k"):
            return {}
        marks = ", ".join("?" for _ in wanted)
        rows = conn.execute(
            f'SELECT code, trade_date, payload FROM "daily_k" '
            f"WHERE code IN ({marks}) ORDER BY trade_date",
            sorted(wanted),
        ).fetchall()
    finally:
        duckdb_store.close(conn)

    ceiling = as_of.isoformat() if as_of is not None else None
    out: dict[str, dict[str, dict[str, float]]] = {}
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
        close = as_float(pick(record, PRICE_KEYS))
        if close is None or close <= 0:
            continue
        bar = {"close": close}
        high = as_float(pick(record, HIGH_KEYS))
        low = as_float(pick(record, LOW_KEYS))
        if high is not None and high > 0:
            bar["high"] = high
        if low is not None and low > 0:
            bar["low"] = low
        out.setdefault(code, {})[day] = bar
    return out


def load_index_closes(code: str = BENCHMARK_CODE, *, root: Path | None = None) -> dict[str, float]:
    """`index_daily` → `{day: close}`；本机没有缓存返回空字典（不联网）。"""
    path = duckdb_store.db_path(root)
    if not path.is_file():
        return {}
    conn = duckdb_store.connect(path, read_only=True)
    try:
        if not duckdb_store.table_exists(conn, INDEX_DATASET):
            return {}
        rows = conn.execute(
            f'SELECT trade_date, payload FROM "{INDEX_DATASET}" WHERE code = ? ORDER BY trade_date',
            (str(code).strip(),),
        ).fetchall()
    finally:
        duckdb_store.close(conn)
    closes: dict[str, float] = {}
    for raw_day, payload in rows:
        day = str(raw_day)[:10]
        try:
            record = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        price = as_float(pick(record, PRICE_KEYS))
        if price is not None and price > 0:
            closes.setdefault(day, price)
    return closes


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #


def _hold_days(trades: list[Any], symbol: str, sell_day: str) -> int | None:
    """卖出前该票最早一笔买入到卖出日的自然日数（近似持有期，不猜缺失日期）。"""
    buys = [
        trade
        for trade in trades
        if str(trade.symbol).strip().upper() == symbol
        and str(trade.type) == "buy"
        and str(trade.date) <= sell_day
    ]
    if not buys:
        return None
    first = min(buys, key=lambda trade: str(trade.date))
    try:
        start = date.fromisoformat(str(first.date))
        end = date.fromisoformat(sell_day)
    except ValueError:
        return None
    return max(0, (end - start).days)


def build_summary(
    *, root: Path | None = None, days: int = 365, as_of: date | None = None
) -> dict[str, Any]:
    """一次汇总：指标 / 净值曲线 / 基准 / 归因 / 行为 / 错题本。"""
    trades = portfolio_store.list_trades(root=root)
    if not trades:
        return _empty_summary("本机还没有交易流水（先 journal add 或页面录入）")

    symbols = sorted({str(trade.symbol).strip().upper() for trade in trades})
    bars = load_daily_bars(symbols, root=root, as_of=as_of)
    if not bars:
        return _empty_summary("本机 daily_k 缓存为空（先同步日线），指标算不出")

    closes = {
        code: {day: bar["close"] for day, bar in days_map.items()}
        for code, days_map in bars.items()
    }
    view = portfolio_perf.compute_view(trades, closes, as_of=as_of, days=days)
    groups = view.get("groups") or []
    group = groups[0] if groups else {}
    if not group.get("ok"):
        reason = group.get("reason") or "组合收益率算不出（原因见 gaps）"
        return _empty_summary(reason, view=view)

    series = group.get("series") or {}
    curve_dates: list[str] = list(series.get("dates") or [])
    curve_values: list[float] = list(series.get("twr_index") or [])
    drawdown = max_drawdown(curve_values)
    sharpe_value, annual_vol = sharpe(curve_values)

    replayed = replay_portfolio(list(trades), "average")
    realized_stats = summarize_realized(list(replayed.realized))

    benchmark = _benchmark_block(curve_dates, root=root)
    excess = None
    if group.get("twr") is not None and benchmark.get("return") is not None:
        excess = round(float(group["twr"]) - float(benchmark["return"]), 6)

    attribution = _attribution_blocks(replayed)
    behavior = _behavior_block(trades, bars)
    mistakes = _mistake_rows(replayed, trades)

    return {
        "ok": True,
        "reason": "",
        "as_of": group.get("end"),
        "start": group.get("start"),
        "span_days": group.get("span_days"),
        "metrics": {
            "twr": group.get("twr"),
            "twr_annualized": group.get("twr_annualized"),
            "xirr": group.get("xirr"),
            "max_drawdown": drawdown,
            "sharpe": sharpe_value,
            "annual_volatility": annual_vol,
            "invested": group.get("invested"),
            "end_value": group.get("end_value"),
            "gain": group.get("gain"),
            "dividends": group.get("dividends"),
            "fees": group.get("fees"),
            "realized": realized_stats,
        },
        "curve": {
            "dates": curve_dates,
            "twr_index": curve_values,
        },
        "benchmark": benchmark,
        "excess_return": excess,
        "attribution": attribution,
        "behavior": behavior,
        "mistakes": mistakes,
        "warnings": group.get("warnings") or [],
        "notes": [
            "全部数字来自本机流水 + 本机日线；缺数给 null 并写明原因，不补 0。",
            "夏普按 TWR 日收益计算（无风险利率取 0），样本不足 20 个交易日不给值。",
            "基准 = 沪深300，区间与组合净值同起同止；本机无指数缓存时不给基准与超额。",
        ],
    }


def _empty_summary(reason: str, *, view: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "reason": reason,
        "as_of": (view or {}).get("as_of"),
        "start": None,
        "span_days": None,
        "metrics": None,
        "curve": None,
        "benchmark": {
            "code": BENCHMARK_CODE,
            "label": BENCHMARK_LABEL,
            "return": None,
            "annualized": None,
            "reason": reason,
        },
        "excess_return": None,
        "attribution": [],
        "behavior": {"buckets": [], "reason": reason},
        "mistakes": [],
        "warnings": [],
        "notes": [],
    }


def _benchmark_block(curve_dates: list[str], *, root: Path | None) -> dict[str, Any]:
    """沪深300 在组合净值同区间内的涨跌幅（同起同止，不做对齐插值）。"""
    block: dict[str, Any] = {
        "code": BENCHMARK_CODE,
        "label": BENCHMARK_LABEL,
        "start": None,
        "end": None,
        "return": None,
        "annualized": None,
        "reason": None,
    }
    if len(curve_dates) < 2:
        block["reason"] = "组合净值序列不足 2 个交易日"
        return block
    closes = load_index_closes(root=root)
    if not closes:
        block["reason"] = "本机没有指数日线缓存（index_daily），基准与超额暂不算"
        return block
    first_day, last_day = curve_dates[0], curve_dates[-1]
    inside = sorted(day for day in closes if first_day <= day <= last_day)
    if len(inside) < 2:
        block["reason"] = "指数缓存没有覆盖组合净值区间，基准与超额暂不算"
        return block
    start_value = closes[inside[0]]
    end_value = closes[inside[-1]]
    if start_value <= 0:
        block["reason"] = "指数起点价非正，基准算不出"
        return block
    span = (date.fromisoformat(inside[-1]) - date.fromisoformat(inside[0])).days
    change = end_value / start_value - 1.0
    block["start"] = inside[0]
    block["end"] = inside[-1]
    block["return"] = round(change, 6)
    if span >= 365 and change > -1.0:
        block["annualized"] = round((1.0 + change) ** (365.0 / span) - 1.0, 6)
    return block


def _attribution_blocks(replayed: Any) -> list[dict[str, Any]]:
    """按标的的净贡献（已实现 + 分红 − 费用），按贡献降序。"""
    rows: list[dict[str, Any]] = []
    for holding in replayed.holdings:
        rows.append(
            {
                "symbol": holding.symbol,
                "name": holding.name,
                "realized": round(holding.realized_pnl, 4),
                "dividends": round(holding.dividends, 4),
                "fees": round(holding.fees, 4),
                "net": round(holding.realized_pnl + holding.dividends - holding.fees, 4),
                "status": "holding",
            }
        )
    for closed in replayed.closed:
        rows.append(
            {
                "symbol": closed.symbol,
                "name": closed.name,
                "realized": round(closed.realized_pnl, 4),
                "dividends": round(closed.dividends, 4),
                "fees": round(closed.fees, 4),
                "net": round(closed.realized_pnl + closed.dividends - closed.fees, 4),
                "status": "closed",
            }
        )
    rows.sort(key=lambda row: (-row["net"], row["symbol"]))
    return rows


def _behavior_block(
    trades: list[Any], bars: dict[str, dict[str, dict[str, float]]]
) -> dict[str, Any]:
    """买入行为分档 + 前瞻 5 日收益：检验「追高 / 低吸」的纪律差异。"""
    buckets: dict[str, list[dict[str, Any]]] = {"chase": [], "neutral": [], "dip": []}
    for trade in trades:
        if str(trade.type) != "buy" or trade.price is None or trade.shares is None:
            continue
        symbol = str(trade.symbol).strip().upper()
        day = str(trade.date)
        bars_map = bars.get(symbol) or {}
        bar = bars_map.get(day)
        if not bar or "high" not in bar or "low" not in bar:
            continue
        bucket = bucket_position(float(trade.price), bar["low"], bar["high"])
        if bucket is None:
            continue
        days = sorted(bars_map)
        try:
            position = days.index(day)
        except ValueError:
            continue
        if position + FORWARD_DAYS >= len(days):
            continue
        forward_day = days[position + FORWARD_DAYS]
        forward_close = bars_map[forward_day]["close"]
        if forward_close <= 0 or float(trade.price) <= 0:
            continue
        forward_return = forward_close / float(trade.price) - 1.0
        buckets[bucket].append(
            {
                "symbol": symbol,
                "date": day,
                "forward_return": round(forward_return, 6),
                "forward_day": forward_day,
            }
        )

    labels = {
        "chase": "追高（买在当日振幅上沿）",
        "dip": "低吸（买在当日振幅下沿）",
        "neutral": "中性",
    }
    rows: list[dict[str, Any]] = []
    for key in ("chase", "dip", "neutral"):
        items = buckets[key]
        if not items:
            continue
        returns = [item["forward_return"] for item in items]
        avg = sum(returns) / len(returns)
        wins = sum(1 for value in returns if value > 0)
        rows.append(
            {
                "bucket": key,
                "label": labels[key],
                "count": len(items),
                "avg_forward_return": round(avg, 6),
                "win_rate": round(wins / len(items), 4),
                "window_days": FORWARD_DAYS,
            }
        )
    return {
        "buckets": rows,
        "window_days": FORWARD_DAYS,
        "reason": "" if rows else "没有可判定的买入（需要日线的 high/low 与至少 5 个后续交易日）",
    }


def _mistake_rows(replayed: Any, trades: list[Any]) -> list[dict[str, Any]]:
    """已实现亏损最大的前 N 笔（带持有天数与单笔亏幅）。"""
    losses = [row for row in replayed.realized if row.pnl < 0]
    losses.sort(key=lambda row: row.pnl)
    rows: list[dict[str, Any]] = []
    for row in losses[:MISTAKE_LIMIT]:
        pct = round(row.pnl / row.cost, 6) if row.cost > 0 else None
        rows.append(
            {
                "symbol": row.symbol,
                "date": row.date,
                "shares": round(row.shares, 2),
                "proceeds": round(row.proceeds, 4),
                "cost": round(row.cost, 4),
                "pnl": round(row.pnl, 4),
                "pnl_pct": pct,
                "hold_days": _hold_days(trades, row.symbol, row.date),
            }
        )
    return rows


__all__ = [
    "BENCHMARK_CODE",
    "BENCHMARK_LABEL",
    "build_summary",
    "bucket_position",
    "daily_returns",
    "load_daily_bars",
    "load_index_closes",
    "max_drawdown",
    "sharpe",
    "summarize_realized",
]
