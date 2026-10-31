"""`ldst position add|list|update|remove` 命令体（仅本机隐私库，DEC-006）。

「我的持仓」是本机私有数据：只读写 `data/journal.sqlite` 的 `positions` 表，
只在本机终端打印；**不进 MCP 工具清单、不进 `LadderEnvelope`、不进 AI 上下文**，
也不会自动生成文件——只有显式 `--export <path>` 才落盘（`docs/MCP_TOOLS.md` §2）。

行情价由本命令读**本机 `daily_k` 缓存**后注入 `journal.position.compute_metrics()`：
`journal/` 目录因此保持「只依赖 `data/sqlite_store`」，取不到价就是 `null`，
不拿成本价冒充现价、不联网补数。

实现与注册分离：`cli/main.py` 只 import + 一行 `register_position(position_app)`。
"""

from __future__ import annotations

import json
import sys
from datetime import date as date_type
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.cli.review import parse_date
from ladderstock.core.ladder import CODE_KEYS, as_float, dataset_snapshot, pick
from ladderstock.data import archive
from ladderstock.errors import EXIT_CONFIG, EXIT_OK, EXIT_USAGE, LadderStockError
from ladderstock.journal.position import (
    add_position,
    compute_metrics,
    list_positions,
    remove_position,
    update_position,
)

#: `daily_k` 里「收盘价」的候选键（同既有指标口径的容差表）
PRICE_KEYS: tuple[str, ...] = ("close", "close_price", "latest_price", "price", "收盘")

PRICE_DATASET = "daily_k"


def _fail(exc: LadderStockError) -> NoReturn:
    error(exc.message)
    raise typer.Exit(exc.code if exc.code else EXIT_USAGE) from exc


def _price_map(codes: list[str], day: str | None) -> tuple[dict[str, float], dict[str, Any]]:
    """从本机 `daily_k` 缓存取最新价（`code -> price`）与溯源状态。

    缺省读缓存里可用的最后一天；缓存没有就是「没有」——`status.reason` 说清原因，
    返回空映射，让每条持仓的 `latest_price` 如实为 `null`。
    """
    status: dict[str, Any] = {
        "dataset": PRICE_DATASET,
        "date": None,
        "backend": None,
        "reason": None,
    }
    if not codes:
        status["reason"] = "本机没有持仓，无需查价"
        return {}, status

    if day:
        wanted = date_type.fromisoformat(parse_date(day))
    else:
        cached = archive.latest_day(PRICE_DATASET)
        if cached is None:
            status["reason"] = (
                f"本机缓存里没有 {PRICE_DATASET} 数据（先 ldst sync --dataset {PRICE_DATASET}）"
            )
            return {}, status
        wanted = cached

    records, backend = dataset_snapshot(PRICE_DATASET, wanted)
    status["date"] = wanted.isoformat()
    status["backend"] = backend
    if not records:
        status["reason"] = f"{PRICE_DATASET} 在 {wanted.isoformat()} 没有缓存记录"
        return {}, status

    wanted_set = {code.upper() for code in codes}
    prices: dict[str, float] = {}
    for record in records:
        code = str(pick(record, CODE_KEYS) or "").strip().upper()
        if code not in wanted_set or code in prices:
            continue
        price = as_float(pick(record, PRICE_KEYS))
        if price is not None and price > 0:
            prices[code] = price
    if not prices:
        status["reason"] = f"{PRICE_DATASET} 缓存里没有这些代码的收盘价字段"
    return prices, status


def position_add(
    code: Annotated[str, typer.Option(help="证券代码")],
    cost: Annotated[float, typer.Option(help="成本价")],
    qty: Annotated[float, typer.Option(help="数量（股）")],
    position_date: Annotated[
        str | None, typer.Option("--date", help="建仓日 YYYY-MM-DD（缺省今天）")
    ] = None,
    industry: Annotated[
        str, typer.Option(help="行业标注（供行业分布统计；不标注就不参与该统计）")
    ] = "",
    note: Annotated[str, typer.Option(help="备注")] = "",
    output: OutputOption = None,
) -> None:
    """新增一条持仓：只写本机 `data/journal.sqlite`，不上传、不通知。"""
    try:
        row = add_position(
            code, cost, qty, entry_date=position_date, note=note, industry=industry
        )
    except LadderStockError as exc:
        _fail(exc)
    emit(
        {
            "ok": True,
            "added": row,
            "local_only": True,
            "store": "local_sqlite",
        },
        output,
    )
    raise typer.Exit(EXIT_OK)


