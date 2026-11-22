"""`ldst valuation`：估值计算器的命令行入口（E 包 / DEC-091 §4）。

用户面换算工具：CLI 参数即输入；不取数、不写库；输出恒带免责声明。
计算器结果**不进**研究管线估值段（DEC-025 红线原样保留）。
"""

from __future__ import annotations

from typing import Annotated

import typer

from ladderstock.cli.output import OutputOption, emit, resolve_format
from ladderstock.errors import EXIT_OK, EXIT_USAGE, LadderStockError
from ladderstock.valuation import calculator


def _kind_help() -> str:
    return "pe / pb / ps / dividend / earnings-yield / dcf"


def valuation_command(
    kind: Annotated[str, typer.Argument(help=f"换算类型：{_kind_help()}")],
    price: Annotated[
        float | None, typer.Option("--price", help="现价（倍数类与股息率需要）")
    ] = None,
    eps: Annotated[float | None, typer.Option("--eps", help="每股收益（PE）")] = None,
    bps: Annotated[float | None, typer.Option("--bps", help="每股净资产（PB）")] = None,
    sps: Annotated[float | None, typer.Option("--sps", help="每股营收（PS）")] = None,
    dps: Annotated[float | None, typer.Option("--dps", help="每股分红（股息率）")] = None,
    pe_value: Annotated[float | None, typer.Option("--pe", help="PE 倍数（盈利收益率）")] = None,
    fcf: Annotated[float | None, typer.Option("--fcf", help="基期自由现金流（DCF）")] = None,
    growth: Annotated[
        float | None, typer.Option("--growth", help="增长段增速（小数，0.08=8%；DCF）")
    ] = None,
    terminal_growth: Annotated[
        float | None,
        typer.Option("--terminal-growth", help="终值增速（小数；DCF，必须小于折现率）"),
    ] = None,
    discount: Annotated[
        float | None, typer.Option("--discount", help="折现率（小数；DCF）")
    ] = None,
    periods: Annotated[
        int | None, typer.Option("--periods", help="显式预测年数（DCF，1–20）")
    ] = None,
    output: OutputOption = None,
) -> None:
    """估值换算器（用户输入 → 倍数 / 收益率 / 简化 DCF；不是估值判断）。"""
    name = str(kind or "").strip().lower()
    try:
        if name == "pe":
            if price is None or eps is None:
                raise LadderStockError("PE 需要 --price 与 --eps", EXIT_USAGE)
            payload = calculator.pe(price, eps)
        elif name == "pb":
            if price is None or bps is None:
                raise LadderStockError("PB 需要 --price 与 --bps", EXIT_USAGE)
            payload = calculator.pb(price, bps)
        elif name == "ps":
            if price is None or sps is None:
                raise LadderStockError("PS 需要 --price 与 --sps", EXIT_USAGE)
            payload = calculator.ps(price, sps)
        elif name == "dividend":
            if price is None or dps is None:
                raise LadderStockError("股息率需要 --price 与 --dps", EXIT_USAGE)
            payload = calculator.dividend_yield(price, dps)
        elif name in ("earnings-yield", "ey"):
            if pe_value is None:
                raise LadderStockError("盈利收益率需要 --pe", EXIT_USAGE)
            payload = calculator.earnings_yield(pe_value)
        elif name == "dcf":
            missing_dcf = [
                label
                for label, value in (
                    ("fcf", fcf),
                    ("growth", growth),
                    ("terminal-growth", terminal_growth),
                    ("discount", discount),
                    ("periods", periods),
                )
                if value is None
            ]
            if missing_dcf:
                raise LadderStockError(
                    f"DCF 需要全部五个参数，缺：{'、'.join(missing_dcf)}",
                    EXIT_USAGE,
                )
            assert fcf is not None and growth is not None and terminal_growth is not None
            assert discount is not None and periods is not None
            payload = calculator.dcf_simple(fcf, growth, terminal_growth, discount, periods)
        else:
            raise LadderStockError(f"未知换算类型 {kind!r}；可用：{_kind_help()}", EXIT_USAGE)
    except LadderStockError as exc:
        typer.echo(f"ldst: {exc.message}", err=True)
        raise typer.Exit(exc.code) from None

    if resolve_format(output) != "table":
        emit(payload, output)
        raise typer.Exit(EXIT_OK)
    typer.echo(f"{payload['kind']}：{payload.get('value')}")
    if "value_pct" in payload:
        typer.echo(f"  = {payload['value_pct']}")
    if name == "dcf":
        typer.echo(f"  显式期现值 {payload['pv_explicit']} · 终值现值 {payload['pv_terminal']}")
        share = payload.get("terminal_share")
        typer.echo(f"  终值占比 {f'{share:.1%}' if share is not None else '—'}")
    for warning in payload.get("warnings") or []:
        typer.echo(f"  ! {warning}")
    typer.echo(f"  {payload['disclaimer']}")
    raise typer.Exit(EXIT_OK)


def register(app: typer.Typer) -> None:
    app.command("valuation", help="估值换算器（PE/PB/PS/股息率/盈利收益率/简化DCF）")(
        valuation_command
    )


__all__ = ["register", "valuation_command"]
