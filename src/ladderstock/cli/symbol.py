"""`ldst symbol refresh|status`：全市场标的字典（代码 + 名称）。

字典解决的是「普通人开口第一句就卡住」的问题：用户说「茅台」，检索层要先知道
「茅台 = 600519」。生态里没有按名称查代码的工具，所以字典由
`data/symbol_universe.py` 用**本机代码段穷举 + `tencent_quote_batch`** 刷出来
（3.5 万个候选号码 ≈ 700 次往返，故不进每日 `sync`，按需刷新）。

退出码：成功 0；拿不到任何一行 4（全批失败，字典不会被空表覆盖）；
边界/配置错误 3（由上游抛 `LadderStockError` / `BoundaryViolation`）。
"""

from __future__ import annotations

from typing import Annotated

import typer

from ladderstock.cli.output import OutputOption, emit
from ladderstock.data import symbol_universe
from ladderstock.data.db_lock import writer_lock
from ladderstock.data.provider_registry import ProviderRegistry
from ladderstock.errors import EXIT_OK, EXIT_SOURCE


def symbol_refresh(
    codes: Annotated[
        str | None,
        typer.Option("--codes", help="只刷新这些代码（逗号分隔；默认穷举代码段）"),
    ] = None,
    chunk: Annotated[int, typer.Option("--chunk", help="每批问多少只（上限 50）")] = 50,
    workers: Annotated[
        int,
        typer.Option("--workers", help="并发批数（默认 1：同一 peer 的 MCP 客户端不是线程安全）"),
    ] = 1,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="只取候选号码前 N 个（调试用，默认全量）"),
    ] = None,
    output: OutputOption = None,
) -> None:
    """刷新全市场标的字典，落本机 duckdb 的 `symbol_basic` 表。"""
    pool: list[str] | None = None
    if codes:
        pool = [item.strip() for item in codes.split(",") if item.strip()]
    else:
        pool = symbol_universe.candidate_codes()
        if limit:
            pool = pool[: max(1, int(limit))]

    registry = ProviderRegistry()

    def progress(done: int, total: int, found: int) -> None:
        if done % 100 == 0 or done == total:
            typer.echo(f"  {done}/{total} 批，已收 {found} 只", err=True)

    with writer_lock(holder="ldst symbol refresh"):
        result = symbol_universe.refresh(
            registry,
            codes=pool,
            chunk=max(1, min(int(chunk or 50), symbol_universe.CHUNK_CODES)),
            workers=max(1, min(int(workers or 1), 4)),
            root=registry.root,
            progress=progress,
        )

    exit_code = EXIT_OK if result.rows else EXIT_SOURCE
    payload = {
        "command": "ldst symbol refresh",
        "result": result.as_dict(),
        "exit_code": exit_code,
    }
    emit(payload, output)
    raise typer.Exit(exit_code)


def symbol_status(output: OutputOption = None) -> None:
    """看本机字典现状：多少只、哪天刷的、表在不在。"""
    registry = ProviderRegistry()
    latest = symbol_universe.latest_trade_date(registry.root)
    rows = symbol_universe.count_rows(registry.root)
    payload = {
        "command": "ldst symbol status",
        "dataset": symbol_universe.DATASET,
        "rows": rows,
        "latest_trade_date": latest.isoformat() if latest else None,
        "candidate_codes": len(symbol_universe.candidate_codes()),
        "exit_code": EXIT_OK,
    }
    emit(payload, output)


def symbol_lookup(
    query: Annotated[str, typer.Argument(help="代码或中文名称")],
    limit: Annotated[int, typer.Option("--limit", help="返回条数")] = 5,
    output: OutputOption = None,
) -> None:
    """按检索口径查一次字典（与 `/api/symbols/search` 同一实现）。"""
    from ladderstock.cli.paths import data_dir
    from ladderstock.web.symbols import SymbolPlane

    plane = SymbolPlane(data_root=data_dir())
    envelope = plane.search(query, limit)
    payload = {
        "command": "ldst symbol lookup",
        "query": query,
        **envelope["payload"],
        "exit_code": EXIT_OK if envelope["payload"].get("matches") else EXIT_SOURCE,
    }
    emit(payload, output)


def register(symbol_app: typer.Typer) -> None:
    """把三个命令挂到 `ldst symbol` 子应用上（与 `cli/schedule.py:register` 同形）。"""
    symbol_app.command("refresh", help="刷新全市场标的字典（代码段穷举 + 批量报价）")(
        symbol_refresh
    )
    symbol_app.command("status", help="看字典现状（行数 / 最近刷新日）")(symbol_status)
    symbol_app.command("lookup", help="按检索口径查一次字典（代码或中文名称）")(symbol_lookup)


__all__ = ["symbol_lookup", "symbol_refresh", "symbol_status", "register"]
