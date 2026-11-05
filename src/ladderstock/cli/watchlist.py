"""`ldst watch add|rm|list`：我的自选清单的本机读写（DEC-052 §2.3 要求的那条写路径）。

⚠️ **同一个 `watch` 组里有两类命令**：`start|status|stop` 是「盘中盯盘」（离线单轮信号
扫描，`cli/watch.py`），本模块的 `add|rm|list` 是「我的自选」清单（本机隐私库 `watchlist`
表）。两者只是共用一个组名，读写的东西完全不同——名字由 QUOT-380 任务书指定，
歧义已在 PR 里登记。

自选的增删只写本机 SQLite：本机页面（`POST /api/watchlist` 等，DEC-055）与这里的两条
命令，落的是同一张表、同一组函数。HTTP 写面是回环独占 + 来源证据，非回环出口一条都不开。
"""

from __future__ import annotations

from typing import Annotated, NoReturn

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.errors import EXIT_OK, EXIT_USAGE, LadderStockError
from ladderstock.journal.watchlist import add_watch, list_watch, remove_watch


def _fail(exc: LadderStockError) -> NoReturn:
    error(exc.message)
    raise typer.Exit(exc.code if exc.code else EXIT_USAGE) from exc


def watchlist_add(
    code: Annotated[str, typer.Option(help="证券代码（6 位数字，可带 sh/sz/bj 前缀）")],
    output: OutputOption = None,
) -> None:
    """加一条自选：只写本机 `data/journal.sqlite`，不上传、不联网、不通知。"""
    try:
        row = add_watch(code)
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


def watchlist_remove(
    code: Annotated[str, typer.Option(help="证券代码")],
    output: OutputOption = None,
) -> None:
    """删一条自选：只删本机记录；不在清单里是「无结果」（exit 6），不静默成功。"""
    try:
        removed = remove_watch(code)
    except LadderStockError as exc:
        _fail(exc)
    emit(
        {
            "ok": True,
            "removed": removed,
            "local_only": True,
            "store": "local_sqlite",
        },
        output,
    )
    raise typer.Exit(EXIT_OK)


def watchlist_list(output: OutputOption = None) -> None:
    """我的自选清单（按加入时间排序）：只出代码与加入时间，**`note` 不出**（DEC-052 §2.2）。"""
    try:
        rows = list_watch()
    except LadderStockError as exc:
        _fail(exc)
    emit(
        {
            "ok": True,
            "local_only": True,
            "store": "local_sqlite",
            "watch_count": len(rows),
            "items": rows,
            "detail": (
                "本机还没有自选（ldst watch add --code 600000）"
                if not rows
                else "自选只存本机，不经 HTTP 写入"
            ),
        },
        output,
    )
    raise typer.Exit(EXIT_OK)


def register(watch_app: typer.Typer) -> None:
    """把自选清单的三个子命令挂到 main.py 已建好的 `watch` 组上。"""
    watch_app.command("add", help="新增自选（仅写本机 SQLite，与盘中盯盘无关）")(watchlist_add)
    watch_app.command("rm", help="删除自选（仅删本机记录，与盘中盯盘无关）")(watchlist_remove)
    watch_app.command("list", help="我的自选清单（仅本机，不含备注）")(watchlist_list)


__all__ = ["register", "watchlist_add", "watchlist_list", "watchlist_remove"]
