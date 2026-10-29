"""`ldst journal add|list|stats` 命令体（仅本机 SQLite，DEC-006 隐私边界）。

实现与注册分离：`cli/main.py` 只 import + 一行 `register_journal(journal_app)`。
本模块不引入任何网络/信封/工具注册，输出只走 `emit()` 打到本机终端。
"""

from __future__ import annotations

from typing import Annotated, Any, NoReturn

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.errors import EXIT_OK, EXIT_USAGE, LadderStockError
from ladderstock.journal.log import add_trade, list_trades
from ladderstock.journal.stats import DEFAULT_DEVIATION_THRESHOLD, compute_stats

DEFAULT_LIST_LIMIT = 200


def _fail(exc: LadderStockError) -> NoReturn:
    error(exc.message)
    raise typer.Exit(exc.code if exc.code else EXIT_USAGE) from exc


def journal_add(
    code: Annotated[str, typer.Option(help="证券代码")],
    side: Annotated[str, typer.Option(help="buy / sell")],
    price: Annotated[float, typer.Option(help="成交价")],
    qty: Annotated[float, typer.Option(help="数量")],
    note: Annotated[str, typer.Option(help="备注")] = "",
    trade_date: Annotated[
        str | None, typer.Option("--date", help="成交日 YYYY-MM-DD（缺省今天）")
    ] = None,
    plan_price: Annotated[
        float | None, typer.Option("--plan-price", help="计划价（供纪律复查统计偏离）")
    ] = None,
    output: OutputOption = None,
) -> None:
    """追加交易日志：只写本机 `data/journal.sqlite`，不上传、不通知。"""
    try:
        row = add_trade(
            code,
            side,
            price,
            qty,
            note=note,
            trade_date=trade_date,
            plan_price=plan_price,
        )
    except LadderStockError as exc:
        _fail(exc)

    emit(
        {
            "ok": True,
            "id": row["id"],
            "trade_date": row["trade_date"],
            "code": row["code"],
            "side": row["side"],
            "price": row["price"],
            "qty": row["qty"],
            "plan_price": row["plan_price"],
            "local_only": True,
            "store": "local_sqlite",
        },
        output,
    )
    raise typer.Exit(EXIT_OK)


def journal_list(
    day_from: Annotated[
        str | None, typer.Option("--from", help="起始日 YYYY-MM-DD")
    ] = None,
    day_to: Annotated[str | None, typer.Option("--to", help="结束日 YYYY-MM-DD")] = None,
    code: Annotated[str | None, typer.Option(help="只看某只证券")] = None,
    limit: Annotated[int, typer.Option(help="最多返回条数")] = DEFAULT_LIST_LIMIT,
    output: OutputOption = None,
) -> None:
    """查询交易日志：只读本机库，库不存在时返回空列表。"""
    try:
        rows: list[dict[str, Any]] = list_trades(
            day_from=day_from,
            day_to=day_to,
            code=code,
            limit=limit,
        )
    except LadderStockError as exc:
        _fail(exc)
    emit(rows, output)
    raise typer.Exit(EXIT_OK)


def journal_stats(
    day_from: Annotated[
        str | None, typer.Option("--from", help="起始日 YYYY-MM-DD")
    ] = None,
    day_to: Annotated[str | None, typer.Option("--to", help="结束日 YYYY-MM-DD")] = None,
    deviation: Annotated[
        float,
        typer.Option(
            "--deviation",
            help="偏离计划阈值（相对计划价的比例，0.01 = 1%）",
        ),
    ] = DEFAULT_DEVIATION_THRESHOLD,
    output: OutputOption = None,
) -> None:
    """纪律复查统计：胜率、盈亏比、偏离计划次数（只读本机）。"""
    try:
        stats = compute_stats(
            day_from=day_from,
            day_to=day_to,
            deviation_threshold=deviation,
        )
    except LadderStockError as exc:
        _fail(exc)
    emit(stats, output)
    raise typer.Exit(EXIT_OK)


def register(app: typer.Typer) -> None:
    """把 `journal` 子命令挂到 main.py 已建好的 Typer 组上（main.py 只加一行注册）。"""
    app.command("add", help="追加交易日志（仅写本机 SQLite）")(journal_add)
    app.command("list", help="查询交易日志（仅本机）")(journal_list)
    app.command("stats", help="纪律复查统计（仅本机）")(journal_stats)


__all__ = ["journal_add", "journal_list", "journal_stats", "register"]
