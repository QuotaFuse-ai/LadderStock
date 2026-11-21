"""`ldst analytics`：组合统计的命令行入口（B 包）。

一次输出「指标 / 基准 / 归因 / 行为 / 错题本」六块，与 Web
`GET /api/analytics/summary` 同一个执行体（`analytics/engine.build_summary`）。
纯本地：只读本机交易流水与 daily_k / index_daily 缓存，不联网。
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from ladderstock.analytics import engine
from ladderstock.cli.output import OutputOption, emit, resolve_format


def _pct(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.2f}%"


def _num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}"


def _print_human(payload: dict[str, Any]) -> None:
    if not payload.get("ok"):
        typer.echo(f"算不出：{payload.get('reason')}")
        return
    metrics = payload["metrics"]
    realized = metrics["realized"]
    typer.echo(f"区间 {payload['start']} → {payload['as_of']}（{payload['span_days']} 天）")
    typer.echo("— 组合指标 —")
    typer.echo(
        f"  总收益 {_pct(metrics['twr'])} · 年化 {_pct(metrics['twr_annualized'])}"
        f" · XIRR {_pct(metrics['xirr'])}"
    )
    typer.echo(
        f"  最大回撤 {_pct(metrics['max_drawdown'])} · 夏普 {_num(metrics['sharpe'])}"
        f" · 年化波动 {_pct(metrics['annual_volatility'])}"
    )
    typer.echo(
        f"  净投入 {_num(metrics['invested'])} · 期末市值 {_num(metrics['end_value'])}"
        f" · 总盈亏 {_num(metrics['gain'])}"
    )
    typer.echo(
        f"  已实现 {realized['count']} 笔：胜率 {_pct(realized['win_rate'])}"
        f" · 盈亏比 {_num(realized['profit_factor'])}"
        f" · 均盈 {_num(realized['avg_win'])} / 均亏 {_num(realized['avg_loss'])}"
    )
    bench = payload["benchmark"]
    if bench.get("return") is not None:
        typer.echo(
            f"— 基准 — 沪深300 同区间 {_pct(bench['return'])}"
            f" · 超额 {_pct(payload.get('excess_return'))}"
        )
    else:
        typer.echo(f"— 基准 — {bench.get('reason')}")

    typer.echo("— 交易归因（按标的净贡献）—")
    if not payload["attribution"]:
        typer.echo("  （没有持仓/已清仓记录）")
    for row in payload["attribution"][:10]:
        typer.echo(
            f"  {row['symbol']} {row['name']}：净 {_num(row['net'])}"
            f"（已实现 {_num(row['realized'])} · 分红 {_num(row['dividends'])}"
            f" · 费用 {_num(row['fees'])} · {row['status']}）"
        )

    typer.echo("— 行为统计（买入价在当日振幅的位置 → 5 日收益）—")
    behavior = payload["behavior"]
    if not behavior["buckets"]:
        typer.echo(f"  {behavior.get('reason')}")
    for row in behavior["buckets"]:
        typer.echo(
            f"  {row['label']}：{row['count']} 笔 · 均值 {_pct(row['avg_forward_return'])}"
            f" · 5 日胜率 {_pct(row['win_rate'])}"
        )

    typer.echo("— 错题本（已实现亏损最大）—")
    if not payload["mistakes"]:
        typer.echo("  （没有亏损的已实现交易）")
    for row in payload["mistakes"]:
        hold = "—" if row["hold_days"] is None else f"{row['hold_days']} 天"
        typer.echo(
            f"  {row['date']} {row['symbol']}：{_num(row['pnl'])}（{_pct(row['pnl_pct'])}）"
            f" · 持有 {hold}"
        )
    for warning in payload.get("warnings") or []:
        typer.echo(f"  ! {warning}")


def analytics_command(
    days: Annotated[int, typer.Option("--days", help="回看自然日数")] = 365,
    output: OutputOption = None,
) -> None:
    """组合统计：指标 / 基准 / 归因 / 行为 / 错题本（只读本机，不联网）。"""
    payload = engine.build_summary(days=days)
    if resolve_format(output) != "table":
        emit(payload, output)
    else:
        _print_human(payload)
    if not payload.get("ok"):
        # 6 = 无结果（没有流水或日线）与全仓口径一致
        raise typer.Exit(code=6)


def register(app: typer.Typer) -> None:
    app.command("analytics", help="组合统计（指标/基准/归因/行为/错题本）")(analytics_command)


__all__ = ["analytics_command", "register"]
