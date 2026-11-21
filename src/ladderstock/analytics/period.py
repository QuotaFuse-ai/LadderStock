"""周期报告（B 包后半）：月报 / 季报 / 年报。

口径：**期间事实包 + 模板叙述**，不调 AI（AI 叙述留给后续单；事实包先保证可复算）。
原料全部本地只读：

- 组合账本 → 期间已实现 / 分红 / 费用 / 按标的归因 / 期间错题（`core.portfolio.replay`）；
- 期间行为纪律 → 与 `analytics/engine` 同一套「买入价在当日振幅位置 → 前向 5 日」
  分档，但只统计**买入日在期间内**的笔；
- 市场宽度 → `weekly/facts.breadth_metrics` 对期间内各交易日（有复盘产物的日子）
  聚合：涨停 / 跌停 / 炸板均值与晋级率均值等，缺的日子如实计入 missing。

输出：`data/report/<month|quarter|year>/<YYYY-MM|YYYY-Qn|YYYY>.{json,md}`。
数字缺就是 `null` + 原因，不补 0——与全仓同一条红线。
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ladderstock.analytics import engine as analytics_engine
from ladderstock.cli.paths import data_dir
from ladderstock.core.portfolio import replay as replay_portfolio
from ladderstock.errors import EXIT_USAGE, LadderStockError
from ladderstock.journal import portfolio as portfolio_store
from ladderstock.weekly import facts as weekly_facts

PERIODS: tuple[str, ...] = ("month", "quarter", "year")

_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_QUARTER = re.compile(r"^(\d{4})-Q([1-4])$")
_YEAR = re.compile(r"^(\d{4})$")


def resolve_period(kind: str, of: str | None) -> tuple[date, date, str, str]:
    """`(start, end, label, tag)`；`of=None` 取上一个完整周期。

    非法格式一律当作法错误（exit 2），不猜。
    """
    period = str(kind or "").strip().lower()
    if period not in PERIODS:
        raise LadderStockError(f"period 只支持 {' / '.join(PERIODS)}，收到 {kind!r}", EXIT_USAGE)
    today = date.today()

    if of is None:
        if period == "month":
            first = today.replace(day=1)
            end = first - timedelta(days=1)
            start = end.replace(day=1)
            return start, end, f"{start:%Y-%m}", f"{start:%Y-%m}"
        if period == "quarter":
            quarter = (today.month - 1) // 3 + 1
            if quarter == 1:
                end = date(today.year - 1, 12, 31)
            else:
                end = date(today.year, quarter * 3 - 2, 1) - timedelta(days=1)
            start = end.replace(month=((end.month - 1) // 3) * 3 + 1, day=1)
            return (
                start,
                end,
                f"{start.year}-Q{(start.month - 1) // 3 + 1}",
                f"{start.year}-Q{(start.month - 1) // 3 + 1}",
            )
        end = date(today.year - 1, 12, 31)
        start = date(end.year, 1, 1)
        return start, end, f"{end.year}", f"{end.year}"

    text = str(of).strip()
    if period == "month":
        match = _MONTH.match(text)
        if not match:
            raise LadderStockError(f"月报需要 --of YYYY-MM，收到 {of!r}", EXIT_USAGE)
        year, month = int(match.group(1)), int(match.group(2))
        if not 1 <= month <= 12:
            raise LadderStockError(f"月份非法：{of!r}", EXIT_USAGE)
        start = date(year, month, 1)
        end = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
        return start, end, f"{year}-{month:02d}", f"{year}-{month:02d}"
    if period == "quarter":
        match = _QUARTER.match(text)
        if not match:
            raise LadderStockError(f"季报需要 --of YYYY-Qn（如 2026-Q4），收到 {of!r}", EXIT_USAGE)
        year, quarter = int(match.group(1)), int(match.group(2))
        start = date(year, quarter * 3 - 2, 1)
        end = date(year + (quarter == 4), (quarter * 3 % 12) + 1, 1) - timedelta(days=1)
        return start, end, f"{year}-Q{quarter}", f"{year}-Q{quarter}"
    match = _YEAR.match(text)
    if not match:
        raise LadderStockError(f"年报需要 --of YYYY，收到 {of!r}", EXIT_USAGE)
    year = int(match.group(1))
    return date(year, 1, 1), date(year, 12, 31), f"{year}", f"{year}"


def _trading_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _market_block(days: list[date], *, root: Path | None) -> dict[str, Any]:
    """期间市场情绪：逐日复盘产物的情绪段（涨停/炸板/晋级率/封板率）求均值。

    只聚合 `available=True` 的日子；缺产物的日子计入 missing，不补数。
    """
    rows: list[dict[str, Any]] = []
    missing = 0
    for day in days:
        try:
            snapshot = weekly_facts.day_snapshots(day, root=root)
        except Exception:  # noqa: BLE001 - 单日读不动只算缺
            missing += 1
            continue
        sentiment = (snapshot.get("metrics") or {}).get("sentiment") or {}
        if snapshot.get("available") and sentiment.get("available"):
            rows.append(sentiment)
        else:
            missing += 1

    def _avg(key: str) -> float | None:
        values = [
            float(row[key])
            for row in rows
            if isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool)
        ]
        if not values:
            return None
        return round(sum(values) / len(values), 4)

    block: dict[str, Any] = {
        "available": bool(rows),
        "covered_days": len(rows),
        "missing_days": missing,
        "limit_up_avg": _avg("limit_up_count"),
        "broken_avg": _avg("broken_count"),
        "promotion_avg": _avg("promotion_rate"),
        "seal_avg": _avg("seal_rate"),
        "reason": "",
    }
    if not rows:
        block["reason"] = "期间没有可用的复盘日数据（先跑每日复盘；缺数不估算）"
    return block


def report_path(kind: str, tag: str, *, suffix: str, root: Path | None = None) -> Path:
    base = (root if root is not None else data_dir()) / "report" / kind
    return base / f"{tag}.{suffix}"


def build_report(kind: str, of: str | None = None, *, root: Path | None = None) -> dict[str, Any]:
    """期间事实包（不落盘；`write_report` 负责落盘与渲染）。"""
    start, end, label, tag = resolve_period(kind, of)
    trades = portfolio_store.list_trades(root=root)
    replayed = replay_portfolio(list(trades), "average") if trades else None

    in_period = [
        row
        for row in (replayed.realized if replayed else [])
        if start.isoformat() <= str(row.date) <= end.isoformat()
    ]
    realized_total = round(sum(row.pnl for row in in_period), 4) if in_period else 0.0
    wins = sum(1 for row in in_period if row.pnl > 0)
    losses = sum(1 for row in in_period if row.pnl < 0)
    decided = wins + losses
    win_rate = round(wins / decided, 4) if decided else None

    attribution: dict[str, float] = {}
    for row in in_period:
        attribution[row.symbol] = round(attribution.get(row.symbol, 0.0) + row.pnl, 4)
    attribution_rows = [
        {"symbol": symbol, "net": net}
        for symbol, net in sorted(attribution.items(), key=lambda item: (-item[1], item[0]))
    ]

    buy_trades = [
        trade
        for trade in trades
        if str(trade.type) == "buy" and start.isoformat() <= str(trade.date) <= end.isoformat()
    ]
    symbols = sorted({str(trade.symbol).strip().upper() for trade in buy_trades})
    bars = analytics_engine.load_daily_bars(symbols, root=root) if symbols else {}
    behavior = analytics_engine._behavior_block(buy_trades, bars)  # noqa: SLF001 - 同一包口径
    mistakes = [
        {
            "symbol": row.symbol,
            "date": str(row.date),
            "pnl": round(row.pnl, 4),
        }
        for row in sorted(in_period, key=lambda item: item.pnl)
        if row.pnl < 0
    ][:5]

    days = _trading_days(start, end)
    market = _market_block(days, root=root)

    return {
        "ok": True,
        "period": kind,
        "label": label,
        "tag": tag,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "trades_available": bool(trades),
        "portfolio": {
            "realized_count": len(in_period),
            "realized_total": realized_total,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "reason": "" if in_period else "期间没有已实现交易",
        },
        "attribution": attribution_rows,
        "behavior": behavior,
        "mistakes": mistakes,
        "market": market,
        "notes": [
            "期间事实包：组合部分只算**已实现**（卖出落袋），未实现盈亏见复盘统计页的实时口径。",
            "市场宽度来自期间内本机复盘产物；缺产物的交易日计入 missing，不补数。",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    """模板叙述（不调 AI）：五节——结论 / 组合 / 归因 / 纪律 / 市场宽度。"""
    portfolio = report["portfolio"]
    label = report["label"]
    period_name = {"month": "月报", "quarter": "季报", "year": "年报"}[report["period"]]

    def pct(value: float | None) -> str:
        return "—" if value is None else f"{value * 100:.2f}%"

    def money(value: float | None) -> str:
        return "—" if value is None else f"{value:,.2f}"

    lines: list[str] = [
        f"# {label} {period_name}（{report['start']} → {report['end']}）",
        "",
        f"> 生成于 {report['generated_at']} · 纯本机事实包 + 模板叙述（不调 AI）",
        "",
        "## 一、一句话",
        "",
    ]
    if portfolio["realized_count"] == 0:
        lines.append("本期没有已实现交易（卖出落袋）；持仓浮盈浮亏请到「复盘统计」看实时口径。")
    else:
        tone = (
            "为正"
            if portfolio["realized_total"] > 0
            else "为负"
            if portfolio["realized_total"] < 0
            else "打平"
        )
        lines.append(
            f"本期 {portfolio['realized_count']} 笔已实现交易，"
            f"合计 {money(portfolio['realized_total'])}（{tone}），"
            f"胜率 {pct(portfolio['win_rate'])}"
            f"（{portfolio['wins']} 胜 / {portfolio['losses']} 负）。"
        )
    lines.extend(["", "## 二、组合结果（已实现）", ""])
    lines.append(f"- 已实现盈亏合计：{money(portfolio['realized_total'])}")
    lines.append(f"- 交易笔数：{portfolio['realized_count']} · 胜率：{pct(portfolio['win_rate'])}")
    if not report["trades_available"]:
        lines.append("- 本机没有组合账本流水（先在我的持仓页录入）。")

    lines.extend(["", "## 三、按标的归因（已实现）", ""])
    if not report["attribution"]:
        lines.append("本期没有已实现归因。")
    for row in report["attribution"]:
        lines.append(f"- {row['symbol']}：{money(row['net'])}")

    lines.extend(["", "## 四、买卖纪律", ""])
    buckets = report["behavior"]["buckets"]
    if not buckets:
        reason = report["behavior"].get("reason") or "需要日线 high/low 与 5 个后续交易日"
        lines.append(f"无可判定的买入（{reason}）。")
    for row in buckets:
        lines.append(
            f"- {row['label']}：{row['count']} 笔 · 前向 {row['window_days']} 日均值 "
            f"{pct(row['avg_forward_return'])} · 胜率 {pct(row['win_rate'])}"
        )
    if report["mistakes"]:
        lines.extend(["", "### 本期错题（已实现亏损前 5）", ""])
        for row in report["mistakes"]:
            lines.append(f"- {row['date']} {row['symbol']}：{money(row['pnl'])}")

    lines.extend(["", "## 五、市场情绪（期间复盘产物）", ""])
    market = report["market"]
    if not market.get("available"):
        lines.append(f"不可用：{market.get('reason')}")
    else:
        if market.get("limit_up_avg") is not None:
            lines.append(f"- 涨停家数均值：{market['limit_up_avg']}")
        if market.get("broken_avg") is not None:
            lines.append(f"- 炸板家数均值：{market['broken_avg']}")
        if market.get("promotion_avg") is not None:
            lines.append(f"- 晋级率均值：{pct(market['promotion_avg'])}")
        if market.get("seal_avg") is not None:
            lines.append(f"- 封板率均值：{pct(market['seal_avg'])}")
        lines.append(
            f"- 有复盘产物的交易日：{market['covered_days']} 天"
            + (f" · 缺 {market['missing_days']} 天（不补数）" if market.get("missing_days") else "")
        )

    lines.extend(["", "---", "", "口径与缺数语义见 `analytics/period.py`；本报告由程序模板生成。"])
    return "\n".join(lines)


def write_report(kind: str, of: str | None = None, *, root: Path | None = None) -> dict[str, Any]:
    """生成并落盘 JSON + Markdown；返回 `{report, json_path, md_path}`。"""
    report = build_report(kind, of, root=root)
    json_path = report_path(kind, report["tag"], suffix="json", root=root)
    md_path = report_path(kind, report["tag"], suffix="md", root=root)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_markdown(report) + "\n", encoding="utf-8")
    return {"report": report, "json_path": str(json_path), "md_path": str(md_path)}


__all__ = [
    "PERIODS",
    "build_report",
    "render_markdown",
    "report_path",
    "resolve_period",
    "write_report",
]