def position_list(
    code: Annotated[str | None, typer.Option(help="只看某只证券")] = None,
    price_date: Annotated[
        str | None, typer.Option("--date", help="行情日期 YYYY-MM-DD（缺省取缓存最后一天）")
    ] = None,
    export: Annotated[
        str | None,
        typer.Option(
            "--export",
            help="把本次结果另存为 JSON 的**路径**（默认不落盘；持仓是隐私数据）",
        ),
    ] = None,
    output: OutputOption = None,
) -> None:
    """我的持仓 + 本地计算：浮动盈亏、集中度、行业分布（只读本机）。"""
    try:
        rows = list_positions(code=code)
        prices, status = _price_map([str(row["code"]) for row in rows], price_date)
        payload = compute_metrics(rows, prices, as_of=status["date"])
    except LadderStockError as exc:
        _fail(exc)

    payload["price_lookup"] = status
    if status["reason"]:
        sys.stderr.write(f"ldst: 持仓现价不可用：{status['reason']}\n")
        sys.stderr.flush()

    if export:
        _write_export(payload, Path(export))

    emit(payload, output)
    raise typer.Exit(EXIT_OK)


def _write_export(payload: dict[str, Any], target: Path) -> None:
    """显式 `--export` 才落盘；写了就必须在 stderr 提醒（同 `ldst export --with-journal`）。"""
    payload["exported"] = str(target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        error(f"导出失败：{target}（{exc}）")
        raise typer.Exit(EXIT_CONFIG) from None
    sys.stderr.write(
        f"ldst: 已导出本机持仓明细（隐私数据，请自行保管，勿上传）：{target}\n"
    )
    sys.stderr.flush()


def position_update(
    code: Annotated[str, typer.Option(help="证券代码")],
    cost: Annotated[float | None, typer.Option(help="新成本价")] = None,
    qty: Annotated[float | None, typer.Option(help="新数量（股）")] = None,
    position_date: Annotated[
        str | None, typer.Option("--date", help="新建仓日 YYYY-MM-DD")
    ] = None,
    industry: Annotated[str | None, typer.Option(help="新行业标注")] = None,
    note: Annotated[str | None, typer.Option(help="新备注")] = None,
    output: OutputOption = None,
) -> None:
    """修改一条持仓：只改显式传入的字段，只写本机。"""
    try:
        row = update_position(
            code,
            cost=cost,
            qty=qty,
            entry_date=position_date,
            industry=industry,
            note=note,
        )
    except LadderStockError as exc:
        _fail(exc)
    emit(
        {
            "ok": True,
            "updated": row,
            "local_only": True,
            "store": "local_sqlite",
        },
        output,
    )
    raise typer.Exit(EXIT_OK)


def position_remove(
    code: Annotated[str, typer.Option(help="证券代码")],
    output: OutputOption = None,
) -> None:
    """删除一条持仓：只删本机记录，不产生任何文件。"""
    try:
        outcome = remove_position(code)
    except LadderStockError as exc:
        _fail(exc)
    emit(
        {**outcome, "local_only": True, "store": "local_sqlite"},
        output,
    )
    raise typer.Exit(EXIT_OK)


def register(app: typer.Typer) -> None:
    """把 `position` 子命令挂到 main.py 已建好的 Typer 组上（main.py 只加一行注册）。"""
    app.command("add", help="新增持仓（仅写本机 SQLite）")(position_add)
    app.command("list", help="查询持仓 + 本地盈亏/集中度/行业分布（仅本机）")(position_list)
    app.command("update", help="修改持仓（仅写本机 SQLite）")(position_update)
    app.command("remove", help="删除持仓（仅删本机记录）")(position_remove)


__all__ = ["position_add", "position_list", "position_remove", "position_update", "register"]
