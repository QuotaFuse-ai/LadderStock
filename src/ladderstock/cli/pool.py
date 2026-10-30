"""`ldst pool` 命令体：当日候选池打分（程序直出，DEC-006 闸门 5）。

实现与注册分离：`cli/main.py` 只 import + 一行 `register_pool(app)`。

契约（docs/CLI.md §D″）：

- 输出外层是 `LadderEnvelope`（与 `ldst ladder show` 同一条装配路径，公式不复制）；
- `--date` 缺省取今天；`--limit` 控制返回条数；
- 涨停池为空 / 无一条可解析 → stdout 仍给完整结构化结果，`available=false`，
  退出码 **6**（DEC-012），stderr 说明原因；
- **只读本机缓存**，不发起任何网络请求；不读本机隐私库（持仓不参与打分）。
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Annotated, Any

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.cli.review import _metric_envelope, parse_date
from ladderstock.core import ladder as ladder_module
from ladderstock.core import pool as pool_module
from ladderstock.errors import EXIT_NOT_FOUND, EXIT_OK, EXIT_USAGE

DEFAULT_LIMIT = 30


def pool(
    date: Annotated[
        str | None, typer.Option("--date", help="交易日 YYYY-MM-DD（缺省今天）")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="最多返回几只候选")] = DEFAULT_LIMIT,
    output: OutputOption = None,
) -> None:
    """候选池打分：强度 / 换手 / 地位 / 资金 / 题材五个分项 + 加权总分。"""
    day = parse_date(date) if date else date_type.today().isoformat()
    if limit <= 0:
        error("--limit 必须大于 0")
        raise typer.Exit(EXIT_USAGE)

    result: dict[str, Any] = pool_module.compute_pool(day, limit=limit)
    emit(
        _metric_envelope(
            domain="sentiment",
            topic="candidate_pool",
            payload=result,
            meta=ladder_module.cache_meta(list(pool_module.POOL_DATASETS), day),
        ),
        output,
    )
    if not result.get("available"):
        error(f"{day} 候选池不可用：{result.get('reason')}")
        raise typer.Exit(EXIT_NOT_FOUND)
    raise typer.Exit(EXIT_OK)


def register(app: typer.Typer) -> None:
    """把 `pool` 挂到根命令上（main.py 只加一行注册）。"""
    app.command("pool", help="当日候选池打分（强度/换手/地位/资金/题材）")(pool)


__all__ = ["DEFAULT_LIMIT", "pool", "register"]
